"""Offline smoke tests for the calendar MCP server. No Home Assistant, no network.

Pins the read-only boundary: which tools exist, that the only HA call is a GET
to the fixed path for a configured calendar, and that a request with the wrong
Host never reaches the MCP endpoint.

    python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.update(HA_TOKEN="t", HA_CALENDARS="calendar.events, calendar.work", HA_URL="http://ha:8123")

from app import calendars  # noqa: E402
from app.calendars import CalendarError, HaCalendars, parse_entities, trim, window  # noqa: E402

NY = ZoneInfo("America/New_York")
TODAY = dt.date(2026, 10, 13)


def test_entities():
    assert parse_entities(" calendar.a, calendar.b ,calendar.a,") == ("calendar.a", "calendar.b")
    for bad in ("sensor.x", "calendar.A", "calendar.a/../b", "calendar."):
        try:
            parse_entities(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_window():
    begin, end = window(TODAY, None, 7, NY)
    assert begin == dt.datetime(2026, 10, 13, tzinfo=NY) and end - begin == dt.timedelta(days=7)
    assert window(TODAY, "2026-10-17", 1, NY)[0].date() == dt.date(2026, 10, 17)
    assert window(TODAY, None, 999, NY)[1] - window(TODAY, None, 999, NY)[0] == dt.timedelta(days=31)
    assert window(TODAY, None, 0, NY)[1] - window(TODAY, None, 0, NY)[0] == dt.timedelta(days=1)
    for bad in ("2028-01-01", "next friday"):
        try:
            window(TODAY, bad, 7, NY)
        except CalendarError:
            continue
        raise AssertionError(f"accepted start {bad!r}")


def test_trim():
    out = trim([
        {"summary": "Date", "start": {"dateTime": "2026-10-17T19:00:00-04:00"},
         "end": {"dateTime": "2026-10-17T21:00:00-04:00"}, "location": "Hartford",
         "description": "text someone else wrote", "attendees": ["x"]},
        {"summary": "All day", "start": {"date": "2026-10-18"}},
        {"summary": "no start"},
        "junk",
    ])
    assert out == [
        {"title": "Date", "start": "2026-10-17T19:00:00-04:00", "end": "2026-10-17T21:00:00-04:00",
         "location": "Hartford"},
        {"title": "All day", "start": "2026-10-18"},
    ]
    assert len(trim([{"start": "2026-10-13"}] * 500)) == calendars.MAX_EVENTS


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        return FakeResp(*self.replies[url])


def test_reads():
    cals = HaCalendars("http://ha:8123/", "t", ("calendar.events", "calendar.work"))
    assert cals.pick(None) == ("calendar.events", "calendar.work")
    assert cals.pick("calendar.work") == ("calendar.work",)
    for bad in ("calendar.other", "../states"):
        try:
            cals.pick(bad)
        except CalendarError:
            continue
        raise AssertionError(f"picked {bad!r}")
    for unset in (HaCalendars("http://ha", "", ("calendar.a",)), HaCalendars("http://ha", "t", ())):
        try:
            unset.pick(None)
        except CalendarError:
            continue
        raise AssertionError("ran without config")

    session = FakeSession({
        "http://ha:8123/api/calendars/calendar.events": (200, [{"summary": "Date", "start": {"date": "2026-10-17"}}]),
        "http://ha:8123/api/calendars/calendar.work": (500, None),
    })
    begin, end = window(TODAY, None, 7, NY)
    out = asyncio.run(cals.events(session, cals.pick(None), begin, end))
    assert out["calendar.events"] == [{"title": "Date", "start": "2026-10-17"}]
    assert "error" in out["calendar.work"], out
    assert [c[0] for c in session.calls] == ["GET", "GET"]
    assert session.calls[0][2] == {"start": "2026-10-13T00:00:00-04:00", "end": "2026-10-20T00:00:00-04:00"}
    assert session.calls[0][3] == {"Authorization": "Bearer t"}

    try:
        asyncio.run(cals.events(FakeSession({"http://ha:8123/api/calendars/calendar.work": (401, None)}),
                                ("calendar.work",), begin, end))
    except CalendarError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("all calendars failing must raise")


def test_read_only_source():
    src = pathlib.Path(calendars.__file__).read_text()
    assert calendars.PATH == "/api/calendars/"
    assert src.count("session.get(") == 1
    for verb in (".post(", ".put(", ".patch(", ".delete(", ".request("):
        assert verb not in src, verb


def test_server():
    from starlette.testclient import TestClient

    from app import __main__ as server

    tools = asyncio.run(server.mcp.list_tools())
    assert sorted(t.name for t in tools) == ["calendar_events", "list_calendars"], [t.name for t in tools]
    assert server.CALENDARS.entities == ("calendar.events", "calendar.work")

    app = server.mcp.streamable_http_app()
    with TestClient(app, base_url="http://mcp-calendar:8000") as client:
        assert client.get("/healthz").status_code == 200
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "smoke", "version": "0"}}}
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        ok = client.post(server.PATH, json=init, headers=headers)
        assert ok.status_code == 200, (ok.status_code, ok.text)
        # The public name must never reach the app directly: Caddy rewrites Host.
        bad = client.post(server.PATH, json=init, headers={**headers, "Host": "mcp.example.com"})
        assert bad.status_code == 421, bad.status_code
        listed = client.post(server.PATH, headers=headers,
                             json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert {t["name"] for t in listed.json()["result"]["tools"]} == {"calendar_events", "list_calendars"}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok   ", name)
