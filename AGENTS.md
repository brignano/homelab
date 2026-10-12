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

## Disk

**CT 100's free-space number is optimistic, and by design.** The rootfs is
provisioned at 400 GB on a `pve/data` thin pool of roughly **348 GiB**, with
~16 GiB free in the VG — so the pool cannot auto-extend. The guest can believe
it has room the pool does not have, and the pool is the ceiling that actually
exists. `node_filesystem_avail_bytes` (and therefore `hl-disk-full` and
`hl-disk-filling`) measures the guest's view: treat every disk alert here as
arriving **later** than it should, never earlier.

The pool is watched by `hl-thinpool-full`, which fires above 80%, and
`scripts/health.sh` reports it on demand. Both read `pve_disk_usage_bytes` /
`pve_disk_size_bytes` with `id="storage/<node>/local-lvm"`, names confirmed
against the running exporter on 2026-10-10. If the exporter renames them, the
alert goes quiet without erroring, the same way the alert described at the top
of `rules.yml` did. `health.sh` printing "thin pool usage unknown" is what
reveals it.

**The pool does not get space back unless someone trims it.** When the guest
deletes a file, the pool still counts those blocks as used until they are
discarded, and Proxmox does not trim containers by itself. Image prunes and
log rotation free a lot, so the pool climbs even while the guest holds steady.
On 2026-10-10 the guest read 18% and the pool 52%, and one `pct fstrim 100`
brought the pool to 16%. The fix is a weekly cron job on the **Proxmox host**
(`pct` does not exist inside CT 100, so `install-cron.sh` cannot install it).
It runs at 05:00 Sunday, an hour after `repo-sync.sh` prunes images at 04:00,
so it hands back what the prune just freed. It lives in its own `/etc/cron.d`
file rather than root's crontab, so it can be written and checked with one
command and needs no editor. Installed on `m5` on 2026-10-10:

```bash
# on the Proxmox host. /etc/cron.d lines name the user, hence "root".
echo '0 5 * * 0 root /usr/sbin/pct fstrim 100 >/dev/null 2>&1' > /etc/cron.d/fstrim-ct100

cat /etc/cron.d/fstrim-ct100                                   # is it there?
journalctl -u cron --since "last sunday" | grep fstrim         # did it run?
```

If `health.sh` shows the pool well above the guest, check that file still
exists before looking for anything else. `crontab -l` will not show it.

```bash
# pve-exporter publishes no host port — ask it from inside the network.
docker exec prometheus wget -qO- \
  'http://pve-exporter:9221/pve?module=default&target=10.0.0.200' \
  | grep -i 'storage.*local-lvm'
```

**Three things grow. Two of them now have a ceiling** (a fourth, media, lives on its own drive):

- **Prometheus** — 30d (`--storage.tsdb.retention.time`), **Loki** — 30d
  (`retention_period`). Both are at steady state.
- **Images.** Every `docker compose pull` leaves the image it replaced on disk,
  untagged and unreferenced. Nightly auto-pull (added 2026-09-19) turned that
  into a ratchet. `repo-sync.sh` now runs `docker image prune -f` and a
  week-old `builder prune` after its pulls, and reports what it freed.
  **Never `image prune -a` on this box** — Sablier scales the Kali webtop to
  zero, so `-a` deletes a GB-scale image that has no container on it by design.
- **Media.** Jellyfin's library is on its own 5 TB drive, never the
  backup drive, and its mount point on CT 100 carries `backup=0` — otherwise the
  nightly vzdump tries to copy the library and fails. See
  `docs/design/tsd-backups-and-monitoring.md`, Phase 1 step 7.
- **Container logs.** `json-file` is Docker's default driver and its default
  max-size is *unlimited*; `restart: unless-stopped` means nothing truncates
  them either. Capped daemon-wide at 10m x 3 in `/etc/docker/daemon.json`
  (written by `bootstrap-docker.sh`), not per-stack, because the containers
  that need it most are the ones no compose file of ours starts. Shipping logs
  to Loki does **not** bound them: Alloy reads through the Docker API, which
  does not truncate what it reads.

**The log cap applies at container create time.** A daemon restart does not
retro-fit a running container, and neither does `docker compose restart` —
existing containers keep their unlimited driver and their existing log file
until they are **recreated**, and the file on disk survives even that. On a
box that predates the cap, the one-time apply is:

```bash
du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail
systemctl restart docker
for f in docker/*/docker-compose.yml; do docker compose -f "$f" up -d --force-recreate; done
```

