# TSD — Backups, restore testing & job monitoring

**Status:** 🚧 Phase 0 built (2026-10-10). Phases 1–3 approved in outline; Phase 1
waits on the backup drive (ordered 2026-10-10).
**Goal:** Give the lab backups that can actually be restored. Today it has none
that cover the data that matters. Then prove they restore on a schedule, and
make every backup job report from off the box when it stops running.

> Moved from `ideas/proposals/` on 2026-06-08 — homelab-specific specs live with
> the implementation. Revised 2026-10-10: the June draft had drifted from the
> lab it describes. What changed and why is under
> [What changed since June](#what-changed-since-june); the original reasoning
> survives in the decisions log.

## The one-paragraph version

The only backup the lab has is a nightly `pg_dumpall` of a Postgres that,
going by the June audit and the compose files, holds nothing. The data that
would hurt to lose — Open WebUI's chat history, Grafana, AdGuard's
configuration, Home Assistant, the Kali home directory, and every `.env` file with the lab's
secrets — has no backup at all, and lives on the same NVMe as the dump. The plan
is: **(0)** now, prove the dump we have restores, and settle three decisions;
**(1)** the day the backup drive arrives, a nightly `vzdump` of the whole of CT 100 to it,
pinging Healthchecks; **(2)** if you approve it, an encrypted `restic` copy of
just the irreplaceable data to Backblaze B2, which likely fits in B2's free
10 GB; **(3)** weekly automated restore checks, and a quarterly manual full
restore.

## What changed since June

| June draft said | True now | Effect on the plan |
|---|---|---|
| Self-host Healthchecks at `checks.home` | The lab already uses **Healthchecks.io** (cloud) through `scripts/healthchecks.sh` for `heartbeat.sh`, `repo-sync.sh` and `pg-backup.sh` | **Dropped.** A self-hosted instance on CT 100 dies with CT 100; the cloud one is the off-box watcher the design wanted. New jobs get a new check there. Net-new services in this plan: none. |
| Job #1: weekly `docker system prune` | `repo-sync.sh` already runs `docker image prune -f` nightly and reports what it freed | **Dropped.** (And never `-a` — see `AGENTS.md` "Disk".) |
| Alert via ntfy | ntfy removed 2026-08-24; Discord `#alerts` is the only path | Already amended; Healthchecks alerts to `#alerts`. |
| Host on ZFS rpool, monthly ZFS scrub | CT 100's rootfs is on **`local-lvm`** (LVM-thin), per the install log | **Verify on the host** (`pvesm status; zpool list`). The scrub row is removed unless ZFS turns out to be present. |
| CT 100 uses 42 GB | Guest read 14–18% of 400 GB in October (≈ 56–72 GB) | Re-measure before sizing; the SSD recommendation below assumes up to ~70 GB. |
| Irreplaceable data ≈ 3.4 GB, mostly Open WebUI + Ollama | Ollama models are re-downloadable; new state since June: assistant state, Google OAuth refresh token, Kali home, AdGuard, Grafana | Inventory redone below. |
| Postgres `homelab` DB empty | Nothing in the repo writes to it (only `postgres-exporter` connects) | The nightly dump protects almost nothing today. Keep it — it is cheap and will matter when something uses Postgres — but stop treating it as the lab's backup. |

## What needs protecting

| Data | Where (in CT 100) | If lost | Covered today |
|---|---|---|---|
| `.env` for every stack (Discord tokens, webhooks, Healthchecks URLs, Google OAuth client + refresh token, DB password) | `/root/homelab/docker/*/.env` | Hours of re-issuing tokens across five services; the Google refresh token needs the consent flow again | ❌ Not in git by design; on one disk |
| Open WebUI chats, users, settings | `ai_open_webui_data` volume (SQLite) | Gone | ❌ |
| Grafana state (users, API tokens incl. the MCP viewer token, annotations) | `grafana_data` volume (SQLite) | Dashboards come back from git; the rest is re-created by hand | ❌ |
| AdGuard configuration (rewrites, filters, clients) | `adguard_conf` volume | Household DNS re-configured from memory | ❌ |
| Kali home directory | `kali_config` volume | Gone | ❌ |
| Home Assistant (users, integration sign-ins, automations, recorder DB) | `homeassistant_config` volume (SQLite recorder) | Onboarding, every integration and every automation redone by hand | ❌ (added 2026-10-10; `docker/homeassistant/README.md` → Backups points here) |
| Portainer settings | `portainer_data` volume | Minor | ❌ |
| Caddy ACME account + certificates | `caddy_data` volume | Re-issued automatically (rate limits apply if repeated) | ❌ (acceptable) |
| Assistant digest state | `/var/lib/homelab/assistant` | One missed-digest alert | ❌ (acceptable) |
| Postgres | `postgres_data` → `/opt/backups/postgres` | Nothing today | ✅ nightly dump, same disk |
| Prometheus, Loki, Alloy | named volumes | 30 days of metrics/logs | Not worth backing up |
| Ollama models, Docker images | volumes / `/var/lib/docker` | Re-pulled | Not worth backing up offsite |
| Jellyfin media (planned) | its own drive, mounted into CT 100 | Re-acquired | **Never backed up** — see Phase 1, step 7 |
| Configs, dashboards, scripts | git | — | ✅ GitHub |

Two things follow. A **whole-CT image** covers every row at once and is the only
thing that restores the lab in one step. An **offsite copy** only needs the rows
marked ❌ that are not "acceptable" — a few GB at most.

## Approach

### Phase 0 — now, no hardware

1. ✅ **Prove the dump we have restores.** Built: `scripts/pg-restore-test.sh`,
   Sundays 03:00. `gzip -t` → a scratch container from the core stack's own
   image (no network, 1 CPU, 512 MB) → replay → databases, roles and tables
   compared with the dump → removed. Any error other than the one
   `role "postgres" already exists` that every pg_dumpall replay produces
   fails it. Pings `HEALTHCHECKS_PG_RESTORE_TEST_URL` (period 7d, grace 1d),
   writes `homelab_pg_restore_test_*`, alerts as `hl-restore-test-failed`,
   and is in `install-cron.sh`. This is the "tier B" restore test
   from June, shipped early for the same reason the job monitoring was: it
   needs no hardware, and a backup never restored is a hope.
2. **Settle the decisions below**, so SSD day is mechanical.
3. **Write the restic password and the Google OAuth client into your password
   manager** if Phase 2 is approved. An encrypted offsite backup whose key only
   exists on the dead disk is not a backup.

### Phase 1 — drive day: nightly image of CT 100

All on the **Proxmox host**, like the `fstrim` job in `AGENTS.md` "Disk" —
`pct`/`vzdump` do not exist inside CT 100. Host-side files live in
`scripts/host/` and are installed by copying, with the install commands in this
section.

1. **Prepare the drive.** Identify it by `lsblk -o NAME,SIZE,MODEL,SERIAL`
   (never by `/dev/sdX` alone), partition GPT, `mkfs.ext4 -L hl-backup`.
   *(If LUKS is approved: `cryptsetup luksFormat` first, with a keyfile at
   `/root/.hl-backup.key` and an entry in `/etc/crypttab`.)*
2. **Mount it so a missing drive fails loudly, never quietly.**
   `/etc/fstab`: `LABEL=hl-backup /mnt/hl-backup ext4 defaults,nofail 0 2`.
   Then add it to Proxmox with **`is_mountpoint`** set:
   ```bash
   pvesm add dir hl-backup --path /mnt/hl-backup --content backup \
     --is_mountpoint 1 --prune-backups keep-daily=7,keep-weekly=4,keep-monthly=3
   ```
   `is_mountpoint` is the load-bearing flag. Without it, an unplugged or
   unmounted drive leaves `/mnt/hl-backup` as an empty directory on the host's
   root filesystem, and vzdump fills the root disk instead of failing.
3. **Take the first backup by hand and time it.**
   `vzdump 100 --storage hl-backup --mode snapshot --compress zstd`.
   Snapshot mode works because `local-lvm` is thin-provisioned; the snapshot
   holds changed blocks in the pool only for the length of the backup. Record
   the archive size and duration in `docs/setup-log.md` — that number decides
   whether the retention above fits.
4. **Schedule it** at **02:30**: after `pg-backup.sh` (02:00) so the newest dump
   is inside the image, before `repo-sync.sh` (04:00) restarts stacks, and well
   before the Sunday `fstrim` (05:00).
   ```bash
   pvesh create /cluster/backup --id hl-ct100 --schedule '02:30' \
     --vmid 100 --storage hl-backup --mode snapshot --compress zstd \
     --script /usr/local/bin/hl-vzdump-hook --notes-template '{{guestname}}'
   ```
5. **Give it a dead man's switch.** `scripts/host/hl-vzdump-hook` (vzdump hook
   script): pings `/start` on `job-start`, success on `job-end`, `/fail` on
   `job-abort` or `backup-abort`. It reads its URL from
   `/etc/homelab/healthchecks.env` (mode 600), because the host has no checkout
   of this repo. New Healthchecks check: period 1 day, grace 2 hours. This is
   the same "silence is the signal" rule as every other job here: a vzdump job
   that was never scheduled, or a drive that fell off the USB bus, must page.
