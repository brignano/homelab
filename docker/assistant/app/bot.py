"""The Discord surface: the daily digest, the weekly import reminder and two
slash commands.

Why Discord:

  * It pushes. The digest arrives; you don't go and fetch it.
  * No inbound anything. The bot dials *out* to Discord over a WebSocket, so
    there's no port, no Caddy route, no tunnel, and no new attack surface — and
    it works when you're off the tailnet.
  * The history is durable and searchable, so digests accumulate into a log.

No privileged gateway intents are needed: slash commands carry their own
payloads, so the bot never reads a channel. Conversation (#ask, Claude through
docker/concierge) was retired on 2026-11-23 (#366) for Claude Duet, which costs
nothing beyond the plan; nothing here calls a model.
"""

from __future__ import annotations

import datetime as dt
import logging
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from . import importreminder
from .config import Config
from .day import SOURCES as DAY_SOURCES, DayCollector, stamp_path as day_stamp_path
from .digest import Card, card as digest_card, prepare as prepare_digest
from .facts import FactCollector
from .google import Google
from .home import Home
from .stamp import clear as clear_stamp, read as read_stamp, write as write_stamp
from .text import chunk, clock

log = logging.getLogger(__name__)

# The furthest back a digest's "new since the last one" reaches.
SINCE_LIMIT = dt.timedelta(days=7)


