# assistant — local-LLM jobs, delivered over Discord

A single small Python container that puts the local `llama3.2:3b` to work on
tasks **nobody is waiting on**, and delivers the results to Discord.

Design + rationale: [`docs/design/tsd-local-llm-discord-jobs.md`](../../docs/design/tsd-local-llm-discord-jobs.md).

```
                     ┌──────────────────────────────────┐
  Discord  ◀────────▶│  assistant                       │
  (outbound WSS,     │   single-worker priority queue   │
   no inbound port)  │      interactive ▶ scheduled     │
                     └───┬─────────────┬────────────────┘
                         │             │
                 ollama:11434    prometheus:9090
                 (narration)     loki:3100  (the facts)
```

## Why this exists

The model wasn't underused because it lacks jobs — it was underused because
Open WebUI is a **pull** interface. You have to show up, type, and watch a
CPU-only 3B emit ~16 tok/s. That's the worst possible ergonomics for a slow
model, so you reach for Claude instead and the local box idles.

Inverting it fixes the ergonomics without touching the hardware: **push, not
pull; batch, not interactive.** A digest that takes 40 seconds to generate costs
nothing when it arrives on its own at 07:30. A `/ask` you fire off and forget
costs nothing either. Slow is only expensive when you're watching it.

## What it does

| Surface | Kind | What it's for |
|---------|------|---------------|
| daily digest | scheduled | Homelab health at `DIGEST_AT`, posted to one channel |
| **`#chat`** | **conversational** | **Just type — every message gets a reply, with memory** |
| `/ask` | interactive | Question to the local model; queued, answers when ready |
| `/summarize` | interactive | Condense pasted text — the 3B's genuine strength |
| *Apps → Summarize message* | interactive | Right-click any message to summarize it in place |
| `/digest` | interactive | Run the digest now instead of waiting |
| `/status` | interactive | Model readiness, queue depth, next scheduled digest |

## Conversational mode

Set `DISCORD_CHAT_CHANNEL_ID` and that channel stops needing slash commands —
type, get a reply, reply back, and it follows the conversation.

**The memory lives in Discord, not in the bot.** No store, no database, no
in-process state: when a message arrives the bot reads the last few messages out
of the channel and hands them to the model as the conversation. That means:

- **Restart-safe.** Rebuild the container mid-conversation and it picks up
  where it was — the history was never in the container.
- **What you see is what it sees.** Delete a message and it leaves the context.
  Scrolling up *is* reading the model's working memory.
- **Threads are separate conversations for free.** A thread has its own history,
  so it's its own context with no bookkeeping. Use one for a side topic, or for
  a long paste you don't want polluting the main thread.
- **`//` is an escape hatch.** A line starting with `//` gets no reply and stays
  out of the context — for notes, links and asides.

History is capped by both a turn count and a character budget
(`CHAT_HISTORY_TURNS`, `CHAT_HISTORY_CHARS`). Prompt evaluation is CPU-bound and
roughly linear in tokens, so an unbounded memory would make every reply slower
than the last. Trimming drops the *oldest* turns; the newest message is always
kept, since it's the one being answered.

Under the hood this uses Ollama's `/api/chat`, not `/api/generate`, so the
model's own chat template is applied to the role-tagged turns. Concatenating
turns into one prompt string produces a format the model was never trained on,
and a 3B degrades fast when that drifts.

> **This needs the Message Content privileged intent** — Developer Portal → Bot
> → Privileged Gateway Intents → **Message Content** → on. Without it Discord
> delivers messages with an empty body and the bot cannot see what you typed.
> No approval is needed under 100 servers.
>
> It is a genuine widening: the bot can now read message text server-wide. It's
> narrowed in code rather than by Discord — `_should_handle` returns immediately
> unless the message is from an allowlisted user in the one configured channel
> (or a thread under it), and it ignores bots including itself, which is what
> stops it replying to its own replies forever. Leave `DISCORD_CHAT_CHANNEL_ID`
> blank and the intent is never requested at all.

## Two rules that make a 3B usable here

**1. Python decides what's true; the model only writes prose.**
Every number in the digest is queried and thresholded in
[`app/facts.py`](app/facts.py) — including the "is this fine?" verdict. The model
receives a finished facts block and is told to restate it. It never queries
anything, never sees a raw log line, and never gets to reach a conclusion.
This is the same conclusion [`tsd-ai-homelab-assistant.md`](../../docs/design/tsd-ai-homelab-assistant.md)
reached before shelving; what changed is that push delivery makes a canned
summary worth having.

**2. If the model fails, the digest still posts.**
Narration is a convenience layer over the numbers, not the product. Ollama down,
slow, or timed out → the same digest posts without the prose paragraph. A
backend that can't be read is reported as *Incomplete*, never rendered as
"all clear".

