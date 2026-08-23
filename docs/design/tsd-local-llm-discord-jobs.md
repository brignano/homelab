# TSD: Local-LLM async jobs, delivered over Discord

**Status:** ✅ approved / shipped — `docker/assistant/`
**Date:** 2026-08-22
**Owner:** Anthony

## Problem

The local model is underused, and the reason is not capability — it's the
interaction model.

Open WebUI is a **pull** interface. Every use of the local model requires you to
go to `chat.home`, type, and then watch a CPU-only 3B emit ~16 tok/s. Against
Claude in a terminal, that comparison is lost before it starts, so in practice
the box idles and everything — including work a 3B would handle fine — goes to
Claude.

Two previous attempts read this as a *model* problem and tried to fix it with
better models (`qwen2.5:7b`, web-augmented retrieval). Both failed on the same
wall: this is a CPU-only, 16 GB, memory-bandwidth-bound box, and a 3B is the
comfortable ceiling (see [`../ai-strategy.md`](../ai-strategy.md) → Decision log).

The unexamined assumption in both is that **someone is sitting there waiting**.
Remove that and the constraint stops mattering: a job that takes 40 seconds and
arrives on its own costs nothing.

## Goals

- Get real, recurring value out of the local model **without** new hardware, a
  bigger model, or any tolerance for waiting.
- Deliver results to a medium that pushes and that works off-tailnet.
- Add no inbound attack surface and no meaningful RAM cost.
- Fail safe: a model failure must never mean a *missing* or *wrong* answer.

## Non-goals

- Replacing Grafana/Prometheus as the source of truth. This narrates telemetry;
  it does not alert on it. Alerting stays with Grafana → ntfy.
- Replacing Claude. The local/Claude split in `ai-strategy.md` is unchanged;
  this makes the *local* half actually get used.
- Tool calling, function calling, RAG, or agentic behaviour. Ruled out on this
  hardware — twice. See "Rejected alternatives".
- Any mutating action. No shell, no container control, no writes anywhere.

## Design

Inverted ergonomics: **push, not pull; batch, not interactive.**

One small Python container (`docker/assistant/`, ~50 MB RSS) joins the `ai` and
`monitoring` networks and connects outbound to Discord.

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

| Surface | Kind | Purpose |
|---------|------|---------|
| daily digest | scheduled | Homelab health, posted at `DIGEST_AT` |
| `/ask` | interactive | Question to the local model, answered when ready |
| `/summarize` + message context menu | interactive | Condense text — the 3B's real strength |
| `/digest`, `/status` | interactive | Run now; model/queue/schedule state |

### Decision 1 — Discord over Open WebUI, Slack, or ntfy

Discord wins on three concrete properties, not just familiarity:

1. **It pushes.** Results arrive on a client already on the phone and desktop.
   This is the entire premise: it makes generation latency invisible.
2. **Zero inbound surface.** The bot opens an *outbound* websocket. No port, no
   Caddy route, no DNS rewrite, no tunnel. It consequently works **off the
   tailnet** — the one thing `chat.home` structurally cannot do.
3. **Durable, searchable history**, so digests accumulate into a scrollable log
   and threads keep interactive work tidy.

ntfy (already deployed) was considered and rejected as the *primary* surface: it
is one-way, so there's no `/ask`. It remains the right tool for alerts, and this
does not displace it. Slack would work equally well on the mechanics; Discord is
already in daily use, which decides it.

### Decision 2 — one worker, always

This is the load-bearing constraint, not an implementation detail.

The tuned model pins `num_thread 4` of the LXC's 6-core quota (AGENTS.md →
"Ollama / AI tuning"). One generation consumes that budget exactly. Two
concurrent generations do **not** run twice as fast — they contend for the same
cores and the same memory bandwidth, so both crawl, and the 2 cores left for the
monitoring and proxy stacks get eaten as well. Ollama will accept the parallel
requests and thrash.

So every LLM call in the process — scheduled and interactive alike — is
serialized through a single worker, with interactive work prioritised ahead of
scheduled. A `/ask` typed while the digest is running goes next, not last.
Because the medium is asynchronous, queueing is invisible; the alternative
(parallel requests) is visibly worse for everyone including the other stacks.

Supporting caps, all for the same reason — protecting one shared CPU budget:
bounded `num_ctx` (prompt eval is CPU-bound and scales with context), bounded
`num_predict` (a rambling generation holds the worker), truncated input, a
bounded backlog that sheds load, and a hard per-job timeout.

> `num_thread` is deliberately **never** sent at request time. Request options
> override the Modelfile, so sending one would silently undo the pin that took
> generation from ~0.5 tok/s back to ~16.

### Decision 3 — Python decides what's true; the model only writes prose

The digest's numbers are queried and thresholded in code, *including the
"is this fine?" verdict*. The model receives a finished facts block and is
instructed to restate it in two or three sentences. It never issues a query,
never sees a raw log line, and never reaches a conclusion.

