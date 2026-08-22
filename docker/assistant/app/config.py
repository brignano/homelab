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
            digest_channel_id=int(_req("DISCORD_DIGEST_CHANNEL_ID")),
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
            max_queue=_int("MAX_QUEUE", 8),
        )
