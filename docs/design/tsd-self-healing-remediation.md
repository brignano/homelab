# TSD — Self-healing / auto-remediation

**Status:** 🚧 first remediations shipped (2026-10-31) — follow-ups tracked in #252, #253, #254
**Goal:** An active auto-remediation layer *above* detection — auto-restart crashed containers (autoheal / health-check restart policies), automatic rollback of a bad deploy, preventive capacity actions (trigger prune / evacuate when disk crosses a threshold instead of just alerting).

> Builds on [`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md). Detection is passive and safe (worst case it pings you); remediation is **active** and can itself cause outages if it misfires. Never automate remediation before detection is trusted — so this only starts once the monitoring layer is proven.

## What runs today

| Failure | Remediation | Where |
|---|---|---|
| Container exits | `restart: unless-stopped` on every long-running service | `docker/*/docker-compose.yml` |
| Stack is stale or down after a merge | Nightly restart, then **verified** (`verify_stack`); a failed verify is reported with the fix command | `scripts/repo-sync.sh` → "Self-healing" |
| Disk fills with replaced images | Nightly `docker image prune` + build-cache prune (keeps 7 days) | `scripts/repo-sync.sh` → "Reclaim" |
| Thin pool does not reclaim deleted blocks | Weekly `pct fstrim` of CT 100, Sunday 05:00 | `ansible/roles/proxmox_host/tasks/main.yml` |
| Backup drive not mounted after a reboot | `hl-drive-watchdog` timer unlocks/remounts it and restarts the exporter; `hl-backup-drive-offline` alert is the backstop | `ansible/roles/proxmox_host/tasks/watchdog.yml` (#250, #251) |
| Remote Control on CT 100 dies or the box reboots | `claude-rc.service`, `Restart=always` | Ansible `docker_lxc` role (#248) |

## Guardrails (decided)

- **Kill switch:** `HL_AUTOHEAL=no` makes repo-sync report-only.
- **Blast radius:** `HL_NO_AUTOHEAL` (default `proxy matterbridge`) — DNS and Alexa are never restarted unattended. gluetun is never restarted alone.
- **Verify, don't fire and forget:** every automatic restart is checked; a stack that does not come back pages.
- **Unknown tree, no action:** auto-heal is skipped when the pull failed.
- **Hypervisor stays on the hypervisor:** remediation for m5 runs on m5. CT 100 never gets root on the host.
- **Proxmox HA:** N/A on a single node — revisit only if a second node appears.

## Not done yet

- **Hung containers** — running but not answering. Only `core` and `ai` have a `healthcheck:`, and nothing acts on `unhealthy`. → #252
- **Threshold-triggered disk cleanup** — cleanup is on a schedule; a spike between runs only alerts. Mind the thin-pool constraint (`pve/data` ~348 GiB, non-extendable 400 GiB rootfs) and AGENTS.md → Disk. → #253
- **Rollback of a bad deploy** — a failed verify is reported, not rolled back. Only safe for stateless stacks: `core` (one-way Portainer migrations, Postgres) and `monitoring` (breaking Grafana upgrades) are not candidates. → #254
