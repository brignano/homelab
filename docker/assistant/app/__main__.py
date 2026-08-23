"""Entrypoint.

  python -m app              run the bot (needs full Discord config)
  python -m app --dry-run    render a digest from synthetic facts, no network
  python -m app --selftest   hit the real Prometheus/Loki/Ollama, print the
                             digest, and exit — no Discord config needed
  python -m app --provision  diff the server against guild.yml and print a plan
  python -m app --provision --apply
                             ...and actually make those changes

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

    for name, facts in (("HEALTHY", healthy), ("DEGRADED", degraded)):
        print(f"\n{'=' * 70}\n{name} — prompt given to the model\n{'=' * 70}")
        print(facts_block(facts))
        print(f"\n{'-' * 70}\n{name} — rendered Discord message (facts + narration)\n{'-' * 70}")
        print(render(
            facts,
            "Everything looks normal this morning." if not facts.concerns
            else "A couple of targets are down and the box is running hot.",
            model="llama3.2:3b", seconds=34.0,
        ))
        print(f"\n{'-' * 70}\n{name} — rendered with the model unavailable\n{'-' * 70}")
        print(render(facts, None, note="narration unavailable"))
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
        message = await build(collector, ollama, dt.datetime.now())

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

    from .bot import run
    from .config import Config

    run(Config.from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
