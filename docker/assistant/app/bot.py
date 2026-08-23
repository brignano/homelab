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

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

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
        # Default intents only: no message content, no members, no presence.
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)
        self.queue = JobQueue(max_size=cfg.max_queue)
        self._session: aiohttp.ClientSession | None = None
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
