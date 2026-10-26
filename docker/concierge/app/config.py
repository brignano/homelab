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


def _google(signing_in: bool = False) -> tuple[str, str, str]:
    """All three, or none. Two of three is a sign-in left half done.

    Except during --google-auth, whose whole job is to produce the third: the
    client ID and secret with no refresh token is exactly where it starts.
    """
    names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    values = tuple(os.environ.get(n, "").strip() for n in names)
    if signing_in:
        return values  # type: ignore[return-value]
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


def _calendars() -> tuple[str, ...]:
    cals = _list("HA_CALENDARS", "")
    bad = [c for c in cals if not c.startswith("calendar.") or not c[9:].replace("_", "").isalnum()]
    if bad:
        raise SystemExit(f"config error: HA_CALENDARS must be calendar.<id> entity IDs, not {', '.join(bad)}")
    return cals


def _github_repos() -> tuple[str, ...]:
    """owner/name, comma-separated, or * for every repository the token can
    see. With a token set and no repositories there is nothing to read, which
    is a mistake rather than "off"."""
    from .github import ALL, REPO_RE
    repos = _list("GITHUB_REPOS", "")
    if ALL in repos:
        if repos != (ALL,):
            raise SystemExit("config error: GITHUB_REPOS is either * (every repository the token "
                             "can see) or a list of owner/name, not both")
        return repos
    bad = [r for r in repos if not REPO_RE.match(r)]
    if bad:
        raise SystemExit(f"config error: GITHUB_REPOS must be owner/name repositories, not {', '.join(bad)}")
    if os.environ.get("GITHUB_TOKEN", "").strip() and not repos:
        raise SystemExit("config error: GITHUB_TOKEN is set but GITHUB_REPOS is empty — "
                         "list the repositories to read, e.g. brignano/brignano.io")
    return repos


def _weather() -> str:
    entity = os.environ.get("HA_WEATHER_ENTITY", "weather.forecast_home").strip()
    if entity and (not entity.startswith("weather.") or not entity[8:].replace("_", "").isalnum()):
        raise SystemExit(f"config error: HA_WEATHER_ENTITY must be a weather.<id> entity ID, not {entity!r}")
    return entity


def _root(name: str, default: str) -> str:
    root = os.environ.get(name, "").strip().rstrip("/") or default
    if not root.startswith("/"):
        raise SystemExit(f"config error: {name}={root!r} must be an absolute path inside the app's container")
    return root


def _region() -> str:
    region = os.environ.get("WATCH_REGION", "").strip().upper() or "US"
    if len(region) != 2 or not region.isalpha():
        raise SystemExit(f"config error: WATCH_REGION={region!r} must be a two-letter country code, e.g. US")
    return region


