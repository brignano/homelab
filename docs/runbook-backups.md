# Runbook: backups

How to set up, check and restore the lab's backups. The design and the reasons
behind it are in
[`design/tsd-backups-and-monitoring.md`](design/tsd-backups-and-monitoring.md).

| Layer | What | Where it runs | Watched by |
|---|---|---|---|
| Postgres dump | `pg_dumpall`, 02:00 nightly | CT 100 (`scripts/pg-backup.sh`) | Healthchecks + `hl-backup-stale` |
| Restore test B | the newest dump replayed into a scratch Postgres, Sun 03:00 | CT 100 (`scripts/pg-restore-test.sh`) | Healthchecks + `hl-restore-test-failed` |
| **Image** (Phase 1) | `vzdump` of all of CT 100 to the 2 TB drive, 02:30 nightly, verified with `zstd -t` | Proxmox host (`scripts/host/hl-vzdump-hook`) | Healthchecks + `hl-backup-drive-full` |
| **Offsite** (Phase 2) | restic to Backblaze B2, 03:30 nightly; `restic check` 5% on Sundays | CT 100 (`scripts/offsite-backup.sh`) | Healthchecks + `hl-offsite-failed` |

Order: the offsite copy needs no hardware, so it can be set up **now**. The
image and the media drive wait for drive day.

## 0. Your password manager first

Three secrets must exist **somewhere other than this box**, or the backups die
with it:

| Item | Why |
|---|---|
| **restic repository password** | Without it the B2 copy is unreadable random bytes. Nobody can recover it. |
| **B2 application key** (keyID + key) | To reach the bucket from a new machine. Can be re-issued from the B2 console, so less critical. |
| **LUKS passphrase** for the backup drive | The keyfile lives on the host. If the host dies, the passphrase is the only way into the backup drive, which is exactly when you need it. |

Generate the two passwords with the password manager itself, save them, then
paste them where the steps below say.

**Where they are:** Google Password Manager. The LUKS passphrase is the entry
`https://hl-backup.m5` (username `luks`, saved 2026-10-11), and the restic
password goes under `https://hl-restic.m5`. The values are never written here.

## 1. Offsite copy (do it now)

**Backblaze** (web console):

1. Create an account at backblaze.com → B2 Cloud Storage.
2. Create a bucket: private, default encryption off (restic encrypts already),
   **Object Lock off**. Name it something unguessable, e.g.
   `hl-offsite-<random>`. Note its **endpoint**, e.g.
   `s3.us-east-005.backblazeb2.com`.
3. Application Keys → Add a New Application Key: access to **that bucket only**,
   Read and Write. Save the keyID and applicationKey in the password manager;
   the key is shown once.
4. Caps & Alerts: set a daily storage cap, e.g. $1. The data is a few GB; a cap
   turns a runaway into an error instead of a bill.

**CT 100** (`hl-ssh`, one command at a time):

```bash
apt-get install -y restic sqlite3
mkdir -p /root/.config/homelab && chmod 700 /root/.config/homelab
nano /root/.config/homelab/restic.env
```

```bash
# /root/.config/homelab/restic.env  (never committed; mode 600)
RESTIC_REPOSITORY=s3:https://<endpoint>/<bucket>/homelab
RESTIC_PASSWORD=<restic password from the password manager>
AWS_ACCESS_KEY_ID=<B2 keyID>
AWS_SECRET_ACCESS_KEY=<B2 applicationKey>
```

```bash
chmod 600 /root/.config/homelab/restic.env
set -a; . /root/.config/homelab/restic.env; set +a; restic init
```

Then the dead man's switch and the first run:

1. Healthchecks: a new check **offsite-backup**, period 1 day, grace 6 hours.
   Paste its ping URL into `docker/monitoring/.env` as
   `HEALTHCHECKS_OFFSITE_BACKUP_URL=`.
2. `./scripts/install-cron.sh`. Until you run it, `hl-cron-missing` alerts for
   `offsite-backup.sh`, which is the reminder.
3. `./scripts/offsite-backup.sh`. The first run uploads everything, so expect
   minutes, not seconds. It ends with `ok: N MiB added, 1 snapshot(s) kept`.
4. Check it from the other side: `restic snapshots` (after the `set -a` line
   above) lists one snapshot with host `homelab`.

What goes up: every `docker/*/.env`, the newest Postgres dump, and the volumes
for Open WebUI, Grafana, AdGuard, Home Assistant, Kali, Portainer and (once it
exists) Jellyfin's config, without caches. SQLite databases are copied with
`sqlite3 .backup` first, so they restore consistent. The list is `VOLUMES` at
the top of the script.

## 2. Drive day: the backup drive (2 TB, encrypted)

On the **Proxmox host**, one command at a time. Identify the drive by size,
model and serial. Never trust `/dev/sdX` alone: the letters move between boots.

```bash
lsblk -o NAME,SIZE,MODEL,SERIAL,FSTYPE
```

Below, `/dev/sdX` is the 2 TB WD drive from that list. **Check twice.** The
next commands erase it.

