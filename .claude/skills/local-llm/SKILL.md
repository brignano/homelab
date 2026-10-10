---
name: local-llm
description: How this homelab runs its local model — adding or reloading an Ollama model, and how the Discord assistant bot in docker/assistant/ works (guild.yml provisioning, #chat memory and scope, live readings by injection, the digest). Use when touching docker/ai/, a Modelfile, docker/assistant/, or anything that calls Ollama.
---

# Local LLM

The three rules that hold anywhere Ollama is touched — pin `num_thread 4` in a
Modelfile, never send `num_thread` per request, one generation at a time — are
in the "Ollama / AI tuning" section of `AGENTS.md`. This skill adds how to apply
them and how the assistant bot is built.

## Adding or reloading a model

- Apply all tuned models at once with `docker/ai/load-models.sh` (runs `ollama create` for every `models/*.Modelfile`, rebuilding each tag in place — no Open WebUI change needed). For a single model: `ollama create <tag> -f docker/ai/models/<name>.Modelfile`.
- There is no global Ollama thread env var, so this is per-model: adding a model means dropping a Modelfile in `docker/ai/models/` and re-running the loader.

## The assistant bot

Local jobs run through the Discord bot in `docker/assistant/` (push) rather than
Open WebUI (pull), because nobody is waiting on them. See
[`docs/ai-strategy.md`](../../../docs/ai-strategy.md) and
[`docs/design/tsd-local-llm-discord-jobs.md`](../../../docs/design/tsd-local-llm-discord-jobs.md).

The Discord server layout is declarative: `docker/assistant/guild.yml` holds the
categories/channels/permissions and `--provision` converges the server to it
(idempotent, additive, never deletes — `#digest` is a log whose history matters).
Creating the server, creating the bot, and restricting a command to a channel
all require a *user* login and stay manual; everything else is in git.

Conversational `#chat` keeps its memory **in Discord** — the bot re-reads
messages as context rather than holding state, so it is restart-safe and what you
see is exactly what the model sees. *Where* you type decides the scope: a plain
message is a one-off, a reply walks its reply chain, and a message in a thread
reads the whole thread. Threads (and forum posts, which are threads) are the
persistence unit for a named conversation. It needs the Message
Content intent, narrowed in code to one channel and the user allowlist; the
first check in `_should_handle` ignores bots (including itself), which is what
prevents an infinite self-reply loop.

`#chat` is given live homelab readings by **injection**, not by tool-calling: the
facts are collected deterministically before every reply, so the model never
decides whether to look. Tool-calling is unreliable on a 3B — adding tools is the
thing this repo has already tried and reverted twice. If collection fails the
readings are omitted rather than served stale.

Two rules when extending it:
- **Python decides what's true; the model only writes prose.** Facts are queried
  and thresholded in code — a 3B is not reliable at tool calling or at staying
  faithful to retrieved sources.
- **A model failure must degrade, not delete.** The digest posts its numbers even
  when Ollama is down; unreadable data is reported, never rendered as "all clear".
