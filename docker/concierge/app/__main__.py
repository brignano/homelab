"""Entry point.

  python -m app                serve /v1/ask on the internal network (default)
  python -m app --selftest     check the CLI, Google, Home Assistant, Radarr, Sonarr, TMDB and Jellyfin; no API spend
  python -m app --ask "..."    run one task from the terminal and print it (costs money)
  python -m app --google-auth  one-time Google sign-in (read, calendar writes, drafts)

From the box:
  docker compose -f docker/concierge/docker-compose.yml run --rm concierge --selftest
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

import aiohttp

from .config import Config


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _cli_path() -> str | None:
    """The Claude Code binary the SDK will drive: bundled in the wheel, else PATH."""
    import claude_agent_sdk
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("claude")


async def _selftest_async(cfg: Config) -> int:
    from .google import Google
    from .home import Home

    failed = False
    cli = _cli_path()
    print(f"claude CLI       {cli or 'MISSING — reinstall claude-agent-sdk'}")
    failed |= cli is None
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"ANTHROPIC_API_KEY {'set' if key else 'MISSING'} (not called — --ask spends money)")
    failed |= not key
    print(f"model            {cfg.model}  ·  ≤{cfg.max_turns} turns, ≤${cfg.max_budget_usd:.2f} per request")

    async with aiohttp.ClientSession() as session:
        if cfg.google_enabled:
            try:
                await Google(session, cfg.google_client_id, cfg.google_client_secret,
                             cfg.google_refresh_token).check()
                print(f"Google           ok (calendars: {', '.join(cfg.google_calendar_ids)})")
            except Exception as exc:  # noqa: BLE001
                print(f"Google           FAILED: {exc}")
                failed = True
        else:
            print("Google           off (no refresh token) — calendar and mail tools not registered")

        from .server import media_from
        media = media_from(cfg, session)
        for label, app, var, root in (("Radarr", media.radarr, "RADARR_API_KEY", cfg.radarr_root),
                                      ("Sonarr", media.sonarr, "SONARR_API_KEY", cfg.sonarr_root)):
            if app is None:
                print(f"{label:<17}off (no {var})")
                continue
            try:
                version = await app.status()
                await app.profile_id(cfg.media_quality_profile)
                print(f"{label:<17}ok (v{version}, profile {cfg.media_quality_profile!r}, folder {root})")
            except Exception as exc:  # noqa: BLE001
                print(f"{label:<17}FAILED: {exc}")
                failed = True

        from .server import watch_from
        watch = watch_from(cfg, session)
        if watch.tmdb is None:
            print("TMDB             off (no TMDB_TOKEN) — movie night picks from Jellyfin only"
                  if watch.jellyfin else "TMDB             off (no TMDB_TOKEN)")
        else:
            try:
                ids = await watch.service_ids()
                line = f"TMDB             ok (region {cfg.watch_region}"
                if cfg.streaming_services:
                    line += f", {len(cfg.streaming_services) - len(watch.missing_services)} of " \
                            f"{len(cfg.streaming_services)} services matched ({len(ids)} provider ids)"
                if cfg.tmdb_list_id:
                    line += f", watchlist {len(await watch.tmdb.list_items(cfg.tmdb_list_id))} titles"
                print(line + ")")
                if watch.missing_services:
                    print(f"                 no TMDB service named: {', '.join(watch.missing_services)}")
                    failed = True
            except Exception as exc:  # noqa: BLE001
                print(f"TMDB             FAILED: {exc}")
                failed = True
        if watch.jellyfin is None:
            print("Jellyfin         off (no JELLYFIN_API_KEY)")
        else:
            try:
                rows = await watch.jellyfin.library()
                movies = sum(r["kind"] == "movie" for r in rows)
                unplayed = sum(not r["played"] for r in rows)
                print(f"Jellyfin         ok ({movies} movies, {len(rows) - movies} shows, "
                      f"{unplayed} not watched yet by {cfg.jellyfin_user or 'its only user'})")
            except Exception as exc:  # noqa: BLE001
                print(f"Jellyfin         FAILED: {exc}")
                failed = True
        if not cfg.watch_enabled:
            print("                 movie_night not registered")

    if cfg.home_enabled:
        try:
            text = await Home(cfg.ha_url, cfg.ha_token).live_context()
            print(f"Home Assistant   ok ({len(text.splitlines())} lines of live context via {cfg.ha_url}/api/mcp)")
        except Exception as exc:  # noqa: BLE001
            print(f"Home Assistant   FAILED: {exc}")
            failed = True
    else:
        print("Home Assistant   off (no HA_TOKEN) — home_overview not registered")

    from .forecast import Forecast
    from .homelab import Homelab
    async with aiohttp.ClientSession() as session:
        if cfg.forecast_enabled:
            try:
                rows = await Forecast(session, cfg.ha_url, cfg.ha_token, cfg.ha_weather_entity).get("daily")
                print(f"Forecast         ok ({len(rows)} days from {cfg.ha_weather_entity})")
            except Exception as exc:  # noqa: BLE001
                print(f"Forecast         FAILED: {exc}")
                failed = True
        else:
            print("Forecast         off (needs HA_TOKEN and HA_WEATHER_ENTITY)")
        if cfg.prometheus_url:
            try:
                status = await Homelab(session, cfg.prometheus_url).status()
                print(f"Prometheus       ok (targets up {status.get('targets_up', '?')}"
                      + (f", could not read: {', '.join(status['could_not_read'])}" if status.get("could_not_read") else "")
                      + ")")
            except Exception as exc:  # noqa: BLE001
                print(f"Prometheus       FAILED: {exc}")
                failed = True
        else:
            print("Prometheus       off (no PROMETHEUS_URL) — homelab_status not registered")
    return 1 if failed else 0


async def _ask_async(cfg: Config, question: str) -> int:
    from . import agent
    from .google import Google
    from .home import Home

    async with aiohttp.ClientSession() as session:
        google = Google(session, cfg.google_client_id, cfg.google_client_secret,
                        cfg.google_refresh_token) if cfg.google_enabled else None
        home = Home(cfg.ha_url, cfg.ha_token) if cfg.home_enabled else None
        from .server import media_from
        media = media_from(cfg, session) if cfg.media_enabled else None
        from .forecast import Forecast
        from .homelab import Homelab
        forecast = Forecast(session, cfg.ha_url, cfg.ha_token, cfg.ha_weather_entity) \
            if cfg.forecast_enabled else None
        homelab = Homelab(session, cfg.prometheus_url) if cfg.prometheus_url else None
        from .server import watch_from
        watch = watch_from(cfg, session) if cfg.watch_enabled else None
        out = await agent.run(cfg, google, home, prompt=question, history=[], memories=[],
                              media=media, forecast=forecast, homelab=homelab, watch=watch)
    print(out.answer)
    for p in out.proposals:
        print(f"[proposal] {p['tool']}({p['args']})")
    cost = f"${out.cost_usd:.4f}" if out.cost_usd is not None else "?"
    print(f"-- {out.stop} · {out.turns} turns · {out.tool_calls} tool calls · {cost} · {out.seconds:.0f}s")
    return 0 if out.ok else 1


async def _google_auth_async(cfg: Config) -> int:
    from .google import auth_request, code_from_redirect, exchange_code, SCOPES

    if not (cfg.google_client_id and cfg.google_client_secret):
        raise SystemExit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in docker/concierge/.env first "
                         "(GOOGLE_REFRESH_TOKEN stays blank until this finishes).")
    req = auth_request(cfg.google_client_id)
    print("Scopes requested (nothing here sends mail; see app/google.py):")
    for s in SCOPES:
        print(f"  {s}")
    print("\n1. Open this URL in a browser signed in to the Google account:\n")
    print(req.url)
    print("\n2. Approve. The browser then fails to load http://127.0.0.1:8765/… — that is expected.")
    print("3. Copy the WHOLE address bar and paste it here:\n")
    try:
        pasted = input("> ")
    except EOFError:
        raise SystemExit("no input — run with a terminal: docker compose run --rm concierge --google-auth")
    code = code_from_redirect(pasted, req.state)
    async with aiohttp.ClientSession() as session:
        body = await exchange_code(session, cfg.google_client_id, cfg.google_client_secret, code, req.verifier)
    refresh = body.get("refresh_token")
    if not refresh:
        raise SystemExit("Google returned no refresh token. Remove this app's access at "
                         "https://myaccount.google.com/permissions and run --google-auth again.")
    print("\nAdd this line to docker/concierge/.env, then recreate the container:\n")
    print(f"GOOGLE_REFRESH_TOKEN={refresh}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--selftest", action="store_true", help="check CLI, Google and HA; no API spend")
    mode.add_argument("--ask", metavar="QUESTION", help="run one task and print it (spends API credit)")
    mode.add_argument("--google-auth", action="store_true", help="one-time Google sign-in")
    args = parser.parse_args(argv)
    _setup_logging()

    if args.google_auth:
        return asyncio.run(_google_auth_async(Config.from_env(require_token=False, signing_in=True)))
    if args.selftest:
        return asyncio.run(_selftest_async(Config.from_env(require_token=False)))
    if args.ask:
        return asyncio.run(_ask_async(Config.from_env(require_token=False), args.ask))

    from .server import serve
    serve(Config.from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
