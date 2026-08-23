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

    # --- Scheduling ---
    tz: ZoneInfo
    digest_at: tuple[int, int]
    digest_enabled: bool

    # --- Inference budget (see README → "Why the caps") ---
    num_ctx: int
    ask_predict: int
    summarize_predict: int
    digest_predict: int
    llm_timeout_s: int
    max_input_chars: int

    # --- Conversational channel ---
    chat_channel_id: int | None
    chat_history_turns: int
    chat_history_chars: int
    chat_predict: int

    # --- Queue ---
    max_queue: int

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

        return cls(
            discord_token=_req("DISCORD_TOKEN"),
            guild_id=int(_req("DISCORD_GUILD_ID")),
            digest_channel_id=_channel_id(),
            allowed_user_ids=allowed,
            ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/"),
            loki_url=os.environ.get("LOKI_URL", "http://loki:3100").rstrip("/"),
            tz=tz,
            digest_at=_hhmm("DIGEST_AT", "07:30"),
            digest_enabled=os.environ.get("DIGEST_ENABLED", "true").lower() != "false",
            num_ctx=_int("OLLAMA_NUM_CTX", 4096),
            ask_predict=_int("ASK_NUM_PREDICT", 400),
            summarize_predict=_int("SUMMARIZE_NUM_PREDICT", 300),
            digest_predict=_int("DIGEST_NUM_PREDICT", 180),
            llm_timeout_s=_int("LLM_TIMEOUT_S", 300),
            max_input_chars=_int("MAX_INPUT_CHARS", 6000),
            chat_channel_id=_optional_id("DISCORD_CHAT_CHANNEL_ID"),
            chat_history_turns=_int("CHAT_HISTORY_TURNS", 12),
            chat_history_chars=_int("CHAT_HISTORY_CHARS", 4000),
            chat_predict=_int("CHAT_NUM_PREDICT", 350),
            max_queue=_int("MAX_QUEUE", 8),
        )
