# Setup Log

Chronological record of significant configuration steps, decisions, and issues.

---

## Template — copy this block for each entry

```
## YYYY-MM-DD — <short title>

**Goal:** What you were trying to accomplish.

**Steps:**
1. …
2. …

**Issues encountered:**
- …

**Resolution:**
- …

**Notes / next steps:**
- …
```

---

## 2026-08-24 — The ntfy contact point outlived its deletion

**Goal:** Finish the job the previous entry claimed was done. Removing the ntfy
receiver from `contactpoints.yml` turned out not to remove it from Grafana, so
the running instance still held a receiver pointing at a container that no
longer exists.

**Steps:**
1. Added a `deleteContactPoints:` block to `contactpoints.yml` naming
   `uid: ntfy_webhook` — the directive that actually deletes, as opposed to
   just ceasing to mention.
2. Documented both traps in `docker/monitoring/README.md`, plus a
   `curl`-the-provisioning-API check for what Grafana actually holds.

**Issues encountered:**
- **File provisioning upserts; it does not sync.** Removing a resource from a
  provisioning file leaves it in Grafana's database, shown as "Unused". The UI
  will not let you delete it either, because provisioned resources have the
  Delete button greyed out — so the state is reachable only by config, and the
  config that created it no longer mentions it. Deletion requires
  `deleteContactPoints:` with the uid.
- **`docker compose up -d` was a no-op on Grafana, and looked like a success.**
  `provisioning/` is bind-mounted and only read at startup. Editing files under
  it does not change the container's config hash, so compose printed
  `✔ Container grafana  Running` and moved on. The deploy appeared clean while
  changing nothing about alerting. `docker compose restart grafana` is required.
- **Neither trap was visible from CI.** Every check passed on the PR, because
  every check validates *files* — YAML parses, compose interpolates, Caddy
  adapts. Nothing asserts anything about the state of a live Grafana, so a
  provisioning change that is syntactically perfect and semantically inert is
  exactly the class of bug this repo's CI cannot see.

**Resolution:**
- `docker compose restart grafana`, then confirm via
  `/api/v1/provisioning/contact-points` that `discord_webhook` is the only uid.
- Deleting by *receiver* uid rather than contact-point name matters here:
  `policies.yml` routes to the name `homelab`, and Grafana refuses to delete a
  contact point a route still points at. Dropping one of two receivers leaves
  the name intact, backed by `discord_webhook`.

**Notes / next steps:**
- The `deleteContactPoints` block is safe to keep: deleting an absent uid is a
  no-op, so a fresh install converges to the same place. It can go once every
  provisioned instance has restarted with it at least once.
- Worth remembering for the parked backups work, which will provision alert
  rules: the same upsert semantics apply to `deleteRules:`.
- Open question not chased here: `docker volume rm monitoring_ntfy_data`
  returned "no such volume" on CT 100, and no orphan container was removed
  either, which suggests ntfy was not running under this compose project at the
  time. Harmless — nothing to clean up — but the name is worth confirming
  against `docker volume ls` before assuming the data is gone.

---

## 2026-08-24 — Alerting consolidated to Discord; ntfy removed

**Goal:** Drop the ntfy phone app. Alerts had been fanning out to both ntfy
(push over the tailnet) and Discord `#alerts` since the off-box alerting work,
and everything was in practice being read in Discord — so ntfy was a container,
a volume, a Caddy route and an app on the phone all serving a path nobody
looked at.

**Steps:**
1. Removed the `ntfy_webhook` receiver from `contactpoints.yml`, leaving the
   `homelab` contact point with Discord alone. `policies.yml` was untouched —
   it targets the contact point, not the receiver.
2. Deleted the `ntfy` service and its `ntfy_data` volume from
   `docker/monitoring/docker-compose.yml`, the `alerts.*` site block from
   `docker/proxy/Caddyfile`, the tile from `docker/dashboard/config/services.yaml`,
   and `NTFY_BASE_URL` / `NTFY_PORT` from `.env.example`.
3. Promoted `DISCORD_ALERT_WEBHOOK` from `${VAR:-}` to `${VAR:?required}`.
4. Swapped the `NTFY_BASE_URL` stub in `.github/workflows/ci.yml` for a
   `DISCORD_ALERT_WEBHOOK` one, since the required-var set changed.
5. Marked the **ntfy stays** decision in
   [`tsd-alerting-off-box.md`](design/tsd-alerting-off-box.md) superseded rather
   than editing it out.

**Issues encountered:**
- **The webhook had been optional on purpose, and that stopped being safe.**
  The old comment in `docker-compose.yml` said an empty `DISCORD_ALERT_WEBHOOK`
  was allowed because "ntfy still works". Remove ntfy and that same default
  turns into a monitoring stack that starts cleanly, evaluates every rule, and
  delivers nothing — the worst failure mode available, because the dashboards
  all look fine. Hence step 3.
- **CI would have caught it, one commit too late.** The compose job supplies
  throwaway values for exactly the `:?required` vars; adding a new one without
  listing it there fails the run. That is the check working as designed, but it
  meant the required-var change and the CI change had to land together.

**Resolution:**
- Grafana → Alerting → Contact points → test `homelab` is now the single check
  that matters, and it is the one to re-run after any webhook change.
- Deployment on the box is not just `up -d`: the ntfy container and its volume
  outlive the compose change and have to be reaped explicitly (see below).

**Notes / next steps:**
- On CT 100: `docker compose up -d --remove-orphans` in `docker/monitoring/`,
  then `docker volume rm monitoring_ntfy_data` once the container is gone.
  Reload Caddy for the dropped `alerts.*` route. Then delete the phone app and
  the `alerts` DNS entry if one was pinned outside the wildcard.
- **Discord is now a single point of delivery**, which is a real reduction in
  redundancy and worth being honest about. The mitigation is that the failure it
  most plausibly hides — the box or its uplink dying — is precisely what
  `heartbeat.sh` catches from off-box, and Healthchecks alerts the same channel
  by an independent path. A Discord-wide outage would still be silent; accepted.
- The parked backups plan (`tsd-backups-and-monitoring.md`) assumed job alerts
  would reuse ntfy. It should use the `#alerts` webhook instead; the note in
  `AGENTS.md` now says so.

---

## 2026-08-23 — A landing page, and CI to stop it drifting

**Goal:** Seven subdomains and growing, none of them memorable. One URL to start
from.

