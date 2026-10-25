"""The daily digest: facts in, a short human-readable post out.

Two rendering paths, and both must work:

  facts + model  -> narrated digest
  facts alone    -> the same digest with the prose omitted

The second path is not a fallback nicety, it's the design. The numbers are the
product; the narration is a convenience layer on top. If Ollama is down, slow,
or returns nonsense, the digest still posts and still tells you the truth.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from .concierge import Brief
from .day import Day, render_lines as day_lines
from .facts import CPU_WARN, DISK_WARN, MEM_WARN, Facts

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
    the model's job is phrasing, not analysis.

    Lab facts only, and that is a security property rather than a scope choice:
    the "Today" section carries event titles and email subjects that strangers
    wrote, so it is rendered by code and never put in front of the model. See
    day.py."""
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
            f"- Container restarts in last {facts.span}: "
            + ", ".join(f"{name} restarted {count} time(s)" for name, count in facts.restarts)
        )
    else:
        lines.append(f"- Container restarts in last {facts.span}: none")

    if facts.log_errors:
        lines.append(
            f"- Log lines matching error/fatal/panic in last {facts.span}: "
            + ", ".join(f"{name}: {count}" for name, count in facts.log_errors)
        )
    else:
        lines.append(f"- Log lines matching error/fatal/panic in last {facts.span}: none")

    # State the verdict explicitly so the model never has to reach one itself.
    if facts.concerns:
        lines.append("- Overall assessment: NEEDS ATTENTION — " + "; ".join(facts.concerns))
    else:
        lines.append("- Overall assessment: NORMAL, nothing needs attention")

    if facts.problems:
        lines.append("- Note, some data could not be collected: " + "; ".join(facts.problems))

    return "\n".join(lines)


# --- Discord rendering --------------------------------------------------------
#
# The digest is read once a day, usually on a phone, usually before coffee. Two
# rules follow from that, and both are about where the ink goes:
#
#   Every fact appears exactly once. An earlier draft opened with a "Needs
#   attention" block built from `facts.concerns` and then listed the same
#   readings again below it — on a bad morning nearly every line was printed
#   twice, which is when a reader starts skimming, which is the one failure
#   mode a digest cannot afford. The readings ARE the attention list: concerns
#   are derived from exactly these numbers, so marking the lines says the same
#   thing in half the space.
#
#   Only the lines that are wrong get a mark. Five green ticks down the left
#   margin is the same as none — the eye has nothing to land on. An unmarked
#   line means "fine", and on a normal day the whole body is unmarked and the
#   ✅ in the heading is the entire verdict.
#
# The marks are not a second opinion: they come from the thresholds in facts.py
# that decide `concerns`, so a red line and a "needs attention" verdict can
# never disagree. smoke.py asserts that.
BAD = "🔴"
# One Discord message, with room to spare. Over this, chunk() would split the
# digest and leave its tail unlabelled.
DIGEST_LIMIT = 1990
WARN = "🟠"

# Dashboard UIDs as provisioned in docker/monitoring/grafana/dashboards/homelab/.
# `/d/<uid>` is enough — Grafana redirects to the slugged URL, so retitling a
# dashboard does not break these. Changing a *uid* would, silently, on the one
# morning the link gets clicked; check-observability.sh compares this list
# against the committed dashboards for exactly that reason.
DASH_TRIAGE = "homelab-triage"
DASH_CAPACITY = "homelab-capacity"
DASH_ENDPOINTS = "homelab-endpoints"
DASH_LOGS = "homelab-logs"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _over(value: float | None, warn: float) -> bool:
    return value is not None and value >= warn


def _hot(value: float | None, warn: float) -> str:
    """A reading, bolded when it's the one over the line.

    The gauges share a line, so the line's mark alone can't say *which* of the
    three tripped it. Bolding the number that did costs no space and removes
    the need for a sentence saying so.
    """
    return f"**{_pct(value)}**" if _over(value, warn) else _pct(value)


def _strained(facts: Facts) -> bool:
    """Any of the three gauges over its threshold.

    One definition, used by both the Load mark and the Capacity link — two
    copies would eventually disagree, and the disagreement would show up as a
    red line with nowhere to click.
    """
    return (
        _over(facts.cpu_pct, CPU_WARN)
        or _over(facts.mem_pct, MEM_WARN)
        or _over(facts.disk_pct, DISK_WARN)
    )


def _line(mark: str, label: str, value: str) -> str:
    return f"{mark + ' ' if mark else ''}**{label}** {value}"