```bash
# 0. Proxmox does not ship cryptsetup.
apt update && apt install -y cryptsetup

# 1. One GPT partition across the drive.
sgdisk --zap-all /dev/sdX && sgdisk -n1:0:0 -c1:hl-backup /dev/sdX

# 2. A keyfile on the host, so nightly backups unlock it unattended,
#    and LUKS with that keyfile as the first key. Nothing is pasted yet.
dd if=/dev/urandom of=/root/.hl-backup.key bs=512 count=8 && chmod 400 /root/.hl-backup.key
cryptsetup luksFormat --key-file /root/.hl-backup.key /dev/sdX1

# 3. The passphrase as a second key. Paste it FROM the password manager at
#    both prompts, then prove that saved copy opens the drive.
cryptsetup luksAddKey --key-file /root/.hl-backup.key /dev/sdX1
cryptsetup open --test-passphrase /dev/sdX1 && echo MATCHES

# 4. Open it and make the filesystem.
cryptsetup open --key-file /root/.hl-backup.key /dev/sdX1 hl-backup
mkfs.ext4 -L hl-backup /dev/mapper/hl-backup
```

Do not go past step 3 without `MATCHES`. Keyfile first is deliberate: the
first time, the passphrase went into `luksFormat` from one clipboard and into
`luksAddKey` from another, they did not match, and the drive had a key nobody
knew. The keyfile cannot be mistyped, and `--test-passphrase` proves the copy
you will actually have on the bad day.

Unlock and mount at boot, without blocking boot if the drive is unplugged:

```bash
echo "hl-backup UUID=$(blkid -s UUID -o value /dev/sdX1) /root/.hl-backup.key luks,nofail" >> /etc/crypttab
mkdir -p /mnt/hl-backup
echo '/dev/mapper/hl-backup /mnt/hl-backup ext4 defaults,nofail 0 2' >> /etc/fstab
systemctl daemon-reload && mount /mnt/hl-backup && df -h /mnt/hl-backup
```

Tell Proxmox about it. **`is_mountpoint 1` is load-bearing**: without it an
unplugged drive leaves an empty directory, and vzdump fills the host's root
disk instead of failing.

```bash
pvesm add dir hl-backup --path /mnt/hl-backup --content backup \
  --is_mountpoint 1 --prune-backups keep-daily=7,keep-weekly=4,keep-monthly=3
pvesm status | grep hl-backup
```

### The hook and its dead man's switch

The host has no checkout of the repo, but CT 100 does. Copy the hook out of
it, on the host:

```bash
pct exec 100 -- git -C /root/homelab pull --ff-only
pct pull 100 /root/homelab/scripts/host/hl-vzdump-hook /usr/local/bin/hl-vzdump-hook
```

Then on the host:

```bash
chmod 755 /usr/local/bin/hl-vzdump-hook
mkdir -p /etc/homelab && chmod 700 /etc/homelab
echo 'HEALTHCHECKS_VZDUMP_URL=<ping URL>' > /etc/homelab/healthchecks.env
chmod 600 /etc/homelab/healthchecks.env
```

The ping URL is a new Healthchecks check, **vzdump**, period 1 day, grace
2 hours.

### First backup, by hand, timed

```bash
time vzdump 100 --storage hl-backup --mode snapshot --compress zstd \
  --script /usr/local/bin/hl-vzdump-hook
```

The lab stays up: snapshot mode copies from an LVM snapshot. At the end the log
shows the hook verifying the archive, and the Healthchecks check turns green
with a body like `archive vzdump-lxc-100-….tar.zst (24G) verified in 190s`,
the SMART result and the free space. **Write the archive size and the time in
`docs/setup-log.md`**: they decide whether the retention fits (open question
in the TSD).

If SMART says "not readable through this enclosure", the USB bridge hides it.
That does not fail the backup, but `hl-smart-unreadable` says so in `#alerts`,
because then nothing is watching that drive's health. Try
`smartctl -d sat -a /dev/sdX`; if no `-d` type reads it, the choice to live
with that is a silence in Grafana, made on purpose.

### SMART metrics (since 2026-10-12)

At `job-end` the hook also writes each drive's SMART verdict and sector counts
to `/var/lib/prometheus/node-exporter/hl_smart.prom`, the textfile directory
the Debian `prometheus-node-exporter` reads by default. That is what the
`hl-smart-*` alerts and the *Drives* row of the Capacity dashboard read. The
copy on the host does not update itself: **re-run the `pct pull` above whenever
`scripts/host/hl-vzdump-hook` changes.** To check it without waiting for 02:30
(an empty env file means no Healthchecks ping):

```bash
HL_HOOK_ENV=/dev/null HL_HOOK_STATE=/tmp/hl-hook.test /usr/local/bin/hl-vzdump-hook job-end
cat /var/lib/prometheus/node-exporter/hl_smart.prom
curl -s localhost:9100/metrics | grep '^homelab_smart'
```

If the file is written but `curl` shows nothing, the exporter is reading a
different directory: compare with `--collector.textfile.directory` in
`/etc/default/prometheus-node-exporter`.

### Drive temperature every 15 minutes (since 2026-10-12)

