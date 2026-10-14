"""Phase 4's writes: checked when proposed, carried out only after a ✅.

See docs/design/tsd-personal-assistant.md, Decision 2 (amended for phase 4).

The flow
--------
1. During a run, a propose-tier tool (`create_event`, `move_event`,
   `draft_email`, `run_scene`, `add_movie`, `add_show`) checks and normalizes its arguments with the
   pure functions below and records a proposal with a fresh random id. Nothing
   outside this process changes.
2. The run ends. The server keeps each proposal in `Pending` and returns it to
   the bot, which renders the arguments verbatim under ✅/❌.
3. On a ✅ from an allowlisted user, the bot POSTs the id, tool and arguments
   back to /v1/execute. `Pending.take()` hands the proposal over once, only if
   the tool and arguments are exactly what was stored, and only before it
   expires. `execute()` then makes the one call that tool names.

So the agent never holds a pause open, and nothing the agent says after
proposing can change what runs: the stored arguments are what was shown.
Expired, unknown, replayed or altered ids do nothing. A restart forgets every
pending proposal, which counts as ❌, like the bot's own timeout.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
import secrets
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from .google import Google, GoogleError
from .media import Media, MediaError
from .scenes import SceneError, Scenes

# Proposals a person can act on. Mirrors the bot's APPROVABLE (minus remember,
# whose effect happens in the bot). A test keeps the two in step with tiers.
EXECUTABLE = frozenset({"create_event", "move_event", "draft_email", "run_scene",
                        "add_movie", "add_show"})

# Proposals the bot carries out itself after a ✅, so they never reach
# /v1/execute: `remember` posts to #memory, `follow_up` to #follow-ups.
IN_BOT = frozenset({"remember", "follow_up"})

# A little longer than the bot's 15-minute prompt, so the bot's own expiry is
# always the one a person sees.
TTL_S = 16 * 60

MAX_TITLE = 200
MAX_LOCATION = 200
MAX_DESCRIPTION = 1000
MAX_SUBJECT = 200
MAX_BODY = 1500          # fits a Discord message alongside the rest of the prompt
MAX_RECIPIENTS = 10
MAX_EVENT_DAYS = 14
HORIZON_DAYS = 400

MAX_NOTE = 200
FOLLOW_UP_DAYS = 365
# What the bot does when a follow-up comes due: `remind` pings the user with
# the note (no model run); `check` runs the note as an #ask request in the
# conversation it came from, so Otto looks again and reports.
FOLLOW_UP_THEN = ("remind", "check")

_ID = re.compile(r"^[A-Za-z0-9_\-]{1,1024}$")


class ActionError(RuntimeError):
    """A write was approved but did not go through."""


def _line(raw: object, limit: int, what: str, required: bool = True) -> str:
    if raw is None or raw == "":
        if required:
            raise ValueError(f"{what} is required")
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{what} must be a string")
    text = " ".join(raw.split())
    if required and not text:
        raise ValueError(f"{what} is required")
    if len(text) > limit:
        raise ValueError(f"{what} is over {limit} characters")
    return text


def _text(raw: object, limit: int, what: str) -> str:
    """Multi-line text: newlines kept, other control characters dropped."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{what} must be a string")
    text = "".join(c for c in raw.replace("\r\n", "\n") if c == "\n" or c == "\t" or c >= " ").strip()
    if len(text) > limit:
        raise ValueError(f"{what} is over {limit} characters")
    return text