6. **First restore test** — the quarterly drill in Phase 3, run once now.
7. **Keep media out of the image.** vzdump includes every mount point of CT 100
   unless that mount point says otherwise. When the Jellyfin drive is attached,
   mount it with `backup=0`, or the next nightly backup tries to copy the whole
   library onto the backup drive, fills it, and fails, so the job that matters
   stops:
   ```bash
   pct set 100 -mp0 /mnt/media,mp=/media,backup=0
   pct config 100 | grep mp0      # must show backup=0
   ```
   The media drive and the backup drive are **separate drives**. Sharing one
   would let a single failure take the library and the only backup together,
   and a growing library would quietly squeeze backup retention.

**Not encrypted at rest** unless LUKS is chosen. The archive contains every
`.env` file and the Postgres role hashes. The drive is the part of this setup
most likely to be lost or carried off on its own.

### Phase 2 — offsite (needs your decision)

`restic` from **CT 100** (where `healthchecks.sh` and `metrics.sh` already live)
to Backblaze B2, nightly at 03:30.

- **What:** the rows in the inventory marked ❌ and not "acceptable" — every
  `docker/*/.env`, the newest Postgres dump, and the volumes above. Nothing
  re-downloadable.
- **SQLite consistency:** copy Open WebUI's, Grafana's and Home Assistant's
  recorder databases with
  `sqlite3 <db> ".backup <staging>"` before restic runs, instead of reading the
  live files. A file-level copy of a SQLite database being written can restore
  as corrupt.
