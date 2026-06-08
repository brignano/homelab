# TSD — Self-healing / auto-remediation

**Status:** 💡 future / not started — depends on backups + monitoring landing first
**Goal:** An active auto-remediation layer *above* detection — auto-restart crashed containers (autoheal / health-check restart policies), automatic rollback of a bad deploy, preventive capacity actions (trigger prune / evacuate when disk crosses a threshold instead of just alerting).

> Builds on [`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md). Detection is passive and safe (worst case it pings you); remediation is **active** and can itself cause outages if it misfires. Never automate remediation before detection is trusted — so this only starts once the monitoring layer is proven.

## Open threads (flesh out when promoted)
- Which failure classes are safe to auto-remediate vs. always require a human.
- Guardrails: max auto-restart attempts, blast-radius limits, a kill switch.
- Proxmox HA probably N/A on a single node — revisit only if a second node appears.
- Preventive disk actions vs. the thin-pool constraint (`pve/data` ~348 GiB, non-extendable 400 GiB rootfs).
