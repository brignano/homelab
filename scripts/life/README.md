# scripts/life — read-only calendar and budget for Life sessions

Claude in the cloud (the Life project, claude.ai chats) can't reach the
tailnet-only budget MCP, and the calendar's public Cloudflare endpoint is being
retired (#330). The one pattern for both: a Life session starts Remote Control on
`docker (homelab)` as the unprivileged `life` user, and runs one of two scripts.

| Script | Prints | Reads through |
|--------|--------|---------------|
| `./calendar [YYYY-MM-DD] [DAYS]` | events: title, start, end, location | `mcp-calendar` container, same code and Home Assistant read as the calendar MCP |
| `./budget [YYYY-MM]` | account balances, category budgeted/spent/left | `mcp-budget` container, Actual API, read-only calls |
| `./budget months [N]` | each category's spent/budgeted for the last N months (default 12, max 24) | same |
| `./budget payees [YYYY-MM[:YYYY-MM]]` | top payees by total spent, with counts (default last 3 months) | same |

`payees` prints names, totals and counts only: no dates, notes, accounts or
individual amounts. Payees seen once in the range are folded into one "other
one-off" line so a single purchase is never shown by itself; transfers, income and
off-budget accounts are left out.

Both run inside their container, so the Home Assistant token and the Actual
password, sync ID and encryption key never reach the session or the output.

## Lock-down

A folder `.claude/settings.json` did **not** hold. On 2026-11-03 a session started
in this folder ran `docker ps` with no prompt, and Docker access is root. So the
limit is the operating system's (Ansible role `docker_lxc`, `tasks/life.yml`):

- Life sessions run as a `life` Linux user in no group (not docker, not sudo), with
  no password.
- `life` can run exactly two things: `life-calendar` and `life-budget`. They are
  root-owned wrappers that `sudo` these two scripts, and one sudoers line allows
  only those. The scripts validate their own arguments and run inside the
  `mcp-calendar` and `mcp-budget` containers.
- Its session folder `/srv/life-session` and the settings in it are root-owned, so
  it can't edit them; the settings allow only the wrappers and refuse the rest
  (`dontAsk`). That is the second layer, not the first.

Worst case for a misbehaving Life session: it prints your calendar or budget. It
can't read `/root`, any `.env`, or Docker.

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
   for the calendar or budget.
3. A scheduled or unattended Life routine can't use this. That's the accepted
   tradeoff of keeping everything private.

## Check it by hand

```bash
# as root on CT 100:
/root/homelab/scripts/life/calendar     # next 7 days
/root/homelab/scripts/life/budget       # this month
# as the life user (this is what a Life session runs):
su - life -c 'life-calendar'
su - life -c 'life-budget'
su - life -c 'docker ps'                # must be refused
```