def links(facts: Facts, base: str) -> list[tuple[str, str]]:
    """Where to go next, chosen by what the digest actually found.

    Triage is always first — it's the front door, and on a normal day it's the
    only one offered. The rest appear only when there is something on them
    worth opening: a static row of four links is scenery, and scenery stops
    being read within a week.
    """
    base = base.rstrip("/")
    out = [("Triage", f"{base}/d/{DASH_TRIAGE}")]
    if facts.targets_down:
        out.append(("Endpoints", f"{base}/d/{DASH_ENDPOINTS}"))
    if _strained(facts):
        out.append(("Capacity", f"{base}/d/{DASH_CAPACITY}"))
    if facts.log_errors:
        out.append(("Logs", f"{base}/d/{DASH_LOGS}"))
    return out


def render(
    facts: Facts,
    narration: str | None,
    *,
    model: str | None = None,
    seconds: float | None = None,
    note: str | None = None,
    grafana_url: str | None = None,
    day: Day | None = None,
    brief: Brief | None = None,
) -> str:
    """Build the Discord message. Numbers always present; prose only if we got it.

    The morning brief, when there is one, is a section of this message rather
    than a second post: it goes last, after Today, and it is the part that gets
    cut if the whole would not fit in one Discord message."""
    # `##` is a real Discord heading, not bold text pretending to be one: it
    # renders large, and it gives the channel a visible day boundary when a
    # month of digests is scrolled back through.
    mark = "⚠️" if facts.concerns else "✅"
    when = facts.collected_at.strftime("%a %d %b")
    out = [f"## {mark} Homelab digest — {when}", ""]

    if narration:
        out += [narration.strip(), ""]

    targets = (
        f"{facts.targets_up}/{facts.targets_total} up"
        if facts.targets_total
        else "n/a"
    )
    # The down targets ride on the Services line rather than getting one of
    # their own: naming them is the point, and a separate line said it twice.
    if facts.targets_down:
        targets += " — " + ", ".join(facts.targets_down)
    out.append(_line(BAD if facts.targets_down else "", "Services", targets))

    out.append(_line(
        BAD if _strained(facts) else "",
        "Load",
        f"CPU {_hot(facts.cpu_pct, CPU_WARN)} · "
        f"RAM {_hot(facts.mem_pct, MEM_WARN)} · "
        f"disk / {_hot(facts.disk_pct, DISK_WARN)}",
    ))
    out.append(_line(
        WARN if facts.restarts else "",
        f"Restarts ({facts.span})",
        " · ".join(f"{n} ×{c}" for n, c in facts.restarts) if facts.restarts else "none",
    ))
    # Deliberately never marked. facts.py does not count log noise as a
    # concern — plenty of things here log an error a minute and are healthy —
    # and a mark here would claim a verdict the code never reached.
    out.append(_line(
        "",
        f"Log errors ({facts.span})",
        " · ".join(f"{n} {c}" for n, c in facts.log_errors) if facts.log_errors else "none",
    ))

    if facts.problems:
        out.append("")
        out.append("⚠️ _Incomplete: " + "; ".join(facts.problems) + "_")

    if day is not None:
        out.append("")
        out += day_lines(day, mail=not (brief and brief.owns_mail))

    head = out
    out = []
    if grafana_url:
        # Masked links, which Discord renders for bot and webhook messages.
        # A bare Grafana URL is ~50 characters of noise per link and would
        # unfurl a preview card under every digest.
        out.append("-# " + " · ".join(f"[{name} ↗]({url})" for name, url in links(facts, grafana_url)))

    footer_bits: list[str] = []
    if model and seconds is not None:
        footer_bits.append(f"{model} · {seconds:.0f}s")
    elif note:
        footer_bits.append(note)
    origins = ["Prometheus", "Loki"]
    if day is not None:
        if {"calendar", "mail"} & set(day.sources):
            origins.append("Google")
        if {"home", "ha-calendar"} & set(day.sources):
            origins.append("Home Assistant")
    footer_bits.append("facts from " + " + ".join(origins))
    if brief and brief.note:
        footer_bits.append("brief " + brief.note)
    out.append("-# " + " · ".join(footer_bits))

    if brief is None:
        return "\n".join(head + [""] + out)
    # Only the brief is cut: the lab's facts and Today are the product.
    fixed = len("\n".join(head + ["", "", ""] + out))
    body = "\n".join(brief.lines)
    room = DIGEST_LIMIT - fixed
    if len(body) > room:
        body = body[:max(room - 1, 0)].rstrip() + "…"
    return "\n".join(head + ["", body, ""] + out)