- **Encrypted client-side** (restic always is). The password lives in
  `/root/.config/homelab/restic.env` (600) **and in your password manager**.
- **Retention:** `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`.
- **Verification:** `restic check --read-data-subset=5%` weekly, so every
  stored byte gets read back about once every five months.
- **Monitoring:** Healthchecks ping and textfile metrics (`homelab_offsite_*`:
  last success, bytes added, snapshot count) through the existing helpers.

### Phase 3 — restore tests

| Tier | When | What | Where |
|---|---|---|---|
| A | Weekly, automated | `zstd -t` on the newest vzdump archive; ping Healthchecks | Host, from the hook script's `job-end` |
| B | Weekly, automated | Postgres dump → scratch DB → sanity query | CT 100 (Phase 0) |
| B′ | Weekly, automated | `restic check --read-data-subset=5%` | CT 100 (Phase 2) |
| C | Quarterly, manual | Full restore of CT 100 as a throwaway container (below) | Host |

**Tier C must never collide with the real CT 100.** A restored copy keeps CT
100's IP, MAC and `onboot=1`. If it boots on the network it answers as
`10.0.0.201` and runs a second AdGuard, and the household's DNS breaks. The
drill:

```bash
pct restore 999 /mnt/hl-backup/dump/<newest>.tar.zst --storage hl-backup --unique 1
pct set 999 --onboot 0 --delete net0      # no network at all
pct start 999
pct exec 999 -- docker ps --format '{{.Names}} {{.Status}}'
pct exec 999 -- ls /root/homelab/docker/*/.env
pct stop 999 && pct destroy 999 --purge
```

