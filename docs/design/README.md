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
| [`tsd-alerting-off-box.md`](tsd-alerting-off-box.md) | ✅ approved / shipped — alerting that survives the box going down (Discord webhook + Healthchecks dead man's switch) |
| [`tsd-local-llm-discord-jobs.md`](tsd-local-llm-discord-jobs.md) | ✅ approved / shipped — local-LLM async jobs delivered over Discord (`docker/assistant/`) |
| [`tsd-ai-homelab-assistant.md`](tsd-ai-homelab-assistant.md) | 🗄 shelved — open-ended telemetry querying; its canned-summary half shipped in `tsd-local-llm-discord-jobs.md` |
