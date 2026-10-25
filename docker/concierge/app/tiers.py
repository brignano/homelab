"""Which tools the agent may run, decided in code. The model has no say.

See docs/design/tsd-personal-assistant.md, Decision 2.

Every tool the concierge registers lives on ONE in-process MCP server named
`concierge`, so the agent sees it as `mcp__concierge__<name>`. Anything else the
agent might ask for (a Claude Code built-in, a tool from some other server, a
name it made up) is not in these tables and is therefore **never**.

Tiers
-----
  read     runs without asking. Looks things up; changes nothing anywhere.
  propose  every write. The call records a proposal and nothing else, so
           allowing it here is safe: the bot renders the arguments verbatim with
           ✅/❌, and only a ✅ from an allowlisted user carries it out — in the
           bot for `remember` and `follow_up`, through /v1/execute for phase 4's calendar,
           draft and scene tools (app/actions.py), never during the run.
  write    a tool that would act from inside the run. There are none, and
           decide() refuses one even if someone registers it: every write goes
           through propose, so the agent never holds a pause open.
  never    everything else. Not registered with the agent at all: send mail,
           delete, unlock, disarm, open the garage, buy anything, remove a
           movie or show.

The scheduled morning brief (/v1/brief) runs with `read_only=True`: the
propose tier is refused too, and tools.build() never registers it. That run
reads mail on a timer with nobody asking, so it holds no write tools at all —
not even proposals (the TSD's rejected "one agent that does everything").

The evening review (/v1/review) runs with `only={"remember", "follow_up"}`:
it reads no mailbox, calendar or home, only what the bot sends it, and the
one thing it can do is propose a memory or a follow-up, both carried out by
the bot after a ✅. What the bot sends can include mail and events that
earlier #ask answers quoted, so this is a timer-started run that sees
stranger-written text second-hand and holds two proposal tools. Accepted:
neither acts without a ✅, and neither reaches /v1/execute.

A follow-up set to `check` runs as an ordinary /v1/ask request when it comes
due, with every read and propose tool. It is a request the user approved
earlier, deferred, not a run nobody asked for; its proposals still wait on a
✅ like any other.

The same `decide()` runs twice per call — in the PreToolUse hook, which the SDK
runs before anything else, and in `can_use_tool`. The hook is the one that
cannot be skipped: an allow rule or permission mode can approve a call before
`can_use_tool` is ever asked, and nothing here relies on that not happening.
"""

from __future__ import annotations

from dataclasses import dataclass

SERVER = "concierge"
PREFIX = f"mcp__{SERVER}__"

READ = frozenset({
    "calendar_events",
    "mail_search",
    "mail_read",
    "home_overview",
    "media_search",
    "media_downloads",
    "weather_forecast",
    "home_calendar_events",
    "homelab_status",
    "github_status",
    "movie_night",
})
PROPOSE = frozenset({"remember", "follow_up", "create_event", "move_event", "draft_email", "run_scene",
                     "add_movie", "add_show"})
# Empty on purpose: see the module docstring.
WRITE: frozenset[str] = frozenset()

assert not (READ & PROPOSE) and not (READ & WRITE) and not (PROPOSE & WRITE)


@dataclass(frozen=True)
class Decision:
    tool: str
    tier: str            # read | propose | write | never
    allowed: bool
    reason: str


def short_name(tool_name: str) -> str | None:
    """`mcp__concierge__mail_read` -> `mail_read`; None for anything else."""
    if not tool_name.startswith(PREFIX):
        return None
    return tool_name[len(PREFIX):]


def tier_of(tool_name: str) -> str:
    name = short_name(tool_name)
    if name in READ:
        return "read"
    if name in PROPOSE:
        return "propose"
    if name in WRITE:
        return "write"
    return "never"


def decide(tool_name: str, read_only: bool = False,
           only: frozenset[str] | None = None) -> Decision:
    """`read_only` is the scheduled morning brief (/v1/brief): no person asked
    for anything, so there is nothing to propose and only read tier runs.
    `only` narrows a run to those tools (the evening review)."""
    tier = tier_of(tool_name)
    if only is not None and short_name(tool_name) not in only:
        return Decision(tool_name, tier, False, "not a tool this run may use")
    if tier == "read" or (tier == "propose" and not read_only):
        return Decision(tool_name, tier, True, f"{tier} tier")
    if tier == "propose":
        return Decision(tool_name, tier, False,
                        "the morning brief is read-only; it cannot propose changes")
    if tier == "write":
        return Decision(tool_name, tier, False,
                        "tools that act during the run are not allowed; writes are proposals")
    return Decision(tool_name, tier, False, "not a tool this assistant may use")