def _list_id() -> str:
    raw = os.environ.get("TMDB_LIST_ID", "").strip()
    # Pasting the list's address works too: themoviedb.org/list/8123456-movie-night
    if "/list/" in raw:
        raw = raw.split("/list/", 1)[1]
    raw = raw.split("-", 1)[0].split("?", 1)[0].strip("/")
    if raw and not raw.isdigit():
        raise SystemExit(f"config error: TMDB_LIST_ID={raw!r} must be the number in the list's address")
    return raw


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
    # The scheduled morning brief's own ceilings (/v1/brief): the first Claude
    # spend nobody asked for, so lower than a question's, and it shares the
    # workspace's monthly limit with #ask.
    brief_max_turns: int = 8
    brief_max_budget_usd: float = 0.30
    # The brief and the evening review run on a timer whether or not anything
    # needs you, and only read and summarize, so they get a cheaper model than
    # #ask by default.
    scheduled_model: str = "claude-haiku-5-5"
    # The evening review (/v1/review): no lookups, at most three proposals
    # and the answer, so fewer turns and less money than the brief.
    review_max_turns: int = 6
    review_max_budget_usd: float = 0.15

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_calendar_ids: tuple[str, ...] = ("primary",)

    ha_url: str = "http://host.docker.internal:8123"
    ha_token: str = ""
    # Scenes the agent may propose running. The allowlist IS the ceiling for
    # HA writes: Assist exposure does not govern the REST call (app/scenes.py).
    ha_scenes: tuple[str, ...] = ()
    # The one weather entity weather_forecast reads (app/forecast.py). Blank = off.
    ha_weather_entity: str = "weather.forecast_home"
    # Calendars home_calendar_events reads (app/hacalendar.py), e.g. the Apple
    # calendar through HA's CalDAV integration. Blank = off.
    ha_calendars: tuple[str, ...] = ()
    # Prometheus, through the port it publishes on the host. Blank = no
    # homelab_status tool (app/homelab.py).
    prometheus_url: str = "http://host.docker.internal:9090"
    # github_status (app/github.py): a read-only fine-grained token and the
    # repositories it reads. Blank token = no github_status tool.
    github_token: str = ""
    github_repos: tuple[str, ...] = ()

    # Radarr / Sonarr. Each is on when its API key is set. The profile and the
    # folders are what every approved add uses; the model does not choose them.
    radarr_url: str = "http://radarr:7878"
    radarr_api_key: str = ""
    radarr_root: str = "/data/library/movies"
    sonarr_url: str = "http://sonarr:8989"
    sonarr_api_key: str = ""
    sonarr_root: str = "/data/library/shows"
    media_quality_profile: str = "HD-1080p"

    # Movie night (app/watch.py): read-only. TMDB for the backlog and where each
    # title streams; Jellyfin for what is already here and what has been watched.
    tmdb_token: str = ""
    tmdb_list_id: str = ""
    watch_region: str = "US"
    streaming_services: tuple[str, ...] = ()
    jellyfin_url: str = "http://jellyfin:8096"
    jellyfin_api_key: str = ""
    jellyfin_user: str = ""
    jellyfin_public_url: str = ""

    @property
    def watch_enabled(self) -> bool:
        return bool(self.tmdb_token or self.jellyfin_api_key)

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_token and self.github_repos)

    @property
    def media_enabled(self) -> bool:
        return bool(self.radarr_api_key or self.sonarr_api_key)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_refresh_token)

    @property
    def home_enabled(self) -> bool:
        return bool(self.ha_token)

    @property
    def ha_calendars_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_calendars)

    @property
    def forecast_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_weather_entity)

    @property
    def scenes_enabled(self) -> bool:
        return bool(self.ha_token and self.ha_scenes)

    @classmethod
    def from_env(cls, *, require_token: bool = True, signing_in: bool = False) -> "Config":
        tz_name = os.environ.get("TZ", "").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            raise SystemExit(f"config error: TZ={tz_name!r} is not a known timezone")
        token = _req("CONCIERGE_TOKEN") if require_token else os.environ.get("CONCIERGE_TOKEN", "")
        if require_token and len(token) < 32:
            raise SystemExit("config error: CONCIERGE_TOKEN is shorter than 32 characters — "
                             "generate one with `openssl rand -hex 32`")
        g_id, g_secret, g_refresh = _google(signing_in)
        return cls(
            token=token,
            model=os.environ.get("CONCIERGE_MODEL", "").strip() or "claude-opus-5-5",
            max_turns=_int("CONCIERGE_MAX_TURNS", 12),
            max_budget_usd=_float("CONCIERGE_MAX_BUDGET_USD", 0.50),
            brief_max_turns=_int("CONCIERGE_BRIEF_MAX_TURNS", 8),
            brief_max_budget_usd=_float("CONCIERGE_BRIEF_MAX_BUDGET_USD", 0.30),
            review_max_turns=_int("CONCIERGE_REVIEW_MAX_TURNS", 6),
            review_max_budget_usd=_float("CONCIERGE_REVIEW_MAX_BUDGET_USD", 0.15),
            scheduled_model=os.environ.get("CONCIERGE_SCHEDULED_MODEL", "").strip() or "claude-haiku-5-5",
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
            ha_weather_entity=_weather(),
            ha_calendars=_calendars(),
            prometheus_url=os.environ.get("PROMETHEUS_URL", "http://host.docker.internal:9090").strip().rstrip("/"),
            github_token=os.environ.get("GITHUB_TOKEN", "").strip(),
            github_repos=_github_repos(),
            radarr_url=os.environ.get("RADARR_URL", "").strip().rstrip("/") or "http://radarr:7878",
            radarr_api_key=os.environ.get("RADARR_API_KEY", "").strip(),
            radarr_root=_root("RADARR_ROOT", "/data/library/movies"),
            sonarr_url=os.environ.get("SONARR_URL", "").strip().rstrip("/") or "http://sonarr:8989",
            sonarr_api_key=os.environ.get("SONARR_API_KEY", "").strip(),
            sonarr_root=_root("SONARR_ROOT", "/data/library/shows"),
            media_quality_profile=os.environ.get("MEDIA_QUALITY_PROFILE", "").strip() or "HD-1080p",
            tmdb_token=os.environ.get("TMDB_TOKEN", "").strip(),
            tmdb_list_id=_list_id(),
            watch_region=_region(),
            streaming_services=_list("STREAMING_SERVICES", ""),
            jellyfin_url=os.environ.get("JELLYFIN_URL", "").strip().rstrip("/") or "http://jellyfin:8096",
            jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY", "").strip(),
            jellyfin_user=os.environ.get("JELLYFIN_USER", "").strip(),
            jellyfin_public_url=os.environ.get("JELLYFIN_PUBLIC_URL", "").strip().rstrip("/"),
        )
