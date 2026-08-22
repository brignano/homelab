"""The daily digest: facts in, a short human-readable post out.

Two rendering paths, and both must work:

  facts + model  -> narrated digest
  facts alone    -> the same digest with the prose omitted

The second path is not a fallback nicety, it's the design. The numbers are the
product; the narration is a convenience layer on top. If Ollama is down, slow,
or returns nonsense, the digest still posts and still tells you the truth.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .facts import Facts

log = logging.getLogger(__name__)

SYSTEM = (
    "You write a short status note about a personal homelab server. "
    "You will be given a finished set of facts that has already been checked. "
    "Rules: use ONLY the facts given; never invent numbers, service names, or events; "
    "do not give advice, recommendations, or next steps; no bullet points, no headings, "
    "no preamble like 'Here is'. Write 2 to 3 plain sentences. "
    "If the facts say everything is normal, say so briefly and stop."
)


def facts_block(facts: Facts) -> str:
    """The exact text the model is given. Deliberately pre-digested and small —
    the model's job is phrasing, not analysis."""
    lines: list[str] = []

    if facts.targets_total:
        lines.append(f"- Monitored targets up: {facts.targets_up} of {facts.targets_total}")
    if facts.targets_down:
        lines.append("- Targets DOWN: " + ", ".join(facts.targets_down))
    if facts.cpu_pct is not None:
        lines.append(f"- CPU used, 1h average: {facts.cpu_pct:.0f}%")
    if facts.mem_pct is not None:
        lines.append(f"- RAM used: {facts.mem_pct:.0f}%")
    if facts.disk_pct is not None:
        lines.append(f"- Root disk used: {facts.disk_pct:.0f}%")

    if facts.restarts:
        lines.append(
            "- Container restarts in last 24h: "
            + ", ".join(f"{name} restarted {count} time(s)" for name, count in facts.restarts)
        )
    else:
        lines.append("- Container restarts in last 24h: none")

    if facts.log_errors:
        lines.append(
            "- Log lines matching error/fatal/panic in last 24h: "
            + ", ".join(f"{name}: {count}" for name, count in facts.log_errors)
        )
    else:
        lines.append("- Log lines matching error/fatal/panic in last 24h: none")

    # State the verdict explicitly so the model never has to reach one itself.
    if facts.concerns:
        lines.append("- Overall assessment: NEEDS ATTENTION — " + "; ".join(facts.concerns))
    else:
        lines.append("- Overall assessment: NORMAL, nothing needs attention")

    if facts.problems:
        lines.append("- Note, some data could not be collected: " + "; ".join(facts.problems))

    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def render(
    facts: Facts,
    narration: str | None,
    *,
    model: str | None = None,
    seconds: float | None = None,
    note: str | None = None,
) -> str:
    """Build the Discord message. Numbers always present; prose only if we got it."""
    heading = "⚠️ Homelab digest" if facts.concerns else "✅ Homelab digest"
    when = facts.collected_at.strftime("%a %d %b")
    out = [f"**{heading} — {when}**", ""]

    if narration:
        out += [narration.strip(), ""]

    targets = (
        f"{facts.targets_up}/{facts.targets_total} up"
        if facts.targets_total
        else "n/a"
    )
    out.append(f"**Services** {targets}")
    if facts.targets_down:
        out.append("**Down** " + " · ".join(facts.targets_down))
    out.append(
        f"**Load** CPU {_pct(facts.cpu_pct)} · "
        f"RAM {_pct(facts.mem_pct)} · disk / {_pct(facts.disk_pct)}"
    )
    out.append(
        "**Restarts (24h)** "
        + (" · ".join(f"{n} ×{c}" for n, c in facts.restarts) if facts.restarts else "none")
    )
    out.append(
        "**Log errors (24h)** "
        + (" · ".join(f"{n} {c}" for n, c in facts.log_errors) if facts.log_errors else "none")
    )

    if facts.problems:
        out.append("")
        out.append("⚠️ _Incomplete: " + "; ".join(facts.problems) + "_")

    footer_bits: list[str] = []
    if model and seconds is not None:
        footer_bits.append(f"{model} · {seconds:.0f}s")
    elif note:
        footer_bits.append(note)
    footer_bits.append("facts from Prometheus + Loki")
    out += ["", "-# " + " · ".join(footer_bits)]

    return "\n".join(out)


async def build(collector, ollama, now: datetime, num_predict: int = 180) -> str:
    """Collect, narrate, render. Never raises for a narration failure."""
    facts = await collector.collect(now)

    narration: str | None = None
    model = seconds = None
    note = None
    try:
        completion = await ollama.generate(
            prompt=f"Facts:\n{facts_block(facts)}\n\nWrite the status note.",
            system=SYSTEM,
            # Low temperature: this is restatement, not creative writing.
            temperature=0.2,
            num_predict=num_predict,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to facts-only, never drop the digest
        log.warning("digest narration failed, posting facts only: %s", exc)
        note = "narration unavailable"
    else:
        narration, model, seconds = completion.text, completion.model, completion.seconds

    return render(facts, narration, model=model, seconds=seconds, note=note)