def parse_when(raw: object, tz: dt.tzinfo) -> dt.date | dt.datetime:
    """`2026-10-14` is all-day; anything with a time is a moment, local if naive."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("start and end must be ISO 8601 strings")
    raw = raw.strip()
    try:
        if len(raw) == 10:
            return dt.date.fromisoformat(raw)
        moment = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{raw!r} is not an ISO 8601 date or date-time") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.replace(microsecond=0)


def _span(args: dict, tz: dt.tzinfo, now: dt.datetime) -> tuple[str, str]:
    start = parse_when(args.get("start"), tz)
    all_day = not isinstance(start, dt.datetime)
    if args.get("end") in (None, ""):
        end = start + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
    else:
        end = parse_when(args.get("end"), tz)
    if isinstance(end, dt.datetime) == all_day:
        raise ValueError("start and end must both be dates or both be date-times")
    if end <= start:
        raise ValueError("end must be after start")
    if end - start > dt.timedelta(days=MAX_EVENT_DAYS):
        raise ValueError(f"events longer than {MAX_EVENT_DAYS} days are not allowed")
    first = start if not all_day else dt.datetime.combine(start, dt.time(), tzinfo=tz)
    if first < now - dt.timedelta(days=1) or first > now + dt.timedelta(days=HORIZON_DAYS):
        raise ValueError(f"start must be between yesterday and {HORIZON_DAYS} days from now")
    return start.isoformat(), end.isoformat()


def _calendar(args: dict, calendars: tuple[str, ...]) -> str:
    cal = args.get("calendar") or calendars[0]
    if cal not in calendars:
        raise ValueError(f"calendar must be one of: {', '.join(calendars)}")
    return cal


def event_args(args: dict, tz: dt.tzinfo, calendars: tuple[str, ...], now: dt.datetime) -> dict:
    """create_event's arguments, normalized. Raises ValueError with a reason."""
    start, end = _span(args, tz, now)
    return {
        "calendar": _calendar(args, calendars),
        "title": _line(args.get("title"), MAX_TITLE, "title"),
        "start": start,
        "end": end,
        "location": _line(args.get("location"), MAX_LOCATION, "location", required=False),
        "description": _text(args.get("description"), MAX_DESCRIPTION, "description"),
    }


def move_args(args: dict, tz: dt.tzinfo, calendars: tuple[str, ...], now: dt.datetime) -> dict:
    """move_event's arguments, before the event itself is looked up."""
    event_id = args.get("event_id")
    if not isinstance(event_id, str) or not _ID.match(event_id):
        raise ValueError("event_id must be an id from calendar_events")
    start, end = _span(args, tz, now)
    return {"calendar": _calendar(args, calendars), "event_id": event_id, "start": start, "end": end}


def _addresses(raw: object) -> list[str]:
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items or not all(isinstance(a, str) for a in items):
        raise ValueError("to must be an address or a list of addresses")
    out = []
    for name, addr in getaddresses(items):
        if not addr or "@" not in addr or any(c in addr for c in "\r\n<>,; "):
            raise ValueError(f"{addr or name!r} is not an email address")
        out.append(addr)
    if len(out) > MAX_RECIPIENTS:
        raise ValueError(f"at most {MAX_RECIPIENTS} recipients")
    return out


def draft_args(args: dict) -> dict:
    """draft_email's arguments, before any reply context is looked up."""
    reply_to = args.get("reply_to_message_id") or ""
    if reply_to and (not isinstance(reply_to, str) or not reply_to.isalnum()):
        raise ValueError("reply_to_message_id must be an id from mail_search")
    body = _text(args.get("body"), MAX_BODY, "body")
    if not body:
        raise ValueError("body is required")
    return {
        "to": _addresses(args.get("to")),
        "subject": _line(args.get("subject"), MAX_SUBJECT, "subject", required=not reply_to),
        "body": body,
        "reply_to_message_id": reply_to,
    }


def scene_args(args: dict, allowed: tuple[str, ...]) -> dict:
    entity = args.get("entity_id")
    if entity not in allowed:
        raise ValueError(f"entity_id must be one of: {', '.join(allowed)}")
    return {"entity_id": entity}


_DIGITS = re.compile(r"^[0-9]{1,10}$")


def media_args(args: dict, id_key: str) -> str:
    """The TMDB or TVDB id add_movie/add_show was given, as digits. Raises ValueError."""
    raw = args.get(id_key)
    text = str(raw).strip() if isinstance(raw, (int, str)) and not isinstance(raw, bool) else ""
    if not _DIGITS.match(text) or int(text) <= 0:
        raise ValueError(f"{id_key} must be the numeric id media_search returned")
    return text


def follow_up_args(args: dict, tz: dt.tzinfo, now: dt.datetime) -> dict:
    """follow_up's arguments, normalized. Raises ValueError with a reason."""
    when = parse_when(args.get("when"), tz)
    if not isinstance(when, dt.datetime):
        raise ValueError("when needs a time of day, e.g. 2026-10-16T09:00; pick 09:00 if the user gave none")
    if when < now + dt.timedelta(minutes=1):
        raise ValueError("when must be in the future")
    if when > now + dt.timedelta(days=FOLLOW_UP_DAYS):
        raise ValueError(f"when must be within {FOLLOW_UP_DAYS} days")
    then = args.get("then") or "remind"
    if then not in FOLLOW_UP_THEN:
        raise ValueError(f"then must be one of: {', '.join(FOLLOW_UP_THEN)}")
    return {"when": when.isoformat(), "note": _line(args.get("note"), MAX_NOTE, "note"), "then": then}


def new_id() -> str:
    return secrets.token_hex(8)


