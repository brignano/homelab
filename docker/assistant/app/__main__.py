"""Entrypoint.

  python -m app             run the bot (needs Discord config)
  python -m app --dry-run   render a digest from synthetic facts, no network
  python -m app --selftest  hit the real Prometheus/Loki/Ollama, print the
                            digest, and exit — no Discord config needed

`--selftest` is the one to run first on the box: it proves the container can
reach the backends and that the model produces something sane, before you go
near a Discord token.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
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
    args = parser.parse_args(argv)

    _setup_logging()

    if args.dry_run:
        return _dry_run()
    if args.selftest:
        return asyncio.run(_selftest_async())

    from .bot import run
    from .config import Config

    run(Config.from_env())
    return 0


if __name__ == "__main__":
    sys.exit(main())