class Assistant(discord.Client):
    def __init__(self, cfg: Config) -> None:
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)
        self._session: aiohttp.ClientSession | None = None
        self.collector: FactCollector | None = None
        self.day: DayCollector | None = None
        self._shown_stamp: str | None = None
        self._guild = discord.Object(id=cfg.guild_id)

    # --- lifecycle ---

    async def setup_hook(self) -> None:
        self._session = aiohttp.ClientSession()
        self.collector = FactCollector(
            self._session,
            prometheus_url=self.cfg.prometheus_url,
            loki_url=self.cfg.loki_url,
        )

        # The digest's "Today" section: Google (calendar, mail) and Home
        # Assistant, each optional. With neither configured, the digest renders
        # exactly as it did before the personal-assistant work.
        #
        # Freshness stamps (for hl-google-stale) are kept only while the
        # schedule runs: sources are read when a digest is, so with the
        # schedule off nothing would refresh them and the alert would fire
        # about a feature working as configured. A source that is off, or
        # unconfigured, has any stamp an earlier run left cleared by arm().
        state_dir = os.path.dirname(self.cfg.digest_stamp_path)
        sources = self.cfg.day_sources
        stamping = bool(sources) and self.cfg.digest_enabled and bool(state_dir)
        if sources:
            google = Google(
                self._session,
                client_id=self.cfg.google_client_id,
                client_secret=self.cfg.google_client_secret,
                refresh_token=self.cfg.google_refresh_token,
            ) if self.cfg.google_enabled else None
            home = Home(self._session, self.cfg.ha_url, self.cfg.ha_token) if (
                self.cfg.home_enabled or self.cfg.ha_calendar_enabled) else None
            self.day = DayCollector(
                google,
                calendar_ids=self.cfg.google_calendar_ids,
                mail_labels=self.cfg.gmail_labels,
                sources=sources,
                state_dir=state_dir if stamping else None,
                home=home,
                home_entities=self.cfg.ha_entities,
                ha_calendars=self.cfg.ha_calendars,
                ha_free_calendars=self.cfg.ha_free_calendars,
            )
            self.day.arm()
        if not stamping and state_dir:
            for source in DAY_SOURCES:
                clear_stamp(day_stamp_path(state_dir, source))

        # When a digest last posted, scheduled or `/digest`, so the next one
        # covers only what is new since. Separate from `last-digest`: that one
        # is a dead man's switch only the schedule may refresh (see below).
        self._shown_stamp = os.path.join(state_dir, "digest-shown") if state_dir else None

        # With the schedule off there is nothing to be stale about, so drop
        # any timestamp a previous run left behind. An alert that fires forever
        # because a feature is deliberately disabled is noise that teaches you
        # to ignore the channel.
        if not self.cfg.digest_enabled:
            clear_stamp(self.cfg.digest_stamp_path)

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
        if self.cfg.import_reminder_active:
            self.import_reminder.change_interval(
                time=dt.time(hour=self.cfg.import_reminder_at[0],
                             minute=self.cfg.import_reminder_at[1], tzinfo=self.cfg.tz)
            )
            self.import_reminder.start()

    async def close(self) -> None:
        self.heartbeat.cancel()
        self.scheduled_digest.cancel()
        self.import_reminder.cancel()
        if self._session is not None:
            await self._session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("connected as %s (guild %s)", self.user, self.cfg.guild_id)
        if self.cfg.import_reminder_active:
            log.info("bank import reminder: weekday %d at %02d:%02d",
                     self.cfg.import_reminder_day, *self.cfg.import_reminder_at)

    # --- schedules ---

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
        ok = True
        complete = False
        try:
            now = dt.datetime.now(tz=self.cfg.tz)
            draft = await prepare_digest(
                self.collector, now, day_collector=self.day, since=self._since(now),
            )
            complete = draft.complete
            message = draft.render(self.cfg.grafana_url)
        except Exception as exc:  # noqa: BLE001 — a bad day must not kill the loop
            log.exception("scheduled digest failed")
            message = f"⚠️ Homelab digest failed to run: `{exc}`"
            ok = False
        await self._send_digest(channel, message)

        # Only the SCHEDULED run records this, and only when it worked.
        # `/digest` at three in the afternoon would refresh the timestamp and
        # hide a schedule that had stopped firing — the one thing this is here
        # to catch. A digest that failed deliberately leaves the clock running,
        # so `hl-digest-missing` fires if the next one fails too.
        if ok:
            write_stamp(self.cfg.digest_stamp_path, dt.datetime.now(tz=dt.timezone.utc).timestamp())
        if complete:
            self._shown(now)

    async def _send_digest(self, out, message: str) -> discord.Message | None:
        """The digest as a card (or plain text if it can't be one), in `out`.
        The first message sent, or None if none could be."""
        quiet = discord.AllowedMentions.none()
        card = digest_card(message, self.cfg.grafana_url)
        if card is not None:
            return await out.send(embed=to_embed(card), allowed_mentions=quiet)
        parts = [await out.send(part, allowed_mentions=quiet, suppress_embeds=True)
                 for part in chunk(message)]
        return parts[0] if parts else None

    @scheduled_digest.before_loop
    async def _before_digest(self) -> None:
        await self.wait_until_ready()

    # --- the weekly bank-statement import reminder (app/importreminder.py) ---

    @tasks.loop(time=dt.time(hour=10))  # replaced in setup_hook
    async def import_reminder(self) -> None:
        """Never raises (a raising loop stops for good). Runs daily, posts on
        IMPORT_REMINDER_DAY only."""
        try:
            now = dt.datetime.now(tz=self.cfg.tz)
            if now.weekday() != self.cfg.import_reminder_day:
                return
            channel = self.get_channel(self.cfg.digest_channel_id)
            if channel is None:
                log.error("import reminder: #digest channel %s not found", self.cfg.digest_channel_id)
                return
            user = min(self.cfg.allowed_user_ids) if self.cfg.allowed_user_ids else None
            await channel.send(
                importreminder.text(now, user),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                suppress_embeds=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("import reminder failed")

    @import_reminder.before_loop
    async def _before_import_reminder(self) -> None:
        await self.wait_until_ready()

    # --- work ---

    def _since(self, now: dt.datetime) -> dt.datetime | None:
        """Where the next digest's "new" starts: when the last one posted.

        None (the last 24 hours) when no digest has posted yet. A week at most,
        so a digest after a long outage stays one readable message."""
        last = read_stamp(self._shown_stamp) if self._shown_stamp else None
        if last is None:
            return None
        since = dt.datetime.fromtimestamp(last, tz=now.tzinfo)
        if since >= now:
            return None
        return max(since, now - SINCE_LIMIT)

    def _shown(self, now: dt.datetime) -> None:
        """Record that a digest collected at `now`, with every source read,
        posted. The next digest starts from its collection time, not from when
        it finished posting, so nothing that arrived while it ran falls between
        the two."""
        if self._shown_stamp:
            write_stamp(self._shown_stamp, now.timestamp())

    async def run_digest(self) -> str:
        now = dt.datetime.now(tz=self.cfg.tz)
        draft = await prepare_digest(
            self.collector, now, day_collector=self.day, since=self._since(now),
        )
        if draft.complete:
            self._shown(now)
        return draft.render(self.cfg.grafana_url)


def to_embed(card: Card) -> discord.Embed:
    """A digest card as a Discord embed: title, status bar, body, footer."""
    embed = discord.Embed(title=card.title, description=card.description,
                          colour=card.colour, url=card.url)
    if card.footer:
        embed.set_footer(text=card.footer)
    return embed


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

        Discord invalidates a deferred interaction after 15 minutes. A slow
        digest can outlast that, so fall back to a plain channel message
        addressed to the requester rather than dropping it.
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

    @tree.command(description="Run the homelab digest now instead of waiting for the schedule")
    async def digest(interaction: discord.Interaction) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        await interaction.response.defer(thinking=True)
        try:
            message = await client.run_digest()
        except Exception as exc:  # noqa: BLE001
            log.exception("on-demand digest failed")
            await reply(interaction, f"Digest failed: `{exc}`")
            return
        card = digest_card(message, client.cfg.grafana_url)
        if card is None:
            await reply(interaction, message)
            return
        try:
            await interaction.followup.send(embed=to_embed(card))
        except discord.HTTPException as exc:
            log.warning("digest followup failed (%s); falling back to text", exc)
            await reply(interaction, message)

    @tree.command(description="Show the next and last scheduled digest")
    async def status(interaction: discord.Interaction) -> None:
        if not allowed(interaction):
            return await deny(interaction)
        await interaction.response.defer(thinking=True, ephemeral=True)
        lines: list[str] = []
        if cfg.digest_enabled:
            nxt = client.scheduled_digest.next_iteration
            when = f"{nxt.astimezone(cfg.tz).strftime('%a %d %b')} {clock(nxt.astimezone(cfg.tz))}" if nxt else "pending"
            lines.append(f"**Next digest** {when}")
            # The question "is the schedule actually firing" should be
            # answerable without reading Grafana or the box.
            last = read_stamp(cfg.digest_stamp_path)
            when_last = f"<t:{last}:R>" if last else "never — no scheduled digest recorded yet"
            lines.append(f"**Last digest** {when_last}")
        else:
            lines.append("**Next digest** disabled")
        await interaction.followup.send("\n".join(lines), ephemeral=True)



def run(cfg: Config) -> None:
    client = Assistant(cfg)
    client.run(cfg.discord_token, log_handler=None)