**Steps:**
1. Added `docker/dashboard/` — [gethomepage](https://gethomepage.dev), config
   bind-mounted from the repo rather than kept in a volume.
2. Served at the bare `{$HOMELAB_DOMAIN}` — the parent of every service name, so
   it is the only URL worth memorising.
3. Added [`scripts/check-dashboard.sh`](../scripts/check-dashboard.sh) and a CI
   job: every Caddyfile site must have a tile, and every tile must point at a
   real site block.

**Decisions:**
- **One dashboard, not two.** Splitting homelab from other projects just moves
  the problem to "which dashboard was it on". Grouped sections instead, with the
  real distinction being *services* (on this box, status-checked) versus
  *bookmarks* (elsewhere, just links).
- **No Docker socket.** Homepage can auto-discover services from container
  labels, but that needs the socket — root-equivalent on this host — to avoid
  maintaining a list CI already keeps honest. The same trade rejected for
  `/deploy`, and it comes out the same way.
- **No credentialed widgets.** A Grafana or AdGuard widget means putting an
  admin password into this stack to render a number that is one click away.
  `siteMonitor` gives up/down and response time and needs nothing.
- **No `siteMonitor` on Kali.** Polling it would boot the container on every
  dashboard refresh, defeating Sablier's scale-to-zero.
- **Where the bookmark list stops: active repositories.** Archived ones are
  excluded, which is a line GitHub already maintains — so the list stays correct
  without anyone making a recurring taste call about what still counts. Archiving
  a repo removes it from here; that is the same decision, made once.
- **Grouped by what a repo *is*, not how active it is.** The first attempt split
  private-personal from public-projects, which put `design` next to `life` and
  `homelab` next to `hoststats` — both wrong. The distinction that holds:
  *Personal* (yours, ongoing, not shipped), *Core* (persistent things simply
  maintained — the site, the lab, the design system; they have no "done", so
  they are not projects), *Projects* (discrete work with a scope and an end).
- **Links go as deep as the URL is stable.** Cloudflare uses `?to=/:account/...`
  so it resolves the account id and lands on the zone's DNS page; Discord links
  into the guild rather than the app root. A bookmark that lands on a product's
  marketing page has saved nothing.
- **brignano.io is a service, not a bookmark.** It is off-box but externally
  reachable, so unlike a bookmark it can carry a real status check, and "is my
  site up?" is worth answering at a glance. The coverage check ignores hrefs
  without `{{HOMEPAGE_VAR_DOMAIN}}`, so an external service is not mistaken for
  a tile pointing at a deleted site block.

**Issues encountered:**
- **A DNS wildcard does not match its own parent.** `*.home` covers
  `stats.home.<zone>` but never `home.<zone>`, so the dashboard needs its own
  `A` record. Missing it looks exactly like a Caddy fault and is not.
- **Homepage rejects unrecognised Host headers.** Without `HOMEPAGE_ALLOWED_HOSTS`
  it answers "Invalid Host header" rather than serving a page — again, looks
  like a proxy problem.
- **The tile list is a second copy of the Caddyfile's list**, hand-maintained,
  in another file. That is the same drift shape as the deployment gap, except a
  stale dashboard never breaks — it just silently stops being complete, so you
  go on trusting it. Hence the CI check, in both directions: a site with no tile
  fails, and a tile pointing at a deleted site fails too.

**Verification:** the check was tested by breaking it on purpose — adding a
Caddyfile site with no tile (`MISSING vault`, exit 1) and a tile pointing at a
non-existent site (`STALE gone`, exit 1). All nine compose files still validate,
the four config files parse as YAML, and stock Caddy parses past the new site
block (failing later on the sablier plugin it does not have), so the block
itself is sound.

- **Then it shipped with exactly the bug it warned about.**
  `HOMEPAGE_ALLOWED_HOSTS` was set to `home.$HOMELAB_DOMAIN`, but the Caddyfile
  serves the dashboard at the *bare* `$HOMELAB_DOMAIN` — so with
  `HOMELAB_DOMAIN=home.brignano.io` the allowlist read `home.home.brignano.io`
  and every request was rejected as "Invalid Host header". The container starts
  fine and Caddy proxies fine; only the page is wrong, which is why it reads as a
  proxy fault. Two files that must hold the same string, in different syntaxes,
  with no check between them — the same shape as the tile drift, introduced in
  the commit that added the check for it. `check-dashboard.sh` now compares them
  too, and was verified by reintroducing the bug.

- **And then a second one, from the same cause: I never ran the container.**
  The config mount was `:ro`, which is right — it is the codified part and
  nothing inside the container should be able to drift it away from what CI
  checks. But Homepage writes its log file to `config/logs`, so the mkdir failed
  on every render and the page 500'd with the config perfectly valid. Fixed with
  a named volume nested inside the read-only bind — which failed too, and worse:
  runc has to *create* the mountpoint inside the bind before mounting over it,
  and the bind is read-only, so the container would not start at all. Settled on
  a plain writable bind. `:ro` there was defensive polish rather than a boundary
  — Homepage never writes its own config, the container has no Docker socket and
  sits on one network, and CI is what actually keeps the directory honest.
  `config/logs/` is gitignored.

  All three dashboard failures were runtime behaviour of an image that was never
  started before it shipped: static checks passed every time. Worth remembering
  next time a stack looks finished because CI is green.

**Notes / next steps:**
- Adding a service is now three edits: a Caddyfile block, a dashboard tile, and
  `docker compose restart caddy`. CI enforces the middle one.

---

## 2026-08-23 — repo-sync heals instead of nagging

**Goal:** The drift report's answer was always "run this command", so stop
printing it and run it — without handing a cron job the power to take the
network down.

**Steps:**
1. `repo-sync.sh` now restarts stale stacks itself, verifies each came back, and
   reports what changed.
2. `HL_NO_AUTOHEAL` (default `proxy`) lists stacks that are only ever reported.
   `HL_AUTOHEAL=no` restores report-only behaviour.
3. Reworked the output: one section per outcome (restarted / failed / needs you)
   and the remaining commands collected into a single block instead of repeated
   after every line.

**Issues encountered:**
- **`proxy` is the exception, and it is not a close call.** AdGuard runs in that
  stack, so auto-restarting it is auto-restarting the household's DNS. Nothing
  guards that failure the way `heartbeat.sh` guards Grafana and Prometheus, and
  a failure at 4am leaves no working name resolution to debug through.
- **A restart without verification is worse than no restart**, because it turns
  "stale but working" into "broken and unattended" while reporting success.
  Every heal is followed by a check that at least as many containers are running
  as before and none are stuck restarting.
- **One failure mode turned out to already be safe:** `up -d --build` builds
  *before* it recreates, so a broken build leaves the previous container
  serving. The case worth catching is a build that succeeds and then crashes.
- **Auto-healing is skipped entirely when the pull failed.** A tree that could
  not fast-forward is in an unknown state and is not one to deploy from.
- Real rollback was considered and rejected: `--build` discards the previous
  image unless it is tagged first, so a genuine rollback needs a tagging scheme
  and a retention policy. Verify-and-shout is the honest version of the 90%.

**Verification:** stubbed `docker`, a throwaway origin and a local webhook
receiver, covering each path — clean heal (exit 0); a stack that comes back with
fewer containers (reported under RESTART FAILED as `1/2 running`, exit 1);
`proxy` routed to "Needs you" with its command; a failed pull suppressing all
healing.

---

## 2026-08-23 — Drift report cried wolf on its first run

**Goal:** Fix a 50% false-positive rate in `repo-sync.sh`, found the first time
it ran for real.

**Steps:**
1. Excluded `**/*.md`, `tests/**` and `.env.example` from the "newest commit
   touching this stack" calculation.

**Issues encountered:**
- **Docs and tests live inside the stack directories but are never deployed.**
  The first real run flagged four stacks; two were changes that could not
  possibly affect them — `mcp` for a `README.md`, `assistant` partly for a
  `README.md` and `tests/smoke.py`, neither of which the Dockerfile copies (it
  takes `requirements.txt`, `app/` and `guild.yml`). A report that is wrong half
  the time is one you learn to ignore, which is worse than not having it.
- **`:(exclude)` silently does nothing with `**`.** Git pathspec needs glob
  magic for `**` to expand, so the exclusion must be written
  `:(exclude,glob)`. Written the obvious way it fails open — the filter appears
  to work and changes nothing. Caught by comparing filtered against unfiltered
  output rather than assuming.

**Verification:**
- Throwaway repo with a stubbed `docker`: a doc-only commit after container
  start does not flag; a compose change after container start does.
- Against the real repo the filter moves `mcp` from "10 hours ago" back to
  "3 months ago" — older than its container, correctly clearing it.

**Notes / next steps:**
- What the first run *did* correctly catch: `monitoring` had been up 24 days
  while its alerting provisioning was written 10 hours earlier. Grafana reads
  alerting provisioning only at startup, so the codified contact points had
  never been loaded — the working alerts were hand-made in the UI. Exactly the
  invisible gap the report exists to surface.

---

## 2026-08-23 — Repo drift: a daily pull, a report, and a CI gate

**Goal:** Stop the working tree on CT 100 silently falling days behind GitHub —
without turning that into an unattended deploy pipeline aimed at the box that
serves the household's DNS.

**Steps:**
1. Added [`scripts/repo-sync.sh`](../scripts/repo-sync.sh) — daily
   `git pull --ff-only` plus a deployment-drift report, posting to `#alerts`
   only when there is something to do.
2. Added `.github/workflows/ci.yml` — the first CI this repo has had. Four jobs:
   assistant smoke tests, `docker compose config` on all eight stacks, `sh -n`
   on the cron scripts, and `caddy adapt` inside the real proxy image.
3. Added [`docker/assistant/tests/smoke.py`](../docker/assistant/tests/smoke.py)
   — 22 offline checks, no pytest, no network.

**Issues encountered:**
- **A pull cron on its own would have made things worse.** Nothing on the box
  runs from the working tree; every service runs from a built image or read its
  config when its container started. Pulling silently leaves the repo *ahead* of
  what is running, so `git log` says you are current when you are not — a
  visible gap converted into an invisible one. The pull is only safe because the
  drift report ships with it.
- **"Is this stack stale?" is two different questions.** A stack that builds its
  own image (assistant, proxy) has to be compared against the *image* creation
  time, because a restart does not rebuild — and a host reboot restarts
  everything, which would otherwise read as fresh. A stack that pulls upstream
  images and bind-mounts its config from the repo only needs a restart, so
  *container start* time is the right basis. The script picks per stack by
  looking for a `build:` key in the compose file.
- **`set -e` plus `[ -n "$X" ] && VAR=…` is a trap.** When the test fails the
  AND-list returns non-zero and the whole script exits — and the test failing is
  the *normal* case when building an optional report. Caught by running it;
  rewritten as `if` blocks.
- **A pull is not inert.** Seven repo files are bind-mounted into running
  containers, and Grafana polls its dashboard provisioning directory. An
  automatic pull can therefore change dashboards with no restart from you.
- **Stock Caddy cannot check this Caddyfile at all.** `acme_dns cloudflare`
  fails with "module not registered" unless the plugin is compiled in, so CI
  builds the real proxy image and checks inside it. Checking against stock Caddy
  would have proved nothing about what actually deploys.
- **`caddy validate` was the wrong verb, and CI proved it.** The first run went
  red: `validate` does not stop at parsing — it *provisions* every module, and
  the cloudflare DNS provider checks its API token's format while doing so, so
  the throwaway `ci` token was rejected. A real `validate` would need a
  credential-shaped secret in CI to say anything at all, which means either
  putting a live Cloudflare token there or testing a fake. `caddy adapt` stops
  at Caddyfile → JSON, which is the right boundary: it still catches syntax
  errors and missing plugin directives, and needs no *valid* secrets. Worth
  noting what the failure *did* prove — the log shows `adapted config to JSON`
  before the provisioning error, so the Caddyfile itself was never in question.
- **Then it went red a second time, for the opposite reason.** Having decided
  the token was not needed, dropping it entirely fails earlier still:
  `acme_dns cloudflare {$CLOUDFLARE_API_TOKEN}` expands to nothing and the
  directive is rejected at *adapt* time with "missing API token". So the token
  must be present but need not be well-formed — presence is checked by the
  adapter, format by the provisioner. Two failures, two different stages, and
  the first run's log was what proved the fix: it had already adapted cleanly
  with exactly this value.

**Verification:**
- `repo-sync.sh` exercised against a throwaway origin and a stubbed `docker`,
  covering all three paths: clean (silent, exit 0), pull-and-stale (correct
  stack flagged, correct restart hint), and pull failure (reported, exit 1).
- Smoke tests mutation-checked: leaking `num_thread` into a request payload and
  deleting the do-not-volunteer guard from the chat prompt each turn the suite
  red, so the checks are load-bearing rather than decorative.
- Three of four CI jobs run locally and pass; the `caddy` job needs a Docker
  daemon and was verified only as far as a stock binary allows — it failed on
  its first real run and was fixed (see above), which is roughly the point of
  having CI.

**Install the cron entry** (on CT 100, as root):

```bash
crontab -e
```

```
0 4 * * * /root/homelab/scripts/repo-sync.sh
```

Silence means the tree is current and every stack is running that code. Run it
by hand once first — it prints the same report it would post.

**Notes / next steps:**
- A `/deploy` slash command is the obvious sequel, and deliberately not built
  yet: it needs the Docker socket in the assistant container (root-equivalent on
  that host), and it is only defensible now that CI gates `main`. The model must
  never be the thing that chooses — Discord's own enumerated choices are the
  picker, Python does the work.
- CI does not yet lint shell beyond `sh -n`; shellcheck on the existing scripts
  is unverified and would need a pass before being made blocking.

---

## 2026-08-23 — #chat can see the homelab

**Goal:** First real conversation in `#chat` produced *"I can't access anything
on the homelab or assess the current load or performance metrics."* True — and
explicitly instructed — but useless, and the data was two seconds away in the
same bot. `/digest` reads Prometheus and Loki; `#chat` could not.

**Steps:**
1. Added `Facts.compact()` — one dense line of readings for a chat prompt,
   terser than the digest's block.
2. Replaced the fixed `CHAT_SYSTEM` with `build_system(facts_line)`: with
   readings it states them as measured fact; without, it falls back to saying it
   cannot see live data.
3. Added `_live_metrics()` to the bot, with a TTL cache.
4. New knobs: `CHAT_LIVE_METRICS` (default true), `CHAT_METRICS_TTL_S` (60).

**Issues encountered:**
- **Tool-calling was the obvious answer and the wrong one.** A 3B is unreliable
  at it — the reason `tsd-ai-homelab-assistant.md` shelved itself — and the same
  hardware constraints still apply.
- **The old prompt made the limitation too salient.** "You cannot see any live
  data" as a standing declaration meant a 3B led with it on every question,
  related or not — the same failure as the persona bug earlier today.
- **Six queries per message** would be wasteful in a fast back-and-forth.
- **A stale reading is worse than no reading.** Numbers presented as current but
  measured minutes ago during an outage would actively mislead.

**Resolution:**
- **Injection, not tools.** Facts are gathered deterministically on every turn;
  the model never decides whether to look, it simply always has them. Python
  measures — including the "needs attention" verdict — and the model only reads
  the numbers out. Same rule that makes the digest trustworthy.
- The no-live-data clause now appears *only* when collection actually failed.
  The no-internet caveat stays, scoped to things genuinely outside the box.
- Cached for 60s; a homelab does not change meaningfully between two messages
  typed seconds apart.
- **Failure yields no readings, never stale ones** — the cache is not served past
  its TTL when a fresh collection fails, and failures are not cached.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
No config change needed — it is on by default. Ask "how's the server doing?" in
`#chat`; expect real numbers. `docker logs assistant` shows the collection.

**Notes / next steps:**
- Web search was **not** added. The 2026-06-07 decision still holds: a 3B
  ignored retrieved sources and answered from its prior, which a better search
  engine does not fix.
- Filesystem/shell access was **not** added. Debugging needs multi-step
  reasoning over tool results — the 3B's weakest area — and `mcp.home` already
  gives Claude that job with a read-only ceiling.
- If replies slow noticeably, the readings cost ~50-60 tokens per turn;
  `CHAT_LIVE_METRICS=false` removes them.

## 2026-08-23 — Conversational #chat, and a layout that means something

**Goal:** Two complaints. The channels felt like stock Discord with no
intention behind them, and talking to the model meant typing `/ask` every single
time — no continuity, no follow-ups, no conversation.

**Steps:**
1. Reworked `guild.yml` into two categories split by **direction**: `HOMELAB`
   (digest, alerts — the lab reporting to you) and `ASSISTANT` (chat — you
   talking to it), with real topics explaining what each is for.
2. Renamed `#ask` to `#chat` and made it conversational: `on_message` answers
   every message, no command needed.
3. Added `app/chat.py` and `Ollama.chat()` (`/api/chat`), factoring the shared
   request handling out of `generate()` into `_post()`.
3b. **Context now follows Discord's own primitives** rather than rolling channel
   history: a plain message is a one-off, a reply walks its reply chain, and a
   message in a thread reads the whole thread. Threads (and forum posts, which
   are threads) are therefore the persistence unit — named conversations you can
   return to. Pointing `DISCORD_CHAT_CHANNEL_ID` at a forum channel gives a
   browsable list of them with no code change.
4. New optional settings: `DISCORD_CHAT_CHANNEL_ID` (blank = feature off),
   `CHAT_HISTORY_TURNS`, `CHAT_HISTORY_CHARS`, `CHAT_NUM_PREDICT`.

**Issues encountered:**
- **A bot that answers every message will answer itself, forever.** The first
  thing `_should_handle` checks is whether the author is a bot.
- **Reading plain messages needs the Message Content privileged intent.** PR #34
  listed "no privileged intents" as a security property, so this walks one back.
- **Conversation memory needs somewhere to live**, and any in-process store is
  lost on the restarts that happen constantly during setup.
- **Rolling channel history was the wrong default.** Two unrelated questions
  typed into the same channel contaminate each other's context, and there is no
  way to end a conversation short of waiting for it to scroll away.
- **Unbounded history would get slower every turn** — prompt evaluation is
  CPU-bound and roughly linear in tokens.
- **Concatenating turns into one prompt** would feed the model a format it was
  never trained on.

**Resolution:**
- **Discord *is* the store**, and *where you type* decides what is read back:
  plain message → itself only; reply → the reply chain; thread → the whole
  thread. No command, no state, and the context is always visibly implied by
  where the message sits. No database, no state: restart-safe, threads get
  their own context for free, and deleting a message actually removes it from
  the model's memory. What you see in the channel is what it sees.
- The intent is requested **only when a chat channel is configured**, and
  narrowed in code — one channel (plus its threads), allowlisted users only.
- Both a turn cap and a character budget, trimming oldest-first while always
  keeping the newest message, since that is the one being answered.
- `/api/chat` applies the model's own chat template to role-tagged turns.
- `//` prefix: no reply, and excluded from context — for notes and asides.

**On the box (apply after merge):**
```bash
# 1. Developer Portal -> Bot -> Privileged Gateway Intents -> Message Content ON
# 2. Rename #ask to #chat IN DISCORD (preserves history), then copy its ID
cd ~/homelab && git pull
nano docker/assistant/.env      # DISCORD_CHAT_CHANNEL_ID=<the #chat id>

docker compose -f docker/assistant/docker-compose.yml run --rm --build \
  assistant --provision                     # review, then --apply
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
Then just type in `#chat`. Expect ~10-30s per reply depending on how much
history is in context.

**Notes / next steps:**
- The bot's display name is now declared too (`bot.nickname` in `guild.yml`,
  applied as a per-server nickname). Deliberately the nickname rather than the
  global username: Discord rate-limits username changes to 2/hour, while a
  nickname is server-scoped and free to change. Renamed `spotter` to `Otto` —
  a role-description read as cold in practice; a plain name reads like a
  participant in the conversation.
