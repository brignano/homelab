"""Which tools the agent may run, decided in code. The model has no say.

See docs/design/tsd-personal-assistant.md, Decision 2.

Every tool the concierge registers lives on ONE in-process MCP server named
`concierge`, so the agent sees it as `mcp__concierge__<name>`. Anything else the
agent might ask for (a Claude Code built-in, a tool from some other server, a
name it made up) is not in these tables and is therefore **never**.

Tiers
-----
  read     runs without asking. Looks things up; changes nothing anywhere.
  propose  the write-tier tools whose effect happens OUTSIDE this process, only
           after a person approves it in Discord. The call itself records a
           proposal and nothing else, so allowing it here is safe: the bot
           renders the arguments verbatim with ✅/❌, and only a ✅ from an
           allowlisted user carries it out. Phase 3 has one: `remember`.
  write    tools that act from inside this process. Phase 4 adds them behind an
           approval gate that pauses the run; until that gate exists, a write
           tool is refused even if someone registers one.
  never    everything else. Not registered with the agent at all: send mail,
           delete, unlock, disarm, open the garage, buy anything.

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
})
PROPOSE = frozenset({"remember"})
# Phase 4. Empty on purpose: see the module docstring.
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


def decide(tool_name: str) -> Decision:
    tier = tier_of(tool_name)
    if tier in ("read", "propose"):
        return Decision(tool_name, tier, True, f"{tier} tier")
    if tier == "write":
        return Decision(tool_name, tier, False,
                        "write tools need the approval gate, which is not built yet (phase 4)")
    return Decision(tool_name, tier, False, "not a tool this assistant may use")
