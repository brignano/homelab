"""Configuration, read once from the environment at startup.

Every knob is an env var so the compose file stays the single place you tune
this stack. Anything with a sane default is optional; the three Discord values
have no sensible default and fail loudly if missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"config error: {name} is required but unset")
    return val


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"config error: {name}={raw!r} is not an integer")


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"config error: {name}={raw!r} is not a number")


def _id_set(name: str) -> frozenset[int]:
    """Parse a comma-separated list of Discord snowflake IDs."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return frozenset()
    out = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            raise SystemExit(f"config error: {name} contains a non-numeric ID {chunk!r}")
        out.add(int(chunk))
    return frozenset(out)


def _optional_id(name: str) -> int | None:
    """A Discord snowflake that may legitimately be unset.

    Unlike the digest channel, conversational mode is opt-in: leaving this blank
    turns it off entirely and the bot keeps working as slash-commands-only.
    """
    raw = os.environ.get(name, "").strip()
    if not raw or set(raw) == {"0"}:
        return None
    if not raw.isdigit():
        raise SystemExit(f"config error: {name}={raw!r} is not a numeric channel ID")
    return int(raw)


def _hhmm(name: str, default: str) -> tuple[int, int]:
    raw = os.environ.get(name, "").strip() or default
    try:
        hh, mm = raw.split(":", 1)
        h, m = int(hh), int(mm)
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError
    except ValueError:
        raise SystemExit(f"config error: {name}={raw!r} is not HH:MM")
    return h, m


def _channel_id() -> int:
    """The digest channel, with a pointer to what produces it.

    This is the one value you cannot know up front — it doesn't exist until
    `--provision --apply` creates the channel. Say so, rather than leaving
    someone staring at a bare "required but unset".
    """
    raw = os.environ.get("DISCORD_DIGEST_CHANNEL_ID", "").strip()
    if not raw or set(raw) == {"0"}:
        raise SystemExit(
            "config error: DISCORD_DIGEST_CHANNEL_ID is unset or still the placeholder.\n"
            "  Run `--provision --apply` first — it creates #digest and prints the ID to paste in."
        )
    if not raw.isdigit():
        raise SystemExit(f"config error: DISCORD_DIGEST_CHANNEL_ID={raw!r} is not a numeric channel ID")
    return int(raw)


def _list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    raw = default if raw is None else raw
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _google() -> tuple[str, str, str]:
    """All three Google values, or none of them.

    None is a supported setup: the digest simply has no "Today" section. Two of
    three is a half-finished sign-in, and running anyway would put "could not
    read calendar" in every digest instead of saying once, at startup, what is
    missing.
    """
    names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    values = tuple(os.environ.get(n, "").strip() for n in names)
    if any(values) and not all(values):
        missing = ", ".join(n for n, v in zip(names, values) if not v)
        raise SystemExit(
            f"config error: Google is half configured — {missing} unset.\n"
            "  Set all three (run `--google-auth` for the refresh token), or none to turn it off."
        )
    return values  # type: ignore[return-value]


GOOGLE_SOURCES = ("calendar", "mail")


def _home() -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """HA_TOKEN with at least one of HA_ENTITIES and HA_CALENDARS, or none.

    Both lists are required to name things, not filters with an "everything"
    default. The digest reads only what you named. A token alone would mean
    either reading every entity in the house or reading none, and neither is
    what someone who set a token meant.
    """
    token = os.environ.get("HA_TOKEN", "").strip()
    entities = _list("HA_ENTITIES", "")
    calendars = _list("HA_CALENDARS", "")
    if token and not (entities or calendars):
        raise SystemExit(
            "config error: HA_TOKEN is set but HA_ENTITIES and HA_CALENDARS are empty.\n"
            "  List the entity IDs the digest should watch, e.g. "
            "HA_ENTITIES=cover.garage_door,lock.front_door"
        )
    if (entities or calendars) and not token:
        raise SystemExit("config error: HA_ENTITIES or HA_CALENDARS is set but HA_TOKEN is not "
                         "— see docker/homeassistant/README.md")
    bad = [e for e in entities if "." not in e]
    if bad:
        raise SystemExit(f"config error: HA_ENTITIES has {', '.join(bad)} — entity IDs look like domain.name")
    bad = [c for c in calendars if not c.startswith("calendar.") or c == "calendar."]
    if bad:
        raise SystemExit(f"config error: HA_CALENDARS has {', '.join(bad)} — calendar entity IDs "
                         "look like calendar.name")
    return token, entities, calendars


