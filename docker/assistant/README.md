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
| `/ask` | interactive | Question to the local model; queued, answers when ready |
| `/summarize` | interactive | Condense pasted text — the 3B's genuine strength |
| *Apps → Summarize message* | interactive | Right-click any message to summarize it in place |
| `/digest` | interactive | Run the digest now instead of waiting |
| `/status` | interactive | Model readiness, queue depth, next scheduled digest |

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

## Setup

### 1. Create the Discord bot
1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** → **Reset Token** → copy it. No privileged intents are needed —
   leave Message Content, Server Members and Presence **off**; slash commands
   and context menus carry their own payloads, so the bot never reads the channel.
3. **Installation** → Guild install, scopes `bot` + `applications.commands`,
   bot permission **Send Messages**. Open the generated URL and add it to your
   server.

### 2. Collect the IDs
Discord → **Settings → Advanced → Developer Mode** on, then right-click to copy:
your **server ID**, the **channel** the digest should post to, and your own
**user ID**.

### 3. Configure
```sh
cp docker/assistant/.env.example docker/assistant/.env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_DIGEST_CHANNEL_ID,
# DISCORD_ALLOWED_USER_IDS, and TZ
```

### 4. Bring it up
The `ai` and `monitoring` stacks must be running first — this stack joins their
networks and Compose will not create external networks.

```sh
docker compose -f docker/ai/docker-compose.yml up -d
docker compose -f docker/monitoring/docker-compose.yml up -d
docker compose -f docker/assistant/docker-compose.yml up -d --build
```

## Verify

Check the backends before going near Discord — this needs no token:

```sh
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --selftest
```

It prints which backends it reached, whether Ollama has the model, and a real
digest. Then:

```sh
docker logs -f assistant          # expect "connected as <bot> (guild …)"
docker inspect --format '{{.State.Health.Status}}' assistant
```

In Discord, `/status` should report the model ready and the next digest time.
`/digest` runs one immediately.

To render sample digests with no network at all (useful when editing the
formatting):

```sh
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --dry-run
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
| Answers take minutes | Expected on this hardware. Check `/status` for queue depth; that's the design, not a fault. |
