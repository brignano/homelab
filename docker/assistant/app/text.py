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


def clamp_input(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim oversized input before it reaches the model.

    Prompt evaluation on a CPU-only box costs real wall-clock per token, so a
    pasted wall of text is the easiest way to tie up the single worker for
    minutes. Returns the (possibly trimmed) text and whether trimming happened,
    so the caller can say so rather than silently answering about half a document.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


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


# Discord caps a thread name at 100 characters.
THREAD_NAME_LIMIT = 100


def thread_name(text: str, limit: int = 80) -> str:
    """A short thread title from the message that starts it.

    The first non-empty line, whitespace collapsed, cut at a word boundary.
    Mentions and links stay as typed: the name is only ever shown to you.
    """
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    line = " ".join(line.split())
    limit = min(limit, THREAD_NAME_LIMIT)
    if not line:
        return "Request"
    if len(line) <= limit:
        return line
    cut = line.rfind(" ", 0, limit - 1)
    if cut < limit // 2:
        cut = limit - 1
    return line[:cut].rstrip() + "…"


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
