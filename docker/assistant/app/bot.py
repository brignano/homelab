"""The Discord surface.

Why Discord rather than the Open WebUI chat page:

  * It pushes. The digest arrives; you don't go and fetch it. On a box where a
    generation takes tens of seconds, "arrives when ready" is the difference
    between a useful model and one you avoid.
  * No inbound anything. The bot dials *out* to Discord over a WebSocket, so
    there's no port, no Caddy route, no tunnel, and no new attack surface — and
    unlike chat.home it works when you're off the tailnet.
  * The history is durable and searchable, so digests accumulate into a log.

No privileged gateway intents are needed: slash commands and context menus carry
their own payloads, so the bot never reads the channel.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from .chat import (
    CHAIN, SINGLE, THREAD, Turn, build_messages, build_system, clean_bot_text,
    context_mode, is_ignorable,
)
from .config import Config
from .digest import build as build_digest
from .facts import FactCollector
from .jobqueue import PRIORITY_INTERACTIVE, PRIORITY_SCHEDULED, JobQueue, QueueFull
from .ollama import Ollama, OllamaError
from .text import chunk, clamp_input

log = logging.getLogger(__name__)

# No persona line, deliberately. An earlier version opened with "You are a
# concise assistant running locally on a small home server… you have no live
# data", and a 3B is small enough that the self-description became the *answer*:
# asked anything, it replied by describing itself as a home server with no
# metrics. Small models answer with whatever the prompt makes most salient, so
# the only thing made salient here is answering the question.
ASK_SYSTEM = (
    "Answer the question directly. No preamble, no restating the question, and "
    "no describing yourself, your model, or your setup — the user already knows "
    "what you are. Plain prose, as brief as the question allows.\n"
    "You cannot browse the internet and cannot see the user's machines, files, "
    "or any live data. If the question needs any of those, say so in one short "
    "sentence, then answer whatever part you can from general knowledge. Never "
    "invent specifics you would need live access to know."
)

SUMMARIZE_SYSTEM = (
    "You summarize text. Return only the summary — no preamble, no title, no "
    "closing remark. Use at most 5 short bullet points, fewer if the text is "
    "short. Use only information present in the text; never add outside facts."
)

# Appended to /ask answers. The local model is a 3B with no web access; saying so
# once, every time, is cheaper than being quietly misled.
ASK_FOOTER = "-# {model} · {secs:.0f}s · local model, no web access — verify anything that matters"


class Assistant(discord.Client):
    def __init__(self, cfg: Config) -> None:
        # Conversational mode needs the Message Content privileged intent —
        # without it Discord delivers messages with an empty `content`, so the
        # bot literally cannot see what you typed. It is enabled only when a
        # chat channel is configured, so a slash-commands-only deployment keeps
        # the smaller surface. Members and Presence stay off in both cases.
        #
        # The intent is server-wide, so the narrowing is done in code: on_message
        # returns immediately unless the message is from an allowlisted user in
        # the one configured channel (or a thread under it). See _should_handle.
        intents = discord.Intents.default()
        if cfg.chat_channel_id is not None:
            intents.message_content = True
        super().__init__(intents=intents)
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)
        self.queue = JobQueue(max_size=cfg.max_queue)
        self._session: aiohttp.ClientSession | None = None
        # (measured_at, line) — see _live_metrics for why this is cached.
        self._metrics_cache: tuple[float, str] | None = None
        self.ollama: Ollama | None = None
        self.collector: FactCollector | None = None
        self._guild = discord.Object(id=cfg.guild_id)

    # --- lifecycle ---

    async def setup_hook(self) -> None:
        self._session = aiohttp.ClientSession()
        self.ollama = Ollama(
            self._session,
            base_url=self.cfg.ollama_url,
            model=self.cfg.ollama_model,
            num_ctx=self.cfg.num_ctx,
            timeout_s=self.cfg.llm_timeout_s,
            keep_alive=self.cfg.ollama_keep_alive,
        )
        self.collector = FactCollector(
            self._session,
            prometheus_url=self.cfg.prometheus_url,
            loki_url=self.cfg.loki_url,
        )
        self.queue.start()

        register_commands(self)
        # Guild-scoped sync shows up immediately, unlike global commands which
        # can take up to an hour to propagate.
        self.tree.copy_global_to(guild=self._guild)
        await self.tree.sync(guild=self._guild)

        self.heartbeat.start()
        if self.cfg.digest_enabled:
            self.scheduled_digest.change_interval(
                time=dt.time(hour=self.cfg.digest_at[0], minute=self.cfg.digest_at[1], tzinfo=self.cfg.tz)
            )
            self.scheduled_digest.start()

    async def close(self) -> None:
        self.heartbeat.cancel()
        self.scheduled_digest.cancel()
        await self.queue.stop()
        if self._session is not None:
            await self._session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("connected as %s (guild %s)", self.user, self.cfg.guild_id)
        if self.cfg.chat_channel_id:
            log.info("conversational mode active in channel %s", self.cfg.chat_channel_id)

    # --- conversational channel ---

    def _should_handle(self, message: discord.Message) -> bool:
        """Narrow the server-wide intent down to one channel and one person.

        The first check is the important one: without it the bot would answer
        its own replies and spin forever.
        """
        if message.author.bot or message.author.id == (self.user.id if self.user else None):
            return False
        if self.cfg.chat_channel_id is None:
            return False
        if message.author.id not in self.cfg.allowed_user_ids:
            return False
        # Threads started under the chat channel count as their own conversations.
        channel = message.channel
        parent_id = getattr(channel, "parent_id", None)
        if channel.id != self.cfg.chat_channel_id and parent_id != self.cfg.chat_channel_id:
            return False
        return not is_ignorable(message.content or "")

    def _to_turn(self, message: discord.Message) -> Turn | None:
        """One Discord message as a conversation turn, or None if it's noise."""
        content = message.content or ""
        if message.author.id == (self.user.id if self.user else None):
            content = clean_bot_text(content)
            role = "assistant"
        elif message.author.bot or is_ignorable(content):
            return None
        else:
            role = "user"
        content = content.strip()
        return Turn(role=role, content=content) if content else None

    async def _thread_turns(self, channel) -> list[Turn]:
        """Everything said in this thread, oldest first.

        Over-fetches, because ignorable and empty messages are dropped after.
        """
        turns: list[Turn] = []
        async for msg in channel.history(limit=self.cfg.chat_history_turns * 2):
            if (turn := self._to_turn(msg)) is not None:
                turns.append(turn)
        turns.reverse()          # history() yields newest-first
        return turns

    async def _chain_turns(self, message: discord.Message) -> list[Turn]:
        """Walk the reply chain back from `message`, oldest first.

        Each hop may cost an API call, so it is capped and guards against a
        cycle. `reference.resolved` is used when Discord already cached the
        parent, which is the common case for a recent exchange.
        """
        collected: list[discord.Message] = []
        current: discord.Message | None = message
        seen: set[int] = set()

        while current is not None and len(collected) < self.cfg.chat_history_turns:
            collected.append(current)
            ref = current.reference
            if ref is None or ref.message_id is None or ref.message_id in seen:
                break
            seen.add(ref.message_id)
            resolved = ref.resolved
            if isinstance(resolved, discord.Message):
                current = resolved
                continue
            try:
                current = await current.channel.fetch_message(ref.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.debug("reply chain stops early: %s", exc)
                break

        collected.reverse()
        return [t for m in collected if (t := self._to_turn(m)) is not None]

    async def _context_for(self, message: discord.Message) -> tuple[list[Turn], str]:
        """Gather the conversation for this message, per the rule in chat.py."""
        mode = context_mode(
            in_thread=isinstance(message.channel, discord.Thread),
            is_reply=message.reference is not None,
        )
        if mode == THREAD:
            return await self._thread_turns(message.channel), THREAD
        if mode == CHAIN:
            return await self._chain_turns(message), CHAIN
        turn = self._to_turn(message)
        return ([turn] if turn else []), SINGLE

    async def _live_metrics(self) -> str | None:
        """A compact line of current homelab readings, or None if unavailable.

        Injected rather than exposed as a tool the model can call. Tool-calling
        is unreliable on a 3B (see docs/design/tsd-ai-homelab-assistant.md), so
        the facts are gathered here, deterministically, on every turn — the model
        never decides whether to look, it simply always has them. Same rule as
        the digest: Python measures, the model only reads the numbers out.

        Cached briefly: the queries are cheap but a rapid back-and-forth would
        re-run six of them per message, and a homelab does not change
        meaningfully between two messages typed seconds apart.
        """
        if not self.cfg.chat_live_metrics or self.collector is None:
            return None

        now = time.monotonic()
        if self._metrics_cache is not None:
            measured_at, line = self._metrics_cache
            if now - measured_at < self.cfg.chat_metrics_ttl_s:
                return line

        try:
            facts = await self.collector.collect(dt.datetime.now(tz=self.cfg.tz))
        except Exception as exc:  # noqa: BLE001 — chat must still work without them
            log.warning("could not collect live metrics for chat: %s", exc)
            return None

        line = facts.compact()
        self._metrics_cache = (now, line)
        return line

    async def _chat_completion(self, message: discord.Message):
        """Gather context, gather readings, generate. Split out of on_message so
        the whole sequence sits inside one typing indicator."""
        try:
            turns, mode = await self._context_for(message)
        except discord.HTTPException as exc:
            log.warning("could not read context: %s", exc)
            turns, mode = [Turn(role="user", content=(message.content or "").strip())], SINGLE
        log.info("chat: %s context, %d turn(s)", mode, len(turns))

        messages = build_messages(
            turns,
            system=build_system(await self._live_metrics()),
            max_turns=self.cfg.chat_history_turns,
            max_chars=self.cfg.chat_history_chars,
        )
        return await self.queue.submit(
            "chat",
            lambda: self.ollama.chat(
                messages,
                num_predict=self.cfg.chat_predict,
                temperature=self.cfg.chat_temperature,
            ),
            priority=PRIORITY_INTERACTIVE,
        )

    async def on_message(self, message: discord.Message) -> None:
        if not self._should_handle(message):
            return

        channel = message.channel
        try:
            # Typing indicator held for the whole wait, starting *before* the
            # history and metrics fetches rather than just around generation.
            # Those are several round trips to Discord, Prometheus and Loki, and
            # any silent gap before the indicator appears reads as "it ignored me".
            async with channel.typing():
                completion = await self._chat_completion(message)
        except QueueFull:
            await message.reply("Busy right now — say that again in a minute.", mention_author=False)
            return
        except OllamaError as exc:
            await message.reply(f"Local model failed: `{exc}`", mention_author=False)
            return
        except Exception:  # noqa: BLE001 — a bad reply must not kill the handler
            log.exception("chat reply failed")
            return

        footer = f"-# {completion.model} · {completion.seconds:.0f}s"
        if completion.truncated:
            # It stopped at CHAT_NUM_PREDICT, not because it was finished. Say so:
            # a reply that just stops mid-sentence otherwise looks like a crash.
            footer += " · hit the length limit — ask me to continue"
        body = completion.text + "\n" + footer
        parts = chunk(body)
        try:
            await message.reply(parts[0], mention_author=False)
            for part in parts[1:]:
                await channel.send(part)
        except discord.HTTPException as exc:
            log.warning("could not post reply: %s", exc)

    # --- background loops ---

    @tasks.loop(seconds=60)
    async def heartbeat(self) -> None:
        """Touch a file so the container healthcheck can spot a wedged gateway.

        A Discord client can lose its socket and sit there without exiting, which
        a plain "is the process alive?" check would call healthy.
        """
        try:
            with open(self.cfg.heartbeat_path, "w") as fh:
                fh.write(str(int(dt.datetime.now(tz=dt.timezone.utc).timestamp())))
        except OSError as exc:
            log.warning("could not write heartbeat: %s", exc)

    @heartbeat.before_loop
    async def _before_heartbeat(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(time=dt.time(hour=7, minute=30))  # replaced in setup_hook
    async def scheduled_digest(self) -> None:
        channel = self.get_channel(self.cfg.digest_channel_id)
        if channel is None:
            log.error("digest channel %s not found", self.cfg.digest_channel_id)
            return
        try:
            message = await self.run_digest(priority=PRIORITY_SCHEDULED)
        except Exception as exc:  # noqa: BLE001 — a bad day must not kill the loop
            log.exception("scheduled digest failed")
            message = f"⚠️ Homelab digest failed to run: `{exc}`"
        for part in chunk(message):
            await channel.send(part)

    @scheduled_digest.before_loop
    async def _before_digest(self) -> None:
        await self.wait_until_ready()

    # --- work ---

    async def run_digest(self, priority: int = PRIORITY_INTERACTIVE) -> str:
        now = dt.datetime.now(tz=self.cfg.tz)
        return await self.queue.submit(
            "digest",
            lambda: build_digest(self.collector, self.ollama, now, self.cfg.digest_predict),
            priority=priority,
        )


# --- command registration -----------------------------------------------------

def register_commands(client: Assistant) -> None:
    cfg = client.cfg
    tree = client.tree

    def allowed(interaction: discord.Interaction) -> bool:
        return interaction.user.id in cfg.allowed_user_ids

    async def deny(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Not authorised to use this bot.", ephemeral=True
        )

    async def reply(interaction: discord.Interaction, content: str) -> None:
        """Deliver a finished answer, surviving an expired interaction token.

        Discord invalidates a deferred interaction after 15 minutes. A long queue
        can outlast that, so fall back to a plain channel message addressed to the
        requester rather than dropping work that already cost CPU time.
        """
        parts = chunk(content)
        try:
            await interaction.followup.send(parts[0])
            for part in parts[1:]:
                await interaction.followup.send(part)
        except discord.HTTPException as exc:
            log.warning("followup failed (%s); falling back to channel send", exc)
            channel = interaction.channel
            if channel is None:
                return
            await channel.send(f"{interaction.user.mention} {parts[0]}")
            for part in parts[1:]:
                await channel.send(part)

    async def run_llm(
        interaction: discord.Interaction,
        label: str,
        prompt: str,
        system: str,
        num_predict: int,
        temperature: float,
        footer: bool = True,
    ) -> None:
        depth = client.queue.depth
        await interaction.response.defer(thinking=True)
        if depth:
            # Set expectations up front — one worker means real waiting.
            await interaction.followup.send(
                f"-# queued behind {depth} job(s) — I'll edit in the answer when it's ready",
                ephemeral=True,
            )
        try:
            completion = await client.queue.submit(
                label,
                lambda: client.ollama.generate(
                    prompt=prompt, system=system,
                    num_predict=num_predict, temperature=temperature,
                ),
                priority=PRIORITY_INTERACTIVE,
            )
        except QueueFull:
            await reply(interaction, "Too many jobs queued right now — try again in a few minutes.")
            return
        except OllamaError as exc:
            await reply(interaction, f"Local model failed: `{exc}`")
            return

        body = completion.text
        if footer:
            body += "\n" + ASK_FOOTER.format(model=completion.model, secs=completion.seconds)
        await reply(interaction, body)

    @tree.command(description="Ask the local model a question (queued; answers when ready)")
    @app_commands.describe(question="What do you want to ask?")
    async def ask(interaction: discord.Interaction, question: str) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        text, trimmed = clamp_input(question, cfg.max_input_chars)
        if not text:
            return await interaction.response.send_message("Empty question.", ephemeral=True)
        note = "\n-# (question was truncated to fit)" if trimmed else ""
        await run_llm(
            interaction, "ask", text, ASK_SYSTEM,
            cfg.ask_predict, temperature=0.6,
        )
        if note:
            await interaction.followup.send(note, ephemeral=True)

    @tree.command(description="Summarize pasted text with the local model")
    @app_commands.describe(text="The text to summarize")
    async def summarize(interaction: discord.Interaction, text: str) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        body, trimmed = clamp_input(text, cfg.max_input_chars)
        if not body:
            return await interaction.response.send_message("Nothing to summarize.", ephemeral=True)
        prompt = f"Summarize the following text.\n\n---\n{body}\n---"
        if trimmed:
            prompt += "\n\n(Note: the text was truncated.)"
        await run_llm(
            interaction, "summarize", prompt, SUMMARIZE_SYSTEM,
            cfg.summarize_predict, temperature=0.3,
        )

    @tree.command(description="Run the homelab digest now instead of waiting for the schedule")
    async def digest(interaction: discord.Interaction) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        await interaction.response.defer(thinking=True)
        try:
            message = await client.run_digest()
        except QueueFull:
            await reply(interaction, "Too many jobs queued right now — try again shortly.")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("on-demand digest failed")
            await reply(interaction, f"Digest failed: `{exc}`")
            return
        await reply(interaction, message)

    @tree.command(description="Show model, queue depth, and the next scheduled digest")
    async def status(interaction: discord.Interaction) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        await interaction.response.defer(thinking=True, ephemeral=True)
        up = await client.ollama.available()
        lines = [
            f"**Model** `{cfg.ollama_model}` — {'ready' if up else '⚠️ unreachable or not loaded'}",
            f"**Queue** {client.queue.depth} waiting · running: {client.queue.running or 'idle'}",
        ]
        if cfg.digest_enabled:
            nxt = client.scheduled_digest.next_iteration
            when = nxt.astimezone(cfg.tz).strftime("%a %d %b %H:%M %Z") if nxt else "pending"
            lines.append(f"**Next digest** {when}")
        else:
            lines.append("**Next digest** disabled")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # Right-click a message → Apps → "Summarize message". Native-feeling, and it
    # avoids re-pasting text that is already in the channel.
    async def summarize_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        body, trimmed = clamp_input(message.content or "", cfg.max_input_chars)
        if not body:
            return await interaction.response.send_message(
                "That message has no text to summarize.", ephemeral=True
            )
        prompt = f"Summarize the following text.\n\n---\n{body}\n---"
        if trimmed:
            prompt += "\n\n(Note: the text was truncated.)"
        await run_llm(
            interaction, "summarize-message", prompt, SUMMARIZE_SYSTEM,
            cfg.summarize_predict, temperature=0.3,
        )

    tree.add_command(app_commands.ContextMenu(name="Summarize message", callback=summarize_message))


def run(cfg: Config) -> None:
    client = Assistant(cfg)
    client.run(cfg.discord_token, log_handler=None)