- Rename channels in Discord rather than in `guild.yml` — the provisioner never deletes,
  so changing the name in the file creates a second, empty channel instead.
- If replies get slow, lower `CHAT_HISTORY_TURNS` before anything else; context
  length is the dominant cost on this hardware.
- `#alerts` stays webhook-fed and is untouched by any of this — deliberately, so
  it keeps working when the bot is down.

## 2026-08-23 — Real domain + real certificates, still not exposed

**Goal:** Replace the `*.home` pseudo-TLD with a real domain and publicly-trusted
certificates, **without publishing anything**. The obvious reading of "put it on
my domain" is a tunnel or a port forward; that was not wanted, and `AGENTS.md`
rules it out for admin services.

**Steps:**
1. Added `--with github.com/caddy-dns/cloudflare` to the Caddy build (it was
   already an `xcaddy` build for the Sablier plugin).
2. Rewrote the Caddyfile: sites are now `<name>.{$HOMELAB_DOMAIN}` with
   `acme_dns cloudflare` in the global block.
3. Kept every `*.home` name as an `http://` site that redirects to its real
   counterpart, so bookmarks, `hl-*` aliases and the dev machines' MCP config
   keep working.
4. Added `HOMELAB_DOMAIN` and `CLOUDFLARE_API_TOKEN` (both `:?required`) to the
   proxy stack; documented the exact token scope in `.env.example`.
