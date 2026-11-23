"""Small text helpers shared by the Discord surfaces."""

from __future__ import annotations

import datetime as dt

# Discord's hard limit is 2000 characters; leave room for the code fences and
# footers we wrap replies in.
DISCORD_LIMIT = 1900


def clock(when: dt.datetime) -> str:
    """A time of day the way people say it here: "10pm", "6:30am"."""
    hour = when.hour % 12 or 12
    mins = f":{when.minute:02d}" if when.minute else ""
    return f"{hour}{mins}{'am' if when.hour < 12 else 'pm'}"


def chunk(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split text into Discord-sized messages, preferring paragraph then line breaks."""
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        # Break at the latest paragraph boundary, then line, then space.
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut <= 0:
            cut = limit  # one very long token — hard split
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def span(delta: dt.timedelta) -> str:
    """A window's length as the digest labels it: "40m", "9h", "3d".

    Rounded to the nearest unit, never below one minute, so a label can't
    read "0m" for a window that did cover something."""
    minutes = max(1, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours}h"
    return f"{round(hours / 24)}d"
