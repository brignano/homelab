# Runbook: rebuilding the lab

What to do when something is gone, from least to most gone. The machinery is
[`ansible/`](../ansible/README.md) plus the backups in
[`runbook-backups.md`](runbook-backups.md).

| What is lost | What brings it back | Rough time |
|---|---|---|
| CT 100 only | `pct restore` of last night's image | ~30 min |
| The host (m5), backup drive survived | New Proxmox + playbook + `pct restore` | ~2 hours |
| Host **and** backup drive | New Proxmox + playbook + B2 restore | an afternoon |
| Host, backup drive **and** B2 not configured | Playbook + every secret re-issued by hand | days |

The last row is the reason B2 must be on. Everything else in it is now code.

## Before any of these: what has to exist off the box

From the password manager (`runbook-backups.md` §0): the **restic password**,
the **B2 key ID and key**, and the **LUKS passphrase**. Also needed: GitHub
(the repo), Tailscale and Healthchecks logins. Nothing else.

## 1. CT 100 is broken, the host is fine

`runbook-backups.md` §5, "All of CT 100, from the image": `pct restore 100`
with `--storage local-lvm`. Then run the playbook once. It should report no
drift.

## 2. The host is gone, the backup drive is fine

1. **Install Proxmox VE** from USB: hostname `m5.homelab.lan`, static
   `10.0.0.200/24`, gateway `10.0.0.1`, DNS `1.1.1.1`. Keep the router's DHCP
   reservation and the UDP 41641 port forward pointing at the new NIC's MAC.
2. **SSH key:** `ssh-copy-id root@10.0.0.200` from the Mac. A rebuilt box has
   new host keys, so first `ssh-keygen -R 10.0.0.200 && ssh-keygen -R 10.0.0.201`.
3. **The backup drive on a new host.** The old keyfile died with the old host,
   so make a new one, add it with the passphrase, and mount the drive for the
   restore (the playbook makes it mount at every boot after this):
   ```bash
   apt update && apt install -y cryptsetup
   lsblk -o NAME,SIZE,MODEL,SERIAL,FSTYPE        # the 2 TB WD, crypto_LUKS
   dd if=/dev/urandom of=/root/.hl-backup.key bs=512 count=8 && chmod 400 /root/.hl-backup.key
   cryptsetup luksAddKey /dev/sdX1 /root/.hl-backup.key   # asks for the passphrase
   blkid -s UUID -o value /dev/sdX1                        # must match hl_backup_luks_uuid
   cryptsetup open --key-file /root/.hl-backup.key /dev/sdX1 hl-backup
   mkdir -p /mnt/hl-backup && mount /dev/mapper/hl-backup /mnt/hl-backup
   ls -t /mnt/hl-backup/dump/*.tar.zst | head -1            # the newest image
   ```
   The UUID belongs to the drive, so it only differs from `group_vars/all.yml`
   if this is a different drive.
4. **Run the playbook, naming the image.** Naming it matters: without it a
   missing CT 100 is created empty from the template.
   ```bash
   ansible-playbook site.yml -e hl_ct_restore_archive=/mnt/hl-backup/dump/<that file>
   ```
5. Mount the media drive (`mount /mnt/media`, or reboot m5) **before** CT 100
   is next restarted, or Jellyfin starts without its library.
6. **By hand, once:** `tailscale up --ssh --advertise-routes=10.0.0.0/24` on m5
   and approve the route in the admin console. Make a new pve-exporter token
   (the playbook prints the command) and update `PVE_TOKEN_VALUE` in
   `docker/monitoring/.env`. Write `/etc/homelab/healthchecks.env`.
7. **Check:** two `dig`s against 10.0.0.201, the dashboard, Healthchecks.

## 3. The host and the backup drive are both gone

Steps 1–2 above, skipping the drive. Then:

1. `ansible-playbook site.yml`: host, CT 100 from the template, Docker, the
   checkout, cron jobs. No stacks are started yet.
2. On CT 100, recreate `/root/.config/homelab/restic.env` from the password
   manager (`runbook-backups.md` §1 has the four lines).
3. Data back, then stacks up:
   ```bash
   ansible-playbook site.yml --limit docker -e hl_restore_offsite=true -e hl_bring_up=true
   ```
   `scripts/restore-offsite.sh` puts back every `.env` and the backed-up
   volumes, and never overwrites anything that exists.
4. **Postgres:** replay the dump into the fresh database
   (`restore-offsite.sh` prints the exact line, and can be re-run any time to
   see it again):
   ```bash
   gunzip -c /var/lib/homelab/restore/var/lib/homelab/offsite-staging/postgres/pgdumpall_*.sql.gz \
     | docker exec -i -u postgres postgres psql -q -d postgres
   ```
5. Host-side items from §2 step 6, and new drives per `runbook-backups.md`
   §2–3. Not restored, by design: Prometheus/Loki history, Ollama models
   (`docker/ai/load-models.sh` re-pulls), and the media library.

## The drill: prove it before you need it

A backup that has never been restored is a hope. Twice a year, and after any
large change to `ansible/`:

1. **Image restore**, tier C in `runbook-backups.md` §5: CT 999, no network.
2. **From-scratch rebuild** (scenario 3, without touching the lab): on m5
   make a throwaway CT 998 from the template with a spare LAN address, add it
   to a scratch inventory as the `docker` host, and run
   ```bash
   ansible-playbook site.yml -i scratch.yml --limit docker \
     -e hl_restore_offsite=true -e hl_install_cron=false
   ```
   Check every `.env` came back and the volumes exist, then
   `pct destroy 998 --purge`. **Never** add `hl_bring_up` here, and never let
   it install cron: the restored `.env` files hold the real Healthchecks URLs
   and webhooks, so a second copy of the lab would ping and post as the real
   one, and its AdGuard would answer DNS on the LAN.
3. Write the times in `docs/setup-log.md`. They replace the guesses in the
   table at the top.
