"""Small text helpers shared by the Discord surfaces."""

from __future__ import annotations

# Discord's hard limit is 2000 characters; leave room for the code fences and
# footers we wrap replies in.
DISCORD_LIMIT = 1900


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
