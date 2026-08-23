"""Conversational mode: talk in a channel, no slash command, with memory.

Where the memory lives
----------------------
Nowhere. There is no store, no database, no in-process dict. When a message
arrives the bot reads the last N messages out of the Discord channel and hands
them to the model as the conversation.

That is deliberate, and it buys several things for free:

* **Restart-safe.** The container can be rebuilt mid-conversation and the thread
  picks up exactly where it was — the history was never in the container.
* **What you see is what it sees.** Delete a message and it leaves the context.
  Scroll up and you are reading the model's actual working memory.
* **Threads are isolated for free.** A Discord thread has its own history, so it
  is its own conversation with no extra bookkeeping.

The cost is that reading channel history requires the Message Content intent —
see the note in bot.py.

Why the history is capped
-------------------------
Prompt evaluation on a CPU-only box costs real wall-clock roughly linear in
token count, so an unbounded history would make every reply slower than the
last until the box crawled. Both a message count and a character budget apply;
the character budget is what actually protects against one pasted wall of text
eating the whole window.
"""

from __future__ import annotations

from dataclasses import dataclass

# Messages starting with this are for humans only — the bot ignores them, and
# they are also left out of the context it reads back. Lets you leave notes,
# links and asides in the channel without provoking a reply or muddying the
# conversation.
IGNORE_PREFIX = "//"

# The bot appends a footer line (model, seconds) to its own replies. Feeding
# those back as conversation would teach it to imitate them.
FOOTER_PREFIX = "-#"

CHAT_SYSTEM = (
    "You are in an ongoing conversation. Reply to the most recent message, using "
    "the earlier turns for context. Answer directly — no preamble, no restating "
    "the question, and no describing yourself or your setup.\n"
    "Keep replies short: a sentence or two unless genuinely more is needed. This "
    "is a chat, not an essay.\n"
    "You cannot browse the internet and cannot see the user's machines, files or "
    "any live data. If something needs those, say so in one short sentence and "
    "then answer whatever part you can. Never invent specifics you would need "
    "live access to know."
)


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


def build_messages(
    turns: list[Turn],
    system: str = CHAT_SYSTEM,
    max_turns: int = 12,
    max_chars: int = 4000,
) -> list[dict[str, str]]:
    """Assemble the /api/chat payload from oldest-to-newest turns.

    Trims from the *front*: the most recent exchanges are what matter, and the
    newest message especially — it's the one being answered. Both caps apply,
    whichever bites first.
    """
    kept: list[Turn] = []
    used = 0
    for turn in reversed(turns):          # newest first while budgeting
        if len(kept) >= max_turns:
            break
        cost = len(turn.content)
        # Always keep the newest turn even if it alone blows the budget; it is
        # the question. clamp_input upstream stops it being enormous.
        if kept and used + cost > max_chars:
            break
        kept.append(turn)
        used += cost
    kept.reverse()                        # back to chronological for the model

    return [{"role": "system", "content": system}] + [
        {"role": t.role, "content": t.content} for t in kept
    ]
