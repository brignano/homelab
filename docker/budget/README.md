# Actual Budget

Private envelope budgeting (<https://actualbudget.org>), self-hosted, for 2027.
`https://budget.home.brignano.io`, **LAN + tailnet only**. Issue #310.

- **No public route.** No Cloudflare Tunnel, no port forward, no published port.
  Caddy reaches the container over `proxy_proxy`; the name resolves to the
  private address `10.0.0.201`, so a device off the LAN and off the tailnet
  cannot connect. Do not add it to the tunnel.
- **No bank sync.** SimpleFIN and GoCardless are not configured and no
  bank-sync secret exists in this repo or its `.env`. Transactions come in as
  files you import yourself.
- **Data** lives in the Docker volume `budget_data` (`/data` in the container):
  the server's account database and every budget file.
- **Image** is pinned by tag and digest in `docker-compose.yml`; Renovate opens
  a PR for each bump. Read it before merging, because the server migrates
  budget data on upgrade.

## First open

1. From a device on the tailnet (or the LAN), open
   `https://budget.home.brignano.io`.
2. Actual asks you to **set a server password**. Generate a long one in your
   password manager, save it there first (as `https://budget.home.brignano.io`),
   then paste it. It is stored hashed in the data volume; it is never in git
   and not in any `.env`. Do this promptly: until it is set, anyone who can
   reach the page can set it.
3. Create the budget file, then turn on **end-to-end encryption** for it:
   *Settings → Show advanced settings → Encryption → Enable end-to-end
   encryption*. It is recommended: the server then holds only ciphertext, and a
   leaked backup is unreadable without that key. Save the key in the password
   manager too. **If you lose it, the budget cannot be recovered**, backups
   included.

## Importing transactions

Download CSV, OFX or QFX from your bank, then in the account's page choose
**Import** (the button beside the account name), pick the file, check the
column mapping and the preview, and confirm. Re-importing the same file is safe:
Actual skips transactions it has already seen.

## Backups and restore

- **Local:** the nightly `vzdump` of CT 100 includes the volume.
- **Offsite:** `scripts/offsite-backup.sh` lists `budget_data` in `VOLUMES`, so
  it goes to B2 nightly (restic, encrypted). SQLite files are copied with
  `sqlite3 .backup` first, so they restore consistent.
- **Your own copy:** *Settings → Export data* in the app downloads a zip of the
  budget; worth doing before any upgrade.

Restore check (do it on a scratch volume, never over the live one):

```bash
set -a; . /root/.config/homelab/restic.env; set +a
restic snapshots | tail -3
restic restore latest --target /root/restore-budget \
  --include "$(docker volume inspect -f '{{.Mountpoint}}' budget_data)"
find /root/restore-budget -name '*.sqlite' -o -name '*.blob' | head
sqlite3 "$(find /root/restore-budget -name account.sqlite | head -1)" 'PRAGMA integrity_check;'   # ok
rm -rf /root/restore-budget
```

To restore for real: stop the container (`docker compose stop budget`), copy the
restored `server-files/` and `user-files/` into the volume, start it again.

## Checks

```bash
docker compose -f docker/budget/docker-compose.yml ps                       # Up
curl -sk https://budget.home.brignano.io/health                             # {"status":"UP"}
ss -tlnp | grep 5006                                                        # nothing: no published port
```

Prometheus probes `http://budget:5006/health` (service `budget`).