5. `shell/aliases.sh` gained `HL_DOMAIN` (defaults to `home`, so the aliases work
   unchanged and just take the redirect).
6. Wrote [`docs/design/tsd-real-domain-private-tls.md`](design/tsd-real-domain-private-tls.md)
   and annotated the original proxy TSD as superseded in part.

**Issues encountered:**
- **HTTP-01 cannot work here.** It needs port 80 reachable from the internet —
  precisely what is being refused.
- **`.home` must never be sent to a CA.** No public CA will issue for it, and an
  attempt would produce repeated failures in the log.
- **Caddy rejects single-line site blocks.** `addr { directive }` on one line is
  a syntax error; the closing brace needs its own line. Caught by validating
  with a real Caddy binary rather than by eye.

**Resolution:**
- **ACME DNS-01**: Caddy proves ownership by writing a TXT record to the
  Cloudflare zone, never by receiving a connection. That is what makes real
  HTTPS possible on a host the internet cannot reach — and it retires the
  internal-CA trust prompt `kali.home` needed.
- Legacy blocks are declared `http://` so Caddy never attempts issuance for them.
- Config validated with `caddy validate` (plugin directives stubbed, since the
  stock binary lacks them) and formatted with `caddy fmt`. All 14 hostnames —
  7 real, 7 redirects — adapt correctly.

**On the box (apply after merge):**
```bash
# 1. Cloudflare DNS: add ONE record, proxy OFF (grey cloud):
#      Type A   Name *.home   Content 10.0.0.201   Proxy status: DNS only
#    (adjust "home" to whatever subdomain you chose)
# 2. Cloudflare API token: dash.cloudflare.com/profile/api-tokens ->
#    Create Token -> Custom -> Zone | DNS | Edit, scoped to this zone only.
#    Do NOT use the Global API Key.
cd ~/homelab && git pull
nano docker/proxy/.env     # HOMELAB_DOMAIN=, CLOUDFLARE_API_TOKEN=

docker compose -f docker/proxy/docker-compose.yml up -d --build
docker logs -f caddy       # watch certificate issuance; ~1-2 min for all seven
```
Then verify: `https://stats.<domain>` loads with a valid padlock, and
`http://stats.home` redirects to it.

**Notes / next steps:**
- **Proxy status must be DNS only.** An orange cloud would route through
  Cloudflare's edge, which cannot reach a private address.
- Update each dev machine's `.mcp.json` to `https://mcp.<domain>/mcp`. The old
  URL still works via redirect, but MCP clients may not follow redirects.
- Once nothing uses `*.home`, delete that Caddyfile section and the AdGuard
  rewrites. AdGuard stays for ad blocking and as the tailnet resolver.
- First issuance is ~7 sequential DNS-01 challenges; subsequent renewals are
  automatic and staggered.

## 2026-08-23 — Alerting that survives the box going down

**Goal:** Close the hole found the hard way — the server went offline and
nothing said so. Grafana evaluates the rules, ntfy delivers the push, and the
assistant posts the digest, all inside CT 100. When the box dies, all three die
with it. The one failure most worth hearing about was guaranteed to be silent.

**Steps:**
1. Added a `discord` receiver alongside the existing ntfy webhook in
   `contactpoints.yml` — one contact point, two receivers, so `policies.yml`
   only changed its target name (`ntfy` → `homelab`).
2. Passed `DISCORD_ALERT_WEBHOOK` into the Grafana container so provisioning can
   interpolate it; documented it in `.env.example`.
3. Added [`scripts/heartbeat.sh`](../scripts/heartbeat.sh) — a dead man's switch
   that pings Healthchecks.io from cron every 5 minutes.
4. Updated `#alerts` in `guild.yml` to say what actually feeds it, and added
   `hl-heartbeat` to `shell/lib.sh`.
5. Wrote [`docs/design/tsd-alerting-off-box.md`](design/tsd-alerting-off-box.md).

**Issues encountered:**
- **A monitoring system cannot report its own death.** No alert rule and no
  extra container on CT 100 can fix this; anything hosted on the watched machine
  inherits the same failure.
- **An unconditional ping would only prove cron ran**, not that monitoring works.
- **Routing alerts through the assistant bot** would have reintroduced exactly
  the dependency being removed.

**Resolution:**
- Inverted the logic: the box pings *out*, and **silence is the signal**. That's
  the only shape that survives the failure it's meant to catch — and it needs no
  inbound access, so still no port, no tunnel, no public endpoint.
- The heartbeat pings only while `grafana` and `prometheus` are running, so
  "host up, Docker wedged" is caught too. If either is missing it pings `/fail`
  and alerts immediately rather than waiting out the grace period.
- `#alerts` is fed by **webhooks only**. A Discord webhook needs no bot process,
  so alerts arrive even when the whole `ai` stack is down.
- Kept ntfy. Redundancy at the notification layer is cheap, and it preserves a
  path that doesn't depend on Discord or on having internet at all.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull

# 1. Discord webhook: Server Settings -> Integrations -> Webhooks ->
#    New Webhook -> channel #alerts -> Copy Webhook URL
# 2. Healthchecks.io: create a check, period 5m, grace 15m, and add its
#    Discord integration pointed at the same webhook. Copy the ping URL.
nano docker/monitoring/.env      # DISCORD_ALERT_WEBHOOK=, HEALTHCHECKS_PING_URL=

docker compose -f docker/monitoring/docker-compose.yml up -d   # reload provisioning
./scripts/heartbeat.sh && echo ok                              # verify by hand

# 3. Schedule it (alongside the 02:00 pg-backup entry):
crontab -e
*/5 * * * * /root/homelab/scripts/heartbeat.sh
```
Verify end to end: in Grafana, **Alerting → Contact points → homelab → Test** —
a message should land in `#alerts`. Then stop the monitoring stack for 15
minutes and confirm Healthchecks alerts. Testing the failure path is the whole
point; an untested dead man's switch is an assumption.

**Notes / next steps:**
- Healthchecks itself going down is uncovered and accepted.
- The same Healthchecks account can later monitor `pg-backup.sh`, which is what
  `tsd-backups-and-monitoring.md` wanted it for. Still ⏸ parked on a USB SSD.
- 5m period / 15m grace = two consecutive misses before alerting. Widen the
  grace before weakening the check if false alarms appear.

