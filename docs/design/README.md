# Design specs (TSDs)

Technical Spec Documents for the homelab — one `tsd-*.md` per design.

**Convention:**
- This folder holds **all** TSDs regardless of lifecycle. The `Status:` field at the top of each doc tracks maturity (draft → parked → approved → shipped). Files are **not** moved when shipped — a TSD stays the permanent design record (rationale + rejected alternatives).
- Operational/reference documentation (how the system works *now* — `setup-log.md`, strategy, runbooks) lives in `docs/`, not here.
- These are homelab-specific specs. Greenfield product/app/business ideas live in the separate `ideas` repo.

| TSD | Status |
|-----|--------|
| [`tsd-proxy-memorable-names.md`](tsd-proxy-memorable-names.md) | ✅ approved / shipped — `*.home` names via Caddy + AdGuard |
| [`tsd-on-demand-desktops.md`](tsd-on-demand-desktops.md) | on-demand Kali webtop via Sablier |
| [`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md) | ⏸ parked — backups + restore testing + job monitoring (blocked on a USB SSD) |
| [`tsd-self-healing-remediation.md`](tsd-self-healing-remediation.md) | 💡 future — auto-remediation layer |
