"""Entrypoint.

  python -m app              run the bot (needs full Discord config)
  python -m app --dry-run    render a digest from synthetic facts, no network
  python -m app --selftest   hit the real Prometheus/Loki/Ollama, print the
                             digest, and exit — no Discord config needed
  python -m app --provision  diff the server against guild.yml and print a plan
  python -m app --provision --apply
                             ...and actually make those changes
  python -m app --google-auth
                             one-time Google sign-in; prints the refresh token
                             for GOOGLE_REFRESH_TOKEN (docker/assistant/README.md)

Order to run these when setting up from scratch:

  1. --selftest    proves the container reaches Ollama/Prometheus/Loki.
                   Needs no Discord token, so it isolates backend problems
                   from Discord problems.
  2. --provision   creates the channels. Prints the channel ID to paste into
                   DISCORD_DIGEST_CHANNEL_ID, which doesn't exist until now.
  3. (no flags)    run the bot for real.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import pathlib
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _guild_file() -> str:
    """Locate guild.yml: explicit override, then the image path, then the repo
    checkout (so `python -m app --provision` works outside the container)."""
    if explicit := os.environ.get("GUILD_FILE", "").strip():
        return explicit
    for candidate in ("/srv/guild.yml", str(pathlib.Path(__file__).resolve().parent.parent / "guild.yml")):
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("could not find guild.yml — set GUILD_FILE to its path")


async def _provision_async(apply_changes: bool) -> int:
    import discord

    from .provision import apply, load_desired, plan, read_server, render_plan

    token = os.environ.get("DISCORD_TOKEN", "").strip()
    guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
    if not token or not guild_id.isdigit():
        # Deliberately not Config.from_env(): provisioning is what *produces*
        # DISCORD_DIGEST_CHANNEL_ID, so it can't require it.
        raise SystemExit(
            "provisioning needs DISCORD_TOKEN and DISCORD_GUILD_ID "
            "(DISCORD_DIGEST_CHANNEL_ID is not needed — this is what gives it to you)"
        )

    desired = load_desired(_guild_file())

    # Login only — no gateway connection needed for REST calls, so this starts
    # and exits in about a second.
    client = discord.Client(intents=discord.Intents.none())
    await client.login(token)
    try:
        guild = await client.fetch_guild(int(guild_id))
        me = await guild.fetch_member(client.user.id)
        snapshot = await read_server(guild, me)

        actions = plan(desired, snapshot.state, snapshot.nickname)
        print(f"Server: {guild.name}\n")
        print(render_plan(actions))

        changes = [a for a in actions if a.kind != "extra_channel"]
        if not changes:
            return 0
        if not apply_changes:
            print("\nDry run — nothing was changed. Re-run with --apply to execute.")
            return 0

        print("\nApplying...")
        ids = await apply(guild, me, actions, snapshot)
    finally:
        await client.close()

    print("\nDone.")
    for cat in desired["categories"]:
        for ch in cat.get("channels") or []:
            if (cid := ids.get(ch["name"])) is not None:
                print(f"  #{ch['name']}  ->  {cid}")
    digest_id = ids.get("digest")
    if digest_id:
        print(f"\nPaste into docker/assistant/.env:\n  DISCORD_DIGEST_CHANNEL_ID={digest_id}")
    return 0


async def _google_auth_async() -> int:
    """Sign in once, in a browser on any machine, and print the refresh token.

    Headless by design: nothing listens on the redirect address. The browser's
    final page fails to load, and its address bar holds the code — so this
    works from `docker compose run` on CT 100 with the browser on a laptop.
    """
    import aiohttp

    from .google import SCOPES, GoogleError, auth_request, code_from_redirect, exchange_code

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in docker/assistant/.env first —\n"
            "  a Desktop-app OAuth client from Google Cloud Console (see the README)"
        )

    req = auth_request(client_id)
    print("Read-only access requested:")
    for scope in SCOPES:
        print(f"  {scope}")
    print("\n1. Open this URL in a browser and approve:\n")
    print(req.url)
    print("\n2. The browser ends on a page that will not load (127.0.0.1:8765).")
    print("   That is expected. Copy the WHOLE address from the address bar and paste it here.\n")
    try:
        pasted = input("URL> ")
    except EOFError:
        raise SystemExit("no input — run with a terminal: docker compose run --rm assistant --google-auth")

    try:
        code = code_from_redirect(pasted, req.state)
        async with aiohttp.ClientSession() as session:
            tokens = await exchange_code(session, client_id, client_secret, code, req.verifier)
    except GoogleError as exc:
        raise SystemExit(f"sign-in failed: {exc}")

    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "Google returned no refresh token. Remove this app's access at "
            "https://myaccount.google.com/permissions and run --google-auth again."
        )
    granted = set(tokens.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("\nwarning: these were not granted (untick them and those sources will fail):")
        for scope in missing:
            print(f"  {scope}")
    print("\nPaste into docker/assistant/.env, then recreate the container:\n")
    print(f"  GOOGLE_REFRESH_TOKEN={refresh}\n")
    return 0


def _sample_day(now: dt.datetime):
    from .day import Day
    from .google import Event, Mail
    from .home import Reading

    tz = now.tzinfo
    at = lambda h, m=0: now.replace(hour=h, minute=m)  # noqa: E731
    return Day(
        now=now,
        events=[
            Event("Standup", at(9), at(9, 15), False, True),
            Event("Dentist", at(15), at(16), False, True),
            Event("1:1 with Sam", at(15, 30), at(16), False, True),
            Event("Focus block", at(13), at(15), False, False),
            Event("Mom's birthday", now.date(), now.date() + dt.timedelta(days=1), True, True),
        ],
        mail=[
            Mail("Alice Chen", "Re: flights for the 24th", at(7, 2).astimezone(tz)),
            Mail("Billing", "Your invoice is ready — see https://example.com/pay", at(6, 40)),
            Mail("@everyone", "**URGENT** [click](https://phish.example)", at(5)),
        ],
        # iCloud, through HA: one event of its own, and Standup again, which
        # the merge must show once.
        ha_events=[
            Event("Standup", at(9), at(9, 15), False, True),
            Event("Pick up dry cleaning", at(17, 30), at(18), False, True),
        ],
        sources=("calendar", "mail", "home", "ha-calendar"),
        home=[
            Reading("climate.thermostat", "Thermostat", "heat", None, "at 52°F"),
            Reading("cover.garage_door", "Garage Door", "open", now - dt.timedelta(hours=2), "open"),
            Reading("lock.front_door", "Front Door", "locked", now - dt.timedelta(hours=9), None),
            Reading("binary_sensor.basement_leak", "Basement Leak", "unavailable",
                    now - dt.timedelta(days=3), "unavailable"),
        ],
    )


def _dry_run() -> int:
    """Exercise the pure rendering path with representative facts. No I/O."""
    from .digest import facts_block, render
    from .facts import Facts

    healthy = Facts(
        collected_at=dt.datetime(2026, 8, 22, 7, 30),
        targets_up=22, targets_total=22,
        cpu_pct=11.4, mem_pct=61.2, disk_pct=38.0,
    )
    degraded = Facts(
        collected_at=dt.datetime(2026, 8, 22, 7, 30),
        targets_up=20, targets_total=22,
        targets_down=["blackbox (kali-linux:3000)", "postgres-exporter (postgres-exporter:9187)"],
        cpu_pct=91.7, mem_pct=93.1, disk_pct=88.4,
        restarts=[("ollama", 3), ("caddy", 1)],
        log_errors=[("caddy", 214), ("open-webui", 37), ("loki", 4)],
        problems=["could not read log errors"],
    )

    # A stand-in domain, so the footer links render at their real width. The
    # bot uses GRAFANA_URL; unset, the footer simply has no links.
    grafana = os.environ.get("GRAFANA_URL", "").strip() or "https://stats.example.com"

    day = _sample_day(dt.datetime(2026, 8, 22, 7, 30, tzinfo=dt.timezone.utc))

    for name, facts in (("HEALTHY", healthy), ("DEGRADED", degraded)):
        print(f"\n{'=' * 70}\n{name} — prompt given to the model\n{'=' * 70}")
        print(facts_block(facts))
        print(f"\n{'-' * 70}\n{name} — rendered Discord message (facts + narration)\n{'-' * 70}")
        print(render(
            facts,
            "Everything looks normal this morning." if not facts.concerns
            else "A couple of targets are down and the box is running hot.",
            model="llama3.2:3b", seconds=34.0, grafana_url=grafana,
            day=day if name == "HEALTHY" else None,
        ))
        print(f"\n{'-' * 70}\n{name} — rendered with the model unavailable\n{'-' * 70}")
        print(render(facts, None, note="narration unavailable", grafana_url=grafana,
                     day=day if name == "HEALTHY" else None))
    return 0


async def _selftest_async() -> int:
    import aiohttp

    from .digest import build
    from .facts import FactCollector
    from .ollama import Ollama

    prom = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
    loki = os.environ.get("LOKI_URL", "http://loki:3100").rstrip("/")
    ourl = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
    grafana = os.environ.get("GRAFANA_URL", "").strip().rstrip("/")

    print(f"prometheus : {prom}\nloki       : {loki}\nollama     : {ourl} ({model})\n")

    async with aiohttp.ClientSession() as session:
        ollama = Ollama(
            session, base_url=ourl, model=model,
            num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
            timeout_s=int(os.environ.get("LLM_TIMEOUT_S", "300")),
        )
        ok = await ollama.available()
        print(f"ollama has {model}: {'yes' if ok else 'NO — digest will post facts only'}\n")

        collector = FactCollector(session, prometheus_url=prom, loki_url=loki)

        # The "Today" section, from whichever of Google and Home Assistant is
        # configured. Parsed with the same helpers the bot uses, so a half-set
        # config fails here too, before you rely on it. No state_dir: a
        # selftest proves the sources answer, it is not a scheduled read, and
        # it must not refresh the stamps hl-google-stale watches.
        from .config import GOOGLE_SOURCES, _google, _home, _list
        g_id, g_secret, g_refresh = _google()
        ha_token, ha_entities, ha_calendars = _home()
        sources = (_list("GOOGLE_SOURCES", ",".join(GOOGLE_SOURCES)) if g_refresh else ()) + (
            ("home",) if ha_entities else ()) + (("ha-calendar",) if ha_calendars else ())
        day_collector = None
        if sources:
            from .day import DayCollector
            from .google import Google
            from .home import Home
            day_collector = DayCollector(
                Google(session, g_id, g_secret, g_refresh) if g_refresh else None,
                calendar_ids=_list("GOOGLE_CALENDAR_IDS", "primary"),
                mail_labels=_list("GMAIL_LABELS", "INBOX,UNREAD,CATEGORY_PERSONAL"),
                sources=sources,
                state_dir=None,
                home=Home(session, os.environ.get("HA_URL", "").strip() or "http://host.docker.internal:8123",
                          ha_token) if ha_token else None,
                home_entities=ha_entities,
                ha_calendars=ha_calendars,
            )
        print(f"google     : {'configured' if g_refresh else 'not configured'}")
        print(f"home       : {f'{len(ha_entities)} entities' if ha_entities else 'not configured'}")
        print(f"ha calendar: {f'{len(ha_calendars)} calendars' if ha_calendars else 'not configured'}\n")

        tz_name = os.environ.get("TZ", "").strip() or "UTC"
        from zoneinfo import ZoneInfo
        message = await build(collector, ollama, dt.datetime.now(tz=ZoneInfo(tz_name)),
                              grafana_url=grafana, day_collector=day_collector)

    print("=" * 70)
    print(message)
    print("=" * 70)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app", description="Homelab assistant")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="render sample digests offline")
    mode.add_argument("--selftest", action="store_true", help="query real backends, print a digest, exit")
    mode.add_argument("--provision", action="store_true", help="diff the server against guild.yml")
    mode.add_argument("--google-auth", action="store_true", help="one-time Google sign-in for the Today section")
    parser.add_argument("--apply", action="store_true", help="with --provision: execute the plan")
    args = parser.parse_args(argv)

    if args.apply and not args.provision:
        parser.error("--apply only makes sense with --provision")

    _setup_logging()

    if args.dry_run:
        return _dry_run()
    if args.selftest:
        return asyncio.run(_selftest_async())
    if args.provision:
        return asyncio.run(_provision_async(args.apply))
    if args.google_auth:
        return asyncio.run(_google_auth_async())

    from .bot import run
    from .config import Config

    run(Config.from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