@dataclass
class Draft:
    """A digest collected and narrated, not yet rendered.

    Split from `build` so the scheduled digest can wait for the morning brief
    *after* leaving the job queue: the queue serialises the local model, and
    holding it while Claude reads mail would stall #chat for minutes."""
    facts: Facts
    day: Day | None
    narration: str | None = None
    model: str | None = None
    seconds: float | None = None
    note: str | None = None

    @property
    def complete(self) -> bool:
        """Every source was read. Only then may the next digest start from
        this one: a source that was down missed its window, and the next
        digest has to cover it again."""
        return not self.facts.problems and not (self.day and self.day.problems)

    def render(self, grafana_url: str | None = None, brief: Brief | None = None) -> str:
        return render(
            self.facts, self.narration, model=self.model, seconds=self.seconds, note=self.note,
            grafana_url=grafana_url, day=self.day, brief=brief,
        )


async def prepare(
    collector,
    ollama,
    now: datetime,
    num_predict: int = 180,
    day_collector=None,
    since: datetime | None = None,
) -> Draft:
    """Collect and narrate. Never raises for a narration failure.

    `since` is when the last digest posted: what counts as new (mail,
    restarts, log errors) starts there instead of 24 hours ago."""
    # Concurrently: these are network reads, not inference. DayCollector never
    # raises, so it cannot take the lab's half of the digest down with it.
    window = {} if since is None else {"since": since}
    if day_collector is not None:
        facts, day = await asyncio.gather(collector.collect(now, **window),
                                          day_collector.collect(now, **window))
    else:
        facts, day = await collector.collect(now, **window), None

    draft = Draft(facts, day)
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
        draft.note = "narration unavailable"
    else:
        draft.narration, draft.model, draft.seconds = completion.text, completion.model, completion.seconds
    return draft


async def build(
    collector,
    ollama,
    now: datetime,
    num_predict: int = 180,
    grafana_url: str | None = None,
    day_collector=None,
    since: datetime | None = None,
) -> str:
    """Collect, narrate, render. Never raises for a narration failure."""
    draft = await prepare(collector, ollama, now, num_predict, day_collector=day_collector, since=since)
    return draft.render(grafana_url)


# --- the digest as a Discord embed ------------------------------------------
#
# The same text as render(), laid out as a card: the heading becomes the
# embed's title, the status its side bar, and the metadata line its footer.
# Built from the rendered text rather than from Facts so the two can never
# disagree, and so the failure path (no heading) simply stays a plain message.
#
# Hex, not tokens: Discord cannot read CSS. These are brignano/design's
# --success and --attention dark values (the second light-dark() argument,
# unchanged through 0.3.0), the ones meant for a fill on a dark ground, which
# is what an embed bar is in Discord's usual theme.
CARD_OK = 0x6FBF95       # --success (dark)
CARD_ATTENTION = 0xE0A44F  # --attention (dark)
CARD_DESCRIPTION_LIMIT = 4096
SUBTEXT = "-# "


@dataclass(frozen=True)
class Card:
    title: str
    description: str
    colour: int
    footer: str | None = None
    url: str | None = None


def card(text: str, grafana_url: str | None = None) -> Card | None:
    """The rendered digest as a card, or None if `text` is not a digest.

    The last line, when it is plain subtext, is the metadata footer. Subtext
    holding links stays in the description: an embed footer cannot link.
    """
    lines = text.strip().split("\n")
    if not lines or not lines[0].startswith("## "):
        return None
    title = lines[0][3:].strip()
    body = lines[1:]
    footer = None
    if body and body[-1].startswith(SUBTEXT) and "](" not in body[-1]:
        footer = body.pop()[len(SUBTEXT):].strip()
    # The links line was subtext only to keep it small under the body; in a
    # card it is the last line of the description, which reads the same.
    body = [ln[len(SUBTEXT):] if ln.startswith(SUBTEXT) else ln for ln in body]
    description = "\n".join(body).strip()
    if len(description) > CARD_DESCRIPTION_LIMIT:
        description = description[:CARD_DESCRIPTION_LIMIT - 1].rstrip() + "…"
    colour = CARD_ATTENTION if title.startswith("⚠️") else CARD_OK
    url = f"{grafana_url.rstrip('/')}/d/{DASH_TRIAGE}" if grafana_url else None
    return Card(title=title, description=description, colour=colour, footer=footer, url=url)
