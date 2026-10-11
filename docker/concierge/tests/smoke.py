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


from app import actions, agent, google, home, scenes, tiers, tools  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import validate  # noqa: E402


def _cfg(**kw) -> Config:
    from zoneinfo import ZoneInfo
    base = dict(token="x" * 32, model="claude-opus-5-5", max_turns=12, max_budget_usd=0.5,
                timeout_s=300, max_queue=3, max_proposals=3, tz=ZoneInfo("UTC"))
    base.update(kw)
    return Config(**base)


def _ctx(google_on: bool = True, home_on: bool = True,
         scene_ids: tuple[str, ...] = ("scene.movie",)) -> tools.TaskContext:
    import datetime as _dt
    g = object.__new__(google.Google) if google_on else None
    h = home.Home("http://ha:8123", "t") if home_on else None
    return tools.TaskContext(task_id="t", tz=_dt.timezone.utc, google=g,
                             calendar_ids=("primary",), home=h, max_proposals=3,
                             scenes=scene_ids if home_on else ())


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


@check("phase 4 writes are propose tier: allowed to run, because running changes nothing")
def _():
    for name in ("create_event", "move_event", "draft_email", "run_scene"):
        d = tiers.decide(tiers.PREFIX + name)
        assert d.allowed and d.tier == "propose", d
    assert tiers.PROPOSE - {"remember"} == actions.EXECUTABLE


@check("a write-tier tool (one that acts during the run) is always refused")
def _():
    original = tiers.WRITE
    try:
        tiers.WRITE = frozenset({"act_now"})
        d = tiers.decide(tiers.PREFIX + "act_now")
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
    assert "run_scene" not in names and "draft_email" in names, names
    _, names = tools.build(_ctx(google_on=False, home_on=True, scene_ids=()))
    assert "run_scene" not in names and "create_event" not in names, names


@check("remember records a bounded one-line proposal and nothing else")
def _():
    ctx = _ctx()
    r = tools.record_proposal(ctx, {"fact": "  prefers\nmorning   appointments "})
    assert not r.get("is_error"), r
    assert [(p["tool"], p["args"]) for p in ctx.proposals] == \
        [("remember", {"fact": "prefers morning appointments"})]
    assert len(ctx.proposals[0]["id"]) == 16
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

@check("Google scopes: calendar events, mail read, drafts — never gmail.send or modify")
def _():
    names = {s.rsplit("/", 1)[1] for s in google.SCOPES}
    assert names == {"calendar.events", "gmail.readonly", "gmail.compose"}, names


@check("google.py has no send, delete or modify call (gmail.compose could send)")
def _():
    src = inspect.getsource(google)
    for bad in ("/send", "drafts/send", '"DELETE"', "'DELETE'", "/trash", "/modify", "batchDelete"):
        assert bad not in src, bad
    writes = [m for m in ("insert_event", "patch_event", "create_draft") if f"def {m}(" in src]
    assert len(writes) == 3 and src.count("self._send(") == 3, "a new write must be added on purpose"


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


# --- phase 4: argument checks, pending store, MIME -------------------------------

NOW = dt.datetime(2026, 10, 10, 9, 0, tzinfo=dt.timezone.utc)


@check("create_event: naive times are local, end defaults, bad spans refused")
def _():
    a = actions.event_args({"title": " Dentist\n", "start": "2026-10-14T09:00"},
                           dt.timezone.utc, ("primary",), NOW)
    assert a == {"calendar": "primary", "title": "Dentist", "start": "2026-10-14T09:00:00+00:00",
                 "end": "2026-10-14T10:00:00+00:00", "location": "", "description": ""}, a
    d = actions.event_args({"title": "Trip", "start": "2026-10-20"}, dt.timezone.utc, ("primary",), NOW)
    assert (d["start"], d["end"]) == ("2026-10-20", "2026-10-21"), d
    for bad in ({"title": "", "start": "2026-10-14T09:00"},
                {"title": "x", "start": "tomorrow"},
                {"title": "x", "start": "2026-10-14T09:00", "end": "2026-10-14T08:00"},
                {"title": "x", "start": "2026-10-14", "end": "2026-10-15T09:00"},
                {"title": "x", "start": "2026-10-14", "end": "2026-11-30"},
                {"title": "x", "start": "2020-01-01T09:00"},
                {"title": "x", "start": "2026-10-14T09:00", "calendar": "someone@else"}):
        try:
            actions.event_args(bad, dt.timezone.utc, ("primary",), NOW)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


