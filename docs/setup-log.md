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