# --- pending proposals ------------------------------------------------------------

@dataclass
class _Held:
    tool: str
    args: dict
    expires: float


class Pending:
    """Proposals waiting on a person. In memory: a restart forgets them (❌)."""

    def __init__(self, ttl_s: float = TTL_S, clock=time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._held: dict[str, _Held] = {}

    def add(self, proposal: dict) -> None:
        self._sweep()
        self._held[proposal["id"]] = _Held(proposal["tool"], proposal["args"], self._clock() + self._ttl)

    def take(self, pid: object, tool: object, args: object) -> tuple[str, dict] | str:
        """The proposal, removed so it runs at most once, or why not."""
        self._sweep()
        held = self._held.get(pid) if isinstance(pid, str) else None
        if held is None:
            return "expired or unknown"
        if tool != held.tool or args != held.args:
            # Not popped: a bad request must not cancel a good proposal.
            return "does not match what was proposed"
        del self._held[pid]
        return held.tool, held.args

    def __len__(self) -> int:
        self._sweep()
        return len(self._held)

    def _sweep(self) -> None:
        now = self._clock()
        for pid in [k for k, v in self._held.items() if v.expires <= now]:
            del self._held[pid]


# --- carrying one out ---------------------------------------------------------------

def _when_body(value: str) -> dict:
    return {"date": value} if len(value) == 10 else {"dateTime": value}


def mime_draft(args: dict, context: dict | None) -> tuple[str, str]:
    """(base64url raw message, thread id). Pure; tested.

    EmailMessage refuses CR/LF in headers, and the arguments were checked when
    proposed, so no field can add a header.
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(args["to"])
    subject = args["subject"]
    thread = ""
    if context:
        original = " ".join(context.get("subject", "").split())
        if not subject:
            subject = original if original.lower().startswith("re:") else f"Re: {original}"
        mid = context.get("message_id", "")
        if mid and "\n" not in mid and "\r" not in mid:
            msg["In-Reply-To"] = mid
            refs = " ".join((context.get("references", "") + " " + mid).split())
            msg["References"] = refs
        thread = context.get("thread_id", "")
    msg["Subject"] = subject
    msg.set_content(args["body"])
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    return raw, thread


async def execute(tool: str, args: dict, google: Google | None, scenes: Scenes | None,
                  media: Media | None = None) -> str:
    """Make the one call `tool` names. Returns a short line for Discord."""
    try:
        if tool in ("create_event", "move_event", "draft_email") and google is None:
            raise ActionError("Google is not configured")
        if tool == "create_event":
            body = {"summary": args["title"], "start": _when_body(args["start"]),
                    "end": _when_body(args["end"])}
            if args.get("location"):
                body["location"] = args["location"]
            if args.get("description"):
                body["description"] = args["description"]
            made = await google.insert_event(args["calendar"], body)
            return f"Event created: {made.get('htmlLink') or args['title']}"
        if tool == "move_event":
            await google.patch_event(args["calendar"], args["event_id"], {
                "start": _when_body(args["start"]), "end": _when_body(args["end"])})
            return f"Event moved to {args['start']}"
        if tool == "draft_email":
            context = None
            if args.get("reply_to_message_id"):
                context = await google.reply_context(args["reply_to_message_id"])
            raw, thread = mime_draft(args, context)
            await google.create_draft(raw, thread)
            return "Draft saved in Gmail, not sent: https://mail.google.com/mail/#drafts"
        if tool == "run_scene":
            if scenes is None:
                raise ActionError("Home Assistant scenes are not configured")
            await scenes.run(args["entity_id"])
            return f"Ran {args['entity_id']}"
        if tool == "add_movie":
            if media is None or media.radarr is None:
                raise ActionError("Radarr is not configured")
            title = await media.radarr.add(args["tmdb_id"], args["quality"], args["folder"])
            return f"Added {title} to Radarr; it is searching now. Ask me \"what's downloading?\" to follow it."
        if tool == "add_show":
            if media is None or media.sonarr is None:
                raise ActionError("Sonarr is not configured")
            title = await media.sonarr.add(args["tvdb_id"], args["quality"], args["folder"])
            return f"Added {title} to Sonarr, every season; it is searching now."
    except (GoogleError, SceneError, MediaError) as exc:
        hint = " (rerun --google-auth: the sign-in predates write access)" \
            if "403" in str(exc) and tool in ("create_event", "move_event", "draft_email") else ""
        raise ActionError(f"{exc}{hint}") from exc
    raise ActionError(f"{tool} is not something I can carry out")
