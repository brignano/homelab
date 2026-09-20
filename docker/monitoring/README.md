# Monitoring stack

Observability for the homelab: metrics (Prometheus), logs (Loki), and dashboards
+ alerting (Grafana). Alerts are delivered to Discord `#alerts` by webhook.

## Components

| Service | Role | Exposure |
|---|---|---|
| prometheus | Metrics store + scraper | LAN/tailnet `:9090` |
| grafana | Dashboards + unified alerting | `stats.$HOMELAB_DOMAIN` / `:3000` |
| node-exporter | LXC/Docker-host OS metrics | internal |
| cadvisor | Per-container metrics | internal |
| pve-exporter | Proxmox VE API metrics | internal |
| postgres-exporter | PostgreSQL metrics (read-only role) | internal + `core_core` |
| blackbox-exporter | HTTP + DNS probes | internal + `core`/`ai`/`proxy` |
| *(grafana `/metrics`)* | Grafana's own alerting + datasource internals | scraped on the `grafana` job |
| loki | Log store (30-day retention) | internal |
| alloy | Ships Docker + journal logs → Loki | internal (`127.0.0.1:12345` UI) |

node-exporter also serves whatever this repo's cron jobs write into
`/var/lib/node_exporter/textfile` (see `scripts/metrics.sh`). That is how facts
no exporter can see — did the backup run, is the repo behind, is a container
missing, is a container reading a config file that has since been replaced —
become metrics at all.

## Dashboards

Two folders, because the two kinds answer different questions.

**Homelab** — written for this lab, committed, each panel there because
something went wrong without it. Provisioned from `grafana/dashboards/homelab/`.

| Dashboard | The question it answers |
|---|---|
| **Triage** | Is anything broken right now? Open this one first; if it is all green, nothing else here is urgent. |
| **Deployment & Drift** | Is the lab running what git says? Tree behind GitHub, stack running old code, container serving a replaced config file. |
| **Scheduled Jobs & Backups** | Did the scheduled work happen, and is what it produced any good? Cron entries, dump age, dump size. |
| **Capacity & Headroom** | How much room is left — on the physical host *and* on CT 100, which is the one that actually fills up. |
| **Endpoints & DNS** | What the prober can reach, including whether AdGuard is still resolving names. |
| **PostgreSQL** | The handful of Postgres numbers worth having for a shared homelab database. |
| **Logs** | Container logs and the host journal, filtered by container. |

**Reference** — community dashboards pulled from grafana.com by
`scripts/fetch-dashboards.sh` into `grafana/dashboards/reference/`: Node Exporter
Full (140 panels), cAdvisor (92), Proxmox. Excellent once you know what you are
looking for, useless as a place to start — hence their own folder. Nothing in
them is maintained here; re-running the fetch script overwrites them wholesale,
and the revisions it pins live in `reference/REVISIONS`.

CI checks that every committed dashboard parses, carries a unique uid, uses no
panel type Grafana has removed, and asks only for `homelab_*` metrics something
actually writes (`scripts/check-observability.sh`).

## First-time setup

1. **Copy env and fill secrets**
   ```bash
   cp .env.example .env
   # set GRAFANA_ADMIN_PASSWORD, HOMELAB_DOMAIN, PVE_*, POSTGRES_EXPORTER_DSN,
   # DISCORD_ALERT_WEBHOOK
   ```
   `HOMELAB_DOMAIN` is the same value as in `docker/proxy/.env`. Grafana builds
   every link it sends out from it — the alert rule and silence links in a
   Discord alert, the embed button, dashboard links — so a wrong value means
   alerts that arrive but cannot be opened from the phone reading them.

2. **Create the Proxmox read-only token** (Datacenter → Permissions):
   - User `monitoring@pve`, API token `grafana` (disable privilege separation),
     permission on path `/` with role **PVEAuditor**. Put the token value in `.env`.

3. **Create the Postgres monitoring role** (on the core stack DB):
   ```sql
   CREATE ROLE monitoring WITH LOGIN PASSWORD 'strong-password';
   GRANT pg_monitor TO monitoring;
   ```

4. **Proxmox host IP** — `prometheus/prometheus.yml` is pre-set to `10.0.0.200`
   (host m5). Change it there if your host IP differs.

5. **Install node_exporter on the Proxmox host** (bare metal, Debian):
   ```bash
   apt install prometheus-node-exporter
   ```

6. **Install the scheduled jobs** (on CT 100, from the repo root). This also
   creates `/var/lib/node_exporter/textfile`, which node-exporter bind-mounts
   and the cron jobs write their metrics into — without it the Deployment and
   Scheduled Jobs dashboards are empty:
   ```bash
   ./scripts/install-cron.sh
   ./scripts/install-cron.sh --check    # report only, non-zero if any are missing
   ```

7. **Fetch the reference dashboards**
   ```bash
   ./scripts/fetch-dashboards.sh
   ```

8. **Bring it up**
   ```bash
   docker compose up -d
   ```