This is exactly what [`tsd-ai-homelab-assistant.md`](tsd-ai-homelab-assistant.md)
concluded before shelving itself: *"the only version that works on local is a
canned `homelab_health()` where the tool does all the work and the model just
narrates."* That TSD then correctly judged it **low value for the effort** —
because you still had to go and ask for it.

Push delivery is the change that makes the same design worth building. An
unprompted 07:30 digest is a genuinely different product from a summary you have
to remember to request, and it needs no reliable tool-calling to work.

### Decision 4 — the server layout is config, not clicks

The channels this depends on are declared in `docker/assistant/guild.yml` and
applied by `--provision`, for the same reason every other stack's config is in
git: a layout that exists only as clicks in a UI can't be reviewed, diffed, or
rebuilt after a mistake.

Three properties, all deliberate:

- **Idempotent** — re-runnable at any time; it diffs and emits only differences.
- **Additive, never destructive** — it creates and fixes drift but never deletes
  a channel. `#digest` accumulates the health history; losing it to a config
  typo would be unrecoverable. Unmanaged channels are reported, not removed.
- **Plan before apply** — `--provision` prints what it would do and changes
  nothing; `--apply` is a separate, explicit flag.

It also solves a chicken-and-egg: `DISCORD_DIGEST_CHANNEL_ID` can't be known
until the channel exists, so the provisioner prints the `.env` line to paste.

Two things stay manual because a bot token is not permitted to do them at all:
creating the server, and restricting a slash command to a channel (the
command-permissions API requires a user OAuth token). Both are one-time clicks
and are documented rather than half-automated.

The one subtlety worth recording: locking `#digest` by denying `@everyone`
Send Messages also denies the bot, which is a member like any other. The
provisioner therefore always pairs that deny with an explicit allow for itself —
without it, locking the channel would silently stop the digest from posting.

### Decision 5 — the numbers are the product; narration is a layer

Failure modes are designed, not incidental:

| Failure | Behaviour |
|---------|-----------|
| Ollama down / slow / times out | Digest posts anyway, without the prose paragraph |
| Prometheus or Loki unreadable | Digest posts, marked **Incomplete**, naming what's missing |
| Model returns nonsense | Bounded blast radius — the numbers beside it are still correct |
| Discord interaction token expires (>15 min queue) | Answer falls back to a channel message mentioning the requester |
| Gateway socket wedges | Heartbeat goes stale → container healthcheck fails → restart |

A monitoring summary that can silently degrade into a false "all clear" is worse
than none. It never renders unread data as healthy.

## Security

- **No inbound anything.** Outbound websocket only; no port, route, or tunnel.
- **Mandatory allowlist.** `DISCORD_ALLOWED_USER_IDS` must be set or the
  container refuses to start — otherwise anyone able to see the bot could queue
  work onto the CPU.
- **No privileged Discord intents.** Message Content, Members and Presence stay
  off; slash commands and context menus carry their own payloads, so the bot
  never reads the channel.
- **Tiny prompt-injection surface.** The model has no tools, no shell, no
  network of its own, and no write path. Worst case from hostile input is that
  it says something wrong in a Discord channel.
- **Read-only telemetry.** Only Prometheus/Loki *query* endpoints, with fixed
  query strings compiled into the image — no arbitrary URL or user-supplied
  PromQL, which closes the SSRF path the shelved TSD also worried about.
- Runs as a non-root user in the container.

## Rejected alternatives

| Option | Why not |
|--------|---------|
| A bigger local model (7B+) | Already tried and reverted — too slow and RAM-hungry on this box. This design makes the 3B sufficient instead. |
| Model-driven tool calling over telemetry | Unreliable on a 3B; the reason the earlier assistant TSD shelved itself. Code does the querying instead. |
| RAG / web-augmented answers | Tried and reverted — a 3B ignores retrieved sources and answers from its prior. |
| Keep using Open WebUI | It's the actual problem: pull-based, tailnet-only, and it makes you watch the tokens. It stays for interactive chat; this covers everything else. |
| ntfy as the primary surface | One-way — no `/ask`. Stays the right tool for alerts. |
| Clicking the channels together by hand | Works, but can't be reviewed, diffed, or rebuilt — and the rest of the lab's config is in git. |
| Cron + a shell script posting via webhook | No interactive half, no queue, no priority, no graceful degradation. The container costs ~50 MB to do all four. |
| Expose the bot via Cloudflare Tunnel | Unnecessary — Discord's outbound socket already solves remote access with strictly less surface. |

## Revisit if

- **A GPU or larger-RAM machine arrives.** Then reliable tool-calling becomes
  viable and the open-ended querying in `tsd-ai-homelab-assistant.md` is worth
  un-shelving. This design stays useful regardless — the delivery model is the
  valuable part, and it would simply get a better narrator.
- **The digest gets ignored.** That means it's reporting the wrong things, not
  that the approach is wrong. Change what `facts.py` collects.
- **A second heavy consumer of Ollama appears.** The single worker protects this
  process only; two containers hitting Ollama would reintroduce contention and
  the queue would need to move behind a shared gateway.
