---
name: assistant-bot
description: "How Otto, the Discord bot in docker/assistant/, works — guild.yml provisioning, the daily digest, #ask conversation scope and memory, and the rules for extending it. Use when touching docker/assistant/ or the digest."
---

# The assistant bot (Otto)

`docker/assistant/` is a Discord bot that posts the daily digest, forwards #ask
to the concierge (`docker/concierge/`), and runs follow-ups. There is no local
model: Ollama and Open WebUI were removed in 2026-11 after the Duet trial, and
the digest is rendered entirely by code.

The Discord server layout is declarative: `docker/assistant/guild.yml` holds the
categories/channels/permissions and `--provision` converges the server to it
(idempotent, additive, never deletes — `#digest` is a log whose history matters).
Creating the server, creating the bot, and restricting a command to a channel
all require a *user* login and stay manual; everything else is in git.

#ask keeps its conversation **in Discord** — the bot re-reads messages as
context rather than holding state, so it is restart-safe and what you see is
what the concierge sees. *Where* you type decides the scope: a plain message is
a one-off, a reply walks its reply chain, and a message in a thread reads the
whole thread (`app/chat.py`). It needs the Message Content intent, narrowed in
code to #ask and the user allowlist; the first check in `_route` (bot.py)
ignores bots (including itself), which is what prevents an infinite self-reply
loop.

Two rules when extending it:
- **Python decides what's true.** Facts are queried and thresholded in code
  (`app/facts.py`); the digest's marks and its verdict come from the same
  thresholds, so they cannot disagree.
- **A failure must degrade, not delete.** The digest posts what it could read;
  unreadable data is reported, never rendered as "all clear".
