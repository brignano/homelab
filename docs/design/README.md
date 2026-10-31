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
| [`tsd-backups-and-monitoring.md`](tsd-backups-and-monitoring.md) | 🚧 Phases 0–1 running — nightly vzdump of CT 100 to the 2 TB drive since 2026-10-11; offsite (restic → B2) built, waits on the B2 account. Steps: [`runbook-backups.md`](../runbook-backups.md) |
| [`tsd-jellyfin.md`](tsd-jellyfin.md) | ✅ deployed 2026-10-11, GPU transcoding verified — Jellyfin in CT 100 with iGPU (VA-API) transcoding, library on the 5 TB drive (`backup=0`), LAN + tailnet only |
| [`tsd-media-downloads.md`](tsd-media-downloads.md) | 🚧 built, not deployed — qBittorrent inside a Proton VPN (gluetun) namespace, Sonarr/Radarr/Prowlarr hardlinking into Jellyfin's library |
| [`tsd-photo-library.md`](tsd-photo-library.md) | 🚧 Phase 1 built — local read-only mirror of iCloud Photos + a cleanup that proposes deletions into review albums |
| [`tsd-self-healing-remediation.md`](tsd-self-healing-remediation.md) | 🚧 first remediations shipped — verified nightly restarts, scheduled disk reclaim, backup-drive watchdog; hung containers, threshold cleanup and rollback in #252–#254 |
| [`tsd-real-domain-private-tls.md`](tsd-real-domain-private-tls.md) | ✅ approved / shipped — real domain + real certs via DNS-01, still unreachable from the internet |
| [`tsd-alerting-off-box.md`](tsd-alerting-off-box.md) | ✅ approved / shipped — alerting that survives the box going down (Discord webhook + Healthchecks dead man's switch) |
| [`tsd-local-llm-discord-jobs.md`](tsd-local-llm-discord-jobs.md) | ✅ approved / shipped — local-LLM async jobs delivered over Discord (`docker/assistant/`) |
| [`tsd-ai-homelab-assistant.md`](tsd-ai-homelab-assistant.md) | 🗄 shelved — open-ended telemetry querying; its canned-summary half shipped in `tsd-local-llm-discord-jobs.md` |
| [`tsd-personal-assistant.md`](tsd-personal-assistant.md) | 🚧 phases 1–4 built — watch lane (mail/calendar/Home Assistant into the digest) + act lane (Claude Agent SDK in `#ask`; calendar events, Gmail drafts and scenes, each approved) |
| [`tsd-dependency-updates.md`](tsd-dependency-updates.md) | ✅ approved / shipped — image staleness watchdog, auto-pull split by blast radius, Renovate on `proxy` |