@check("draft_email: addresses checked, no header injection, reply needs no subject")
def _():
    a = actions.draft_args({"to": "Ann <ann@example.com>", "subject": "Hi", "body": "Hello\r\nthere\x07"})
    assert a == {"to": ["ann@example.com"], "subject": "Hi", "body": "Hello\nthere",
                 "reply_to_message_id": ""}, a
    assert actions.draft_args({"to": ["a@b.c"], "body": "ok", "reply_to_message_id": "abc123"})["subject"] == ""
    for bad in ({"to": "nobody", "subject": "s", "body": "b"},
                {"to": ["a@b.c"], "subject": "", "body": "b"},
                {"to": ["a@b.c"], "subject": "s", "body": ""},
                {"to": ["a@b.c"], "subject": "s", "body": "x" * 2000},
                {"to": ["a@b.c"], "body": "b", "reply_to_message_id": "../x"},
                {"to": [f"a{i}@b.c" for i in range(11)], "subject": "s", "body": "b"}):
        try:
            actions.draft_args(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")
    subj = actions.draft_args({"to": "a@b.c", "subject": "Hi\nBcc: evil@x.y", "body": "b"})["subject"]
    assert "\n" not in subj
    raw, thread = actions.mime_draft({"to": ["a@b.c"], "subject": "", "body": "Sure.",
                                      "reply_to_message_id": "m1"},
                                     {"subject": "Lunch?", "message_id": "<x@y>", "references": "",
                                      "thread_id": "t9"})
    text = base64.urlsafe_b64decode(raw + "==").decode()
    assert thread == "t9" and "Subject: Re: Lunch?" in text and "In-Reply-To: <x@y>" in text, text


@check("move_event: events with other people on them are spotted")
def _():
    assert google.other_attendees({}) == 0
    assert google.other_attendees({"organizer": {"self": True},
                                   "attendees": [{"self": True}, {"resource": True}]}) == 0
    assert google.other_attendees({"organizer": {"self": True},
                                   "attendees": [{"self": True}, {"email": "a@b.c"}]}) == 1
    assert google.other_attendees({"organizer": {"email": "boss@x.y"}}) == 1
    assert google.other_attendees({"organizer": {"email": "boss@x.y"},
                                   "attendees": [{"email": "boss@x.y", "organizer": True}, {"self": True}]}) == 1


@check("run_scene: only allowlisted scene IDs")
def _():
    assert actions.scene_args({"entity_id": "scene.movie"}, ("scene.movie",)) == {"entity_id": "scene.movie"}
    for bad in ("lock.front_door", "scene.other", None):
        try:
            actions.scene_args({"entity_id": bad}, ("scene.movie",))
        except ValueError:
            continue
        raise AssertionError(bad)
    assert scenes.SERVICE_PATH == "/api/services/scene/turn_on"


@check("pending: runs once, only unaltered, only before expiry")
def _():
    t = [0.0]
    p = actions.Pending(ttl_s=60, clock=lambda: t[0])
    prop = {"id": "a1", "tool": "create_event", "args": {"title": "x"}}
    p.add(prop)
    assert p.take("a1", "create_event", {"title": "y"}) == "does not match what was proposed"
    assert p.take("a1", "draft_email", {"title": "x"}) == "does not match what was proposed"
    assert p.take("a1", "create_event", {"title": "x"}) == ("create_event", {"title": "x"})
    assert p.take("a1", "create_event", {"title": "x"}) == "expired or unknown", "replayed"
    p.add({"id": "b2", "tool": "run_scene", "args": {}})
    t[0] = 61
    assert p.take("b2", "run_scene", {}) == "expired or unknown" and len(p) == 0
    assert p.take(None, "x", {}) == "expired or unknown"


@check("execute: refuses unknown tools and unconfigured families")
def _():
    for tool, g in (("send_email", None), ("create_event", None), ("run_scene", None)):
        try:
            asyncio.run(actions.execute(tool, {"entity_id": "scene.x"}, g, None))
        except actions.ActionError:
            continue
        raise AssertionError(tool)


@check("config: HA_SCENES must be scene.* IDs")
def _():
    keep = dict(os.environ)
    try:
        os.environ.update(CONCIERGE_TOKEN="a" * 64, HA_SCENES="scene.movie, lock.front_door")
        try:
            Config.from_env()
            raise AssertionError("lock accepted as a scene")
        except SystemExit:
            pass
        os.environ.update(HA_SCENES="scene.movie,scene.good_night", HA_TOKEN="t")
        c = Config.from_env()
        assert c.ha_scenes == ("scene.movie", "scene.good_night") and c.scenes_enabled
    finally:
        os.environ.clear()
        os.environ.update(keep)


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
