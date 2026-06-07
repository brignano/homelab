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