## 2026-08-22 — Discord server layout codified (guild.yml + `--provision`)

**Goal:** Start the Discord side from scratch — no server, no bot, no channels —
without the layout ending up as undocumented clicks in a UI. Everything else in
this lab is config-in-git; the channels the digest depends on should be too.

**Steps:**
1. Added `docker/assistant/guild.yml` — declarative categories, channels, topics
   and permissions.
2. Added `docker/assistant/app/provision.py` and a `--provision` mode:
   `--provision` prints a plan and changes nothing; `--provision --apply`
   executes it.
3. Layout: category `HOMELAB` with `#digest` (bot posts only), `#ask`
   (interactive), `#alerts` (reserved for a future ntfy bridge).
4. Documented the manual-vs-codified split in `docker/assistant/README.md`.
5. Added PyYAML to the pinned requirements; `guild.yml` is copied into the image.

**Issues encountered:**
- **Locking `#digest` would have silently broken the digest.** Denying
  `@everyone` Send Messages also denies the bot — it's a member like any other.
  Without an explicit self-allow, the daily post would fail into a channel the
  bot itself created.
- **Chicken-and-egg on `DISCORD_DIGEST_CHANNEL_ID`.** It can't be known until
  the channel exists, but `Config.from_env()` requires it, so provisioning
  couldn't reuse the normal config path.
- **A bot token cannot do everything.** Creating a server and restricting a
  slash command to a channel both need a *user* OAuth token.
- **Deleting channels to converge would be unrecoverable** — `#digest` is the
  health history.

**Resolution:**
- Every locked channel gets a paired overwrite: deny `@everyone`, explicitly
  allow the bot — `send_messages` only, since Discord rejects an overwrite
  granting a permission the acting bot doesn't itself hold.
- `--provision` uses a minimal config path needing only `DISCORD_TOKEN` and
  `DISCORD_GUILD_ID`, and prints the `.env` line to paste when it finishes.
- The two bot-impossible steps are documented as clicks rather than
  half-automated.
- The provisioner is additive only: it creates and fixes drift, never deletes.
  Channels not in `guild.yml` are reported and left alone.
- Login-only (no gateway connect) so it starts and exits in about a second.

**On the box (apply after merge):**
```bash
# 1. Create the server + bot by hand (see docker/assistant/README.md).
#    Invite with View Channels + Send Messages + Manage Channels + Manage Roles
#    (permissions=268438544).
# 2. Fill DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_ALLOWED_USER_IDS, TZ.
cd ~/homelab && git pull
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --selftest
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --provision
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --provision --apply
# 3. Paste the printed DISCORD_DIGEST_CHANNEL_ID into .env, then:
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
Manage Channels / Manage Roles can be removed afterwards — the bot needs only
View Channels + Send Messages to run.

**Notes / next steps:**
- Still not deployed; no Docker daemon was available. The diff engine and the
  apply path were verified offline against fakes, and every discord.py call was
  checked against the pinned 2.4.0 API, but nothing has run against a real guild.
- `#alerts` is created but nothing writes to it yet — ntfy still serves alerts at
  `alerts.home`. Bridging it is the obvious next step.
- Restricting `/ask` to `#ask` (Server Settings → Integrations) is worth doing
  once, or `#digest` stops being a clean log.

## 2026-08-22 — Local LLM moved from pull (chat page) to push (Discord jobs)

**Goal:** Actually use the local `llama3.2:3b` instead of reaching for Claude by
default. The blocker was never the model — it was that Open WebUI is a *pull*
interface: you go to `chat.home`, type, and watch ~16 tok/s. Against Claude that
comparison is lost before it starts, so the box idled.

**Steps:**
1. Added the `assistant` stack (`docker/assistant/`) — a small Python container
   running a Discord bot. No `ports:`, no Caddy route, no DNS rewrite: it opens
   an *outbound* websocket to Discord, so it adds zero inbound surface and works
   off-tailnet, which `chat.home` can't.
2. Scheduled daily digest (`DIGEST_AT`, default 07:30): services up/down,
   CPU/RAM/disk, container restarts, and log-error counts by container, queried
   from Prometheus + Loki.
3. Interactive surfaces: `/ask`, `/summarize`, right-click → *Summarize message*,
   plus `/digest` and `/status`.
4. All LLM work funnels through a single-worker priority queue
   (`app/jobqueue.py`), interactive ahead of scheduled.
5. Wrote `docs/design/tsd-local-llm-discord-jobs.md`; reframed
   `docs/ai-strategy.md` around "is anyone waiting on the answer?"; noted in
   `tsd-ai-homelab-assistant.md` that its canned-summary half now ships.
6. Added `assistant` to `HL_STACKS` in `shell/lib.sh` so `hl-up` includes it.

**Issues encountered:**
- **Concurrency would have made things worse, not better.** The tuned model pins
  `num_thread 4` of the LXC's 6-core quota, so two generations at once contend
  for the same cores and memory bandwidth — both crawl *and* the monitoring/proxy
  stacks lose their remaining 2 cores. Ollama accepts the parallel requests
  happily and thrashes.
- **Request-time options override the Modelfile.** Passing `num_thread` on
  `/api/generate` would silently undo the pin that took generation from
  ~0.5 tok/s back to ~16 (the 2026-06-07 fix below).
- **A digest that can silently say "all clear" is worse than none.** Naive error
  handling would render an unreachable Prometheus as zero problems.

**Resolution:**
- Single worker for every LLM call; supporting caps on context, output length,
  input size, backlog depth, and per-job timeout.
- `num_thread` deliberately never sent; documented as a rule in `AGENTS.md`.
- Python queries *and thresholds* every fact, including the "is this fine?"
  verdict — the model only restates a finished facts block. If Ollama fails the
  digest still posts without prose; an unreadable backend is reported as
  **Incomplete**, never as healthy.
- Container healthcheck watches a 60s heartbeat file, since a Discord client can
  lose its gateway socket while the process stays alive.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
cp docker/assistant/.env.example docker/assistant/.env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_DIGEST_CHANNEL_ID,
# DISCORD_ALLOWED_USER_IDS, TZ

# check the backends before involving Discord — needs no token:
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --selftest

docker compose -f docker/assistant/docker-compose.yml up -d --build
docker logs -f assistant     # expect "connected as <bot> (guild ...)"
```
Discord app setup (bot token, guild install with `bot` + `applications.commands`,
**no** privileged intents) is in
[`docker/assistant/README.md`](../docker/assistant/README.md).

**Notes / next steps:**
- Not yet deployed — the image has not been built or run against real Discord,
  Prometheus, Loki or Ollama. Logic was verified offline (parsers against real
  API payload shapes, queue serialization/priority and load-shedding, and the full
  digest path over stub HTTP servers); `--selftest` is the first real check.
- Grafana-managed alert state is deliberately not in the digest — the rules live
  in Grafana, not Prometheus, so `ALERTS` isn't queryable. Could be added later
  via the Grafana API.
- If a second container ever drives Ollama, the single-worker guarantee breaks —
  the queue would need to move behind a shared gateway.

## 2026-06-07 — Reverted web search + 7B; consolidated to single local model

**Goal:** Walk back the SearXNG + `qwen2.5:7b` web-search experiment (below). In
practice the 7B was too slow and RAM-hungry on this CPU-only / 16 GB box, and a
3B can't faithfully use retrieved sources anyway — so the whole web-augmented
path added latency and confidently-wrong answers for no real gain.

**Steps:**
1. Removed the `searxng` service + `docker/ai/searxng/settings.yml`, the
   web-search env on `open-webui`, and `SEARXNG_SECRET` from `.env.example`.
2. Removed `docker/ai/models/qwen2.5.Modelfile`; back to a single `llama3.2:3b`.
3. Restored `OLLAMA_KEEP_ALIVE=-1` (one small model, keep it resident — no
   cold-load lag).
4. Rewrote the README AI section; recorded the rationale + local-vs-Claude split
   in `docs/ai-strategy.md`; shelved `docs/design/tsd-ai-homelab-assistant.md`.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/ai/docker-compose.yml up -d --remove-orphans   # drops searxng
docker exec ollama ollama rm qwen2.5:7b                                  # reclaim ~5 GB
# remove the leftover bind-mount dir + the now-unused SEARXNG_SECRET line in .env
rm -rf docker/ai/searxng
```
In Open WebUI: delete the "🔎 Research" preset; keep "⚡ Quick Chat" (llama3.2:3b,
web search off). Web-search settings persist in the `open_webui_data` volume but
are harmless once the engine/container is gone.