## Why the caps

One generation at a time uses the CPU budget exactly: the tuned model pins
`num_thread 4` of the LXC's 6-core quota. Two concurrent generations don't run
twice as fast — they contend for the same cores and memory bandwidth, so both
crawl and the other stacks starve. Hence:

| Cap | Default | Why |
|-----|---------|-----|
| single worker | — | All LLM work is serialized ([`app/jobqueue.py`](app/jobqueue.py)); interactive jumps ahead of scheduled |
| `OLLAMA_NUM_CTX` | 4096 | Prompt evaluation is CPU-bound and scales with context length |
| `*_NUM_PREDICT` | 180–400 | A runaway generation would hold the worker as long as it rambled |
| `MAX_INPUT_CHARS` | 6000 | A pasted wall of text is the easiest way to tie up the box for minutes |
| `MAX_QUEUE` | 8 | Shed load rather than pile it up |
| `LLM_TIMEOUT_S` | 300 | Hard ceiling on one job |

> **`num_thread` is deliberately never sent** with a request. Request-time
> options override the Modelfile, so passing a thread count would silently undo
> the pin in [`../ai/models/llama3.2.Modelfile`](../ai/models/llama3.2.Modelfile)
> — the fix that took generation from ~0.5 tok/s back to ~16 (AGENTS.md →
> "Ollama / AI tuning").

## Setup from scratch

Three of these steps need your Discord login and can't be automated — a bot
token isn't allowed to create a server or install itself. Everything else is
declared in [`guild.yml`](guild.yml) and applied by `--provision`.

| Step | Who does it | Why |
|------|-------------|-----|
| Create the server | you, in the Discord app | Bots can't create servers |
| Create the app + bot, copy the token | you, in the developer portal | Requires your account |
| Invite the bot | you, via the install URL | Requires your account |
| **Categories, channels, topics, permissions** | **`--provision`** | Declared in `guild.yml`, in git |
| Restrict a command to one channel | you, in server settings | Needs a *user* OAuth token; a bot token can't |

### 1. Create the server
Discord → **+** in the server list → **Create My Own**. Name it whatever you
like; nothing here depends on the name.

### 2. Create the bot
1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** → **Reset Token** → copy it. Leave Message Content, Server Members
   and Presence **off** — slash commands and context menus carry their own
   payloads, so the bot never needs to read your channels.
3. **Installation** → Guild install, scopes `bot` + `applications.commands`.
   Bot permissions: **View Channels**, **Send Messages**, **Manage Channels**,
   **Manage Roles**.
4. Open the generated URL and add it to your server.

Or skip that UI and use a direct invite link with exactly those four
(`permissions=268438544`), substituting your own application ID:

```
https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=268438544
```

| Permission | Needed for |
|------------|------------|
| View Channels | Reading current state so the `--provision` diff works |
| Send Messages | Posting the digest and command replies |
| Manage Channels | Creating the category and channels |
| Manage Roles | Setting the `#digest` lock (channel permission overwrites) |

> **The invite must cover every permission an overwrite grants.** Discord
> rejects an overwrite that hands out a permission the acting bot doesn't hold
> itself — `403 Forbidden (50013)`, even when the bot has Manage Roles. That's
> why the `#digest` lock grants only `send_messages`, and why adding any
> permission to `_overwrites()` in `provision.py` means adding it to the invite
> too.

> **After provisioning**, Manage Channels and Manage Roles can be removed —
> View Channels and Send Messages are all the running bot needs. Keep them if
> you expect to re-run `--provision` often.

### 3. Configure what you have so far
Discord → **Settings → Advanced → Developer Mode** on, then right-click to copy
your **server ID** and your own **user ID**. The digest channel ID doesn't exist
yet — step 5 produces it.

```sh
cp docker/assistant/.env.example docker/assistant/.env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_ALLOWED_USER_IDS, TZ
```

### 4. Check the backends before involving Discord
Needs no Discord token at all, so it isolates "can't reach Ollama" from
"bad bot token" — the two things that actually go wrong:

```sh
docker compose -f docker/assistant/docker-compose.yml run --rm --build assistant --selftest
```

### 5. Create the channels
```sh
# prints a plan and changes nothing
docker compose -f docker/assistant/docker-compose.yml run --rm --build assistant --provision

# execute it
docker compose -f docker/assistant/docker-compose.yml run --rm --build assistant --provision --apply
```

> **Always pass `--build`.** `app/` is copied into the image at build time, not
> mounted, so `docker compose run` without it silently reuses the last image —
> and you debug a bug you already fixed. This costs a couple of seconds; a
> stale image costs an hour.

It ends by printing the line to paste into `.env`:

