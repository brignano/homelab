# Ansible: the lab as code

One playbook, `site.yml`, describes both machines. The **Proxmox host** (m5)
gets apt sources, packages, Tailscale, the node exporter flag, the
pve-exporter user, drive mounts, the vzdump hook, job and storage, the weekly
fstrim, and CT 100 itself. **CT 100** gets locale, IPv6, the repo checkout,
Docker, the scheduled jobs and, when asked, its data and stacks.

Before this existed, all of that was commands in `docs/setup-log.md` and the
runbooks, typed once on the day each one was needed. The point is a host you
can rebuild in an afternoon instead of a weekend of archaeology. The
scenarios, in order of how bad the day is, are in
[`docs/runbook-rebuild.md`](../docs/runbook-rebuild.md).

## Running it

From the Mac (or WSL), with SSH keys already on both boxes:

```bash
brew install ansible            # or: pipx install ansible-core
cd ansible

ansible-playbook site.yml --check --diff   # what WOULD change; changes nothing
ansible-playbook site.yml                  # make it so
```

**Always `--check --diff` first.** Read-only probes still run in check mode,
so the drift report and the "say how to…" messages appear in both.

What a plain run does **not** do, on purpose. The house's DNS lives in CT 100,
so nothing that restarts it happens unless you ask:

| It never… | Unless you pass |
|---|---|
| changes CT 100's config (it reports drift instead) | `-e hl_apply_ct_config=true`, then `pct reboot 100` yourself |
| starts any stack | `-e hl_bring_up=true` |
| restores data from B2 | `-e hl_restore_offsite=true` (only fills in what is missing) |
| creates CT 100 from an image | `-e hl_ct_restore_archive=/mnt/hl-backup/dump/<file>.tar.zst` (only when CT 100 does not exist) |
| partitions, encrypts or formats a drive | never: `docs/runbook-backups.md` §2–3 by hand |
| writes a secret | never. It checks each one exists and names the step that makes it |
| pulls the repo on CT 100 | never: `repo-sync.sh` owns deploys |

## Where things are

| File | What |
|---|---|
| `group_vars/all.yml` | Every value that describes the lab: CT 100's size and devices, drive UUIDs, schedules, and the rebuild switches |
| `inventory.yml` | m5 (10.0.0.200) and CT 100 (10.0.0.201) |
| `roles/proxmox_host/` | The host. `ct100.yml` creates CT 100 or reports how it differs |
| `roles/docker_lxc/` | Inside CT 100. Calls the repo's own scripts (`bootstrap-docker.sh`, `install-cron.sh`, `restore-offsite.sh`, `bootstrap-plan.py`) rather than repeating them |

## Changing the lab

A change to anything on the host now has two halves: the change, and the line
in `group_vars/all.yml` or a role that makes it repeatable. If you `pct set`
something by hand, the next run reports it as drift. That is the reminder.

`scripts/host/hl-vzdump-hook` reaches m5 by running the playbook. That
replaces the `pct pull` the backup runbook used to ask for after every edit.

CI runs `ansible-playbook --syntax-check` and `ansible-lint` on every PR
(`.github/workflows/ci.yml`, job `ansible`).
