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