The nightly read sees 02:30 only. The same hook has a `temp` phase that reads
just the temperature, into its own `hl_drive_temp.prom`, and leaves a sleeping
drive asleep. It runs from a cron file on the host (not root's crontab, so
`crontab -l` will not show it):

```bash
echo '*/15 * * * * root /usr/local/bin/hl-vzdump-hook temp' > /etc/cron.d/hl-drive-temp
/usr/local/bin/hl-vzdump-hook temp && curl -s localhost:9100/metrics | grep '^homelab_drive_temp'
```

`hl-smart-hot` reads this and fires on the hour's average above 55C;
`hl-drive-temp-stale` fires if it stops for an hour. During a long download
backlog, silence `Drive running hot` in Grafana rather than raise the
threshold.

### Schedule it

```bash
pvesh create /cluster/backup --id hl-ct100 --schedule '02:30' \
  --vmid 100 --storage hl-backup --mode snapshot --compress zstd \
  --script /usr/local/bin/hl-vzdump-hook --notes-template '{{guestname}}'
pvesh get /cluster/backup/hl-ct100
```

02:30: after the Postgres dump at 02:00 (so it is inside the image), before
repo-sync at 04:00 and the Sunday `fstrim` at 05:00.

## 3. Drive day: the media drive (5 TB, not encrypted)

Media is not backed up and holds nothing secret, so no LUKS. On the host:

```bash
lsblk -o NAME,SIZE,MODEL,SERIAL,FSTYPE           # find the 5 TB drive: /dev/sdY
sgdisk --zap-all /dev/sdY && sgdisk -n1:0:0 -c1:hl-media /dev/sdY
mkfs.ext4 -L hl-media -m 0 /dev/sdY1             # -m 0: no root reserve on a data drive

mkdir -p /mnt/media
chattr +i /mnt/media                              # unmounted, the directory refuses writes
echo 'LABEL=hl-media /mnt/media ext4 defaults,nofail 0 2' >> /etc/fstab
systemctl daemon-reload && mount /mnt/media
mountpoint /mnt/media                             # must say "is a mountpoint"
```

`chattr +i` is the media drive's version of `is_mountpoint`. If the drive is
ever missing at boot, `/mnt/media` is an empty, unwritable directory, so
Jellyfin's cache cannot land on the host's root disk.

Into CT 100, **with `backup=0`**, or the next vzdump tries to copy the whole
library onto the 2 TB drive:

```bash
pct set 100 -mp0 /mnt/media,mp=/media,backup=0
pct config 100 | grep mp0                         # must show backup=0
pct reboot 100                                    # ~1 minute; DNS too
```

Then Jellyfin: [`docker/jellyfin/README.md`](../docker/jellyfin/README.md)
§2–3, with `RENDER_GID=993`.

## 4. Checking on it

| Question | Where |
|---|---|
| Did tonight's backups run? | Healthchecks: **vzdump**, **pg-backup**, **offsite-backup** all green |
| How big are the archives? | Host: `ls -lh /mnt/hl-backup/dump/` |
| What is offsite? | CT 100: `set -a; . /root/.config/homelab/restic.env; set +a; restic snapshots` |
| Logs | CT 100: `/var/log/offsite-backup.log`. Host: `journalctl -u pvescheduler` and the task log in the Proxmox UI |

## 5. Restoring

### One file or volume, from B2

On CT 100 (or any machine with restic and the four values from the password
manager):

```bash
set -a; . /root/.config/homelab/restic.env; set +a
restic snapshots
restic restore latest --target /root/restore --include /var/lib/homelab/offsite-staging/env
```

The layout inside a snapshot:

- `/var/lib/homelab/offsite-staging/env/<stack>.env`
- `/var/lib/homelab/offsite-staging/postgres/`: the newest dump
- `/var/lib/homelab/offsite-staging/sqlite/<volume>/<path>`: consistent
  copies of each SQLite database. Copy these over the live file **with the
  container stopped**.
- `/var/lib/docker/volumes/<volume>/_data/…`: everything else in each volume

### All of CT 100, from the image: the quarterly drill (tier C)

Restore as **CT 999 with no network**. A copy that boots with CT 100's network
answers as 10.0.0.201 and runs a second AdGuard, and the house's DNS breaks.
Restore onto the backup drive, **never `local-lvm`**: a second full copy can
fill the thin pool and take the lab down.

```bash
pvesm set hl-backup --content backup,rootdir      # once
pct restore 999 /mnt/hl-backup/dump/<newest>.tar.zst --storage hl-backup --unique 1
pct set 999 --onboot 0 --delete net0
pct start 999
pct exec 999 -- docker ps --format '{{.Names}} {{.Status}}'
pct exec 999 -- ls /root/homelab/docker/
pct stop 999 && pct destroy 999 --purge
```

For a real restore after losing CT 100, the same `pct restore` with `100` and
`--storage local-lvm`, keeping the network.

### The backup drive itself, from another machine

If the host is gone, so is the keyfile. Plug the drive into any Linux machine:

```bash
cryptsetup open /dev/sdX1 hl-backup               # asks for the passphrase
mount /dev/mapper/hl-backup /mnt
ls /mnt/dump/
```