9. **Discord webhook**: Server Settings → Integrations → Webhooks → New Webhook
   → pick `#alerts` → Copy Webhook URL, and put it in `.env` as
   `DISCORD_ALERT_WEBHOOK`. This is required — with ntfy gone it is the only
   path an alert takes, so the stack will not start without it.

## Verify

- **Targets**: Prometheus → Status → Targets, every job `UP`
  (`curl -s localhost:9090/api/v1/targets`).
- **Dashboards**: open **Homelab → Triage**. Every tile green means the rest is
  optional reading.
- **Job metrics are flowing** — the Deployment and Scheduled Jobs dashboards are
  empty without them, and an empty dashboard looks exactly like a healthy one:
  ```bash
  ls -l /var/lib/node_exporter/textfile/          # heartbeat.prom, repo_sync.prom, pg_backup.prom
  curl -s 'localhost:9090/api/v1/query?query=homelab_cron_job_installed' \
    | python3 -c 'import json,sys; [print(r["metric"]["job"], r["value"][1]) for r in json.load(sys.stdin)["data"]["result"]]'
  ```
  A job reporting `0`, or missing entirely, is fixed with `./scripts/install-cron.sh`.
  `heartbeat.prom` appears within 5 minutes; the other two only after their
  first nightly run, so run them once by hand to avoid waiting.
- **Grafana's own metric names still match the panels.** Worth re-running after
  any Grafana upgrade, because the names move between versions and a panel that
  loses its metric renders a calm zero rather than an error:
  ```bash
  curl -s -o /dev/null -w 'status=%{http_code}\n' localhost:3000/metrics
  for m in grafana_alerting_rule_evaluation_failures_total \
           grafana_alerting_rule_group_rules \
           grafana_alerting_notification_latency_seconds_count \
           grafana_alerting_active_alerts \
           grafana_plugin_request_total; do
    printf '%-52s %s\n' "$m" \
      "$(curl -s localhost:3000/metrics | grep -c "^$m")"
  done
  ```
  A `0` against any name means that panel is dead. Use `curl` from the host
  rather than `wget -qO-` inside the container: `-q` silences connection
  failures too, so an empty result cannot be told apart from a missing metric —
  which is the whole failure this check exists to catch, and it bit us once
  already.

  Do not expect a notification *failure* counter. Grafana 13.2 exports the send
  histogram (`notification_latency_seconds`) but no success/failure counters, so
  delivery trouble is read off the Triage dashboard as alerts going active while
  notifications sent stays flat.
- **Logs**: Grafana → Drilldown → Logs, filter `{job="docker"}`.
- **Alerts**: Grafana → Alerting → Contact points → test `homelab`; a message
  should land in Discord `#alerts`. Since this is now the only delivery path,
  re-run this test after any change to the webhook.
