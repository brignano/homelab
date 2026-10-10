# TSD: A personal assistant that watches and acts

**Status:** 🚧 phases 1–3 built (calendar, mail and Home Assistant in the digest: `docker/assistant/`, `docker/homeassistant/`; the concierge with read tools in #ask: `docker/concierge/`); phases 4–5 draft
**Date:** 2026-10-10
**Owner:** Anthony

## Problem

The lab already behaves like half an assistant. It is always on (CT 100), it
speaks first (the `assistant` bot posts to Discord unprompted), it serializes its
work (`jobqueue.py`), and it notices when its own schedule stops firing
(Healthchecks, `hl-digest-missing`). What it can see is the lab, and only the lab.

The question that prompted this was "how do I get something like Meta's Muse
(launched 2026-09-08): an agent that is always available, connects to my mail,
calendar, car and home, alerts me, and does small tasks for me?" Read against
this repo, that is two separate asks that are easy to blur together:

1. **Watch.** Read more of my life, notice what matters, and tell me. That is
   the digest and `#alerts`, with more sources.
2. **Act.** Do a task when I ask, which means reasoning over several steps and
   calling tools that change things.

They have opposite requirements. Watching is scheduled, nobody waits on it, it
reads untrusted content (every email is written by a stranger) and must never
change anything. Acting is on demand, someone is waiting, it needs reliable
tool calling, and it changes things by definition. The design below keeps them
in separate lanes. Most of the risk in a "personal agent" comes from one process
both reading a stranger's email and holding the keys to the front door.

## Goals

- **Watch:** calendar, email and home state reach the daily digest and, where
  urgent, `#alerts`, alongside the lab's own numbers.
- **Act:** `/ask` in Discord runs a multi-step task with tools (look up,
  draft, schedule, set a scene) and comes back with an answer or a proposed
  action.
- **Every write is approved by a person, with the exact parameters shown.**
  The approval prompt is rendered by code from the tool call, not by the model
  describing what it wants to do.
- **Physical security is out of reach by capability, not by instruction.**
  Locks, alarm and garage are not exposed to the agent at all.
- **It remembers.** Preferences and corrections persist across conversations,
  where you can see and edit them.
- No inbound attack surface: outbound connections only, as the bot does today.

## Non-goals

- **iPhone Messages.** Apple has no API for it. The only route is a Mac that
  stays on, reading `chat.db` and sending through AppleScript. There is no Mac
  in this lab. See "Revisit if".
- **Self-healing the lab.** That is
  [`tsd-self-healing-remediation.md`](tsd-self-healing-remediation.md), and it
  stays blocked on backups for the reason given there. This agent gets no
  Docker socket, no shell and no Portainer.
- **Training or fine-tuning.** "Self-learning" here means memory: written
  notes the agent reads back. See Decision 5.
- **Purchases, payments, and sending on my behalf without review.** Drafts,
  yes. A sent email or a bought item, no, not even with approval, in the first
  version.
- **Replacing Grafana as the source of lab alerts.**
- **Running the agent on the local model.** Tool calling on a 3B has been tried
  and reverted twice ([`tsd-ai-homelab-assistant.md`](tsd-ai-homelab-assistant.md),
  [`../ai-strategy.md`](../ai-strategy.md)). The act lane uses Claude.

## Design

```
                     Discord (outbound WSS — the only front door)
                        │                         ▲
               /ask, ✅/❌ buttons          digest, #alerts (webhooks)
                        ▼                         │
  ┌─────────────────────────────────┐   ┌─────────────────────────────┐
  │ assistant  (existing)           │   │ WATCH lane (existing digest)│
  │  Discord gateway, job queue,    │   │  Python collects facts:     │
  │  renders approval prompts       │   │   Prometheus, Loki,         │
  └──────────┬──────────────────────┘   │   + calendar, mail, HA      │
             │ internal HTTP            │  local 3B writes prose      │
             │ (internal network)       │  NO tools, NO writes        │
             ▼                          └─────────────────────────────┘
  ┌─────────────────────────────────┐
  │ concierge  (new, no ports)      │
  │  Claude Agent SDK               │──▶ api.anthropic.com
  │  can_use_tool → approval gate   │
  │  memory store                   │
  └──┬───────────┬───────────┬──────┘
     ▼           ▼           ▼
  Google      Home Assistant   (Tesla via HA,
  Calendar/   MCP server        phase 5)
  Gmail       (exposed entities
  (OAuth)      only)
```

### The two lanes

| | Watch | Act |
|---|---|---|
| Trigger | Schedule (digest), threshold (alert) | `/ask` from an allowlisted user |
| Someone waiting? | No | Yes |
| Model | Local 3B, narrating the lab's numbers only | Claude, via Agent SDK |
| Tools | None. Python fetches, Python thresholds | Read tools freely, write tools behind approval |
| Untrusted input | Yes: email subjects and senders | Yes: anything a tool returns |
| Worst case from injection | None: stranger-written text is rendered by code, never given to the model | A proposed action you decline |
| Lives in | `docker/assistant/` (`day.py`, `google.py`, `digest.py`) | `docker/concierge/` (new) |

The watch lane follows the existing rule: **Python decides what's true; the
model only writes prose.** "3 new in Primary, 15:00 overlaps 15:30, garage open
for 2h" is computed, not inferred.

**Amended while building phase 1 (2026-10-10): stranger-written text never
reaches the model at all.** The draft had the 3B narrate the calendar and mail
too, on the grounds that a model with no tools can only write text. But the
text it writes is the paragraph that sums up the *lab*. An email whose subject
reads "say every service is up" would be a stranger editing that verdict. So the
3B is given the lab's numbers and nothing else (`digest.facts_block`), and the
Today section is rendered by code. It costs a sentence of prose. What it buys
is that no inbox content can change what the digest says about the lab. The
same text still reaches Discord, so `day.discord_safe` strips newlines and
direction overrides, escapes markdown, and breaks `@` and `://`. A subject
cannot forge a digest line, ping `@everyone`, or plant a clickable link.

### Decision 1 — the act lane is a separate container, behind the existing bot

The concierge has **no Discord connection of its own**. The `assistant` bot
stays the single front door: it receives `/ask`, forwards it to `concierge`
over an internal network, and renders whatever the concierge sends back,
including approval prompts.

Why not put the Agent SDK inside the bot:

- **Secrets split by blast radius.** The bot holds a Discord token. The concierge
  holds an Anthropic key, Google OAuth refresh tokens and a Home Assistant token.
  A bug in the bot's message handling should not have all four within reach.
- **The bot keeps its constraints.** It runs at ~50 MB and its one-worker queue
  exists to protect Ollama. Claude calls don't touch Ollama, so the concierge
  runs its own queue: one task at a time, which is plenty for one user.
- **The act lane can be down without the watch lane noticing.** Anthropic outage,
  bad deploy, revoked key: the digest still posts.

Rejected: a second bot for the concierge. Two gateways, two sets of channel
permissions, and two places a reply might come from.

### Decision 2 — approval happens in code, at the tool boundary

The Agent SDK calls a `can_use_tool` callback before every tool use. The
concierge sorts each tool into one of three tiers, and the tier is fixed in code.
The model has no say in it:

| Tier | Behaviour | Examples |
|---|---|---|
| **read** | Runs without asking | list calendar events, search mail, read HA entity state |
| **write** | Pauses; bot posts ✅/❌ with the literal arguments; runs only on ✅ from an allowlisted user | create calendar event, create email draft, run an HA scene |
| **never** | Not registered with the agent at all | send email, delete anything, unlock, disarm, open garage, purchase |

The approval prompt is generated from the tool call's arguments by Python
(`create_event(title="Dentist", start=2026-10-14T09:00, attendees=[])`), not
from the model's summary of them. A model that has been steered by an email can
describe an action misleadingly. It cannot change what the arguments say.

Approvals time out after 15 minutes and count as ❌. An unanswered prompt must
never turn into a yes.

**Amended while building phase 3 (2026-10-10): the tier check runs in a
`PreToolUse` hook as well as `can_use_tool`.** The SDK only consults
`can_use_tool` when nothing earlier settles the call. An allow rule or a
permission mode can approve a call first, and then the callback never runs.
Hooks run before every other step, so `app/tiers.py`'s `decide()` runs in
both places, and the hook is the check that cannot be skipped. The agent also
gets `tools=[]` (no Claude Code built-ins), `allowed_tools=[]`,
`permission_mode="default"` and `setting_sources=[]`. `tests/smoke.py` pins
each of those.

Phase 3's only write is `remember`, and its effect never happens in the
concierge. The tool records a proposal. The bot renders it from its arguments
after the answer, and only a ✅ posts it to `#memory`. That makes it a fourth
tier in code, **propose**: allowed to run, because running it changes nothing.
Phase 4's write tools act from inside the concierge, so they need the
mid-run pause this design describes. Until that gate exists, `decide()`
refuses anything in the write tier.

### Decision 3 — Home Assistant is the hub for everything physical

Home, cameras, presence and the car go through **one** integration:
[Home Assistant](https://www.home-assistant.io/), in a new `docker/homeassistant/`
stack, and its built-in **MCP server** integration. That avoids writing and
maintaining a separate MCP server for each device vendor.

The capability ceiling is **which entities are exposed to Assist** in Home
Assistant's own settings, the same way the MCP gateway's ceiling is a Viewer
service account rather than a flag
([`tsd-homelab-mcp-server.md`](tsd-homelab-mcp-server.md)). Locks, alarm panels
and garage covers are **not exposed**. That means the agent cannot touch them
however it is prompted. The tier table above is the second layer, not the only
one.

**Amended while building phase 2 (2026-10-10): the watch lane cannot use that
ceiling.** Assist exposure governs HA's conversation and MCP surfaces. The REST
API ignores it, and **Home Assistant has no read-only token**: any long-lived
token, even a non-admin user's, can call services. So the watch lane's
"read-only" rests on weaker ground than Google's scopes, and the docs say so:

- a dedicated non-admin `digest` user, which cannot change HA's configuration
  or mint tokens, but can still operate entities;
- code that only ever sends `GET /api/states/<entity_id>`, for an explicit
  `HA_ENTITIES` list, with no write path. A test fails if anything POSTs;
- no model in the loop, so nothing read can steer what is done.

Reading a lock's *state* is in scope for the watch lane ("Front Door unlocked
6h" is exactly what a morning digest is for). It is *operating* locks that the
act lane must never reach, and phase 3 keeps them unexposed to Assist as
designed above.

**Amended while building phase 3 (2026-10-10): the agent is not connected to
HA's MCP server directly.** Connected directly, every Assist intent
(`HassTurnOn`, `HassLightSet`, …) would sit in the agent's tool list, refused
only by the tier check. The **never** tier means "not registered", so the
concierge registers one tool of its own, `home_overview`. That tool calls one
HA tool, `GetLiveContext`, which reports only exposed entities. No code path
names any other HA tool, and a test checks that. The `concierge` HA user and
token are separate from the digest's.

Camera *video analysis* (Frigate or similar) is out of scope. Object detection
on a CPU-only box competes with Ollama for the same 6 cores. Home Assistant's
own camera and motion entities, as state, are in scope.

### Decision 4 — Google access uses our own OAuth client, with narrow scopes

The Gmail and Calendar connectors on claude.ai belong to claude.ai sessions;
a container on CT 100 cannot use them. The concierge and the watch lane use a
Google Cloud OAuth client of our own, authorised once from a browser. The
refresh token lives in `docker/concierge/.env` (and the assistant's, for the
watch lane), never in git.

Scopes, deliberately split:

| Who | Scopes |
|---|---|
| Watch lane (`assistant`) | `gmail.metadata`, `calendar.events.readonly` |
| Act lane (`concierge`) | `gmail.compose` (drafts), `gmail.readonly`, `calendar.events` |

`gmail.send` is not requested by anything. If the agent cannot send, approval
for sending is not something that can go wrong.

**Email bodies leave the box only through the act lane, on request.** The
watch lane holds `gmail.metadata`, and Google refuses to return a message body
to that scope. "Metadata only" is a ceiling Google enforces, not a setting that
can be got wrong. It also forbids the `q` search parameter, so mail is chosen by
label IDs and the 24h window is applied in code. A body reaches Anthropic's API
only when you `/ask` about mail.

**Built (phase 1):** a Desktop-app OAuth client, with a one-time
`--google-auth` sign-in (PKCE, loopback redirect, code pasted back from the
address bar so it runs headless). The consent screen must be set to
*In production*: in *Testing*, Google expires refresh tokens after 7 days.

### Decision 5 — memory lives in Discord, where you can see it

`#chat` already keeps its context in Discord so that "what you see is exactly
what the model sees". Memory uses the same idea. A new bot-only `#memory`
channel holds one message per remembered fact ("prefers morning appointments",
"Dr. Lee's office is the dentist"). The concierge reads the channel at the start
of each task.

- The agent **proposes** a memory with a `remember` tool, which is
  **write**-tier: you approve it like any other write.
- You edit or delete a memory by editing or deleting the message.
- Restart-safe, no new datastore, and history is visible.

Rejected: a vector database. One user's preferences fit in a context window
for years, and retrieval adds a ranking step that decides what the model does
not see.

**Amended while building phase 3 (2026-10-10):** the concierge has no Discord
connection (Decision 1), so the **bot** reads `#memory` and sends the
memories with each request. It keeps only posts by the bot or an allowlisted
user. Discord does not let a person edit a bot's message, so "edit" means
delete and re-post. As server owner you can post a memory into the bot-only
channel by hand.

### Decision 6 — scheduled work gets a dead man's switch

Every watch-lane source (calendar, mail, HA) writes a success timestamp through
`scripts/metrics.sh`'s textfile pattern, as the digest does with `stamp.py`.
The rule is simple: an alert past 2× the expected interval. A Google token that
expires silently must show up as an alert, not as a digest that no longer
mentions mail.

**Built (phase 1):** `source-calendar` and `source-mail` in the digest's state
directory, published by `heartbeat.sh` as
`homelab_assistant_source_timestamp_seconds{source}`. `hl-google-stale` fires
past 25h, the digest's own interval plus an hour, matching `hl-digest-missing`.
A daily read cannot use 2×. The bot **arms** a missing stamp at startup, so a
sign-in that never worked still ages into the alert instead of publishing
nothing. Stamps are cleared whenever no digest would refresh them.

The act lane is not scheduled and needs no switch. If it is down, `/ask`
says so.

## Phases

Each phase is useful alone, and later phases can be dropped without affecting
earlier ones.

| Phase | Ships | Writes anything? |
|---|---|---|
| **1. Calendar + mail in the digest** ✅ built | Google OAuth client, `app/google.py` + `app/day.py`, freshness metrics + `hl-google-stale` | No |
| **2. Home Assistant, read-only** ✅ built | `docker/homeassistant/` stack, `ha.` route, probe, tile; `app/home.py` and a **Home** line in Today; `source-home` stamp | No |
| **3. Concierge, read tools only** ✅ built | `docker/concierge/`, `/ask` routed through the bot, Google + HA read tools, `#memory` | No (memory is approved) |
| **4. Write tools behind approval** | Approval gate, calendar events, email drafts, HA scenes and notifications | Yes, each approved |
| **5. Car** | Tesla through HA's Tesla integration: battery, location, climate; "send destination to car" as a write | Yes, approved |

### Status and next up

The working checklist. A new session starts here: "read this TSD and do the
next unchecked item". Tick items in the PR that finishes them. Items marked
**(you)** are done in a UI or on the box, and nothing in git can do them.

**Done**
- [x] Phase 1 deployed and verified live (calendar, overlaps, mail; `source-*`
      metrics in Prometheus).
- [x] Phase 2 stack deployed. HA on host networking (brignano/homelab#116),
      reverse-proxy trust set in the UI.
- [x] Tesla Fleet integration added in HA (key served from brignano.io).
- [x] iCloud calendar added to HA through the CalDAV integration.

**Setup left on the box (you)**
- [ ] IP banning on, threshold 10+ (Settings → System → Network). See
      `docker/homeassistant/README.md` → "Exposure" for why not 5.
- [ ] Pair the Honeywell T6 Pro thermostat: **HomeKit Device**, code from its
      label. Remove it from Apple Home first if it is there.
- [ ] Non-admin `digest` HA user and token; `HA_TOKEN` and `HA_ENTITIES` in
      `docker/assistant/.env`; `--selftest` shows a **Home** line. Steps in
      `docker/homeassistant/README.md` → "Connect the digest".
- [ ] Tuya/Smart Life switches: pair by scanning HA's QR code from inside the
      Smart Life app, not the phone camera.

**Next to build**
- [ ] **Digest: thermostat and iCloud calendar.** Needs the `climate.…` and
      `calendar.…` entity IDs from the steps above. `climate` gets a rule in
      `app/home.py`'s `notable()` (current temperature below 55 °F or above
      85 °F, or HVAC mode `off`). iCloud events come from HA's
      `calendar.*` entities and merge with Google's in `app/day.py`, still
      rendered by code and never shown to the model.
- [x] **Phase 3, concierge with read tools.** `docker/concierge/`, `#ask` and
      `#memory` in `guild.yml`, the bot's forwarding and ✅/❌ prompts in
      `docker/assistant/app/concierge.py`. Setup is in
      `docker/concierge/README.md`.

**Setup left for phase 3 (you)**
- [ ] `homelab` workspace in the Claude Console with its own spend limit
      (e.g. $20, alert at $10), and a workspace-scoped key with a 1-year
      expiry, kept only in `docker/concierge/.env`. Calendar reminder to renew.
- [ ] `CONCIERGE_TOKEN` in both `.env` files; `--provision --apply` for
      `#ask`/`#memory`, IDs into `docker/assistant/.env`.
- [ ] Concierge's Google sign-in (`--google-auth`, read-only scopes).
- [ ] HA: non-admin `concierge` user and token, the MCP Server integration,
      and Assist exposure with **no** locks, alarm panels or garage covers.
- [ ] Bring up `concierge`, then `docker stats` with Ollama loaded (the RAM
      check under "Cost and footprint").
- [ ] **Probe through Caddy.** Today's HA probe goes to HA through the host,
      so a broken Caddy → HA route (`ha.` answering 502) pages nobody.
- [ ] **Matter Server**, only once a Matter device is bought. HA's host
      networking is in place, and the Matter Server container also needs host
      networking and IPv6.

## Security

- **Inbound:** none added. The concierge has no published port and joins only
  the internal network it shares with the bot. It reaches HA, which runs on
  host networking, as `host.docker.internal:8123` via `extra_hosts`, like the
  assistant does.
  Home Assistant's UI goes behind Caddy on the tailnet like every other admin UI,
  and is **never** exposed through Cloudflare Tunnel.
- **Injection:** assume every tool result is hostile. The watch lane has no
  tools, so injection costs a wrong sentence. The act lane can only *propose*
  writes, rendered verbatim, and cannot reach anything in the **never** tier.
- **Who can ask:** the existing user allowlist applies to `/ask` and to ✅
  buttons. A ✅ from anyone else is ignored and logged.
- **Secrets:** all `${VAR:?required}` in each stack's compose file. The
  concierge's are `ANTHROPIC_API_KEY`, `GOOGLE_OAUTH_*`, `HA_TOKEN`. The HA token
  is a long-lived token for a dedicated, non-admin HA user.
  *As built (phase 3):* `ANTHROPIC_API_KEY` and `CONCIERGE_TOKEN` (the bot's
  shared bearer) are `:?required`. Google and HA are optional, for the same
  reason as the assistant's digest channel: `--google-auth` produces the
  refresh token, and Compose would refuse to run it if the token were
  required. `config.py` refuses a half-set Google config, and an unconfigured
  family's tools are not registered.
- **Internal network:** `assistant_concierge`, owned by the assistant stack, so
  the watch lane never waits on the act lane. The concierge is the only other
  member, and it still checks a bearer token.
- **Audit:** the concierge logs every tool call, its tier and the approval
  outcome to stdout, which reaches Loki through Alloy. "What did it do last
  Tuesday" is a LogQL query.

## Cost and footprint

- **RAM:** Home Assistant typically sits at a few hundred MB. The concierge is a
  Python process plus the Claude Code CLI that the Agent SDK drives. Measure both
  with `docker stats` after phase 2 against the 14 GB limit, with Ollama loaded,
  before committing to phase 3.
- **Money:** the watch lane is $0 (local model, Google APIs within free quota).
  The act lane is Claude API usage, on demand only, and nothing in it is
  scheduled. **Phases 1–2 never call the Claude API** (Google, Home
  Assistant and the local 3B only). Phase 3 is the first that does, and it
  follows the same budget isolation as the brignano.io site chat:
  - its own Console **workspace**, `homelab`, with its own monthly spend limit
    and alert (for example $20 limit, alert at $10). Workspace limits are kept
    summed under the org's $50 cap, alongside `brignano-io`'s $10;
  - its own API key, **scoped to that workspace**, with a 1-year expiry. Put a
    renewal reminder on the calendar when it is created;
  - the key only in `ANTHROPIC_API_KEY` in `docker/concierge/.env`, as
    `${ANTHROPIC_API_KEY:?required}` in compose. **This repo is public**: the key
    is never committed, and `.env.example` holds a placeholder;
  - Anthony creates the workspace and the key in the Console. Claude has no
    Console access.

  A future cloud model anywhere else in the lab follows the same steps.
  Tesla bills Fleet API usage: check current pricing before phase 5.

## Rejected alternatives

- **One agent that does everything.** That is the Muse shape: one process with
  every connector and a schedule. On a single-user homelab, it means the process
  that reads stranger-written email on a timer also holds write tools. The
  two-lane split costs one extra container.
- **The local 3B as the agent.** See Non-goals. It narrates well and calls tools
  badly, and this repo has the history to prove both.
- **A custom MCP server per device** (Tesla, cameras, sensors). Home Assistant
  already integrates them and already speaks MCP; the work is exposure settings,
  not code.
- **Claude Code routines / scheduled cloud sessions as the always-on piece.**
  They run in Anthropic's cloud, so they cannot reach Home Assistant on the LAN
  without a public endpoint, which this lab does not have by design.
- **Auto-approving "safe" writes.** Every write starts approved by hand. If a
  write is still approved every time after a month, promote it to **read**-tier
  in code, in a reviewed commit, rather than through a setting the agent can
  see.

## Open questions

- [x] Home Assistant networking: **host** (2026-10-10), after starting as a
      bridge on its own network plus `proxy` the same day. The bridge kept HA
      off the LAN and let Caddy and the assistant reach it by name. It could
      not carry the mDNS/SSDP multicast that HomeKit, Matter and discovery
      need, and the first real device (a Honeywell T6 Pro Smart thermostat,
      whose only local integration is HomeKit, with Resideo's cloud API
      closed to new developers) needed exactly that. Host mode puts :8123 on
      the LAN and tailnet behind HA's login (never on the Tunnel). Caddy,
      blackbox and the assistant reach it as `host.docker.internal` via
      `extra_hosts: host-gateway`. Reasoning, exposure and rollback are in
      `docker/homeassistant/README.md` → "Host networking".
- [x] Tesla Fleet API registration needs a public key at
      `/.well-known/appspecific/com.tesla.3p.public-key.pem` on a public
      domain. Resolved 2026-10-10: served from `brignano.io`
      (brignano/brignano.io#263), with a Vercel firewall **Bypass** rule for
      `/.well-known/appspecific/`. Bot Protection is set to *Challenge*, which
      had been serving Tesla's fetcher a browser-check page. The rule lives in
      the Vercel dashboard, not in git. HA's built-in Tesla Fleet integration
      is used directly, with no third-party proxy.
- [x] Which Gmail signal is worth a digest line? Started with unread in the
      Primary tab, last 24h (`GMAIL_LABELS`). Revisit once it has been read for
      a few weeks.
- [x] Is `/ask` a slash command, a channel (`#ask`, like `#chat`), or both?
      **A channel, `#ask`** (2026-10-10). It gets threads for multi-turn tasks
      for free, with the same scope rules as `#chat` (a thread is one
      conversation, a reply follows its chain, anything else is a one-off),
      and it needs no slash-command restriction set by hand. The existing
      `/ask` slash command stays as it was, a one-shot question to the local
      model. Where you type decides which model answers.

## Revisit if

- **A Mac joins the lab:** iMessage becomes a watch-lane source (read `chat.db`
  metadata) and maybe a write-tier draft. It would be the most sensitive source
  here, so it gets its own TSD.
- **Backups land:** the self-healing TSD unblocks. Even then, lab remediation
  stays in its own design, not in this agent.
- **The lab gets a GPU:** a local model with reliable tool calling could take
  over the act lane, and email bodies would stop leaving the box at all.