Restore onto the backup drive (add `rootdir` to `hl-backup`'s content types for
the drill), **never `local-lvm`**: the thin pool is about 348 GiB under a
400 GiB rootfs, and a second full copy can fill it and take the lab down
(`AGENTS.md` "Disk").

## Decisions needed

1. **Backup drive.** Recommendation: a **2 TB portable USB HDD**, or a 1 TB
   SSD if it costs about the same. Retention keeps up to 14 archives. At an
   assumed 20–35 GB per compressed archive (to be measured in Phase 1, step 3),
   that is 280–490 GB, which does not fit on 500 GB with headroom. Speed does
   not matter for a 02:30 job, so an HDD is fine; 2 TB leaves room to keep
   backups longer. It holds backups and nothing else. Jellyfin gets its own
   drive (Phase 1, step 7).
2. **LUKS on the SSD?** Recommendation: **yes, with a keyfile on the host.** It
   protects against losing the drive by itself, the likeliest loss. It does not
   protect against theft of the whole machine, which takes the keyfile too.
3. **Offsite at all, and where?** Recommendation: **yes, B2.** The data set is a
   few GB, which likely fits in B2's free 10 GB, so the cost is about $0 to
   $1 a month. Without it, a fire or a theft takes the lab and its only backup
   together.
4. ~~Approve Phase 0~~ — built 2026-10-10.

## Rejected alternatives

- **Proxmox Backup Server.** Better tool (dedup, encryption, built-in verify),
  but it is another VM or container to run, patch and back up, on a 16 GB box,
  to protect a single CT. Revisit if the lab grows past a handful of guests.
- **Excluding `/var/lib/docker` from vzdump** to shrink archives. Named volumes
  live under it; excluding only image layers leaves Docker's metadata pointing
  at layers that are not there. A restore that needs repair before it boots is
  not a restore.
- **Self-hosted Healthchecks.** See [What changed since June](#what-changed-since-june).
- **Backing up to a second partition on the NVMe.** Same disk, same failure.

## Cost

- Backup drive (2 TB portable HDD or 1 TB SSD): one-time, roughly $50–80.
- Jellyfin's media drive is separate, and costed with Jellyfin rather than here.
- B2: likely $0 (within the free 10 GB); at most about $1/month.
- RAM/CPU: none resident. vzdump and restic run for minutes a night.

## Decisions log

- 2026-06: Both an image (vzdump) and a data copy (restic). Offsite must be
  encrypted, because dumps and `.env` files contain secrets. Restore tests are
  tiered (weekly automated, quarterly manual). Self-healing is out of scope; see
  [`tsd-self-healing-remediation.md`](tsd-self-healing-remediation.md).
- 2026-08-24: Alerts go to the Discord `#alerts` webhook only (ntfy removed).
- 2026-09-19: Job monitoring for `pg-backup.sh` shipped early (Healthchecks
  ping, textfile metrics, **Scheduled Jobs & Backups** dashboard, stale-backup
  alert). The dashboard says, deliberately, that a green panel means "the one
  backup we have ran", not "the lab is recoverable".
- 2026-10-10: Revised against the lab as it is now. Self-hosted Healthchecks
  and the prune job are dropped (already covered). Restic moves into CT 100.
  vzdump runs at 02:30 with a hook-script dead man's switch. Media stays on
  its own drive, mounted with `backup=0`. `is_mountpoint`
  guards the SSD mount. Tier C restores with no network. The inventory shows
  the existing dump protects an empty database.

## Open questions

- [ ] Host filesystem: confirm LVM-thin vs ZFS (`pvesm status; zpool list`) and
      whether any scrub job exists.
- [ ] Measured archive size and duration after the first vzdump (sets retention).
