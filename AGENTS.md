# Homelab — Claude Code Context

## Hardware
- **Device:** GMKtec M5 Ultra
- **CPU:** AMD Ryzen 7 7730U (8c/16t) — CPU-only, no discrete GPU
- **RAM:** 16 GB DDR4
- **Storage:** 512 GB NVMe

## LXC Configuration

Planned Proxmox LXC container for Docker workloads:

| Parameter | Value |
|-----------|-------|
| RAM | 14 GB (limit) |
| vCPUs | 6 cores |
| Disk | 400 GB (thin-provisioned) |
| Disk bus | VirtIO |
| Network | VirtIO |
| OS | Debian (latest stable) |
| Privilege | Privileged container |
| Features | `nesting=1` (required for Docker-in-LXC) |

**Rationale:** LXC RAM is a limit, not a hard carve-out, and the disk is thin-provisioned, leaving host headroom on a 16 GB / 512 GB box.

## Stack overview

| Service | Stack file | Network exposure |
|---------|-----------|-----------------|
| Proxmox VE | bare-metal hypervisor | Tailscale only (host mgmt) |
| Portainer | `docker/core/` | LAN + tailnet (`apps.home`) |
| PostgreSQL 16 | `docker/core/` | internal only (`127.0.0.1` + Docker network) |
| Prometheus | `docker/monitoring/` | LAN + tailnet |
| Grafana | `docker/monitoring/` | LAN + tailnet (`stats.home`) |
| node-exporter | `docker/monitoring/` | internal |
| cAdvisor | `docker/monitoring/` | internal |
| pve-exporter | `docker/monitoring/` | internal (Proxmox API) |
| postgres-exporter | `docker/monitoring/` | internal (+ `core_core`) |
| blackbox-exporter | `docker/monitoring/` | internal (+ `core`/`ai`/`proxy`) |
| Loki | `docker/monitoring/` | internal (log store) |
| Alloy | `docker/monitoring/` | internal (log shipper) |
| Ollama | `docker/ai/` | LAN + tailnet |
| Open WebUI | `docker/ai/` | LAN + tailnet (via Caddy, `chat.home`) |
| assistant (Discord bot) | `docker/assistant/` | **outbound only** — no ports, no Caddy route |
| Caddy | `docker/proxy/` | LAN + tailnet (`:80`, routes `*.home`) |
| AdGuard Home | `docker/proxy/` | LAN + tailnet (`:53` DNS, `dns.home`) |
| cloudflared | `docker/tunnel/` | public via Cloudflare Zero Trust (not yet deployed) |
| Sablier | `docker/desktops/` | internal (on-demand engine for desktops) |
| Kali webtop | `docker/desktops/` | LAN + tailnet (via Caddy, `kali.home`, on-demand) |
| Jellyfin | planned | Cloudflare Tunnel |