**Notes:**
- Decision recorded in `docs/ai-strategy.md` → Decision log. Live-data / reasoning
  tasks (incl. trip & climbing-weather planning) go to Claude.

---

## 2026-06-07 — Self-hosted web search for Open WebUI (SearXNG)

> ⚠️ **Superseded** by the entry above — this setup was reverted the same day.

**Goal:** Give the local Llama model working web search. DuckDuckGo (DDGS) via
Open WebUI's built-in engine kept returning "no sources found" (DuckDuckGo
rate-limits/blocks the scraped queries), so answers silently fell back to stale
training data.

**Steps:**
1. Added a `searxng` service to `docker/ai/` (same `ai` network as Open WebUI,
   **no host port** — internal-only). Hardened with `cap_drop: ALL` + minimal
   `cap_add`, healthcheck on `/healthz`.
2. Committed `docker/ai/searxng/settings.yml` with `use_default_settings: true`,
   `limiter: false`, and the critical `search.formats: [html, json]` — Open WebUI
   talks to SearXNG over JSON; without it every query 403s.
3. Wired Open WebUI to it via env (`WEB_SEARCH_ENGINE=searxng`,
   `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>`) and added
   `SEARXNG_SECRET` to `.env.example` (entrypoint injects it into `secret_key`).

**Issues encountered:**
- DDGS "no sources found" → DuckDuckGo throttling, not a config bug.
- Existing Open WebUI volume persists web-search config (PersistentConfig), so the
  new env vars don't override it on an already-running instance.

**Resolution:**
- On the box: set `SEARXNG_SECRET` in `.env`, `docker compose up -d`, then in
  Open WebUI **Admin > Settings > Web Search** switch the engine to **searxng**
  and set the Query URL to `http://searxng:8080/search?q=<query>`.

**Follow-up — model upgrade for RAG faithfulness:**
- With SearXNG working, search retrieved the *correct* sources (Proxmox VE 9.0),
  but `llama3.2:3b` ignored them and still answered "7.0.18" from its training
  prior — a 3B model is too small to faithfully use retrieved context.
- Added `qwen2.5:7b` (pinned `num_thread 4`) in `docker/ai/models/`. Select it in
  Open WebUI when Web Search is on; keep `llama3.2:3b` for quick chats.
- Changed `OLLAMA_KEEP_ALIVE` from `-1` to `5m`: pinning both models resident is
  ~8 GB and crowds the 14 GB LXC. Ollama now loads whichever model the chat
  selects and frees it after idle (cost: ~10-40s reload on switch).
- Apply on the box: `./docker/ai/load-models.sh` (pulls + tunes qwen2.5:7b).

**Notes / next steps:**
- Result count 3 + fetch length capped to keep prompts small on the CPU-only LXC
  (long context = slow time-to-first-token on the 7730U).
- Verify with a current-events query ("latest stable Proxmox VE version" → should
  return **9.0 with source citations** when using qwen2.5:7b + Web Search).

---

## 2026-06-07 — Observability stack deployed & verified in production

**Goal:** Deploy the monitoring buildout (exporters, Loki/Alloy, dashboards, ntfy
alerting) to the LXC and confirm it works end to end.

**Steps:**
1. On host **m5**: created read-only PVE token (`monitoring@pve!grafana`, role
   `PVEAuditor`); installed `prometheus-node-exporter` on the bare metal.
2. In the LXC: created read-only Postgres role (`monitoring`, `pg_monitor`);
   filled `.env`; `docker compose up -d` (all 11 containers up).