`proxy` is on `HL_NO_AUTOHEAL` for a reason and a recreate of it takes the
household's DNS down for a few seconds — do it deliberately and verify with two
`dig`s, not one (see `.claude/commands/deploy.md`).

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
| concierge (Claude Agent SDK) | `docker/concierge/` | **outbound only** — no ports, no Caddy route; reached by the assistant over `assistant_concierge` (bearer token); also on `proxy_proxy` to reach Radarr/Sonarr, where the token is what keeps other containers out; reads HA and Prometheus through the host |
| Caddy | `docker/proxy/` | LAN + tailnet (`:80`, routes `*.home`) |
| AdGuard Home | `docker/proxy/` | LAN + tailnet (`:53` DNS, `dns.home`) |
| cloudflared | `docker/tunnel/` | public via Cloudflare Zero Trust (not yet deployed) |
| Sablier | `docker/desktops/` | internal (on-demand engine for desktops) |
| Kali webtop | `docker/desktops/` | LAN + tailnet (via Caddy, `kali.home`, on-demand) |
| Home Assistant | `docker/homeassistant/` | LAN + tailnet: **host networking** (`:8123` direct, and via Caddy at `ha.$HOMELAB_DOMAIN`), for HomeKit/Matter/discovery. Reached by other containers as `host.docker.internal:8123`. |
| Matterbridge | `docker/matterbridge/` | **host networking** for Matter: UDP 5540 (LAN + tailnet, authenticated), mDNS 5353 (LAN), needs IPv6. Web UI on `127.0.0.1:8283` only, no Caddy route, opened over SSH. Offers HA entities labelled `Alexa` to an Echo |
| Jellyfin | `docker/jellyfin/` (running since 2026-10-11) | LAN + tailnet (via Caddy, `jellyfin.$HOMELAB_DOMAIN`) — **not** Cloudflare Tunnel; joins `proxy_proxy`; iGPU via `dev0`; library read-only on its own drive (`backup=0`) |
| gluetun (Proton VPN) | `docker/downloads/` (built; waits on `/dev/net/tun` + key) | **no ports**; WireGuard tunnel out only; `/dev/net/tun` via `dev1`. Its network namespace is qBittorrent's only network |
| qBittorrent | `docker/downloads/` | LAN + tailnet (via Caddy, `torrent.$HOMELAB_DOMAIN` → `gluetun:8080`); inside gluetun's namespace, so offline whenever the VPN is |
| Sonarr / Radarr / Prowlarr | `docker/downloads/` | LAN + tailnet (via Caddy, `sonarr.` / `radarr.` / `prowlarr.`); join `proxy_proxy`; hardlink downloads into Jellyfin's library |
| FlareSolverr | `docker/downloads/` | internal (`flaresolverr:8191` on `proxy_proxy`, no route); Prowlarr's Cloudflare-challenge solver, outside the VPN |

## Networking rules
- **Remote access:** the Proxmox host runs Tailscale as a subnet router advertising `10.0.0.0/24`, so tailnet devices reach the LXC and its services at `10.0.0.201`. (Tailscale runs on the host, not in the LXC. `/dev/net/tun` is passed in as `dev1` only for gluetun, `docker/downloads/`; Tailscale stays on the host.)
- **Service names (`*.$HOMELAB_DOMAIN`):** a real domain with real Let's Encrypt certificates. A single wildcard DNS record (`*.<base> → 10.0.0.201`, Cloudflare proxy **off**) covers every service; Caddy routes by Host header. **These are not public** — the name resolves publicly but the address is private, so only LAN/tailnet devices can reach it. Certificates come via the ACME **DNS-01** challenge (Caddy writes a TXT record to the zone), so no inbound connection is ever needed. Adding a service needs only a Caddyfile block — the wildcard already covers it.
- **Legacy `*.home`:** still served, now as redirects to the real names. AdGuard keeps the `*.home → 10.0.0.201` rewrites for these; once nothing uses them, both can go. AdGuard itself stays for ad blocking and as the tailnet resolver.
- **Port bindings:** admin UIs and the AI/metrics services (Portainer, Grafana, Prometheus, Ollama) bind to all interfaces — reachable over LAN + tailnet, **not** public. Home Assistant runs on **host networking** (HomeKit/discovery need LAN multicast), so its `:8123` is on all interfaces too; hand out `ha.$HOMELAB_DOMAIN`, not the plain-HTTP port. Matterbridge is host-networked for the same reason, but its web UI binds to `127.0.0.1` only: it can install npm packages as root and authenticates by client IP, so behind Caddy one login would unlock it for everyone (`docker/matterbridge/README.md` → "The web UI"). PostgreSQL stays on `127.0.0.1` (apps reach it over the internal Docker network).
- **Public access:** only via Cloudflare Tunnel (`cloudflared`), reserved for app previews — not yet deployed. **Not for Jellyfin:** Cloudflare's terms require non-Enterprise plans to use paid products (Stream) to serve video through its network. Whether a personal tunnel counts is disputed, but enforcement would land on the account that also serves `life` and `hoststats`. Jellyfin is reached over Tailscale like every other service here. `cloudflared` joins `core_core` and `ai_ai` so it can proxy to other stacks without opening host ports.

