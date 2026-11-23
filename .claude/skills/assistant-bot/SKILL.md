---
name: assistant-bot
description: "How Otto, the Discord bot in docker/assistant/, works — guild.yml provisioning, the daily digest, the weekly import reminder, and the rules for extending it. Use when touching docker/assistant/ or the digest."
---

# The assistant bot (Otto)

`docker/assistant/` is a Discord bot that posts the daily digest and a weekly
bank-import reminder, and answers `/digest` and `/status`. It calls no model
and costs nothing to run: Ollama went in 2026-11 (#406), and the paid #ask
assistant (`docker/concierge/`, Claude via the API, with #memory, follow-ups,
the morning brief and the evening review) was retired on 2026-11-23 (#366) for
Claude Duet. Don't add a model or an API-billed feature back without its own
issue: Anthony wants this to cost nothing.

The Discord server layout is declarative: `docker/assistant/guild.yml` holds the
categories/channels/permissions and `--provision` converges the server to it
(idempotent, additive, never deletes — `#digest` is a log whose history matters).
Creating the server, creating the bot, and restricting a command to a channel
all require a *user* login and stay manual; everything else is in git.

The bot needs no privileged intents: it never reads a channel, and slash
commands carry their own payloads.

Two rules when extending it:
- **Python decides what's true.** Facts are queried and thresholded in code
  (`app/facts.py`); the digest's marks and its verdict come from the same
  thresholds, so they cannot disagree.
- **A failure must degrade, not delete.** The digest posts what it could read;
  unreadable data is reported, never rendered as "all clear".