3. Follow-ups (PR #11): switched the ntfy contact point to `?template=grafana`
   for readable pushes; fronted ntfy with Caddy as `alerts.home`; baked the
   Proxmox host IP (`10.0.0.200`) into `prometheus.yml`. Added
   `GF_SERVER_ROOT_URL=http://stats.home` so alert links work from the phone.

**Verified:**
- All Prometheus targets `UP` — host, LXC, containers, `pve`, `postgres`, all 7
  blackbox probes, loki, alloy.
- Grafana provisioning loaded cleanly (datasources, 6 dashboards, 5 alert rules).
- Loki shows live `{job="docker"}` logs via Alloy.
- Test alert delivered to the ntfy phone app, formatted by the Grafana template.

**Notes / next steps:**
- Grafana admin password was reset on the box via
  `grafana cli admin reset-admin-password` (env var doesn't change an already-
  initialised instance).
- ntfy web UI shows a harmless "notifications only over HTTPS" banner — browser
  API limitation only; phone app + Grafana delivery are unaffected.
- Optional later: custom ntfy template to drop markdown / add severity+host.

---

## 2026-06-07 — `.home` names stopped resolving (wedged Tailscale subnet session)

**Goal:** `chat.home` (and all `*.home`) stopped loading from the MacBook, while the
direct `http://10.0.0.201:3010` still worked. Determine whether DNS was broken and fix it.

**Diagnosis:**
1. Confirmed the DNS record itself was fine: `dig @10.0.0.201 chat.home` → `10.0.0.201`,
   Caddy `:80` open, Open WebUI up. But `dig @100.100.100.100 chat.home` (Tailscale
   resolver) timed out and `curl http://chat.home/` hung (HTTP 000).
2. Ruled out config: AdGuard rewrite `*.home → 10.0.0.201` correct, `allowed_clients: []`
   (allows all), Caddy route correct, m5 `ip_forward=1` (persisted), `ts-forward`/masquerade
   chains intact, m5 → `10.0.0.201` on LAN fine. Nothing mis-set.
3. Found the fault in the tailnet path: `tailscale status` showed m5 as
   `relay "nyc", tx … rx 0` — sending but receiving nothing. SSH to m5's *own* tailnet IP
   (`100.116.69.120`) worked, but traffic *forwarded through* m5 to the subnet-routed
   `10.0.0.201` (how every device resolves `.home`) died. Only 49 packets had ever hit the
   subnet masquerade.

**Root cause:**
- A stale, half-open WireGuard session to m5 stuck on the DERP relay and never re-formed a
  direct path (the UPnP-based direct path had lapsed). The node stayed reachable, but
  subnet-router forwarding to `10.0.0.201` was effectively dead — so split-DNS lookups for
  `*.home`, which forward to `10.0.0.201`, timed out. **Operational fault, not config or
  architecture.**

**Resolution:**
- Restarted `tailscaled` on m5 (detached `systemd-run` so it survived the Tailscale-SSH drop).
  The session immediately re-formed **direct** (`10.0.0.200:41641`, `rx > 0`); `tailscale ping
  10.0.0.201` went from DERP-40ms/timeout → direct 3ms; `chat.home` → **HTTP 200 in 17ms**.
- Hardened inside the existing single-gateway design (did **not** add Tailscale to the LXC —
  consistent with the `/dev/net/tun` constraint): added a static **UDP `41641` →
  `10.0.0.200`** port-forward on the Xfinity router + DHCP reservation for m5, so the direct
  path is deterministic instead of depending on UPnP lease renewal. Verified m5's tailscaled
  listens on `:41641` and it now advertises `73.143.128.196:41641` as a peer endpoint.
  (Closes the long-pending DHCP-reservation item for `10.0.0.200`.)

**Notes / next steps:**
- **Runbook:** if `*.home` goes flaky again, first check `tailscale status | grep m5` on any
  device — `relay` instead of `direct` = this same failure; fix is `systemctl restart
  tailscaled` on m5. The router port-forward should now prevent the relay-wedge recurring.
- The true off-LAN test (direct handshake inbound on `41641`) happens next time a device
  connects from outside the home network — it should go direct instead of relay.

---

## 2026-06-07 — Observability buildout: exporters, logs, dashboards, alerting

**Goal:** Prometheus + Grafana were running but observing nothing — no exporters
beyond node/cadvisor, no dashboards, no logs, no alerts. Stand up full coverage
(Proxmox host, LXC, containers, PostgreSQL, endpoint uptime) plus log aggregation
and push alerting, within the 16 GB RAM budget.

**Steps (all in `docker/monitoring/`, branch `feat/observability-stack`):**
1. Added exporters to the compose: `pve-exporter` (Proxmox API, read-only
   `PVEAuditor` token via `PVE_*` env), `postgres-exporter` (read-only `pg_monitor`
   role, joins `core_core`), `blackbox-exporter` (HTTP probes, joins
   `core`/`ai`/`proxy`).
2. Added logs: `loki` (filesystem store, 30-day retention) + `alloy` (ships Docker
   container logs via the read-only socket + host journal → Loki).
3. Added `ntfy` for push alerts (`NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` so iOS
   gets instant APNs delivery; only a wake-up poke leaves the box).
4. Provisioned Grafana as code: Prometheus + Loki datasources, a dashboard file
   provider, and unified alerting (ntfy webhook contact point + default policy +
   5 alert rules: target down, disk >85%, mem <10%, probe down, postgres down).
5. Extended `prometheus.yml` with jobs: `node-proxmox`, `pve`, `postgres`,
   `blackbox`, `loki`, `alloy` (+ `--web.enable-lifecycle` for hot reload).
6. `scripts/fetch-dashboards.sh` downloads community dashboards (1860, 19792,
   10347, 9628, 7587, 13639) and pins datasource inputs to the fixed UIDs.

**Notes / next steps:**
- **Manual host steps before deploy:** `apt install prometheus-node-exporter` on
  the Proxmox host; set `PROXMOX_HOST_IP` (×2) in `prometheus.yml`; create the PVE
  token and Postgres `monitoring` role; fill `.env`; run `fetch-dashboards.sh`.
- Decision: kept secrets in `.env` (`${VAR:?required}`) per repo convention rather
  than secret files — the PG role is read-only and the DB is internal-only.
- Validated locally: `docker compose config` and YAML parse all pass. `promtool`
  and live target/alert verification must run on the host (Docker daemon not on
  the Mac). See `docker/monitoring/README.md` for the verify checklist.
- Follow-up: front ntfy with Caddy as `alerts.home`; consider a relay for prettier
  alert message formatting (currently raw Grafana JSON).

---

## 2026-06-07 — Doc sync: networking reality + Ollama model loader

**Goal:** Bring `AGENTS.md` back in line with the deployed setup and reduce the per-model tuning toll.

**Steps:**
1. Fixed the stale `AGENTS.md` networking docs: it still claimed "all ports bind to `127.0.0.1`," untrue since the rebind of Portainer/Grafana/Prometheus/Ollama to all interfaces. Rewrote the networking rules (subnet route, `*.home` split-DNS, the loopback-vs-all-interfaces split) and added the missing **proxy stack** (Caddy + AdGuard) to the stack overview.
2. Added `docker/ai/load-models.sh` — runs `ollama create` for every `docker/ai/models/*.Modelfile`, rebuilding each tag in place. Adding a tuned model is now "drop a Modelfile, run the loader."

**Notes / next steps:**
- New-service guidance in `AGENTS.md` now says: default to `127.0.0.1`, open to all interfaces + a Caddy `*.home` route only when LAN/tailnet access is needed.

---

## 2026-06-07 — Ollama still slow after the num_thread commit: it was never applied + cold-load tax

**Goal:** The `num_thread 4` Modelfile was committed but Open WebUI was still slow. Find out why.

**Diagnosis:**
1. `ollama show llama3.2:3b --modelfile | grep num_thread` returned **empty** — the *committed* Modelfile had never been applied to the *running* model. Committing the file does nothing; the tag must be rebuilt with `ollama create` on the box.
2. `load-models.sh` couldn't apply it either: Ollama runs as a **Docker container** (`ollama`), not on the LXC PATH, so `pct exec 100 -- ollama …` fails with `Failed to exec "ollama"`. Must go through `docker exec ollama …`.
3. `ollama create -f -` (Modelfile via stdin) is **not supported** on this version — needs a real file path. Used `docker cp` to land the Modelfile in the container, then `ollama create -f /tmp/…`.
4. After rebuild, `ollama show … | grep num_thread` → `PARAMETER num_thread 4`. A timed `ollama run … --verbose`: **eval rate 16.25 tok/s** (generation fixed). But `load duration: 42s` dominated total time — the model reload into RAM.

**Root cause of the *lingering* slowness:**
- Two separate things: (a) the tuned params were never live, and (b) Ollama's default `keep_alive` is 5 min, so after any idle gap the next chat pays a ~40s cold load before the first token — felt as "still slow" even though generation now runs at ~16 tok/s.

**Resolution:**
- Rebuilt the live model in the container (`docker cp` Modelfile → `docker exec ollama ollama create llama3.2:3b -f …`).
- Set `OLLAMA_KEEP_ALIVE: "-1"` on the ollama container in `docker/ai/docker-compose.yml` so the model stays resident (3B ≈ 2-3 GB, fits the 14 GB LXC).
- Rewrote `load-models.sh` to operate on the container (`docker cp` + `docker exec ollama ollama create`), since the CLI isn't on the LXC PATH.
- Open WebUI's per-model `num_thread` left on **Default** (not 0): Default = not sent, so the Modelfile's value wins. `0` would mean "auto-detect" and re-trigger the 16-thread oversubscription.

**Notes / next steps:**
- Standing gotcha: editing a Modelfile is inert until `./docker/ai/load-models.sh` rebuilds the tag *inside the container*. Commit ≠ deploy.
- Apply the keep_alive change: `cd <repo-on-lxc>/docker/ai && docker compose up -d ollama`.

---

## 2026-06-07 — Fixed pathologically slow Ollama (LXC thread oversubscription)

**Goal:** Open WebUI chat responses were extremely slow; find out why and fix it.

**Diagnosis:**
1. Queried the Ollama API directly (`/api/ps`, `/api/generate`). Model `llama3.2:3b` (Q4_K_M) runs **CPU-only** (`size_vram: 0` — expected, no GPU).
2. Benchmarked generation: **~0.5 tok/s** by default — about 30× slower than this CPU should manage for a 3B Q4 model. An 80-token request even timed out.
3. Re-ran with an explicit thread count: `num_thread=4` → **16.1 tok/s**, `num_thread=6` → **17.0 tok/s**. Explicit threads = ~30× faster.

**Root cause:**
- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. It spawns more inference threads than the quota allows; the kernel CFS-throttles them (scheduled → hit quota → stall), so throughput collapses. Capping threads ≤ the quota removes the throttling.

**Resolution:**
- Added `docker/ai/models/llama3.2.Modelfile` (`FROM llama3.2:3b` + `PARAMETER num_thread 4`) — version-controlled, reproducible.
- Apply on the box: `ollama create llama3.2:3b -f docker/ai/models/llama3.2.Modelfile` (rebuilds the same tag in place, so Open WebUI needs no change).
- Chose `num_thread 4` over 6: same throughput (~16 vs 17 tok/s) while leaving 2 cores for the other stacks.
- Documented the constraint as a standing convention in `AGENTS.md` (every new model must pin `num_thread`).

**Notes / next steps:**
- No global Ollama thread env var exists, so this is per-model — repeat the Modelfile pattern for every model added.
- `AGENTS.md` networking rules still say "all ports bind to 127.0.0.1"; that's stale since today's rebind of Portainer/Grafana/Prometheus/Ollama to all interfaces — worth a separate doc cleanup.

---

## 2026-06-07 — Memorable service names (proxy stack: Caddy + AdGuard)

**Goal:** Reach services at memorable, port-free names (`chat.home`, `stats.home`, `apps.home`, `dns.home`) that resolve on any tailnet device, anywhere.

**Steps:**
1. Added a `docker/proxy/` stack: **Caddy** (reverse proxy on `:80`, routes by Host header, `auto_https off` — plain HTTP since Tailscale encrypts) attached to the `core`/`monitoring`/`ai` networks, and **AdGuard Home** (DNS on `:53` + ad-blocking).
2. Configured AdGuard via its install API (admin on `:80` behind Caddy, DNS on `:53`) and added a `*.home → 10.0.0.201` rewrite.
3. Tailscale admin → DNS → **Split DNS**: custom nameserver `10.0.0.201` restricted to domain `home`, so every tailnet device resolves `*.home` via AdGuard (reached over the existing subnet route).

**Issues encountered:**
- **AdGuard setup port 3000 collided with Grafana** (now published on `:3000`). Moved the first-run wizard mapping to `3001`.
- Dropped the original plan to run **Tailscale inside the LXC** — `/dev/net/tun` isn't exposed to the container. Pointing split-DNS at the subnet-routed `10.0.0.201` instead is simpler and needs no Proxmox device config.

**Resolution:**
- Verified end to end: AdGuard resolves all `*.home → 10.0.0.201`, Caddy routes each name to the right service (HTTP 200), and the Mac resolves the names on its own via split-DNS.

**Notes / next steps:**
- AdGuard admin password stored in password manager; reachable at `dns.home`.
- Optional: set AdGuard (`10.0.0.201`) as the router's DHCP DNS so `.home` + ad-blocking apply to *all* home-LAN devices, not just tailnet ones.
- The `3001` setup-wizard port mapping can be removed from the compose now that AdGuard is configured.

---

## 2026-06-07 — Tailscale subnet routing + first stacks brought up

**Goal:** Make the LXC reachable from the MacBook over Tailscale, then clone the repo and bring up the `core`, `monitoring`, and `ai` stacks.

**Steps:**
1. Logged the MacBook into Tailscale (already installed; was logged out) with `--accept-routes`. Tailnet domain `tail58e272.ts.net`; host is `m5.tail58e272.ts.net` (`100.116.69.120`).
2. On the host: enabled IP forwarding persistently (`/etc/sysctl.d/99-tailscale.conf`) and ran `tailscale set --advertise-routes=10.0.0.0/24`. Approved the route + disabled key expiry for `m5` in the admin console. The LXC (`10.0.0.201`) is now reachable from any tailnet device.
3. Installed the MacBook's SSH key on the host (`ssh-copy-id root@10.0.0.200`) for passwordless management via `pct exec 100`.
4. Cloned `brignano/homelab` into the LXC, generated random secrets into `docker/*/.env` (`chmod 600`), and brought up `core` → `monitoring` → `ai`. All 8 containers running.

**Issues encountered:**
- **Open WebUI never started.** The Ollama healthcheck ran `curl`, which isn't in the `ollama/ollama` image (`exec: "curl": not found`), so Ollama never went healthy and Open WebUI (which waits on `service_healthy`) never came up.
- **Services unreachable from the Mac.** Every port was bound to `127.0.0.1` inside the LXC, so the new subnet route still couldn't reach them.

**Resolution:**
- Changed the healthcheck to `ollama list` (in-image binary). Ollama → healthy, Open WebUI started. ([#3](https://github.com/brignano/homelab/pull/3))
- Rebound Portainer, Grafana, Prometheus, and the Ollama API to all interfaces; kept Postgres on `127.0.0.1` (apps use the internal Docker network). Services are now reachable over LAN + tailnet, but not public.
- Pulled `llama3.2:3b` into Ollama so Open WebUI has a model to chat with.

**Notes / next steps:**
- `tunnel` (cloudflared) still not deployed — needs a Cloudflare Zero Trust tunnel token for public access.
- Still pending: DHCP reservation on the router (`84-47-09-86-96-A4` → `10.0.0.200`), Jellyfin media stack.

---

## 2026-06-07 — Bare-metal Proxmox install + Docker LXC provisioned

**Goal:** Stand up the GMKtec M5 Ultra as the Proxmox host and create the privileged Docker LXC per the VM→LXC decision, ending with a working Docker + Compose foundation.

**Steps:**
1. Installed **Proxmox VE 9.2** bare metal, wiping the preinstalled Windows 11. Node FQDN `m5.homelab.lan`, static IPv4 `10.0.0.200/24`, gateway `10.0.0.1`, DNS `1.1.1.1`.
2. Disabled the two enterprise APT repos, added `pve-no-subscription`, ran `apt dist-upgrade` (new kernel `7.0.6-2-pve` + AMD microcode), rebooted onto the new kernel.
3. Installed **Tailscale** on the host (`tailscale up --ssh`); host tailnet IP `100.116.69.120`.
4. Downloaded the `debian-13-standard` LXC template and created **CT 100** (`docker`) via `pct create`: privileged (`--unprivileged 0`), `--features nesting=1`, 14 GB RAM limit, 6 cores, 400 GB thin rootfs on `local-lvm`, static `10.0.0.201/24`, `--onboot 1`.
5. Inside the container: installed **Docker CE 29.5.3** + Compose v2 (`v5.1.4`) via `get.docker.com`. `docker run hello-world` succeeded → Docker-in-LXC via nesting confirmed working.
6. Generated `en_US.UTF-8` locale to clear the perl/locale warnings.

**Issues encountered:**
- **Container had no internet (DNS).** Tailscale rewrote the host's `/etc/resolv.conf` to MagicDNS (`100.100.100.100`); the LXC inherited it via "use host settings," but MagicDNS is unreachable from inside the container (Tailscale only runs on the host). Raw-IP routing worked; name resolution hung.
- **Thin pool overprovisioned.** `pve/data` thin pool is only **~348 GiB**, but the rootfs is provisioned at 400 GiB, and the VG has just 16 GiB free (pool can't auto-extend). Fine for containers/configs (currently <1% used), but the real ceiling is ~348 GiB.
- **Create CT wizard hid the privileged/nesting toggles** (require "Advanced" mode); used `pct create` on the CLI instead.

**Resolution:**
- DNS fixed with `pct set 100 --nameserver 1.1.1.1` (plus a live `echo` to `/etc/resolv.conf` to unblock the running container). Persistence verified — `nameserver: 1.1.1.1` is in the container config, so it survives restarts.
- Thin pool: left as-is (thin provisioning is the chosen tradeoff). Keep large media (Jellyfin) off this pool or monitor `lvs` pool usage so actual data stays under ~348 GiB.

**Notes / next steps:**
- Add a DHCP reservation on the Xfinity router (`10.0.0.1`) for the host NIC MAC `84-47-09-86-96-A4` → `10.0.0.200`.
- Decide how Tailscale reaches the LXC services: host subnet router (`--advertise-routes=10.0.0.0/24`) vs. Tailscale inside the LXC vs. a Tailscale sidecar container.
- Clone `brignano/homelab` into the container and bring up stacks in order: `core` → `monitoring` → `ai` → `tunnel`; populate `.env` from `.env.example`; supply the Cloudflare Tunnel token.
- Fill in the Tailscale hostname placeholder in `README.md`.

---

## 2026-06-07 — Switched planned Docker host from VM to LXC

**Goal:** Pick the right host type for Docker workloads on the 16 GB / 512 GB GMKtec M5 Ultra without starving Proxmox.

**Steps:**
1. Compared three host options for the Docker workload.
2. Selected a Proxmox LXC container and updated `AGENTS.md` (`## LXC Configuration`) accordingly.

**Options considered:**
- **Proxmox + VM:** Strong isolation, but the 12 GB RAM reservation is a hard carve-out — on a 16 GB host that left Proxmox only ~2 GB of headroom.
- **Proxmox + LXC (chosen):** RAM is a limit rather than a hard reservation and disk is thin-provisioned, so the host keeps real headroom while still running under Proxmox.
- **Bare-metal Debian:** Maximum performance, but loses Proxmox snapshots/management and the ability to run other VMs/containers on the box.

**Resolution:**
- Going with a **Proxmox LXC container**. The 12 GB VM reservation left Proxmox only ~2 GB on the 16 GB host; an LXC's 14 GB limit plus thin-provisioned disk leaves usable host headroom.

**Notes / next steps:**
- The LXC must be **privileged** with **`nesting=1`** enabled (required for Docker-in-LXC).
- Workload runs identically inside the LXC — no changes to compose files or the bootstrap script.

---

## 2026-06-06 — Pre-provisioning hardening and tooling

**Goal:** Make initial server setup smoother before the Proxmox VM is provisioned.

**Steps:**
1. Added healthcheck to `ollama` service in `docker/ai/docker-compose.yml` (polls `http://localhost:11434/` every 30s).
2. Updated `open-webui` `depends_on` to use `condition: service_healthy` so it waits for Ollama to be ready.
3. Added `## VM Configuration` section to `AGENTS.md` documenting planned Proxmox VM specs (12GB RAM, 6 cores, 400GB VirtIO disk, Debian).
4. Created `.claude/commands/preflight.md` — checks `.env` presence, required vars, external Docker networks, and Tailscale connectivity before any stack is brought up.
5. Created `.claude/commands/bootstrap-stack.md` — brings stacks up in dependency order (core → monitoring → ai → tunnel) with health polling between each step.
6. Added `## Tailscale Hostname` placeholder to `README.md` to fill in post-provisioning.

**Notes / next steps:**
- Provision Debian VM in Proxmox with the specs in `AGENTS.md`.
- Connect VM to Tailscale, then fill in hostname in `README.md`.
- Run `/preflight` before first `docker compose up` on the new VM.

---

## 2026-06-06 — Initial repo created

**Goal:** Scaffold the homelab repository and document the hardware.

**Steps:**
1. Created GitHub repo `brignano/homelab`.
2. Added Docker Compose stacks for monitoring, core services, and local AI.
3. Added `scripts/bootstrap-docker.sh` for fresh host setup.

**Notes / next steps:**
- Install Proxmox VE on the GMKtec M5 Ultra.
- Provision a Debian VM inside Proxmox for Docker workloads.
- Run `bootstrap-docker.sh` on that VM.
- Connect host to Tailscale before exposing any service ports.
