"""Configuration, read once from the environment at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"config error: {name} is required but unset")
    return val


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise SystemExit(f"config error: {name}={raw!r} is not an integer")


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        raise SystemExit(f"config error: {name}={raw!r} is not a number")


def _list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    raw = default if raw is None else raw
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _google() -> tuple[str, str, str]:
    """All three, or none. Two of three is a sign-in left half done."""
    names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    values = tuple(os.environ.get(n, "").strip() for n in names)
    if any(values) and not all(values):
        missing = ", ".join(n for n, v in zip(names, values) if not v)
        raise SystemExit(
            f"config error: Google is half configured — {missing} unset.\n"
            "  Set all three (run `--google-auth` for the refresh token), or none to turn it off."
        )
    return values  # type: ignore[return-value]


def _scenes() -> tuple[str, ...]:
    scenes = _list("HA_SCENES", "")
    bad = [s for s in scenes if not s.startswith("scene.") or not s[6:].replace("_", "").isalnum()]
    if bad:
        raise SystemExit(f"config error: HA_SCENES must be scene.<id> entity IDs, not {', '.join(bad)}")
    return scenes


@dataclass(frozen=True)
class Config:
    # Not read here: ANTHROPIC_API_KEY. The bundled Claude Code CLI reads it from
    # the environment itself. Compose requires it (`:?required`), so the
    # container cannot start without it; --selftest says if it is missing.
    token: str                       # shared with the assistant bot
    model: str
    max_turns: int
    max_budget_usd: float
    timeout_s: int
    max_queue: int
    max_proposals: int
    tz: ZoneInfo
    port: int = 8080
    workdir: str = "/srv/work"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_calendar_ids: tuple[str, ...] = ("primary",)

    ha_url: str = "http://host.docker.internal:8123"
    ha_token: str = ""
    # Scenes the agent may propose running. The allowlist IS the ceiling for
    # HA writes: Assist exposure does not govern the REST call (app/scenes.py).
    ha_scenes: tuple[str, ...] = ()

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_refresh_token)

    @property
    def home_enabled(self) -> bool:
        return bool(self.ha_token)

    @property
    def scenes_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_scenes)

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "Config":
        tz_name = os.environ.get("TZ", "").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            raise SystemExit(f"config error: TZ={tz_name!r} is not a known timezone")
        token = _req("CONCIERGE_TOKEN") if require_token else os.environ.get("CONCIERGE_TOKEN", "")
        if require_token and len(token) < 32:
            raise SystemExit("config error: CONCIERGE_TOKEN is shorter than 32 characters — "
                             "generate one with `openssl rand -hex 32`")
        g_id, g_secret, g_refresh = _google()
        return cls(
            token=token,
            model=os.environ.get("CONCIERGE_MODEL", "").strip() or "claude-opus-5-5",
            max_turns=_int("CONCIERGE_MAX_TURNS", 12),
            max_budget_usd=_float("CONCIERGE_MAX_BUDGET_USD", 0.50),
            timeout_s=_int("CONCIERGE_TIMEOUT_S", 300),
            max_queue=_int("CONCIERGE_MAX_QUEUE", 3),
            max_proposals=_int("CONCIERGE_MAX_PROPOSALS", 3),
            tz=tz,
            google_client_id=g_id,
            google_client_secret=g_secret,
            google_refresh_token=g_refresh,
            google_calendar_ids=_list("GOOGLE_CALENDAR_IDS", "primary") or ("primary",),
            ha_url=os.environ.get("HA_URL", "").strip().rstrip("/") or "http://host.docker.internal:8123",
            ha_token=os.environ.get("HA_TOKEN", "").strip(),
            ha_scenes=_scenes(),
        )
