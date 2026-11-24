# TSD — Self-healing / auto-remediation

**Status:** 🚧 first remediations shipped (2026-10-31) — follow-ups tracked in #252, #253, #254
**Goal:** An active auto-remediation layer *above* detection — auto-restart crashed containers (autoheal / health-check restart policies), automatic rollback of a bad deploy, preventive capacity actions (trigger prune / evacuate when disk crosses a threshold instead of just alerting).

> Builds on [`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md). Detection is passive and safe (worst case it pings you); remediation is **active** and can itself cause outages if it misfires. Never automate remediation before detection is trusted — so this only starts once the monitoring layer is proven.

## What runs today

| Failure | Remediation | Where |
|---|---|---|
| Container exits | `restart: unless-stopped` on every long-running service | `docker/*/docker-compose.yml` |
| Stack is stale or down after a merge | Nightly restart, then **verified** (`verify_stack`); a failed verify is reported with the fix command | `scripts/repo-sync.sh` → "Self-healing" |
| A merge is not live yet | Deployed within ~10 minutes and verified with the same `verify_stack` (which now also fails on `unhealthy`) | `scripts/auto-deploy.sh` (#335, [`tsd-auto-deploy.md`](tsd-auto-deploy.md)) |
| Disk fills with replaced images | Nightly `docker image prune` + build-cache prune (keeps 7 days) | `scripts/repo-sync.sh` → "Reclaim" |
| Thin pool does not reclaim deleted blocks | Weekly `pct fstrim` of CT 100, Sunday 05:00 | `ansible/roles/proxmox_host/tasks/main.yml` |
| Backup drive not mounted after a reboot | `hl-drive-watchdog` timer unlocks/remounts it and restarts the exporter; `hl-backup-drive-offline` alert is the backstop | `ansible/roles/proxmox_host/tasks/watchdog.yml` (#250, #251) |
| A container hangs (running, not answering) | `healthcheck:` on sonarr, radarr, prowlarr, jellyfin, prometheus, grafana (plus postgres); `scripts/autoheal.sh` (cron, 5 min) restarts `unhealthy` ones, max 3 per 6h each, never `HL_NO_AUTOHEAL` stacks or gluetun; `hl-autoheal-restarted` notifies, `hl-autoheal-gave-up` pages when it stops | `scripts/autoheal.sh`, `scripts/test-autoheal.sh` (#252) |
| Remote Control on CT 100 dies or the box reboots | `claude-rc.service`, `Restart=always` | Ansible `docker_lxc` role (#248) |

## Guardrails (decided)

- **Kill switch:** `HL_AUTOHEAL=no` makes repo-sync report-only.
- **Blast radius:** `HL_NO_AUTOHEAL` (default `proxy matterbridge`) — DNS and Alexa are never restarted unattended. gluetun is never restarted alone.
- **Verify, don't fire and forget:** every automatic restart is checked; a stack that does not come back pages.
- **Unknown tree, no action:** auto-heal is skipped when the pull failed.
- **Hypervisor stays on the hypervisor:** remediation for m5 runs on m5. CT 100 never gets root on the host.
- **Proxmox HA:** N/A on a single node — revisit only if a second node appears.

## Not done yet

- **Threshold-triggered disk cleanup** — cleanup is on a schedule; a spike between runs only alerts. Mind the thin-pool constraint (`pve/data` ~348 GiB, non-extendable 400 GiB rootfs) and AGENTS.md → Disk. → #253

## Rollback of a bad deploy (#254)

`scripts/auto-deploy.sh` rolls a stack back when `verify_stack` says it did not come back after a deploy, but only for stacks on `HL_ROLLBACK_STACKS`. Default: `clipreview epg mcp mcp-calendar`, the stacks with no database and no state the new version could have migrated.

- **How:** `git checkout <last-good> -- docker/<stack>`, `docker compose up -d --build`, verify again, then `git checkout HEAD -- docker/<stack>` so the box still matches main and git stays clean. The containers run the old version until the fix merges.
- **Report:** the run is still red (a change broke it), and the message says whether the rollback came back or did not. `last-failed` stops the retry loop.
- **Never:** `core` (one-way Portainer migrations, Postgres), `monitoring` (breaking Grafana upgrades), `assistant`, `budget` and `mcp-budget` (they hold data), and `proxy` / `matterbridge` (never deployed unattended). Those only report.
- **Not covered:** floating tags (`:latest`) pulled fresh by a rebuild come back as the same image. Pin a tag to make a stack rollback-safe, then add it to the list.
- **Tests:** `scripts/test-auto-deploy.sh` (on the list: rolled back and verified; off the list: reported only).