## Ollama / AI tuning (LXC constraint)

- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. Left at its default it oversubscribes the quota, the kernel CFS-throttles the inference threads, and generation collapses to ~0.5 tok/s.
- **Every model must pin `num_thread` ≤ the LXC core count** via a Modelfile in `docker/ai/models/`. Use `num_thread 4` (matches 6's ~16 tok/s while leaving 2 cores for other stacks).
- **Never send `num_thread` as a request-time option.** Options passed on `/api/generate` override the Modelfile, so doing so silently undoes the pin above. The `assistant` stack deliberately omits it.
- **One generation at a time.** `num_thread 4` of the 6-core quota means concurrent generations contend for the same cores and memory bandwidth — both crawl and the other stacks starve. Anything driving Ollama must serialize its requests; `docker/assistant/app/jobqueue.py` is the reference implementation (single worker, interactive prioritised over scheduled).

Adding or reloading a model (`docker/ai/load-models.sh`) is in
[`.claude/skills/local-llm/SKILL.md`](.claude/skills/local-llm/SKILL.md).

## Repo conventions
- Each Docker stack lives in its own `docker/<name>/` directory with its own `docker-compose.yml` and `.env.example`.
- Never commit `.env` files — only `.env.example` with placeholder values.
- Secrets that must exist use `${VAR:?required}` syntax so Compose fails loudly if unset.
- Document every significant change in `docs/setup-log.md` using the template at the top of that file.
- New services default to `127.0.0.1:<port>` bindings. Bind to all interfaces only when the service must be reached over LAN/tailnet, and prefer fronting it with Caddy for a `*.home` name rather than exposing a raw port.
- **Anything with a visual choice in it follows [brignano/design](https://github.com/brignano/design)** — never pick a colour. The rules for icons, favicons, the app manifest, tile icons and design tokens are in [`.claude/skills/dashboard-visuals/SKILL.md`](.claude/skills/dashboard-visuals/SKILL.md); read it before touching `docker/dashboard/` or adding a service tile.
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
- **Anything scheduled needs a dead man's switch, including the scheduler.** A
  job that only speaks when something is wrong cannot report never having run.
  Ping Healthchecks through `scripts/healthchecks.sh`, write results through
  `scripts/metrics.sh`, and install the schedule with `scripts/install-cron.sh`.

The rest — arming a ping URL, metrics, dashboards, probes, grouping, the
`summary` annotation, how Discord renders a message — is in
[`.claude/skills/alerting/SKILL.md`](.claude/skills/alerting/SKILL.md). Read it
before adding or changing an alert, probe, dashboard, metric or scheduled job.

## Config mounts

**A config bind-mounted as a single file needs the container *recreated*, not
restarted or reloaded.** Docker pins a file mount to an inode at container
creation, and git replaces files rather than editing them, so after a
`git pull` the container is mapped to the old unlinked copy — and a reload
returns 200 while changing nothing. Use
`docker compose up -d --force-recreate <svc>`. Mounting a whole *directory*
avoids this (that is why `grafana/provisioning/` only needs a restart), so
prefer a directory mount for new config where the directory holds no secrets.

**A directory mount fixes the inode problem and nothing else — the process
still has to be replaced.** Compose decides recreates by hashing the *service
definition*; bytes behind a mount are not in that hash and cannot be, so
`up -d` on a pinned image with an unchanged compose file exits 0 having
replaced nothing. Whether that matters depends on when the service reads the
files: Grafana and Homepage read theirs once at boot, so a change is invisible
until the container restarts. Homepage is the sharpest case — Next.js
enumerates `/app/public` *once at startup* (`setupFsCheck`; the refresh path
is behind `if (opts.dev)`) and matches every later request against that frozen
list, so a **new filename** under `docker/dashboard/icons/` is a 404 no matter
how current the mount is, while an edit to a file already there is served
fine. That is how the dashboard's mark shipped, passed CI and was never served
once. Ask what reads the file and when, not just how it is mounted.

## Local LLM usage

The deciding question is **is anyone waiting on the answer?** — synchronous work
goes to Claude, asynchronous work to the local 3B. That's why local jobs run
through the Discord bot in `docker/assistant/` (push) rather than Open WebUI
(pull). See [`docs/ai-strategy.md`](docs/ai-strategy.md) and
[`docs/design/tsd-local-llm-discord-jobs.md`](docs/design/tsd-local-llm-discord-jobs.md).

How the bot works (`guild.yml`, `#chat` memory and scope, live readings by
injection) and the two rules for extending it are in
[`.claude/skills/local-llm/SKILL.md`](.claude/skills/local-llm/SKILL.md).

## Planned / proposals (not yet deployed)
- [`docs/design/tsd-backups-and-monitoring.md`](docs/design/tsd-backups-and-monitoring.md) — backups + restore testing + job monitoring. **🚧 Phases 0–1 running.** Since 2026-10-11 a nightly 02:30 `vzdump` images all of CT 100 (every volume and `.env`) to the 2 TB LUKS drive on the host, verified with `zstd -t` and watched by Healthchecks. ⚠️ **That copy is on the same desk as the original** — fire, theft or a surge takes both — and it has **never been restored** (the drill, Phase 1 step 6, is still to do). Offsite (restic → B2, Phase 2) is built and waits on the B2 account. The media drive is `backup=0` and has no copy by design.
- [`docs/design/tsd-jellyfin.md`](docs/design/tsd-jellyfin.md) — **✅ deployed 2026-10-11.** Running in `docker/jellyfin/` with VA-API transcoding (verified ~40x real time); library on the 5 TB drive at `/media` (`backup=0`). It still refuses to start without the GPU and the media drive, on purpose.
- [`docs/design/tsd-media-downloads.md`](docs/design/tsd-media-downloads.md) — **🚧 built 2026-10-11** in `docker/downloads/`: gluetun (Proton VPN, port forwarding) with qBittorrent inside its namespace, plus Sonarr, Radarr and Prowlarr hardlinking into Jellyfin's library. **Never restart or recreate gluetun alone**: qBittorrent keeps the dead namespace and loses its network; recreate the stack. Steps: `docker/downloads/README.md`.
- [`docs/design/tsd-photo-library.md`](docs/design/tsd-photo-library.md) — **📝 draft.** A nightly, download-only `icloudpd` mirror of iCloud Photos onto the 5 TB drive (the second copy; iCloud stays primary and offsite), plus a Mac-side cleanup that reads Apple's own quality scores and *proposes* deletions into review albums. No script ever deletes a photo.
- [`docs/design/tsd-self-healing-remediation.md`](docs/design/tsd-self-healing-remediation.md) — future auto-remediation layer; depends on the above.
- [`docs/design/tsd-personal-assistant.md`](docs/design/tsd-personal-assistant.md) — **🚧 phases 1–4 built** (calendar, mail metadata and Home Assistant states in the digest; the concierge answering in `#ask`, proposing calendar events, Gmail drafts, allowlisted scenes and movie/show downloads that run only on a ✅; see `docker/assistant/README.md` → "Today", `docker/homeassistant/README.md` and `docker/concierge/README.md`). Personal assistant in two lanes: *watch* (calendar, mail metadata, Home Assistant state into the digest — rendered by code; text strangers wrote is never shown to the model) and *act* (`docker/concierge/`, Claude Agent SDK behind the bot in `#ask`, every write approved in Discord with its literal arguments). Locks, alarm and garage are never exposed to it.

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

Rules that only matter for some tasks live in `.claude/skills/` rather than
here, so they load when the task needs them instead of in every session. Claude
picks them up from their descriptions; any other agent should open the file
when the task matches.

| Skill | Read it when |
|-------|--------------|
| [`alerting`](.claude/skills/alerting/SKILL.md) | Adding or changing an alert, probe, dashboard, textfile metric or scheduled job |
| [`dashboard-visuals`](.claude/skills/dashboard-visuals/SKILL.md) | Touching `docker/dashboard/`, an icon, a colour, or any visual choice |
| [`local-llm`](.claude/skills/local-llm/SKILL.md) | Touching `docker/ai/`, a Modelfile, `docker/assistant/`, or anything that calls Ollama |

Subagents live in `.claude/agents/`. They run in their own context and hand back
a report:

| Agent | Use it |
|-------|--------|
| [`invariants-reviewer`](.claude/agents/invariants-reviewer.md) | Before pushing a change to `docker/`, `scripts/`, `grafana/` or Caddy config. Read-only: runs the fast CI checks, then checks the diff against the rules above and in the skills, and lists what the deploy will need beyond `git pull` |

`/deploy` detects which containers are serving a stale single-file bind mount
rather than naming them, because a hardcoded recreate list is a second copy of
something Docker already knows — and one was got wrong within a day of being
written, in a way that would have paged `#alerts` about the house having no DNS.

[`scripts/deploy-plan.sh`](scripts/deploy-plan.sh) applies the same reasoning to
the *treatment*: given the pulled range it prints which stacks need `--build`,
which need a Grafana restart, which need a recreate, and what needs a human.
Given no argument it asks Docker instead — each container's start time against
the mtime of the files it reads — which is the answer when the pull already
happened and nothing recorded where it started. It reports; it never deploys.

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