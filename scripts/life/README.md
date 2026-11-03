# scripts/life — read-only calendar and budget for Life sessions

Claude in the cloud (the Life project, claude.ai chats) can't reach the
tailnet-only budget MCP, and the calendar's public Cloudflare endpoint is being
retired (#330). The one pattern for both: a Life session starts Remote Control on
`docker (homelab)`, in **this folder**, and runs one of two scripts.

| Script | Prints | Reads through |
|--------|--------|---------------|
| `./calendar [YYYY-MM-DD] [DAYS]` | events: title, start, end, location | `mcp-calendar` container, same code and Home Assistant read as the calendar MCP |
| `./budget [YYYY-MM]` | account balances, category budgeted/spent/left | `mcp-budget` container, Actual API, read-only calls |

Both run inside their container, so the Home Assistant token and the Actual
password, sync ID and encryption key never reach the session or the output.

## Lock-down

`.claude/settings.json` here allows only those two commands and denies edits,
web access, `docker`, `git`, `curl`, `ssh`, `sudo` and a few ways to read
secrets. `CLAUDE.md` tells the session to do the job and stop. This is a
guardrail, not a sandbox: the session still runs as root on CT 100, and Anthony
approving the session is the real gate. Keep the budget MCP and the budget off
every public path.

## Using it

1. Add `/root/homelab/scripts/life` as a device folder (approve it once).
2. In the Life project, start Remote Control on that folder when you need the
   calendar or budget. It needs your approval tap each time.
3. A scheduled or unattended Life routine can't use this. That's the accepted
   tradeoff of keeping everything private.

## Check it by hand

```bash
cd /root/homelab/scripts/life
./calendar            # next 7 days
./budget              # this month
```