- **Alert messages read heading-first**: emoji + rule name in bold, a blank
  line, one line per alert (the rule's `summary`), an italic timing line, then a
  small `-#` line of links — `alert rule ↗`, and `silence ↗` when the group is a
  single firing alert — with the `All alerts in Grafana →` embed beneath. If a
  test instead produces a wall of labels and URLs, Grafana is falling back to
  `default.message` — either the restart has not happened or `templates.yml`
  failed to parse. Alerting → Notification templates should list `homelab`.
- **The rule link goes to the rule, the embed goes to the list.** That split is
  not a preference: Grafana's Discord notifier hardcodes the embed's URL to
  `/alerting/list` and the contact point cannot override it, so the deep link
  has to live in the message body. Check a real alert's `alert rule ↗` lands on
  `/alerting/grafana/<uid>/view` for the rule that fired.
- **A test notification's `alert rule ↗` going to `/alerting/list` is correct.**
  The test alert has no rule behind it — no `__alert_rule_uid__`, no
  `GeneratorURL` — so the template falls through to the alert list, and no
  `silence ↗` appears beside it. Only a real alert exercises the deep link, and
  it cannot be faked: Grafana has no POST route for its built-in Alertmanager
  (only for external ones), so posting an alert answers "data source not
  found". To check the URL form alone, open `/alerting/grafana/<uid>/view` for
  any uid in `rules.yml`.
- **Contact points are what the file says**: the list should show `homelab`
  with exactly one integration (Discord). A leftover marked "Unused" means a
  provisioning deletion did not apply — see the note below.
  ```bash
  # Every provisioned receiver uid Grafana currently holds:
  curl -su admin:"$GRAFANA_ADMIN_PASSWORD" \
    localhost:3000/api/v1/provisioning/contact-points \
    | python3 -c 'import json,sys; [print(c["uid"], c["type"], c["name"]) for c in json.load(sys.stdin)]'
  ```

## When `#alerts` is noisy

Start by separating the three things that produce identical-looking Discord
traffic, because they have opposite fixes:

```bash
./scripts/probe-status.sh          # run on CT 100, from the repo root
```

It prints, in order: whether Prometheus is scraping the targets *this checkout*
declares, what each target answers when probed right now, and how many times
each probe has changed state in the last 6h.

| What it shows | What it means | Fix |
|---|---|---|
| `NOT SCRAPED` | Prometheus is running an older config than the repo | The script says which: a reload, or a recreate — see below |
| `DOWN` + 0 state changes | The probe is stuck — it has never passed, so it is asking the wrong question | Point it at a path the service answers 2xx on |
| `DOWN` + many state changes | The service really is bouncing | Fix the service |
| Everything `up`, messages continue | Grafana has not reloaded the provisioning | `docker compose restart grafana` |

A repeating Discord message is **not** evidence of a repeating failure. One
never-resolving alert produces a message every `repeat_interval` (4h) forever,
which reads as "firing constantly" and sends you looking for a flap that is not
there.

### `restart` and `reload` are not enough after a `git pull`

`prometheus.yml`, `blackbox.yml`, `loki-config.yml`, `config.alloy` and the
Caddyfile are bind-mounted **as single files**, and Docker resolves a file mount
to an inode when the container is created. git does not edit files in place — it
writes a new file and renames it over the old one — so `git pull` gives the path
a new inode and leaves the container mapped to the original, now unlinked.

The container then serves the old config indefinitely, and nothing says so:

```
$ git pull                                   # Already up to date.
$ curl -X POST localhost:9090/-/reload       # HTTP/1.1 200 OK
$ docker exec prometheus grep caddy /etc/prometheus/prometheus.yml
          - http://caddy:80/                 # ...the file from six days ago
```

`docker compose restart` does not help either — same container, same mounts.
**Recreate**, which is what re-resolves the mount:

```bash
docker compose -f docker/monitoring/docker-compose.yml up -d --force-recreate prometheus
```

`probe-status.sh` tells this apart from a plain missing reload by diffing the
container's copy against the one on disk, so you get the right command rather
than the plausible one.

`grafana/provisioning/` is mounted as a **directory**, which does not have this
problem — file replacements inside it are visible to the container. It only
needs `docker compose restart grafana`, because provisioning is read at startup.

## Notes

- **Grafana runs on `:latest`, and that has already broken dashboards once.**
  Grafana 11 disabled Angular panels by default and Grafana 12 removed them, so
  three community dashboards here (blackbox 7587, postgresql 9628, loki-logs
  13639) quietly stopped rendering — 10 of 11, 32 of 35 and 1 of 2 panels
  respectively. Nothing errored; they just went blank, which reads like a quiet
  lab. They have been replaced by committed dashboards, and
  `scripts/check-observability.sh` now fails CI on any removed panel type.
  Pinning the Grafana image would trade this for a different silence (an
  unpatched version nobody upgrades), so the check is the fix, not the pin.
- **A dashboard that is empty is not the same as a lab that is healthy**, and
  the two look identical. That is the reasoning behind most of what is checked
  in CI here: a panel querying a metric nobody writes, a dashboard using a panel
  type Grafana no longer ships, and a probe pointed at a path that returns 404
  all render as calm.
- **Textfile metrics persist until their writer runs again.** `repo-sync.sh` is
  daily, so a `homelab_config_drift` or `homelab_stack_deploy_drift_seconds`
  alert keeps firing until the next 04:00 run even after you have fixed the
  cause. Re-run `./scripts/repo-sync.sh` by hand to clear it immediately.
- **Each layer is watched by the one outside it, and that is the whole design.**
  Grafana watches the lab. Prometheus scrapes Grafana, so a rule that stops
  evaluating and a notification that fails to send are visible rather than
  silent — a rule whose query errors does not fire and does not warn, which
  until this existed was the one failure nothing in the stack could report.
  `up{job="grafana"}` covers the scrape itself failing. And `heartbeat.sh`
  covers all of it from off the box, because nothing running on CT 100 can
  report that CT 100 is gone. Adding a check means asking which failure it
  cannot see, and where that one is observed from.
- **Alerting is Discord-only.** ntfy previously ran here for phone push and was
  removed once Discord covered the same ground — see `docs/setup-log.md`. The
  box being *down* is still covered from off-box by `scripts/heartbeat.sh`,
  whose Healthchecks.io check alerts the same `#alerts` channel.
- **Changing alert provisioning needs a Grafana restart, and deletions need a
  directive.** Two separate traps, both silent:
  1. `grafana/provisioning/` is bind-mounted and read only at Grafana startup.
     Editing a file under it does not change the container's config hash, so
     `docker compose up -d` reports `Running` and changes nothing. Use
     `docker compose restart grafana`.
  2. File provisioning **upserts**. Removing a contact point, receiver or rule
     from a file does not delete it from Grafana's database — it lingers,
     marked "Unused", and the UI will not let you delete a provisioned resource
     either. Deletion needs an explicit `deleteContactPoints:` (or
     `deleteRules:`) block naming the uid. See `contactpoints.yml`.