## Networking rules
- **Remote access:** the Proxmox host runs Tailscale as a subnet router advertising `10.0.0.0/24`, so tailnet devices reach the LXC and its services at `10.0.0.201`. (Tailscale runs on the host, not in the LXC — `/dev/net/tun` isn't exposed to the container.)
- **Service names (`*.$HOMELAB_DOMAIN`):** a real domain with real Let's Encrypt certificates. A single wildcard DNS record (`*.<base> → 10.0.0.201`, Cloudflare proxy **off**) covers every service; Caddy routes by Host header. **These are not public** — the name resolves publicly but the address is private, so only LAN/tailnet devices can reach it. Certificates come via the ACME **DNS-01** challenge (Caddy writes a TXT record to the zone), so no inbound connection is ever needed. Adding a service needs only a Caddyfile block — the wildcard already covers it.
- **Legacy `*.home`:** still served, now as redirects to the real names. AdGuard keeps the `*.home → 10.0.0.201` rewrites for these; once nothing uses them, both can go. AdGuard itself stays for ad blocking and as the tailnet resolver.
- **Port bindings:** admin UIs and the AI/metrics services (Portainer, Grafana, Prometheus, Ollama) bind to all interfaces — reachable over LAN + tailnet, **not** public. PostgreSQL stays on `127.0.0.1` (apps reach it over the internal Docker network).
- **Public access:** only via Cloudflare Tunnel (`cloudflared`), reserved for app previews and Jellyfin (planned) — not yet deployed. `cloudflared` joins `core_core` and `ai_ai` so it can proxy to other stacks without opening host ports.

## Ollama / AI tuning (LXC constraint)

- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. Left at its default it oversubscribes the quota, the kernel CFS-throttles the inference threads, and generation collapses to ~0.5 tok/s.
- **Every model must pin `num_thread` ≤ the LXC core count** via a Modelfile in `docker/ai/models/`. Use `num_thread 4` (matches 6's ~16 tok/s while leaving 2 cores for other stacks).
- Apply all tuned models at once with `docker/ai/load-models.sh` (runs `ollama create` for every `models/*.Modelfile`, rebuilding each tag in place — no Open WebUI change needed). For a single model: `ollama create <tag> -f docker/ai/models/<name>.Modelfile`.
- There is no global Ollama thread env var, so this is per-model: adding a model means dropping a Modelfile in `docker/ai/models/` and re-running the loader.
- **Never send `num_thread` as a request-time option.** Options passed on `/api/generate` override the Modelfile, so doing so silently undoes the pin above. The `assistant` stack deliberately omits it.
- **One generation at a time.** `num_thread 4` of the 6-core quota means concurrent generations contend for the same cores and memory bandwidth — both crawl and the other stacks starve. Anything driving Ollama must serialize its requests; `docker/assistant/app/jobqueue.py` is the reference implementation (single worker, interactive prioritised over scheduled).

## Repo conventions
- Each Docker stack lives in its own `docker/<name>/` directory with its own `docker-compose.yml` and `.env.example`.
- Never commit `.env` files — only `.env.example` with placeholder values.
- Secrets that must exist use `${VAR:?required}` syntax so Compose fails loudly if unset.
- Document every significant change in `docs/setup-log.md` using the template at the top of that file.
- New services default to `127.0.0.1:<port>` bindings. Bind to all interfaces only when the service must be reached over LAN/tailnet, and prefer fronting it with Caddy for a `*.home` name rather than exposing a raw port.
- **Anything with a visual choice in it follows [brignano/design](https://github.com/brignano/design)** — the shared design system, which already names `homelab` as a tool-tier consumer. Take colour from its tokens rather than picking one: identity is its `mark` hue (larch amber), and it only ever inks a graphic, never a control. `docker/dashboard/icons/` is the worked example, including why a favicon is the one place its "never hardcode a hex" rule cannot hold.
- **Page icons follow the standard `life` sets** (`scripts/gen-icons.mjs` there, `scripts/gen-dashboard-icons.py` here): the SVG is the real mark — transparent ground, `prefers-color-scheme` step, because a filled tile disappears against a tab strip that matches it — and the rasters (`.ico`, `apple-touch`, 32px PNG) take an ink ground for the surfaces that put the icon somewhere we do not control. Safari probes `/favicon.ico` and `/apple-touch-icon.png` at the root without reading markup, so those paths have to answer; the Caddyfile points them at the mark.
- **Third-party marks stay as their projects draw them.** The dashboard's tile icons are vendored into `docker/dashboard/icons/` by `scripts/update-tile-icons.sh` rather than fetched from a CDN by the browser — a dashboard that needs the internet to render is useless on the day the internet is what broke. Adding a service means `icon: /icons/<name>.svg` and a run of that script; CI fails if the file is not there.
- **The dashboard wears that system at runtime.** `scripts/update-design-tokens.sh` vendors `tokens.css` (and Geist) from npm into `docker/dashboard/assets/` and generates the RGB-channel palette Homepage themes itself from; `config/custom.css` bridges the rest through Tailwind v4's own theme variables, never through class names. Update it by re-running the script, not by editing generated files or typing a colour — CI diffs the generated palette against the vendored tokens.
- **Docs vs. design specs:** `docs/` holds operational/reference docs (`setup-log.md`, strategy, runbooks — *how the system works now*). Design specs/TSDs live in `docs/design/` (`tsd-*.md`, all lifecycle stages — the `Status:` field tracks maturity; files are not moved when shipped). Homelab-specific specs live here, not in the `ideas` repo (which is greenfield products/apps only).

## Alerting

**Nothing that runs on CT 100 can tell you CT 100 is down.** Grafana and the
assistant bot both live on the machine they watch, so a dead host is silent.
That gap is closed from outside by `scripts/heartbeat.sh` — a dead man's switch
that pings Healthchecks.io from cron, so *silence* is the signal. See
[`docs/design/tsd-alerting-off-box.md`](docs/design/tsd-alerting-off-box.md).

- `#alerts` in Discord is fed by **webhooks only** (Grafana + Healthchecks),
  never by the assistant bot — routing through the bot would reintroduce the
  dependency the design removes.
- **Discord is the only delivery path.** ntfy was removed once Discord covered
  the same ground; `DISCORD_ALERT_WEBHOOK` is therefore `:?required` in
  `docker/monitoring/docker-compose.yml` so the stack cannot start unable to
  page you.
- When adding an alert path, ask which failures it can *not* report, and where
  that one is observed from.
- **Anything scheduled needs a dead man's switch, including the scheduler.** A
  job that only speaks when something is wrong cannot report never having run:
  `repo-sync.sh` was silent for three weeks because its cron entry had never
  been installed, and the box drifted three weeks behind `main` while every
  signal said fine. It now pings `HEALTHCHECKS_REPO_SYNC_URL` on every run, and
  `scripts/install-cron.sh` makes installing the schedule a command rather than
  a ritual (`--check` reports what is missing). `pg-backup.sh` was the last job
  without one; it now pings `HEALTHCHECKS_PG_BACKUP_URL`, and every job's
  crontab entry is a metric (`homelab_cron_job_installed`), so an uninstalled
  job alerts rather than waiting to be noticed.
- **A job that detects something must leave the result behind, not just report
  it.** Discord answers "does this need me now" and is read once; a time series
  answers "is it still true", "how long has it been true" and "did the fix
  work", which is what you want when the 4am message has scrolled away. Every
  scheduled script writes what it already computed to
  `/var/lib/node_exporter/textfile` via `scripts/metrics.sh` — repo drift,
  config drift, container inventory, backup age. Emitting a metric must never be
  able to fail the job: every function there degrades to a no-op.
- **An empty dashboard and a healthy lab look identical.** Three of the seven
  dashboards here had been blank for an unknown length of time — they were built
  on Angular panels that Grafana 12 removed, and nothing errors when a panel
  plugin is missing. Same shape as a panel querying a metric nobody writes, or a
  probe pointed at a 404. `scripts/check-observability.sh` fails CI on all
  three, and anything the lab relies on is a committed dashboard in
  `grafana/dashboards/homelab/`, not a grafana.com ID pasted into a fetch
  script.
- **Blackbox probes must target a path the service answers 2xx on.** The
  `http_2xx` module treats anything else — including a 404 or a redirect — as
  down. This matters most for Caddy, which routes by Host header and sees the
  container name `caddy` from inside Docker: it serves `/health` for the probe
  and 404s everything else, and `scripts/check-probes.sh` keeps the two files in
  step.
- **Group notifications by `instance`, not by `alertname` alone.** Grouping is
  what decides how loud `#alerts` is: a notification group is re-sent whenever
  its membership changes, throttled to `group_interval`, so coarse grouping
  means one flapping probe re-announces every other firing alert alongside it.
  See `grafana/provisioning/alerting/policies.yml`.
- **The `summary` annotation is the alert.** Every rule gets one, written as a
  sentence with the value already interpolated ("docker-lxc /var is 87% full"),
  because that is the whole Discord message — `templates.yml` renders a heading,
  those sentences, and one line of timing, and drops Grafana's default dump of
  every label, value and URL. A rule with no summary falls back to its own name,
  which is readable but says nothing; write the sentence.
- **A Discord message's content renders ABOVE its embeds.** Grafana's Discord
  notifier puts `message` in the content and `title` in an embed, so a title set
  there arrives *under* the body — which is why the heading is the first line of
  the message and the embed title is left to be the one clickable element.
  Anything this repo sends itself (`repo-sync.sh`) uses an embed for the whole
  report instead, where title and description render in the order written.
- **`instance` is not identity.** Every alert derived from a textfile metric —
  stack drift, config drift, backup age, cron — carries
  `instance=node-exporter:9100`, because that is only where the metric was
  scraped. It is also what `policies.yml` groups on, so those alerts share a
  group and arrive as one message listing each subject. Name the subject in the
  summary (`{{ $labels.stack }}`, `{{ $labels.container }}`), never rely on
  `instance` to say what broke.
- **Before re-diagnosing an alert you already fixed, check it is deployed.**
  `prometheus.yml`, the Caddyfile and `grafana/provisioning/` are all
  bind-mounted, so `git pull` changes the files while the containers keep
  serving the old config — the repo looks right, CI is green, and Discord keeps
  firing. `scripts/probe-status.sh` answers this from the box in one command.
- **A container that owns a bind-mounted config directory writes to it.**
  Homepage drops 0-byte skeletons (`custom.css`, `custom.js`, `docker.yaml`, …)
  into `docker/dashboard/config/` whenever they are missing. The day the repo
  starts tracking one of those paths, `git pull` refuses to overwrite the empty
  local copy and the box stops pulling *entirely* — every stack, over a file
  with nothing in it. `repo-sync.sh` clears that narrow case (untracked, empty,
  and added by an incoming commit) and reports it; anything with content in it
  still stops the pull.
- **A config bind-mounted as a single file needs the container *recreated*, not
  restarted or reloaded.** Docker pins a file mount to an inode at container
  creation, and git replaces files rather than editing them, so after a
  `git pull` the container is mapped to the old unlinked copy — and a reload
  returns 200 while changing nothing. Use
  `docker compose up -d --force-recreate <svc>`. Mounting a whole *directory*
  avoids this (that is why `grafana/provisioning/` only needs a restart), so
  prefer a directory mount for new config where the directory holds no secrets.

## Local LLM usage

The deciding question is **is anyone waiting on the answer?** — synchronous work
goes to Claude, asynchronous work to the local 3B. That's why local jobs run
through the Discord bot in `docker/assistant/` (push) rather than Open WebUI
(pull). See [`docs/ai-strategy.md`](docs/ai-strategy.md) and
[`docs/design/tsd-local-llm-discord-jobs.md`](docs/design/tsd-local-llm-discord-jobs.md).

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

## Planned / proposals (not yet deployed)
- [`docs/design/tsd-backups-and-monitoring.md`](docs/design/tsd-backups-and-monitoring.md) — backups + restore testing + job monitoring. **⏸ Parked** on a ~$50 USB SSD. ⚠️ **The lab currently has NO backups** — a disk/CT loss is unrecoverable. Zero-cost stopgaps are live: configs-in-git, and nightly `pg_dumpall` via [`scripts/pg-backup.sh`](scripts/pg-backup.sh) (cron 02:00). The **job monitoring** half of the spec shipped early on 2026-09-19 — the dump now pings Healthchecks and publishes age/size/result as metrics, with alerts on the Discord `#alerts` webhook — because it was cheap and did not need the hardware. The backup half is what is still parked: monitoring a job is not the same as having a backup you can restore.
- [`docs/design/tsd-self-healing-remediation.md`](docs/design/tsd-self-healing-remediation.md) — future auto-remediation layer; depends on the above.

## Custom commands
These slash commands are available in `.claude/commands/`:

| Command | Purpose |
|---------|---------|
| `/deploy` | Pull onto CT 100 and make the containers match, including the ones `up -d` leaves on stale config |
| `/preflight` | Check `.env`, required vars, networks and Tailscale before bringing a stack up |
| `/bootstrap-stack` | Bring every stack up in dependency order |
| `/new-service` | Scaffold a new Docker Compose stack |
| `/log-entry` | Write a dated entry to docs/setup-log.md |
| `/debug-container` | Diagnose a failing or unhealthy container |
| `/expose-service` | Add a service to the Cloudflare Tunnel config |

`/deploy` detects which containers are serving a stale single-file bind mount
rather than naming them, because a hardcoded recreate list is a second copy of
something Docker already knows — and one was got wrong within a day of being
written, in a way that would have paged `#alerts` about the house having no DNS.

## How to help me
- When adding a new service, follow the existing stack pattern: separate directory, `.env.example`, `127.0.0.1` port bindings by default (open to all interfaces + a Caddy `*.home` route only if it needs LAN/tailnet access), named volume, restart policy.
- When I describe a problem with a container, check `docker logs`, `docker inspect`, and the compose file before suggesting fixes.
- When writing setup log entries, use the template in `docs/setup-log.md` and today's date.
- Prefer `docker compose` (v2) over `docker-compose` (v1).
- Don't suggest exposing admin services (Portainer, Grafana, PostgreSQL) via Cloudflare Tunnel.

## Token usage rules

- Prefer reading only the smallest relevant files before proposing changes.
- Do not scan the whole repo unless explicitly asked.
- Summarize findings before making large edits.
- For broad repo questions, first inspect README.md, AGENTS.md, and docs/setup-log.md.
- Use local Ollama/Open WebUI for low-risk, high-token tasks:
  - repo summaries
  - log summaries
  - documentation drafts
  - boilerplate
  - first-pass scripts
  - test scaffolding
- Use Claude for:
  - architecture decisions
  - multi-file edits
  - hard debugging
  - security-sensitive changes
  - final review
- Before starting a large task, produce a short plan and list the files likely needed.
- Avoid repeated full-file reads when a targeted grep/search is sufficient.