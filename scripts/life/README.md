# scripts/life — calendar and budget for Life sessions (read-only, plus budgeted amounts)

Claude in the cloud (the Life project, claude.ai chats) can't reach the
tailnet-only budget MCP, and the calendar's public Cloudflare endpoint is being
retired (#330). The one pattern for both: a Life session starts Remote Control on
`docker (homelab)` as the unprivileged `life` user, and runs one of three scripts.
Two only read. `budget-set` (#416) makes one kind of change: a category's
budgeted amount for a month.

| Script | Prints | Reads through |
|--------|--------|---------------|
| `./calendar [YYYY-MM-DD] [DAYS]` | events: title, start, end, location | `mcp-calendar` container, same code and Home Assistant read as the calendar MCP |
| `./budget [YYYY-MM]` | account balances, category budgeted/spent/left | `mcp-budget` container, Actual API, read-only calls |
| `./budget months [N]` | each category's spent/budgeted for the last N months (default 12, max 24) | same |
| `./budget payees [YYYY-MM[:YYYY-MM]]` | top payees by total spent, with counts (default last 3 months) | same |
| `./budget-set <YYYY-MM> <category> <amount>` | month, category, budgeted before and after | same container; **writes** with `setBudgetAmount` only, then syncs |

`payees` prints names, totals and counts only: no dates, notes, accounts or
individual amounts. Payees seen once in the range are folded into one "other
one-off" line so a single purchase is never shown by itself; transfers, income and
off-budget accounts are left out.

All three run inside their container, so the Home Assistant token and the Actual
password, sync ID and encryption key never reach the session or the output.

## budget-set

- `<YYYY-MM>` must be this month or later. Earlier months are refused.
- `<category>` must name exactly one existing, non-income category (any case).
  No match or several matches exits non-zero, changes nothing, and lists the
  candidates.
- `<amount>` is the new budgeted amount in dollars (`250`, `250.5`, `250.50`), not
  a change. Negative or malformed amounts are refused before Actual is touched.
- It calls `setBudgetAmount` and `sync`, nothing else that writes: no
  transactions, transfers, moves, carryover, or creating, renaming or deleting.

Every change is appended to `/var/log/life-budget-set.log` on CT 100 (root only,
mode 0600, so `life` can't read it). Each line is tab-separated: UTC time, month,
category id, category name, before, after.

**To undo a change**, run `budget-set` again with the month, category and the
"before" value from its output or the log line.

## Lock-down

A folder `.claude/settings.json` did **not** hold. On 2026-11-03 a session started
in this folder ran `docker ps` with no prompt, and Docker access is root. So the
limit is the operating system's (Ansible role `docker_lxc`, `tasks/life.yml`):

- Life sessions run as a `life` Linux user in no group (not docker, not sudo), with
  no password.
- `life` can run exactly three things: `life-calendar`, `life-budget` and
  `life-budget-set`. They are root-owned wrappers that `sudo` these three scripts,
  and one sudoers line allows only those. The scripts validate their own arguments and run inside the
  `mcp-calendar` and `mcp-budget` containers.
- Its session folder `/srv/life-session` and the settings in it are root-owned, so
  it can't edit them; the settings allow only the wrappers and refuse the rest
  (`dontAsk`). That is the second layer, not the first.

Worst case for a misbehaving Life session: it prints your calendar or budget, or
sets budgeted amounts for this month or later, each one logged with its "before"
value. It can't touch transactions or accounts, read `/root`, any `.env`, or Docker.

## Using it

1. One-time setup on CT 100 (after this is merged and pulled): run the Ansible
   playbook (`docker_lxc` role), then sign `life` in once:
   `su - life`, `cd /srv/life-session && claude`, sign in, `/exit`, and run the
   playbook again so the `claude-rc-life` service starts.
   Also run `cd /srv/life-session && claude remote-control` once as `life` and
   answer `y` to "Enable Remote Control?", then Ctrl+C: the service has no keyboard,
   so without that answer it restarts in a loop. Needed again after a rebuild.
   Run the playbook with `claude-rc-life` stopped if it changes `life`'s UID.
2. In the Life project, start Remote Control on the box's `life` session and ask
   for the calendar or budget, or to set a category's budgeted amount.
3. A scheduled or unattended Life routine can't use this. That's the accepted
   tradeoff of keeping everything private.

## Check it by hand

```bash
# as root on CT 100:
/root/homelab/scripts/life/calendar     # next 7 days
/root/homelab/scripts/life/budget       # this month
# budget-set: pick a category, note its budgeted figure, change it, then set it back
m=$(date +%Y-%m)
/root/homelab/scripts/life/budget-set $m 'Some category' 123.45
/root/homelab/scripts/life/budget $m        # shows the new budgeted figure
tail -n 1 /var/log/life-budget-set.log           # the change, with its "before"
/root/homelab/scripts/life/budget-set $m 'Some category' <before>
# each of these must exit non-zero and change nothing:
/root/homelab/scripts/life/budget-set 2020-01 'Some category' 1     # past month
/root/homelab/scripts/life/budget-set $m 'No such thing' 1     # unknown
/root/homelab/scripts/life/budget-set $m 'Some category' -1    # negative
/root/homelab/scripts/life/budget-set $m 'Some category' 1.234 # malformed
# as the life user (this is what a Life session runs):
su - life -c 'life-calendar'
su - life -c 'life-budget'
su - life -c "life-budget-set $m 'Some category' <before>"
su - life -c 'cat /var/log/life-budget-set.log'  # must be refused
su - life -c 'docker ps'                # must be refused
```
