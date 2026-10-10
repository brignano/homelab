---
name: alerting
description: Rules for this homelab's alerts, dead man's switches, metrics and dashboards. Use when adding or changing a Grafana alert rule, a contact point or notification policy, a blackbox probe, a Healthchecks ping, a textfile metric, a Grafana dashboard, or any scheduled job — and when an alert keeps firing after you thought it was fixed.
---

# Alerting

Lessons this lab paid for. Each rule names the failure that taught it; keep that
when you add one, because the reason is what lets the next person tell whether
the rule still applies.

**Nothing that runs on CT 100 can tell you CT 100 is down.** Grafana and the
assistant bot both live on the machine they watch, so a dead host is silent.
That gap is closed from outside by `scripts/heartbeat.sh` — a dead man's switch
that pings Healthchecks.io from cron, so *silence* is the signal. See
[`docs/design/tsd-alerting-off-box.md`](../../../docs/design/tsd-alerting-off-box.md).

- `#alerts` in Discord is fed by **webhooks only** (Grafana + Healthchecks),
  never by the assistant bot — routing through the bot would reintroduce the
  dependency the design removes.
- **Discord is the only delivery path.** ntfy was removed once Discord covered
  the same ground; `DISCORD_ALERT_WEBHOOK` is therefore `:?required` in
  `docker/monitoring/docker-compose.yml` so the stack cannot start unable to
  page you.
- When adding an alert path, ask which failures it can *not* report, and where
  that one is observed from.
- **Anything scheduled needs a dead man's switch, including the scheduler.** A
  job that only speaks when something is wrong cannot report never having run:
  `repo-sync.sh` was silent for three weeks because its cron entry had never
  been installed, and the box drifted three weeks behind `main` while every
  signal said fine. It now pings `HEALTHCHECKS_REPO_SYNC_URL` on every run, and
  `scripts/install-cron.sh` makes installing the schedule a command rather than
  a ritual (`--check` reports what is missing). `pg-backup.sh` was the last job
  without one; it now pings `HEALTHCHECKS_PG_BACKUP_URL`, and every job's
  crontab entry is a metric (`homelab_cron_job_installed`), so an uninstalled
  job alerts rather than waiting to be noticed.
- **A dead man's switch has to prove it is armed.** `HEALTHCHECKS_PG_BACKUP_URL`
  spent its first hours holding `https://hc-ping.com/your-uuid` — the
  placeholder from `.env.example`. Non-empty, so the script's "unset" warning
  never fired; pinged with `|| true`, so the 404 went nowhere; and no check
  existed, so nothing was ever late. `scripts/healthchecks.sh` is the answer:
  a value that is not a plausible ping URL counts as unset, a failed ping is
  reported rather than swallowed, and both leave `homelab_healthchecks_ping_success`
  behind for `hl-hc-ping-failing` to alert on. Read a ping URL through
  `hc_url`, never with an ad-hoc `sed`. The assistant's digest is watched
  differently, and deliberately: it writes a timestamp
  ([`app/stamp.py`](../../../docker/assistant/app/stamp.py)) that `heartbeat.sh` reads
  from the host into `homelab_digest_timestamp_seconds`, with
  `hl-digest-missing` alerting past 25h. No fourth external check — the
  box-being-down case is already watched from outside by `heartbeat.sh`
  itself, and what is left (bot up, schedule not firing) is narrow enough to
  see from inside. Only the **scheduled** run records it: an on-demand
  `/digest` would refresh the clock and hide a schedule that had stopped.
- **A job that detects something must leave the result behind, not just report
  it.** Discord answers "does this need me now" and is read once; a time series
  answers "is it still true", "how long has it been true" and "did the fix
  work", which is what you want when the 4am message has scrolled away. Every
  scheduled script writes what it already computed to
  `/var/lib/node_exporter/textfile` via `scripts/metrics.sh` — repo drift,
  config drift, container inventory, backup age. Emitting a metric must never be
  able to fail the job: every function there degrades to a no-op.
- **An empty dashboard and a healthy lab look identical.** Three of the seven
  dashboards here had been blank for an unknown length of time — they were built
  on Angular panels that Grafana 12 removed, and nothing errors when a panel
  plugin is missing. Same shape as a panel querying a metric nobody writes, or a
  probe pointed at a 404. `scripts/check-observability.sh` fails CI on all
  three, and anything the lab relies on is a committed dashboard in
  `grafana/dashboards/homelab/`, not a grafana.com ID pasted into a fetch
  script.
- **Blackbox probes must target a path the service answers 2xx on.** The
  `http_2xx` module treats anything else — including a 404 or a redirect — as
  down. This matters most for Caddy, which routes by Host header and sees the
  container name `caddy` from inside Docker: it serves `/health` for the probe
  and 404s everything else, and `scripts/check-probes.sh` keeps the two files in
  step.
- **Group notifications by `instance`, not by `alertname` alone.** Grouping is
  what decides how loud `#alerts` is: a notification group is re-sent whenever
  its membership changes, throttled to `group_interval`, so coarse grouping
  means one flapping probe re-announces every other firing alert alongside it.
  See `grafana/provisioning/alerting/policies.yml`.
- **The `summary` annotation is the alert.** Every rule gets one, written as a
  sentence with the value already interpolated ("docker-lxc /var is 87% full"),
  because that is the whole Discord message — `templates.yml` renders a heading,
  those sentences, and one line of timing, and drops Grafana's default dump of
  every label, value and URL. A rule with no summary falls back to its own name,
  which is readable but says nothing; write the sentence.
- **A Discord message's content renders ABOVE its embeds.** Grafana's Discord
  notifier puts `message` in the content and `title` in an embed, so a title set
  there arrives *under* the body — which is why the heading is the first line of
  the message and the embed title is left to be the one clickable element.
  Anything this repo sends itself (`repo-sync.sh`) uses an embed for the whole
  report instead, where title and description render in the order written.
- **`instance` is not identity.** Every alert derived from a textfile metric —
  stack drift, config drift, backup age, cron — carries
  `instance=node-exporter:9100`, because that is only where the metric was
  scraped. It is also what `policies.yml` groups on, so those alerts share a
  group and arrive as one message listing each subject. Name the subject in the
  summary (`{{ $labels.stack }}`, `{{ $labels.container }}`), never rely on
  `instance` to say what broke.
- **Before re-diagnosing an alert you already fixed, check it is deployed.**
  `prometheus.yml`, the Caddyfile and `grafana/provisioning/` are all
  bind-mounted, so `git pull` changes the files while the containers keep
  serving the old config — the repo looks right, CI is green, and Discord keeps
  firing. `scripts/probe-status.sh` answers this from the box in one command.
  The "Config mounts" section of `AGENTS.md` says why, and what to do about it.
