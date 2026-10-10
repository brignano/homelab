#!/usr/bin/env python3
"""Offline smoke tests for the concierge.

Run:  python3 tests/smoke.py        (from docker/concierge/)

No network, no API key, no Claude Code process. What it pins down is the
security boundary from docs/design/tsd-personal-assistant.md, Decision 2: which
tools exist, which tier each is in, and that the agent options leave nothing
auto-approved and no built-in tool reachable.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import inspect
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_failures: list[str] = []
_passed = 0


def check(name: str):
    def wrap(fn):
        global _passed
        try:
            fn()
            _passed += 1
            print(f"ok    {name}")
        except Exception:  # noqa: BLE001
            _failures.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
        return fn
    return wrap


from app import agent, google, home, tiers, tools  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import validate  # noqa: E402


def _cfg(**kw) -> Config:
    from zoneinfo import ZoneInfo
    base = dict(token="x" * 32, model="claude-opus-5-5", max_turns=12, max_budget_usd=0.5,
                timeout_s=300, max_queue=3, max_proposals=3, tz=ZoneInfo("UTC"))
    base.update(kw)
    return Config(**base)


def _ctx(google_on: bool = True, home_on: bool = True) -> tools.TaskContext:
    import datetime as _dt
    g = object.__new__(google.Google) if google_on else None
    h = home.Home("http://ha:8123", "t") if home_on else None
    return tools.TaskContext(task_id="t", tz=_dt.timezone.utc, google=g,
                             calendar_ids=("primary",), home=h, max_proposals=3)


# --- tiers ----------------------------------------------------------------------

@check("read tools are allowed, as read")
def _():
    for name in tiers.READ:
        d = tiers.decide(tiers.PREFIX + name)
        assert d.allowed and d.tier == "read", d


@check("remember is propose tier and allowed (its effect waits for a ✅)")
def _():
    d = tiers.decide(tiers.PREFIX + "remember")
    assert d.allowed and d.tier == "propose", d


@check("built-ins, other servers, HA write intents and made-up names are never")
def _():
    for name in ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Agent", "Task",
                 "mcp__home__HassTurnOn", "mcp__home__GetLiveContext",
                 "mcp__concierge__send_email", "mcp__concierge__unlock",
                 "mcp__concierge__HassTurnOn", "mcp__concierge_x__mail_read",
                 "mcp__concierge__", "mail_read", ""):
        d = tiers.decide(name)
        assert not d.allowed and d.tier == "never", (name, d)


@check("a write-tier tool is refused until the phase 4 gate exists")
def _():
    original = tiers.WRITE
    try:
        tiers.WRITE = frozenset({"create_event"})
        d = tiers.decide(tiers.PREFIX + "create_event")
        assert d.tier == "write" and not d.allowed, d
    finally:
        tiers.WRITE = original


# --- registered tools -----------------------------------------------------------

@check("every registered tool has a read or propose tier; nothing else is registered")
def _():
    _, names = tools.build(_ctx())
    assert set(names) == tiers.READ | tiers.PROPOSE, names


@check("unconfigured families are not registered at all")
def _():
    _, names = tools.build(_ctx(google_on=False, home_on=False))
    assert names == ["remember"], names
    _, names = tools.build(_ctx(google_on=True, home_on=False))
    assert "home_overview" not in names and "mail_read" in names, names


@check("remember records a bounded one-line proposal and nothing else")
def _():
    ctx = _ctx()
    r = tools.record_proposal(ctx, {"fact": "  prefers\nmorning   appointments "})
    assert not r.get("is_error"), r
    assert ctx.proposals == [{"tool": "remember", "args": {"fact": "prefers morning appointments"}}]
    assert tools.record_proposal(ctx, {"fact": ""}).get("is_error")
    assert tools.record_proposal(ctx, {"fact": 3}).get("is_error")
    tools.record_proposal(ctx, {"fact": "x" * 1000})
    assert len(ctx.proposals[-1]["args"]["fact"]) == tools.MAX_FACT_CHARS
    tools.record_proposal(ctx, {"fact": "three"})
    assert tools.record_proposal(ctx, {"fact": "four"}).get("is_error"), "cap not enforced"
    assert len(ctx.proposals) == 3


# --- agent options: the boundary itself -----------------------------------------

@check("agent options: no built-ins, nothing pre-approved, default mode, no settings")
def _():
    server, _ = tools.build(_ctx())
    o = agent.options_for(_cfg(), "t", server, "sys")
    assert o.tools == [], o.tools
    assert o.allowed_tools == [], o.allowed_tools
    assert o.permission_mode == "default", o.permission_mode
    assert o.setting_sources == [], o.setting_sources
    assert list(o.mcp_servers) == [tiers.SERVER], o.mcp_servers
    assert o.can_use_tool is not None
    assert o.hooks and o.hooks["PreToolUse"][0].matcher is None, "gate hook must match every tool"
    assert o.max_budget_usd == 0.5 and o.max_turns == 12


@check("the PreToolUse hook denies a never-tier tool and passes a read")
def _():
    hook = agent._gate_hook("t")
    deny = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "id"}}, "u", None))
    out = deny["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny", deny
    allow = asyncio.run(hook({"tool_name": tiers.PREFIX + "mail_search", "tool_input": {}}, "u", None))
    assert allow == {}, allow


@check("can_use_tool agrees with the hook")
def _():
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    gate = agent._gate_callback("t")
    assert isinstance(asyncio.run(gate("mcp__home__HassTurnOn", {}, None)), PermissionResultDeny)
    assert isinstance(asyncio.run(gate(tiers.PREFIX + "home_overview", {}, None)), PermissionResultAllow)


@check("system prompt: memories folded in, capped; no memories, no section")
def _():
    now = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.timezone.utc)
    s = agent.system_prompt(["prefers mornings", "  "], now)
    assert "- prefers mornings" in s and "Saturday 10 October 2026" in s
    assert "remember:" not in agent.system_prompt([], now)
    big = agent.system_prompt(["y" * 5000, "z" * 5000], now)
    assert big.count("- ") >= 1 and "z" * 5000 not in big


@check("user prompt: history becomes a transcript, newest kept when trimming")
def _():
    assert agent.user_prompt("hi", []) == "hi"
    p = agent.user_prompt("and Tuesday?", [{"role": "user", "content": "Monday?"},
                                           {"role": "assistant", "content": "Free."}])
    assert p.index("User: Monday?") < p.index("Assistant: Free.") < p.index("and Tuesday?")
    long = [{"role": "user", "content": "old " * 4000}, {"role": "user", "content": "recent"}]
    p = agent.user_prompt("q", long)
    assert "recent" in p and "old old" not in p


# --- Google ---------------------------------------------------------------------

@check("Google scopes are read-only, and never gmail.send")
def _():
    for s in google.SCOPES:
        assert s.endswith(".readonly"), s
    assert not any("send" in s or "compose" in s or "modify" in s for s in google.SCOPES)


@check("mail body: text/plain preferred, html reduced, attachments skipped, capped")
def _():
    def b(s: str) -> str:
        return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    payload = {"payload": {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": b("Plain body")}},
            {"mimeType": "text/html", "body": {"data": b("<p>HTML body</p>")}},
        ]},
        {"mimeType": "text/plain", "filename": "evil.txt", "body": {"data": b("attachment")}},
    ]}}
    assert google.extract_text(payload) == "Plain body"
    html_only = {"payload": {"mimeType": "text/html", "body": {"data": b(
        "<style>x{}</style><p>Hello&amp;bye</p><script>alert(1)</script>")}}}
    assert google.extract_text(html_only) == "Hello&bye"
    long = {"payload": {"mimeType": "text/plain", "body": {"data": b("a" * 100)}}}
    assert google.extract_text(long, limit=10).startswith("a" * 10 + "\n[… cut")


@check("events: cancelled and declined dropped")
def _():
    ev = google.parse_events({"items": [
        {"summary": "Keep", "start": {"dateTime": "2026-10-10T09:00:00Z"}, "end": {"dateTime": "2026-10-10T10:00:00Z"}},
        {"summary": "Gone", "status": "cancelled", "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-11"}},
        {"summary": "No", "attendees": [{"self": True, "responseStatus": "declined"}],
         "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-11"}},
    ]})
    assert [e["title"] for e in ev] == ["Keep"], ev


# --- Home Assistant -------------------------------------------------------------

@check("home.py calls exactly one HA tool, GetLiveContext")
def _():
    src = inspect.getsource(home)
    assert home.LIVE_CONTEXT_TOOL == "GetLiveContext"
    assert src.count("call_tool(") == 1 and "call_tool(LIVE_CONTEXT_TOOL" in src
    for intent in ("HassTurnOn", "HassTurnOff", "HassLightSet", "HassClimateSet"):
        assert f'"{intent}"' not in src and f"'{intent}'" not in src


# --- HTTP request validation ----------------------------------------------------

@check("request validation")
def _():
    assert validate({"prompt": " hi ", "history": [], "memories": []}) == ("hi", [], [])
    assert isinstance(validate({"prompt": ""}), str)
    assert isinstance(validate({"prompt": "x" * 9000}), str)
    assert isinstance(validate({"prompt": "q", "history": [{"role": "system", "content": "x"}]}), str)
    assert isinstance(validate({"prompt": "q", "memories": [1]}), str)
    assert isinstance(validate([]), str)


@check("config: token required and long, Google all-or-none")
def _():
    keep = dict(os.environ)
    try:
        for k in ("CONCIERGE_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
            os.environ.pop(k, None)
        try:
            Config.from_env()
            raise AssertionError("missing token accepted")
        except SystemExit:
            pass
        os.environ["CONCIERGE_TOKEN"] = "short"
        try:
            Config.from_env()
            raise AssertionError("short token accepted")
        except SystemExit:
            pass
        os.environ["CONCIERGE_TOKEN"] = "a" * 64
        os.environ["GOOGLE_CLIENT_ID"] = "id"
        try:
            Config.from_env()
            raise AssertionError("half Google accepted")
        except SystemExit:
            pass
        os.environ.pop("GOOGLE_CLIENT_ID")
        cfg = Config.from_env()
        assert cfg.model == "claude-opus-5-5" and not cfg.google_enabled and not cfg.home_enabled
    finally:
        os.environ.clear()
        os.environ.update(keep)


print(f"\n{_passed} passed, {len(_failures)} failed")
sys.exit(1 if _failures else 0)