@dataclass(frozen=True)
class Config:
    # --- Discord ---
    discord_token: str
    guild_id: int
    digest_channel_id: int
    allowed_user_ids: frozenset[int]

    # --- Backends (Docker-network DNS names; see docker-compose.yml) ---
    ollama_url: str
    ollama_model: str
    prometheus_url: str
    loki_url: str
    # Public Grafana base URL, used only to build the links in the digest
    # footer. Blank = no links, which is the right default: this is the one URL
    # here that must work from OUTSIDE the Docker network, since it is clicked
    # on a phone. An internal `http://grafana:3000` would render links that
    # dead-end everywhere they are actually read.
    grafana_url: str

    # --- Scheduling ---
    tz: ZoneInfo
    digest_at: tuple[int, int]
    digest_enabled: bool
    # Where the bot records that a scheduled digest posted. Read from the host
    # by scripts/heartbeat.sh, which turns it into
    # homelab_digest_timestamp_seconds for `hl-digest-missing` to alert on.
    # A host bind mount, not /tmp: it has to survive `up -d --build`, and
    # heartbeat.sh has to be able to read it while this container is stopped.
    digest_stamp_path: str

    # --- Inference budget (see README → "Why the caps") ---
    num_ctx: int
    ask_predict: int
    summarize_predict: int
    digest_predict: int
    llm_timeout_s: int
    max_input_chars: int
    ollama_keep_alive: str

    # --- Conversational channel ---
    chat_channel_id: int | None
    chat_history_turns: int
    chat_history_chars: int
    chat_predict: int
    chat_temperature: float
    chat_live_metrics: bool
    chat_metrics_ttl_s: int

    # --- Queue ---
    max_queue: int

    # --- Google (the digest's "Today" section; all blank = off) ---
    # See docs/design/tsd-personal-assistant.md, phase 1. Read-only scopes only:
    # calendar.events.readonly and gmail.metadata (headers, never bodies).
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_sources: tuple[str, ...] = GOOGLE_SOURCES
    google_calendar_ids: tuple[str, ...] = ("primary",)
    # Gmail label IDs a message must carry ALL of. Labels, not a search: the
    # gmail.metadata scope does not allow `q`.
    gmail_labels: tuple[str, ...] = ("INBOX", "UNREAD", "CATEGORY_PERSONAL")

    # --- Home Assistant (the digest's "Home" line; blank = off) ---
    # See docs/design/tsd-personal-assistant.md, phase 2, and app/home.py for
    # why this token is read-only by code, not by capability.
    ha_url: str = "http://host.docker.internal:8123"
    ha_token: str = ""
    ha_entities: tuple[str, ...] = ()
    # calendar.* entities (iCloud through the CalDAV integration), merged into
    # the Calendar line. Blank = off.
    ha_calendars: tuple[str, ...] = ()

    heartbeat_path: str = field(default="/tmp/assistant-heartbeat")

    @classmethod
    def from_env(cls) -> "Config":
        tz_name = os.environ.get("TZ", "").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            raise SystemExit(f"config error: TZ={tz_name!r} is not a known timezone")

        allowed = _id_set("DISCORD_ALLOWED_USER_IDS")
        if not allowed:
            raise SystemExit(
                "config error: DISCORD_ALLOWED_USER_IDS is required — without an "
                "allowlist anyone in the guild could queue jobs on your CPU"
            )

        g_id, g_secret, g_refresh = _google()
        ha_token, ha_entities, ha_calendars = _home()
        sources = _list("GOOGLE_SOURCES", ",".join(GOOGLE_SOURCES))
        unknown = [x for x in sources if x not in GOOGLE_SOURCES]
        if unknown:
            raise SystemExit(
                f"config error: GOOGLE_SOURCES has {', '.join(unknown)} — "
                f"choose from {', '.join(GOOGLE_SOURCES)}"
            )

        return cls(
            discord_token=_req("DISCORD_TOKEN"),
            guild_id=int(_req("DISCORD_GUILD_ID")),
            digest_channel_id=_channel_id(),
            allowed_user_ids=allowed,
            ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/"),
            loki_url=os.environ.get("LOKI_URL", "http://loki:3100").rstrip("/"),
            grafana_url=os.environ.get("GRAFANA_URL", "").strip().rstrip("/"),
            tz=tz,
            digest_at=_hhmm("DIGEST_AT", "07:30"),
            digest_enabled=os.environ.get("DIGEST_ENABLED", "true").lower() != "false",
            digest_stamp_path=os.environ.get("DIGEST_STAMP_PATH", "/state/last-digest").strip(),
            num_ctx=_int("OLLAMA_NUM_CTX", 4096),
            ask_predict=_int("ASK_NUM_PREDICT", 400),
            summarize_predict=_int("SUMMARIZE_NUM_PREDICT", 300),
            digest_predict=_int("DIGEST_NUM_PREDICT", 180),
            llm_timeout_s=_int("LLM_TIMEOUT_S", 300),
            max_input_chars=_int("MAX_INPUT_CHARS", 6000),
            ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "").strip() or "30m",
            chat_channel_id=_optional_id("DISCORD_CHAT_CHANNEL_ID"),
            chat_history_turns=_int("CHAT_HISTORY_TURNS", 12),
            chat_history_chars=_int("CHAT_HISTORY_CHARS", 4000),
            chat_predict=_int("CHAT_NUM_PREDICT", 350),
            chat_temperature=_float("CHAT_TEMPERATURE", 0.6),
            chat_live_metrics=os.environ.get("CHAT_LIVE_METRICS", "true").lower() != "false",
            chat_metrics_ttl_s=_int("CHAT_METRICS_TTL_S", 60),
            max_queue=_int("MAX_QUEUE", 8),
            google_client_id=g_id,
            google_client_secret=g_secret,
            google_refresh_token=g_refresh,
            google_sources=sources,
            google_calendar_ids=_list("GOOGLE_CALENDAR_IDS", "primary") or ("primary",),
            gmail_labels=_list("GMAIL_LABELS", "INBOX,UNREAD,CATEGORY_PERSONAL")
            or ("INBOX", "UNREAD"),
            ha_url=os.environ.get("HA_URL", "").strip().rstrip("/") or "http://host.docker.internal:8123",
            ha_token=ha_token,
            ha_entities=ha_entities,
            ha_calendars=ha_calendars,
        )

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_refresh_token and self.google_sources)

    @property
    def home_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_entities)

    @property
    def ha_calendar_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_calendars)

    @property
    def day_sources(self) -> tuple[str, ...]:
        """Every source the Today section reads, in render order."""
        return (self.google_sources if self.google_enabled else ()) + (
            ("home",) if self.home_enabled else ()) + (
            ("ha-calendar",) if self.ha_calendar_enabled else ())
