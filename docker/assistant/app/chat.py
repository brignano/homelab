"""How #ask reads a conversation out of Discord.

Where the memory lives
----------------------
Nowhere. There is no store, no database, no in-process dict. When a message
arrives the bot reads the relevant messages out of Discord and hands them to
the concierge as the conversation.

That is deliberate, and it buys several things for free:

* **Restart-safe.** The container can be rebuilt mid-conversation and the thread
  picks up exactly where it was — the history was never in the container.
* **What you see is what it sees.** Delete a message and it leaves the context.
* **Threads are isolated for free.** A Discord thread has its own history, so it
  is its own conversation with no extra bookkeeping.

The cost is that reading channel history requires the Message Content intent —
see the note in bot.py.

(This module once also held the local model's #chat prompts. #chat and the
local model were removed in 2026-11.)
"""

from __future__ import annotations

from dataclasses import dataclass

# Messages starting with this are for humans only — the bot ignores them, and
# they are also left out of the context it reads back. Lets you leave notes,
# links and asides in the channel without provoking a reply or muddying the
# conversation.
IGNORE_PREFIX = "//"

# The bot appends a footer line to its own replies. Feeding
# those back as conversation would teach it to imitate them.
FOOTER_PREFIX = "-#"

# --- How much context, and from where -----------------------------------------

# Rolling channel history was the first attempt and it was wrong: two unrelated
# questions typed into the same channel contaminate each other, and there is no
# way to end a conversation short of waiting for it to scroll away.
#
# Discord already has the right primitives, so the rule follows them rather than
# inventing one. Where you type decides what the model remembers:
#
#   in a thread   ->  the whole thread. Threads (and forum posts, which are
#                     threads) are the persistence unit: titled, listed, and
#                     scoped. This is the "individual chat with its own context".
#   a reply       ->  walk the reply chain. Picks up an exchange without
#                     ceremony, and the chain is exactly what you can see.
#   anything else ->  just this message. A one-off question stays a one-off and
#                     leaves no residue for the next one.
#
# The nice property is that it needs no commands and no state: the context is
# always visibly implied by where the message sits.

THREAD = "thread"
CHAIN = "chain"
SINGLE = "single"


def context_mode(in_thread: bool, is_reply: bool) -> str:
    """Which context rule applies. Thread membership wins over reply-ness —
    inside a thread the whole thread is the conversation, so a reply there adds
    nothing."""
    if in_thread:
        return THREAD
    return CHAIN if is_reply else SINGLE


@dataclass(frozen=True)
class Turn:
    """One message in the conversation, already reduced to what the model needs."""
    role: str      # "user" or "assistant"
    content: str


def clean_bot_text(text: str) -> str:
    """Strip the metadata footer from one of the bot's own past replies."""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(FOOTER_PREFIX)]
    return "\n".join(lines).strip()


def is_ignorable(content: str) -> bool:
    """Messages the bot should neither answer nor remember."""
    stripped = content.strip()
    return not stripped or stripped.startswith(IGNORE_PREFIX)

