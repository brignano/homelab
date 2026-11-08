# 0001: Deploy to CT 100 automatically after a merge

**Status:** accepted, 2026-11-08 · **TSD:** [`tsd-auto-deploy.md`](../docs/design/tsd-auto-deploy.md) · **Issue:** #335

## Decision

CT 100 polls `origin/main` every 5 minutes with a new `scripts/auto-deploy.sh`
and deploys what changed, using the same rules as `/deploy`
(`deploy-plan.sh --machine`) and the same `verify_stack` as repo-sync.

- Pull-based only: no self-hosted runner, no webhook, no inbound access.
- Stacks in `HL_NO_AUTOHEAL` (`proxy`, `matterbridge`), new `.env` keys and
  host-side work are reported with the manual line, never deployed.
- One `flock` for every deploy. A manual `/deploy` writes a pause marker that
  expires after 30 minutes.
- Verify fails on a missing, restarting or `unhealthy` container. A failed
  commit alerts once and is not retried. A revert PR is the rollback.
- Paused by `/var/lib/homelab/auto-deploy.pause` or `HL_AUTOHEAL=no`.
- Healthchecks ping plus a "behind main" alert catch a stalled timer.

## Why

Merges waited up to 24 hours (repo-sync at 04:00) or for a manual `/deploy`,
and cloud sessions can't reach the box. Polling keeps the lab closed to the
internet.

## Replaces

`/deploy` as the main path. It stays as the manual fallback.