```
Paste into docker/assistant/.env:
  DISCORD_DIGEST_CHANNEL_ID=1234567890123456789
```

### 6. Bring it up
The `ai` and `monitoring` stacks must be running first — this stack joins their
networks and Compose will not create external networks.

```sh
docker compose -f docker/ai/docker-compose.yml up -d
docker compose -f docker/monitoring/docker-compose.yml up -d
docker compose -f docker/assistant/docker-compose.yml up -d --build
```

### 7. Keep `/ask` out of the log (optional, manual)
Server Settings → **Integrations** → your bot → restrict `/ask` and
`/summarize` to `#ask`. This is the one layout step a bot token genuinely
cannot do — the command-permissions API requires a user OAuth token — so it
stays a click.

## The server layout

[`guild.yml`](guild.yml) is the source of truth:

```
📁 HOMELAB      the lab reporting to you — you scroll these
   # digest     daily health log, bot posts only
   # alerts     Grafana + dead-man's-switch, webhook-fed

📁 ASSISTANT    you talking to the model
   # chat       conversational, remembers recent messages
```

The split is by **direction** — things the lab tells you unprompted, versus
things you ask it. Output channels are worth scrolling back through; the
conversation isn't.

Within that, one decision carries the weight: **the digest is a log, not a
chat.** "Was disk climbing last week?" is a question you'll actually scroll back
for, and interleaved conversation would destroy it. So `#digest` and `#alerts`
deny `@everyone` Send Messages, and everything you type goes in `#chat`.

> **Renaming an existing channel:** do it in Discord, not here. Renaming
> preserves the channel and its history, and the provisioner then sees the new
> name as already existing. Change the name in `guild.yml` instead and it
> creates a *new* empty channel and reports the old one as unmanaged — it never
> deletes, so nothing is lost, but you end up with both.

`--provision` is **idempotent and additive**: re-run it any time to fix drift
(someone edits a topic in the UI), and it will never delete a channel —
deleting one destroys its history, and `#digest` *is* the history. Channels it
finds that aren't in `guild.yml` are reported and left alone.

## Verify

```sh
docker logs -f assistant          # expect "connected as <bot> (guild …)"
docker inspect --format '{{.State.Health.Status}}' assistant
```

In Discord, `/status` should report the model ready and the next digest time.
`/digest` runs one immediately.

To render sample digests with no network at all (useful when editing the
formatting):

```sh
docker compose -f docker/assistant/docker-compose.yml run --rm --build assistant --dry-run
```

## Notes

- **No inbound exposure.** No `ports:`, no Caddy route, no DNS rewrite. The bot
  dials out. It therefore also works when you're off the tailnet — the one thing
  `chat.home` can't do.
- **Allowlist is mandatory.** `DISCORD_ALLOWED_USER_IDS` must be set or the
  container refuses to start; otherwise anyone who can see the bot could queue
  work onto your CPU.
- **Prompt-injection surface is tiny.** The model has no tools, no shell, no
  network of its own, and no write path anywhere. Worst case from a hostile
  input is that it says something wrong in your Discord channel.
- **RAM cost is ~50 MB.** Inference happens in the existing `ollama` container;
  this one just orchestrates. It adds no new model to keep resident.
- Slash commands are registered **guild-scoped**, so they appear immediately
  rather than taking up to an hour to propagate. Changing `DISCORD_GUILD_ID`
  leaves the old guild's commands behind until they're removed manually.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Commands don't appear | Bot added without the `applications.commands` scope, or wrong `DISCORD_GUILD_ID`. Re-invite and restart. |
| "Not authorised to use this bot" | Your user ID isn't in `DISCORD_ALLOWED_USER_IDS`. |
| Digest posts without prose | Ollama unreachable or the model isn't loaded — check `/status`, then `docker exec ollama ollama list`. |
| Digest says *Incomplete* | Prometheus or Loki couldn't be read; confirm the `monitoring` stack is up. |
| Container unhealthy but running | Gateway socket wedged — the heartbeat went stale. `docker restart assistant`. |
| `--provision` fails with 403 Forbidden (50013) | Bot is missing an invite permission. It needs all four above — and note it must also hold any permission `_overwrites()` grants, not just Manage Roles. Re-invite with the link above. |
| `--provision` failed partway through | Safe to re-run. It's idempotent: it skips what already exists and creates only what's missing. |
| A fix you just pulled seems to have no effect | You're running a stale image. `docker compose run` reuses the built image unless you pass `--build`. |
| Digest channel exists but nothing posts | `DISCORD_DIGEST_CHANNEL_ID` not updated after provisioning, or the bot lost its Send Messages allow on the locked channel — re-run `--provision`. |
| Answers take minutes | Expected on this hardware. Check `/status` for queue depth; that's the design, not a fault. |
