"""Follow-ups: the concierge proposes one, you approve it, the bot keeps it.

See docs/design/tsd-personal-assistant.md, "Status and next up" → Follow-ups.

A follow-up is one bot message in the bot-only #follow-ups channel, the same
idea as #memory: restart-safe, no datastore, and you can see every pending one
and cancel it by deleting its message. The bot writes the message in a fixed
three-line shape and reads it back with `parse`, so nothing but the bot's own
post can schedule anything (`parse` is only ever given the bot's messages).

    ⏰ <t:1792155600:f> (<t:1792155600:R>) · remind
    Did Dr. Lee's office reply about Thursday?
    -# for <@123> in https://discord.com/channels/<guild>/<thread>

When one comes due the bot deletes the message first, so a follow-up fires at
most once, then goes back to the thread it was set in: `remind` pings you with
the note and costs nothing; `check` also runs the note as an #ask request in
that thread, so Otto looks again (has the reply come?) and reports.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from .text import clock

MAX_NOTE = 200
THEN = ("remind", "check")
# Fired this long after it was due, a follow-up says so: the bot was down.
LATE_S = 10 * 60

_HEAD = re.compile(r"^⏰ <t:(\d{1,12}):f> \(<t:\1:R>\) · (remind|check)$")
_TAIL = re.compile(r"^-# for <@(\d{1,20})> in https://discord\.com/channels/(\d{1,20})/(\d{1,20})$")


@dataclass(frozen=True)
class FollowUp:
    due: int            # Unix time
    note: str
    then: str           # remind | check
    user_id: int        # who approved it, and who gets pinged
    guild_id: int
    channel_id: int     # the #ask thread it was set in


def clean_note(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    note = " ".join(raw.split())
    return note[:MAX_NOTE] if note else None


def due_of(raw: object) -> int | None:
    """An ISO 8601 date-time with an offset (as the concierge normalizes it)."""
    if not isinstance(raw, str):
        return None
    try:
        when = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return int(when.timestamp())


def valid_args(args: dict) -> bool:
    """Is a follow_up proposal something a ✅ can save?"""
    return (due_of(args.get("when")) is not None and clean_note(args.get("note")) is not None
            and (args.get("then") or "remind") in THEN)


def from_args(args: dict, user_id: int, guild_id: int, channel_id: int) -> FollowUp | None:
    if not valid_args(args):
        return None
    return FollowUp(due_of(args["when"]), clean_note(args["note"]), args.get("then") or "remind",
                    user_id, guild_id, channel_id)


def render(f: FollowUp) -> str:
    """The #follow-ups message. Posted with no mentions allowed."""
    return (f"⏰ <t:{f.due}:f> (<t:{f.due}:R>) · {f.then}\n{f.note}\n"
            f"-# for <@{f.user_id}> in https://discord.com/channels/{f.guild_id}/{f.channel_id}")


def parse(content: str) -> FollowUp | None:
    """A follow-up the bot wrote, or None for anything else."""
    lines = (content or "").split("\n")
    if len(lines) != 3:
        return None
    head, tail = _HEAD.match(lines[0]), _TAIL.match(lines[2])
    note = clean_note(lines[1])
    if head is None or tail is None or note is None:
        return None
    return FollowUp(int(head.group(1)), note, head.group(2), int(tail.group(1)),
                    int(tail.group(2)), int(tail.group(3)))


def show_when(due: int, tz: dt.tzinfo) -> str:
    when = dt.datetime.fromtimestamp(due, tz=tz)
    return f"{when.strftime('%a %d %b')} {clock(when)}"


def prompt_text(args: dict, tz: dt.tzinfo) -> str:
    """The approval prompt's code block lines, from the arguments alone."""
    due = due_of(args.get("when"))
    then = args.get("then") or "remind"
    lines = [f"when: {show_when(due, tz)}" if due is not None else "when: ?",
             f"note: {clean_note(args.get('note')) or ''}",
             "then: " + ("ping you here with the note" if then == "remind"
                         else "look into it again here and report (one #ask request)")]
    return "\n".join(lines)


def fired_text(f: FollowUp, now: int) -> str:
    """What the thread sees when it comes due. The note is defanged by the caller."""
    late = f" (late: due <t:{f.due}:f>, Otto was offline)" if now - f.due > LATE_S else ""
    lead = "Follow-up" if f.then == "remind" else "Following up, looking into it now"
    return f"⏰ <@{f.user_id}> {lead}{late}: {f.note}"


def check_prompt(f: FollowUp) -> str:
    """The #ask request a `check` follow-up runs, in the thread it was set in."""
    return (f"(A follow-up the user approved earlier has come due.) {f.note}\n"
            "Look into it now and tell them where it stands in a few lines. "
            "Propose a next step only if one is needed.")


def upcoming(items: list[FollowUp], now: int, within_s: int) -> list[FollowUp]:
    """Pending follow-ups due in the next `within_s` seconds, soonest first."""
    return sorted((f for f in items if now <= f.due <= now + within_s), key=lambda f: f.due)
