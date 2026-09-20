# Setup Log

Chronological record of significant configuration steps, decisions, and issues.

---

## Template — copy this block for each entry

```
## YYYY-MM-DD — <short title>

**Goal:** What you were trying to accomplish.

**Steps:**
1. …
2. …

**Issues encountered:**
- …

**Resolution:**
- …

**Notes / next steps:**
- …
```

---

## 2026-09-20 — The digest could not report not running

**Goal:** The digest pushes, so nobody checks it — which means the failure that
matters is not "I forgot to look", it is "nothing was posted and that looked
identical to a quiet morning". Close it the way every other scheduled job here
is closed.

**Steps:**
1. Established what was already covered. A dead container is:
   `heartbeat.sh` publishes `homelab_container_running{required="yes"}` for
   every container declared in any compose file, only `kali-linux` and
   `cloudflared` are optional, so `assistant` being down for 10m fires
   `hl-container-missing`. A wedged gateway socket fails the container
   healthcheck. What is **not** covered: bot up, healthy, connected, and 07:30
   passes with nothing posted — `tasks.loop(time=...)` has no catch-up, so a
   bot down at 07:30 and back at 07:35 skips that day in silence.
2. Added `app/healthchecks.py` and `HEALTHCHECKS_DIGEST_URL`. The **scheduled**
   run pings; a failed digest pings `/fail` rather than waiting out the grace
   period.
3. `/status` now reports the switch's armed state, masked.

**Issues encountered:**
- **The obvious wiring would have hidden the failure it exists to catch.**
  Pinging from `run_digest()` would mean `/digest` at three in the afternoon
  checks the switch in — so a schedule that had stopped firing would look
  perfectly healthy as long as somebody ran one by hand. Only
  `scheduled_digest` pings.
- **This container cannot write the metric.** The shell jobs leave
  `homelab_healthchecks_ping_success` behind for `hl-hc-ping-failing`; the
  assistant runs as uid 10001 and the textfile directory is root-owned, so that
  would need a mount and a uid change for something the external check already
  covers. Documented rather than bodged, and the Healthchecks check is the
  alarm for this one.
- **A switch has to prove it is armed**, and "I set that variable months ago"
  is not proof — which is why the armed state is one `/status` away rather
  than a thing you assume. The validation repeats `scripts/healthchecks.sh`'s
  rules exactly (empty, then shape, then placeholder) instead of inventing its
  own: `https://hc-ping.com/your-uuid` is the value that cost the `pg_dumpall`
  switch its first day, and it satisfies every naive check.

**Resolution:**
- Ten rejection cases in `tests/smoke.py`, including the bare host that answers
  200 forever while arming nothing. Verified by mutation: deleting the
  placeholder gate turns the suite red.
- A failed ping is logged and never raised. The digest is the product; the ping
  is the proof it happened, and a switch that could take the digest down with
  it would be a worse bargain than no switch.

**Notes / next steps:**
- Create the check at healthchecks.io (period 1 day, grace ~2h) pointed at the
  same `#alerts` webhook, paste the URL into `docker/assistant/.env`, and
  confirm with `/status` — it says `NOT ARMED` until you do.

---

## 2026-09-20 — The drift check could not fail, and two containers had been stale for a day

**Goal:** `deploy-plan.sh` kept flagging `prometheus.yml` and the `Caddyfile`
while `/deploy` step 4 said no container was serving stale config. One of them
was wrong. It was step 4.

**Steps:**
1. Read what the containers are actually mapped to, through
   `/proc/<pid>/root<dest>` — which resolves via the container's own mount
   namespace rather than the host path:

   ```
   prometheus  prometheus.yml  host=19400439  seen=19400327  DIFFERENT
   caddy       Caddyfile       host=19400601  seen=19400310  DIFFERENT
   ```

   Different inode **and** different content. `prometheus` had been running
   since 19 Sep 21:23 on a config superseded at 21:59; `caddy` since 20 Sep
   00:30 on a Caddyfile superseded by the 04:00 `repo-sync` pull — which is
   why the favicon routes from *"the mark was never served"* still were not.
2. Replaced `docker cp` with `/proc/<pid>/root` in step 4, and built the same
   comparison into `deploy-plan.sh` for file mounts.

**Issues encountered:**
- **`docker cp` resolves a bind mount back to its host source.** So step 4 was
  copying out the very file it then compared against, and `cmp` could only ever
  say "identical". It had reported all-clear for weeks, including on the
  morning two containers were provably stale. The check guarding this repo's
  single most documented failure had never once been able to detect it.
- **The two checks disagreeing is what exposed it**, and only because the
  timestamp heuristic was noisy enough to argue with. Three rounds of chasing
  its false positives ended in a true one.
- **Same bytes, different inode is its own state.** A container pinned to an
  unlinked copy is fine today and deaf to every future edit of that path.
  `deploy-plan.sh` reports it separately — worth knowing, not worth a recreate.

**Resolution:**
- File mounts are compared by content and inode; directory mounts and image
  build times stay timestamp-based, and both the script and the runbook now say
  which lines are findings and which are prompts to look.
- Tested with a stub pointing `.State.Pid` at a live process, one fixture whose
  mapped copy differs and one identical-but-relinked.

**Notes / next steps:**
- `prometheus` recreated unattended; `caddy` by hand, watching, after `dig`
  confirmed AdGuard was answering.
- Worth asking of every check here: what would make this report a problem? If
  nothing can, it is decoration. `docker cp` looked like evidence for weeks.

---

## 2026-09-20 — The planner blamed cadvisor's uptime on Grafana's dashboards

**Goal:** First real run of `deploy-plan.sh` after a pull that touched four
files reported 21 stale files across the whole monitoring stack — and
`docker/proxy changed`, on a pull that did not touch `docker/proxy`.

**Steps:**
1. Listed every container's start time. `cadvisor` and `sablier` have been up
   since **2026-07-30**; everything else restarted on the 19th or 20th.
2. That is the whole bug. State mode compared each container's start time
   against **every file under its stack directory**, and `docker/monitoring`
   holds nine containers. cadvisor mounts nothing from this repo, so it was
   charged with every monitoring file changed in the seven weeks since it
   started — Grafana's dashboards included, which it has never read.
3. Rewrote the comparison to use the files a container actually bind-mounts,
   plus the compose file that defines it. The image-staleness check is now
   gated on the stack having a `Dockerfile`, so a pulled image's upstream
   build date can never flag anything.

**Issues encountered:**
- **The false positive pointed at the household's DNS.** `docker/proxy` holds
  four tracked files and `git log --since` over the containers' start time
  shows no commit touching any of them, yet the planner said proxy changed —
  because caddy and adguard share a working directory with each other and
  with every file under it. The 2026-09-19 entry below is what acting on bad
  DNS evidence costs; a tool that manufactures that evidence is worse than no
  tool, so this was fixed the hour it appeared rather than filed.
- **It was reported as a deploy result, which is exactly how it should have
  surfaced** — but the runbook's own content-based drift check (step 4) had
  already come back empty, and the two answers disagreeing is what made it
  obviously a bug rather than a finding. Two checks that overlap are worth
  their cost.

**Resolution:**
- Per-container mounts, tested against a stub modelling the real shape: three
  containers sharing `docker/monitoring` with different mounts and one
  (cadvisor) mounting nothing of ours, plus caddy on `docker/proxy` and the
  image-baked assistant. Only files a container actually reads are flagged.

**Notes / next steps:**
- A container that writes into its own bind-mounted config directory will
  still look newer than itself forever. None of the tracked paths here are
  written by their container, but Homepage's `config/` is the shape that
  would do it — worth remembering before mounting a written-to directory.

---

## 2026-09-20 — Two verification instructions that could not verify what they claimed

**Goal:** Deploying the previous two entries turned up two checks that look like
proof and are not. Both were mine, written the same day.

**Steps:**
1. `templates.yml` said to verify with Alerting → Contact points → Test. The
   test message's `alert rule ↗` went to `/alerting/list`, which reads exactly
   like the bug the link was added to fix. It is not: Grafana's test
   notification is a synthetic alert carrying `alertname=TestAlert` and
   `instance=Grafana` and nothing else, so there is no `__alert_rule_uid__`
   and no `GeneratorURL` — the template falls through to its last resort,
   correctly. Documented in `templates.yml` and the monitoring README, with
   the fingerprint that confirms it (no `silence ↗` beside it, since Grafana
   builds SilenceURL from the same missing label).
2. Taught `deploy-plan.sh` state mode to ask the **image** when it was built,
   not only the container when it started, for stacks whose source is baked in.

**Issues encountered:**
- **A recreated container is not a rebuilt one, and start time cannot tell
  them apart.** Adding `GRAFANA_URL` to the assistant's compose file changed
  the *service definition*, so a plain `up -d` recreates the container — on
  the old image. It comes up healthy, connects to Discord, reports a start
  time newer than every file on disk, and runs last week's code. State mode
  called that current, on the very deploy it was written for. It now compares
  `docker image inspect .Created` against the files that go into an image
  (`app/`, `tests/`, `Dockerfile`, `requirements.txt`) and emits `--build`.
  Pulled images are never flagged: their stacks contain no such files.
- **There is no way to fake a real alert.** Two attempts at posting one into
  Grafana's Alertmanager failed differently — `bad request data`, because the
  body must be an object keyed `PostableAlerts` (the Go binding uses the field
  name; the swagger annotation advertises a bare array and is wrong), then
  `data source not found`, because Grafana registers POST
  `/api/alertmanager/{DatasourceUID}/api/v2/alerts` for **external**
  Alertmanagers only. There is no POST route for the built-in one. Both were
  guesses where the source was a fetch away; the third attempt read it.

**Resolution:**
- The deep link is confirmed as far as it can be without waiting: the URL form
  resolves (`/alerting/grafana/hl-disk-full/view` opens the right rule), the
  template is loaded (Grafana's rule page shows "Last updated 12:28:05", the
  restart), and the fallback chain was already verified offline against
  Alertmanager's real funcmap. The remaining branch — a live alert carrying
  the uid label — is what the next real alert proves.
- State mode's new branch was tested against a stubbed `docker` in three
  configurations: image older than source (flags `--build`), both newer
  (clean), both older (full plan).

**Notes / next steps:**
- The pattern under both: a check that cannot fail is not a check. The Test
  button exercises the message shape and the template parsing, and nothing
  about the links; a start time exercises the config and nothing about the
  image. Write down what each one is blind to, next to the instruction.

---

## 2026-09-20 — The deploy rules were a list in a runbook, so they became a script

**Goal:** `/deploy` decides the treatment by reasoning about each changed path —
`--build` for a stack baked into an image, a restart for Grafana's provisioning,
a *recreate* for the dashboard's icons, a human for proxy. That reasoning is
right, and it is the fourth hand-maintained copy of something the repo already
knows, done at the end of a deploy when attention is lowest. Make it derivable.

**Steps:**
1. Added `scripts/deploy-plan.sh`. Range mode (`deploy-plan.sh <rev>`) reads
   `git diff --name-only` and prints the commands. One rule table, used by both
   modes, so they cannot drift.
2. Added state mode (no argument), for the case that actually happens: you
   pulled, you did not capture `$BEFORE`, and now nothing knows what changed.
   It asks Docker when each container last started and compares that against
   the mtime of the files that stack owns — git rewrites a changed file on
   pull, so a file newer than the container reading it is a container on old
   config. No reflog, no memory, no git at all.
3. Taught it the upsert trap: a uid the diff removed from `provisioning/alerting/`
   with nothing left naming it. Checking for an *added* `deleteRules:` key was
   the obvious test and the wrong one — the normal fix adds a uid under a block
   that already exists. It checks whether the uid still appears in the file.
4. `/deploy` gains step 2a and an `ORIG_HEAD` recovery note in step 2; step 5
   now reads as the reasoning behind what 2a printed.

**Issues encountered:**
- **`set -e` and a trailing false conditional.** `[ "$quiet" = 1 ] && say "..."`
  as the last line of a function makes the function return 1 when the test is
  false, and the caller then dies. Every run that hit the proxy branch silently
  skipped the provisioning-deletion section. Found by running it, not by
  reading it. Now `if`/`fi`, with an explicit `return 0`.
- **Backticks inside double quotes are command substitution.** One status line
  read ``say "# ORIG_HEAD mode (the revision `git pull` left behind)"`` — which
  would have run `git pull` on the box, from a script whose entire promise is
  that it only reports. Single-quoted now, and the comment says why.
- **A fresh clone makes every file look new**, and `__pycache__` buried the two
  lines that mattered. State mode filters through `git ls-files` — only files
  git manages can change on a pull — and caps the listing at 15.
- **Timestamps cannot see an inode.** `docker restart` updates `StartedAt`
  without re-resolving a single-file bind mount, so state mode would report
  clean on exactly the failure `/deploy` step 4 exists for. The script says so
  in its output rather than leaving it to be inferred.

**Resolution:**
- Verified by hand across six scenarios, since CI only `sh -n`s it: two ranges
  (one with icons + proxy in it, one without), the real 2026-09-19 ntfy removal
  (fires), a synthetic removal *with* a delete directive (silent) and the same
  removal without one (fires), and state mode against a stubbed `docker` — both
  a container started after everything, and one started before.

**Notes / next steps:**
- CI parses it and nothing more. A functional test wants git history and a fake
  Docker; `actions/checkout` fetches depth 1, so range mode has nothing to diff
  against. Worth doing if the rule table grows.
- The rule table is now the only copy of these rules, which is the point — but
  it is also a thing that can fall behind a new stack. A stack whose source is
  baked into an image and is not matched by `app/|tests/|Dockerfile|requirements.txt`
  would get `up -d` instead of `--build`.

---

## 2026-09-20 — The alert link goes to the alert now, and the digest stopped saying everything twice

**Goal:** Two complaints about the same channel. The link at the bottom of every
Discord alert opens the list of every alert in the lab, never the one that just
fired. And the daily digest, read on a phone before coffee, is five bold lines
of equal weight with nothing marking which one is the problem.

**Steps:**
1. Found where the alert link actually comes from. Not `templates.yml`, not
   `contactpoints.yml`: Grafana's Discord notifier builds the embed's URL itself
   as `ExternalURL + "/alerting/list"`, hardcoded, with no contact-point setting
   to override it. The September 19 entry below fixed the *host* in that URL;
   the path was never ours to set. So the embed was always going to land on the
   list — the deep link has to go in the message body instead.
2. Added a `homelab.links` template emitting a `-#` subtext line: `alert rule ↗`
   to `/alerting/grafana/<uid>/view`, plus `silence ↗` when the group is a
   single firing alert. Three sources for the rule URL in descending order of
   trust — Grafana's own `.GeneratorURL`, the same URL rebuilt from the reserved
   `__alert_rule_uid__` label, and the alert list as the last resort, which is
   no worse than the old behaviour.
3. Renamed the embed title `Open in Grafana →` → `All alerts in Grafana →`. It
   goes where it goes; it should say so.
4. Rewrote `digest.render()`. Marks (🔴/🟠) on the lines that are wrong and
   nothing on the lines that are not, the down targets moved onto the Services
   line, the over-threshold gauge bolded in place, a `##` heading so a month of
   digests has visible day boundaries, and a footer of Grafana links chosen by
   what the digest found — Triage always, Endpoints/Capacity/Logs only when
   there is something on them.

**Issues encountered:**
- **The first digest draft printed everything twice.** It opened with a "Needs
  attention" block built from `facts.concerns`, then listed the same readings
  below. But concerns are *derived* from those readings — on a bad morning
  nearly every line appeared in both halves, which is exactly when a reader
  starts skimming. Marking the readings says the same thing in half the space.
- **A green tick on every line is the same as none.** Five 🟢 down the margin
  gives the eye nothing to land on. Unmarked now means fine.
- **A mark must not out-run the verdict.** `facts.py` deliberately does not
  count log noise as a concern — several containers here log an error a minute
  and are healthy — so the log line is never marked. A mark there would be the
  render claiming a conclusion the code never reached, which is the one rule
  this assistant is built around.
- **Grafana templates fail closed.** A template that does not parse takes the
  whole notification with it, and CI only checks that the file is valid YAML.
  So the template was parsed and rendered offline against Alertmanager's real
  `template.DefaultFuncs` — the funcmap Grafana builds on — over six cases:
  single firing, a group of three with one resolved, resolved, empty
  `GeneratorURL`, neither `GeneratorURL` nor the uid label, and an
  `ExternalURL` with a trailing slash (`reReplaceAll "/+$" ""` is what keeps
  that from producing `//alerting`).

**Resolution:**
- `templates.yml` gains `homelab.links`; `contactpoints.yml` gains an honest
  embed title; `digest.py` gains marks, inline detail and a `links()` helper;
  `GRAFANA_URL` is new in the assistant's env — blank renders the digest with
  no links rather than broken ones, since an internal `http://grafana:3000`
  would dead-end on every phone that opened it.
- Six checks in `tests/smoke.py`, including one asserting the marks and
  `facts.concerns` can never disagree. Verified by mutation: dropping the
  restart mark turns it red.

**Notes / next steps:**
- Masked links (`[text](url)`) render in Discord message content for bots and
  webhooks. They are confined to the footer on purpose — if that ever stops
  being true the alert lines themselves are still plain readable text, and the
  lock-screen preview is unaffected.
- Nothing in CI parses a Go template. The check above was run by hand; worth
  wiring up if these templates grow.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
# GRAFANA_URL=https://stats.<domain> in docker/assistant/.env
docker compose -f docker/monitoring/docker-compose.yml restart grafana
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
Then: Alerting → Contact points → `homelab` → Test (exercises both the firing
and resolved paths), and `/digest` in Discord.

---

## 2026-09-20 — `/` on CT 100 is on a four-day slope, and nothing had a ceiling

**Goal:** `hl-disk-filling` fired in `#alerts` — *docker-lxc / is on course to be
full within 4 days* — alongside `hl-stack-drift` for `proxy` (774h) and
`monitoring` (1243h). Work out what is consuming the disk and give it a bound.

**Steps:**
1. Read the rule before reading the disk. `hl-disk-filling` is
   `predict_linear(node_filesystem_avail_bytes[6h], 4d) < 0`, held for 30m: it
   says the **last six hours** of slope, extended four days, reaches zero. It is
   a statement about a *rate*, not about how full the disk is now —
   `hl-disk-full` (>85%) is the one that says that, and it is not in this
   batch. So: room left, and a leak.
2. Went looking for what in this repo has no ceiling. Prometheus keeps 30d and
   Loki keeps 30d, both configured, both long since at steady state. That left
   two, and both turned out to be unbounded by construction.
3. **Container logs.** `grep -rn "logging:" docker/` returns nothing across all
   nine stacks, and `bootstrap-docker.sh` never wrote an `/etc/docker/daemon.json`.
   `json-file` is Docker's default driver and its default `max-size` is
   *unlimited*; `restart: unless-stopped` means nothing rotates them on restart
   either. Every container in the lab has been appending to
   `/var/lib/docker/containers/<id>/<id>-json.log` since June.
4. **Images.** `docker image prune` appears nowhere in this repo — and did not
   need to, until 2026-09-19. Auto-pull landed that day (`89207c1`, `fd8f5f9`):
   four stacks now `compose pull` nightly and two rebuild with `--pull always`.
   A floating tag that moves does not delete what it moved off, so every run
   leaves the superseded image behind. The alert fired one day later.

**Issues encountered:**
- **Shipping logs to Loki looked like it already solved this, and does not.**
  Alloy reads containers through the Docker API (`loki.source.docker`), which
  does not truncate what it reads. The copy in Loki ages out at 30 days; the
  original never does. Having a log pipeline made the unbounded file easier to
  miss, not harder.
- **The obvious cleanup is the dangerous one.** `docker image prune -a` removes
  tagged images with no container on them — which on this box means the Kali
  webtop, because Sablier's whole job is to scale it to zero. It would be
  deleted nightly and re-pulled on the next visit. Bare `prune` (dangling only)
  is exactly the set a `pull` creates, and nothing else.
- **The alert is reporting the wrong ceiling, and always has.** CT 100's rootfs
  is 400 GB on a `pve/data` thin pool of ~348 GiB that cannot auto-extend, so
  the guest's free-space number is optimistic about the limit that actually
  exists. Every disk alert here arrives later than it should. The pool is
  watched by nothing — `pve-exporter` is in the stack and the API exposes it,
  but no rule was written, and this is not the repo to guess a metric name in
  (see the `hl-notifications-failing` note at the top of `rules.yml`).
- **A daemon-level log cap does not retro-fit running containers.** The driver
  and its options are fixed at container create time, so `systemctl restart
  docker` changes nothing for anything already up, and the existing log files
  survive a recreate too. Applying it is a recreate plus a deliberate look at
  what is already on disk.

**Resolution:**
- `scripts/bootstrap-docker.sh` writes `/etc/docker/daemon.json` with
  `json-file` at 10m x 3 — *before* installing Docker CE, so the daemon comes up
  with it rather than needing a restart `systemctl enable --now` would not
  perform. Daemon-level rather than per-stack `logging:` blocks, because the
  containers that most need the cap are the ones no compose file of ours starts
  (the Sablier-created webtop, anything run by hand). An existing `daemon.json`
  is never rewritten — it prints the two keys to add, since merging JSON from
  bash is how a config file gets corrupted.
- `scripts/repo-sync.sh` runs `docker image prune -f` and
  `docker builder prune -f --filter until=168h` after its pulls and rebuilds,
  and reports what each freed. Both best-effort: a failed prune must not fail a
  sync that has already deployed. Silent when it reclaims nothing, so the
  channel does not learn a daily "0B" line to ignore. Build cache is kept a week
  rather than dropped — `assistant` and `proxy` rebuild here, and a cold cache
  turns the nightly run's cheap rebuild into a full one.
- `AGENTS.md` gains a **Disk** section: the thin-pool ceiling and why the alert
  under-reports it, what grows and what now bounds it, why `-a` is forbidden
  here, and the one-time recreate that applies the log cap to a running lab.

**Notes / next steps:**
- **Run this on CT 100 before anything else** — the fixes bound future growth;
  they do not say what is on the disk today:
  ```bash
  df -h / && docker system df
  du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail
  du -sh /var/lib/docker/volumes/* | sort -h | tail
  ```
  A single huge `-json.log` is a container in a loop, which is its own bug and
  wants fixing at the source rather than capping. `docker system df` splitting
  the total between *Images* and *reclaimable* confirms or kills the auto-pull
  theory in one line.
- The thin pool deserves an alert. Get the metric name off the running exporter
  first, then write the rule — the command is in `AGENTS.md`.
- The two `hl-stack-drift` alerts in the same batch are the 2026-09-19 bug, not
  a new one: `proxy` (774h, `basis=image`) is on `HL_NO_AUTOHEAL` and has always
  needed a human, and `monitoring` (1243h, `basis=start`) is a stack whose
  containers `up -d` kept declining to replace. The `--force-recreate` fix
  merged in #86 clears both, but only once the box runs it.

---

## 2026-09-19 — The mark was never served, and `up -d` had been lying about deploying it

**Goal:** `home.brignano.io` showed a globe in a Chrome tab and a generic icon
on the installed app window, a day after the mark was drawn, committed and
passed CI. Find out why.

**Steps:**
1. Asked for one fact before touching anything: what does
   `/icons/favicon.svg` return in a browser. **404.** That ended the favicon
   investigation — nothing about markup, link order, SVG media queries or
   favicon caches can explain a file that is not being served — and started a
   serving one.
2. Read Next.js 15.5 rather than guessing. `setupFsCheck`
   (`server/lib/router-utils/filesystem.js`) walks `public/` **once at
   startup** into `publicFolderItems` and matches every later request against
   that Set. The refresh path exists and is behind `if (opts.dev)`. A file that
   appears in the mount afterwards is a 404 no matter how current the mount is.
3. Checked the timestamps, which settled it. `docker-compose.yml` last changed
   at 19:48; `favicon.svg`, `favicon.ico`, `favicon-32.png` and
   `apple-touch-icon.png` were all added at 20:27, the tile icons at 20:00 and
   22:52. The running process enumerated the directory at 19:48 and has been
   serving that list ever since.
4. Found why the daily deploy never fixed it. `repo-sync.sh` heals a stale
   bind-mount stack with `docker compose up -d`, and Compose recreates by
   hashing the *service definition* — image, env, ports, mount source and
   target. Bytes behind a mount are not in that hash and cannot be. With the
   image pinned and the compose file unchanged, `up -d` found nothing to do and
   exited 0. `verify_stack` then counted containers that had never stopped and
   reported a successful heal. Now `up -d --force-recreate`, on both branches —
   `proxy` has the same hole from the other direction: it bind-mounts the
   Caddyfile, the Dockerfile does not `COPY` it, and a fully cached rebuild
   yields the same image id, so `--build` does not rescue it. `proxy` is on
   `HL_NO_AUTOHEAL`, which means the broken command was the one printed for a
   human to run.
5. Added the web app manifest, which is a second gap with the same symptom.
   Chrome draws an installed app's titlebar, taskbar entry and install dialog
   from the manifest and never from `rel="icon"`, so none of the previous day's
   work reached that surface even once the files were served.

**Issues encountered:**
- **Every icon in the repo was a 404, not just the favicon.** The tile icons
  landed after the same cutoff, so Grafana's G and the rest had been blank too.
  Nobody reports a missing tile icon the way they report a missing one.
- **The heal loop had never healed anything.** `StartedAt` never moved, so the
  stack was flagged stale again the next day, "fixed" again, and
  `homelab_stack_deploy_drift_seconds` climbed the whole time — the metric was
  telling the truth and reading as noise next to a success line.
- **The documented fix made the symptom worse.** `icons/README.md` said to
  cache-bust by renaming. A new name is exactly what the frozen list cannot
  serve, so the remedy for a stuck icon reliably produced no icon.
- **The compose file asserted the opposite of what happens.** Its comment
  argued for a directory mount so a `git pull` alone would pick up a new icon.
  That is true of Docker and false of the application on top of it, and the
  comment was confident enough that nobody looked past it.
- **The manifest override cannot be a file mount.** Homepage hard-codes
  `/site.webmanifest?v=4` in its `_document` and bakes its own logo into the
  image. Rewriting the root path in Caddy is the same technique already used
  for Safari's probes, and it avoids the stale-inode problem.
- **Caddy sorts directives, so "after the rewrite" is not a thing.** `caddy
  adapt` on the site block shows `headers` emitted ahead of every `rewrite`, so
  a Content-Type matcher on `/icons/site.webmanifest` would never have fired.
  The matcher names both spellings.

**Resolution:**
- `scripts/repo-sync.sh` adds `--force-recreate` to both heal commands, so a
  config change that lives inside a bind mount is actually deployed.
- `scripts/gen-dashboard-icons.py` also writes `icon-192.png`, `icon-512.png`
  and `icon-maskable-512.png`; `icons/site.webmanifest` names them and the
  Caddyfile rewrites `/site.webmanifest` onto it.
- `check-dashboard.sh` now validates the manifest's JSON, checks every
  `icons[].src` exists and is mounted, and fails if the Caddyfile stops
  rewriting the root path — the last one matters because dropping it serves
  Homepage's logo rather than a 404.
- The `public/` startup-scan trap is written at the top of `icons/README.md`
  and beside the mount in `docker-compose.yml`, with the one-request check
  (open `/icons/favicon.svg`) that tells it apart from a cache.
- Verified: `caddy adapt` accepts the site block and puts the rewrite in;
  `check-dashboard.sh` passes and fails correctly when the manifest names a
  file that is not there; every raster re-rendered and inspected.

**Notes / next steps:**
- The `.ico` was being built by handing Pillow one 48px render plus a `sizes=`
  list, which downsamples that one image — so the 16px frame, the one a tab
  actually draws, was a resampled 48 with its rounded corners smeared. The
  original rendered all three sizes from the vector and then used only the
  first; `append_images` passes the other two, which is what the dead code was
  reaching for.
- CI cannot catch this class of bug: the repo was correct the whole time. What
  would catch it is asking the box what it serves — a probe on
  `/icons/favicon.svg` belongs with the other check-probes.
- Deploying this needs the recreate it describes, and the running container
  predates the manifest:
  `docker compose -f docker/dashboard/docker-compose.yml up -d --force-recreate`
  plus `docker compose -f docker/proxy/docker-compose.yml up -d --force-recreate`
  for the Caddyfile.

---

## 2026-09-19 — The dashboard was "down" for one browser, and the lab was fine

**Goal:** `home.brignano.io` stopped loading and appeared not to resolve. Find
out what broke.

**Steps:**
1. Checked the zone from off-box first: `home.brignano.io`,
   `stats.home.brignano.io` and an invented wildcard name all resolved to
   `10.0.0.201`, so the Cloudflare records — including the bare name's own `A`
   record — were intact.
2. From the laptop: `dig @1.1.1.1` and `dig @10.0.0.201` both answered, and
   `ping 10.0.0.201` replied. Resolver, subnet route and host were all up.
3. On the box: `curl --resolve home.brignano.io:443:10.0.0.201` returned `200`,
   `dashboard` had been up for three hours, and Caddy was renewing certificates
   on schedule.
4. The same `curl` from the laptop also returned `200` — full dashboard HTML,
   `via: 1.1 Caddy` — while Chrome on that same machine, at that same moment,
   showed `ERR_ADDRESS_UNREACHABLE`. Safari loaded it.
5. That narrowed it to Chrome. Ruled out `AAAA` records (none exist on any of
   these names, so no happy-eyeballs fallback to a dead IPv6 address) and
   Chrome's Secure DNS (turned off, still failed).
6. **Privacy & Security → Local Network** listed *ten* `Google Chrome` entries,
   all toggled on. Quitting Chrome fully and reopening it fixed the site.

**Issues encountered:**
- **Two failures that look identical from a browser.** A lab that is down and a
  lab a browser cannot open render the same way, because `*.$HOMELAB_DOMAIN`
  resolves publicly and points at a private address. The name looking up fine
  and the page not loading is the *designed* behaviour off-LAN — so it carries
  no information about whether the box is alive.
- **macOS grants local-network access per application.** Chrome's auto-updater
  creates a fresh TCC entry on each update, they accumulate, and the grant for
  the binary actually running goes stale while every entry in the list still
  reads "on". Chrome alone then cannot open connections to `10.x`; the kernel
  returns host-unreachable and Chrome renders `ERR_ADDRESS_UNREACHABLE`. Public
  sites keep working, so nothing else in the browser looks wrong. Safari is a
  system app and is always allowed, and Terminal holds its own grant — which is
  why `curl` and Safari worked side by side with a browser that could not.
- **Diagnosis reached for the resolver and made things worse.** On the theory
  that AdGuard was down, `docker compose up -d --force-recreate adguard` was
  run — on evidence that already showed AdGuard answering. It takes a few
  seconds to bind `:53`, the verification `dig` was fired immediately, and
  `connection refused` came back. That reads exactly like a recreate that did
  not come back. The household briefly had no DNS, caused entirely by the
  attempt to fix DNS that was never broken.

**Resolution:**
- Nothing in `docker/` changed. Every stack was healthy throughout.
- `.claude/commands/deploy.md` now retries the post-recreate DNS check instead
  of querying once, and says outright that a `proxy` recreate must be justified
  by evidence that something is actually broken.

**Notes / next steps:**
- **The split test is `curl` versus the browser, on the same machine.** It costs
  one command and separates "the lab is down" from "this client cannot reach
  it" — which is the first fork in the tree and the one that was skipped here.
  Everything else follows from which way it goes.
- A single failed query against `10.0.0.201` is not evidence of anything. Two,
  seconds apart, are.

---

## 2026-09-19 — The backup's dead man's switch was pinging `your-uuid`

**Goal:** Wire up the two Healthchecks checks that had never been created, and
then fix the reason one of them went unnoticed.

**Steps:**
1. Created `repo-sync` and `pg-backup` checks (1d period, 6h grace) and put
   their ping URLs in `docker/monitoring/.env`. Ran `./scripts/pg-backup.sh` by
   hand to prove the whole path rather than the variable: it dumped, pinged,
   and the check went green.
2. Then fixed what let the bad value sit there: added `scripts/healthchecks.sh`,
   routed all three jobs through it, and added `hl-hc-ping-failing`.

**Issues encountered:**
- **`HEALTHCHECKS_PG_BACKUP_URL` was `https://hc-ping.com/your-uuid`** — the
  placeholder straight out of `.env.example`, and every layer agreed it was
  fine. `pg-backup.sh` warned only on an EMPTY value, and a placeholder is not
  empty. The ping was `curl -fsS ... || true`, so the 404 went nowhere. No such
  check existed, so nothing was ever late. The job guarding the only copy of
  the data had a switch that pinged the void and was indistinguishable from one
  that worked.
- **A second `HEALTHCHECKS_REPO_SYNC_URL`** had been pasted pointing at the
  *heartbeat's* check. Harmless only by luck: the reader ends in `tail -n1`, so
  the later line won. One reordering away from repo-sync checking the box in
  once a night on a switch that fires after fifteen minutes of silence.
- **The obvious fix was the one already tried.** `pg-backup.sh` was printing its
  warning to a cron log, which is what it did with the placeholder in place —
  except the warning never fired. Writing it more loudly would have changed
  nothing.

**Resolution:**
- `scripts/healthchecks.sh`, sourced after `metrics.sh`, is now the only way a
  ping URL is read. `hc_url` rejects a value with no path (`https://hc-ping.com`
  alone answers 200 — a ping that succeeds forever while arming nothing) and
  the placeholder shapes, and says which variable and why. `hc_ping` reports a
  failed ping instead of swallowing it.
- Both outcomes publish `homelab_healthchecks_ping_success{job=...}`, and
  `hl-hc-ping-failing` alerts on it after an hour — so a switch that stops
  arming pages the way everything else here does, rather than waiting to be
  noticed. That is the part the previous two attempts at this lacked.
- URLs are masked to scheme, host and eight characters in every message. They
  are capability URLs: anyone holding one can check a job in, which is exactly
  how you would hide a box that had stopped.
- `heartbeat.sh` still exits non-zero when it cannot deliver — that ping *is*
  the job. The two jobs that ping from an EXIT trap discard the result, so a
  failed ping cannot overwrite the verdict of the work itself.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
./scripts/heartbeat.sh && echo armed          # all three read through hc_url now
docker compose -f docker/monitoring/docker-compose.yml restart grafana
```
The restart is for the new alert rule; the scripts need none.

**Notes / next steps:**
- The API was nearly wrong in the way this file is about. `hc_url` first
  returned the URL on stdout, so callers wrote `URL=$(hc_url ...)` — a
  **subshell**, where `HC_REASON` was set and then discarded with it, leaving
  all three callers reporting an empty reason. Found by testing the rejection
  path; it renders correctly in the Discord report now because of that test.
- Tested against a local sink answering 204 on one path and 404 on another:
  nine URL shapes (valid, placeholder, angle brackets, no path, trailing slash,
  quoted, empty, duplicated key, missing key), both ping outcomes, the `/fail`
  body path, and a box with no textfile directory, where it degrades to a
  no-op rather than failing the job.
- The Discord integration turned out never to have existed at all: the
  Integrations page held one entry, email. The heartbeat check has therefore
  been alerting an inbox since 2026-08-21, not `#alerts`, and the 2026-08-21
  entry's claim that it was wired to the webhook was wrong the day it was
  written. Added as a project-level Discord integration (`basecamp`), which
  Healthchecks assigned to all three checks.
- **And then tested the failure path**, which is what that entry asked for and
  never got: `curl .../fail` on the repo-sync check, then a plain ping. Both the
  down alert and the recovery arrived in `#alerts`. The off-box half of this
  lab's alerting had never once delivered a message before 22:34 tonight.
- Worth adding later: `homelab_healthchecks_ping_success` on the jobs
  dashboard. The alert covers "it broke"; a panel answers "has it ever worked",
  which is the question this entry is really about.

---

## 2026-09-19 — The one clickable thing in an alert pointed at a name phones can't resolve

**Goal:** Every Discord alert ends in an `Open in Grafana →` link. It went to
`http://stats.home`, not `https://stats.$HOMELAB_DOMAIN`.

**Steps:**
1. Traced the link back past the template: it is not written in `templates.yml`
   or `contactpoints.yml` at all. Grafana builds every outbound URL — the embed
   link, silence links, dashboard links — from `GF_SERVER_ROOT_URL`, which
   `docker/monitoring/docker-compose.yml` had set to `http://stats.home` from
   before the real domain existed.
2. Set it to `https://stats.${HOMELAB_DOMAIN:?required}` and added
   `HOMELAB_DOMAIN` to `docker/monitoring/.env.example` (same value as
   `docker/proxy/.env`) and to the README's first-time setup step.

**Issues encountered:**
- **The legacy redirect does not rescue this one.** `docker/proxy/Caddyfile`
  still serves `http://stats.home` and 301s it to the real name, so on a laptop
  using AdGuard the old link worked and the bug looked cosmetic. Alerts are read
  on a phone, and a phone on cellular resolves `.home` nowhere — the link
  dead-ends in DNS, before any redirect can run. The failure was invisible in
  exactly the place the link exists for.
- The real name does not make Grafana public: `*.$HOMELAB_DOMAIN` resolves from
  anywhere but points at 10.0.0.201. Off-tailnet the link now fails as "can't
  connect" rather than "no such host", and works the moment Tailscale is on.

**Resolution:**
- `GF_SERVER_ROOT_URL: https://stats.${HOMELAB_DOMAIN:?required}`. `:?required`
  rather than a default, because a silently wrong root URL still delivers alerts
  — it only breaks the link inside them, which is the part you find out about
  while standing up at 3am. CI already supplies `HOMELAB_DOMAIN`, so the compose
  config check covers it.

**Notes / next steps:**
- `HOMELAB_DOMAIN` is now in three stacks' `.env` files (proxy, dashboard,
  monitoring). Copies drift; worth a single source at some point.
- The `.home` names left in `shell/aliases.*` and `docker/mcp/README.md` are
  deliberate — those run on machines that do use AdGuard. The Caddyfile's legacy
  redirect block can go once they do too.

---

## 2026-09-19 — The other two tiles, and the part CSS can do that a filter cannot

**Goal:** Finish what the brignano.io tile started. Portainer and Open WebUI had
the same disagreement — the file asks the OS, the dashboard answers to its own
theme — and the entry below left them unfixed because inverting a colour logo is
not a re-ink.

**Steps:**
1. Checked what CSS can actually do to an `<img>` before designing around it,
   in headless Chromium against real files: `content: url(...)` **replaces** the
   drawing (this is the element-level `content`, the one WebKit and Blink
   shipped for real elements and Gecko later followed). That is the whole fix —
   the page picks the file, the file stops guessing.
2. `scripts/compose-icon.py` now writes three files per dual-drawing mark
   instead of one: `<name>-on-light.svg`, `<name>-on-dark.svg`, and the combined
   `<name>.svg` it already wrote. Named for the card the drawing lands *on*,
   because upstream's own `-dark` / `-light` suffixes mean opposite things
   across the set.
3. `config/custom.css` swaps the tile between the two singles on `data-theme`,
   the same key the monogram uses.
4. Regenerated both icons at the pinned SOURCE ref — `rm` the two files and
   re-run `update-tile-icons.sh`, which refetches only what is missing. The
   combined files came back byte-identical apart from their comment header,
   which is the reproducibility check worth having.

**Issues encountered:**
- **A four-line list of filenames in a stylesheet is a list, and lists rot.**
  Composing a third of these and forgetting `custom.css` would leave it looking
  exactly like the bug being fixed. `check-dashboard.sh` now fails both ways: a
  `-on-dark.svg` not named in `custom.css`, or a path in `custom.css` with no
  file. Verified by breaking it on purpose — the check exits 1 and names the
  pair.
- **Blink already propagates `color-scheme` into an embedded SVG.** So in
  Chrome the combined file was often right, and the OS is not strictly the
  question there. Not built on: one engine, and it needs `custom.js` to have
  run. Written down in the icons README so the next person does not re-derive
  it.
- **Headless Chromium lies quietly.** `--blink-settings=preferredColorScheme`
  is `0=dark, 1=light` (not 1/2 as first assumed), localhost goes through the
  agent proxy unless `--no-proxy-server`, and a small `--window-size` with
  `--force-device-scale-factor=2` screenshots a blank page. Every one of those
  produced identical PNGs across cases that should have differed — the same
  false all-clear an empty dashboard gives.

**Resolution:**
- Rendered the matrix: dark page and light page are each **pixel-identical
  under a light OS and a dark OS** — the OS no longer changes anything. The
  fallback page (no `data-theme`) still differs between the two, which is the
  old behaviour, kept on purpose for a browser that will not swap.

**Notes / next steps:**
- Adding another mark upstream draws twice: `COMPOSITES` in
  `update-tile-icons.sh`, then four lines in `custom.css`. CI names the second
  step if it is missed.

---

## 2026-09-19 — The brignano.io tile was invisible on the theme it ships with

**Goal:** The dashboard's own site tile could not be seen. `theme: dark` is what
the dashboard opens in, and the `A|B` monogram was drawing dark on it.

**Steps:**
1. Read the drawing rather than the card. `icons/brignano.svg` inked itself
   `n-900` and lifted to `n-0` under `@media (prefers-color-scheme: dark)` — and
   that query is answered by the **OS**, because an SVG referenced by `<img>` is
   its own document and cannot see this page. Homepage ignores the OS entirely.
   So a browser in light mode asked for the light-ground drawing and got
   #111111 on a near-black card: the icon was there, at 1.2:1.
2. Made the mark stop guessing. One ink in the file, no media query, and a rule
   in `config/custom.css` that inverts it when `data-theme` is `dark` — the
   attribute `custom.js` already mirrors from whatever theme Homepage settled
   on, and the same one the design tokens read.
3. Rendered both themes in headless Chromium against the real file before
   pushing: ink on the light card, #eeeeee on the dark one (measured 16.73:1
   against `--card`).

**Issues encountered:**
- **The README predicted this and it still shipped.** `icons/README.md` already
  said the OS switch and Homepage's toggle "agree unless the dashboard is pinned
  to a theme the phone is not in" — which, with `theme: dark` pinned, is the
  default state for anyone whose phone is in light mode. A known limit written
  down next to the thing it breaks is not the same as a handled one.
- **`filter` cannot take a token**, so this is the one bridge in `custom.css`
  that does not name one. `invert(1)` on the single ink lands 2/255 off
  `--ink`'s dark step, which is why the mark stays one colour — the moment it
  is two, inverting it stops being a re-ink.

**Resolution:**
- `icons/brignano.svg` is one ink; `config/custom.css` re-inks it from the page.
  `scripts/check-dashboard.sh` still passes — no file moved.

**Notes / next steps:**
- `portainer.svg` and `open-webui.svg` have the same disagreement and are not
  fixed: each is two whole vendor drawings switched by the same OS query, and
  inverting a colour logo is not a re-ink. On a light-mode OS with the dashboard
  in dark, Portainer's mark is dark-on-dark again — the exact thing composing
  the two drawings was meant to end. The fix is two files per icon and a `src`
  swap in `custom.js`, which is worth doing the next time that pipeline is open.

---

## 2026-09-19 — The heading was arriving after the message it was heading

**Goal:** Two follow-ups from reading the new alerts in the channel rather than
in a template: they start mid-sentence, and the nightly `repo-sync.sh` report
next to them is still a stack of code fences.

**Steps:**
1. Looked at a real message instead of the rendered template. Grafana's Discord
   notifier puts `message` in the Discord **content** and `title` in an **embed**
   — and Discord renders content *above* embeds. So the heading written this
   morning was arriving underneath the lines it was meant to head.
2. Moved the heading into the message: emoji + bold rule name, blank line, one
   line per alert, timing. The embed title became `Open in Grafana →`, which is
   the one job it is actually good at — it is the only clickable element.
3. Rewrote the `repo-sync.sh` report as an embed with a headline, bullets and a
   colour, and exercised it against a local webhook sink.

**Issues encountered:**
- **`instance` was in the alert title, and it is not identity.** A real alert
  read `Stack running old code · node-exporter:9100` — true and useless: every
  textfile-metric rule (stack drift, config drift, backup age, cron) carries
  that instance, because that is merely where the metric is scraped from. The
  subjects were `proxy` and `monitoring`, and they were already named in the
  summaries.
- **That same shared instance means groups of several are normal**, despite
  grouping on `instance` — which the previous layout, built around one alert per
  message, handled by listing lines under no heading at all.
- **Code fences do not wrap.** The report's lists were fenced so their alignment
  survived; on a phone that turns three stack names into a horizontally
  scrolling grey slab. Fences now appear only for raw error output and commands
  meant to be copied.

**Resolution:**
- Alerts: heading, blank line, one line per alert (bulleted only when there is
  more than one), italic timing. No `instance` anywhere — the summary names the
  subject, which is now written into `AGENTS.md` as a rule for new alerts.
- Report: a Discord embed rather than content, because inside an embed the title
  renders first, in the order the thing is read. It also raised the character
  budget from 2000 to 4096, so `MAX_CHARS` went 1800 → 3800 and fewer reports
  get truncated.
- The report opens with a headline — `pulled 4 commits · restarted 2 stacks · 1
  stack needs you` — and carries a colour from the design system's semantic
  tokens (dark-surface step, since a Discord embed is read on a dark card):
  `--success` when the run only deployed, `--attention` when something wants a
  person, `--danger` when something failed.
- The dead man's switch nag moved to the end as one italic line. It is a
  standing condition, not tonight's news, and it was leading the report.
- The last bare IP in a message was in the `Target down` summary itself, which
  printed `instance`. Three targets scrape an address rather than a container
  name — both node jobs and pve — so the summary now prefers the `host` label
  where there is one, and `pve` gained the label the node jobs already carried.
  "pve target proxmox is DOWN" needs no lookup before it can be acted on.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/monitoring/docker-compose.yml restart grafana
```
The report needs no restart — cron runs the script from the working tree.

**Notes / next steps:**
- Tested by slicing the real report section out of `repo-sync.sh` with `sed` and
  running it against a `python3` HTTP sink, which both proves the hand-rolled
  JSON escaping survives quotes and backslashes in a git error message, and
  prints what Discord would render. Five shapes: silent, healthy deploy, needs
  you, failure, image update.
- Not changed: `policies.yml`. Grouping on `instance` was worth questioning now
  that it is known to be shared, but the outcome — every stack-drift alert in
  one message rather than one message each — is the behaviour that was wanted
  anyway.

---

## 2026-09-19 — Two cron jobs were mailing their output to nobody

**Goal:** Close the last gap in the scheduled-job story: the jobs were installed
and watched, but two of them threw away the only thing they can say when their
own reporting is what broke.

**Steps:**
1. `install-cron.sh --check` reported all three jobs `ok`. Reading the entries it
   printed showed only `pg-backup` had `>> /var/log/... 2>&1`; `heartbeat` and
   `repo-sync` had none. Those two predate this script, which only ever added
   redirection to entries it created itself.
2. Cron mails a job's output to a local mailbox that nobody reads and no MTA
   delivers. `repo-sync.sh` reports to Discord — but a run that cannot *reach*
   Discord (unreadable `.env`, curl failing, the webhook rejected) says so on
   stderr and nowhere else. That is exactly the run whose output was going in the
   bin.

**Issues encountered:**
- The header promised an existing entry is "never rewritten, only reported",
  which is right for a deliberately moved hour or a custom log path — but an
  entry with *no* redirection is not a choice, it is the absence of one. The
  invariant needed narrowing rather than keeping or discarding.
- Testing that surfaced a second, older bug: `grep -q "scripts/$script"` matches
  **commented-out** lines, so a job someone had disabled with a `#` reported as
  scheduled. A disabled job that reads as healthy is the same failure this
  script was written for, one `#` further along.

**Resolution:**
- An entry containing any redirection is still left untouched. One with none gets
  the redirection appended and nothing else about the line changed; `--check`
  reports it and exits non-zero rather than fixing it.
- Commented-out lines no longer count as scheduled: the job is treated as absent,
  so it is reinstalled and `--check` reports it. The comment itself is left in
  place — it is somebody's note.

**Notes / next steps:**
- Verified against a stubbed `crontab` reproducing CT 100 exactly, plus: a custom
  log path and a `| logger` pipeline both survive untouched, a moved schedule is
  preserved, a commented job is reinstalled, and the whole thing stays idempotent.
- Run `./scripts/install-cron.sh` on CT 100 to apply; it rewrites the two entries
  in place.
- **Applying it exposed a third fault, and a worse one.** With the redirection
  added, `crontab -l` showed `repo-sync.sh` scheduled **twice**, identically —
  two `git pull`s and two `docker compose up -d` racing on the same tree and the
  same stacks at 04:00, two Healthchecks pings, two Discord reports. `--check`
  said `ok`, because every lookup in this script takes `head -n1`, so a second
  copy is never printed. The duplicate predates all of this; verified that a
  single entry stays single across repeated runs, so nothing here created it —
  making the two lines identical is just what finally made it visible.
- `install-cron.sh` now counts uncommented entries per job and reports more than
  one, failing `--check`. It does **not** remove them: this script only ever adds
  to a crontab, and a tool that can delete a schedule can cause the exact failure
  it was written to prevent. It prints the one-line `awk '!seen[$0]++'` fix
  instead, which drops exact duplicates and leaves genuinely different entries
  alone to be looked at.

---

## 2026-09-19 — Alerts said everything except the one sentence worth reading

**Goal:** `#alerts` had become noisy to read. Not noisy in volume — that was
fixed by grouping on `instance` on 2026-08-30 — but noisy per message: fifteen
lines of Grafana's default template to deliver one fact.

**Steps:**
1. Looked at what the default actually sends. Grafana's `default.message`
   prints, for every alert in the group, the value of each query, every label
   (including `grafana_folder`, and `alertname`/`instance`, which the title
   already carries), every annotation, then Source, Silence, Dashboard and Panel
   URLs. The useful sentence is in the middle of that, in the smallest type
   Discord has.
2. Added `grafana/provisioning/alerting/templates.yml` with `homelab.title`,
   `homelab.line` and `homelab.message`, and pointed the Discord receiver's
   `title` and `message` settings at them in `contactpoints.yml`.
3. Rendered all six shapes — firing critical, firing warning, resolved, a mixed
   group, a rule with no `instance` label, a rule with no `summary` — against a
   mock of Grafana's `ExtendedData` before committing anything.

**Issues encountered:**
- **The summary annotation was already doing the work, and nothing showed it.**
  Every rule in `rules.yml` has a `summary` written as a sentence with the value
  interpolated in — "docker /var is 87% full". That line is the alert; the
  surrounding fifteen were a template that could not assume it existed.
- **Two things that must not be formatted here: time and state.** A time
  formatted in the template renders in the Grafana container's timezone, which
  is UTC — it sets no `TZ` — so every message would be off by the local offset
  for the one reader it has. And firing/resolved was about to be stated a third
  time, in a `**Firing**` header, on a message Grafana already colours red or
  green.
- **A broken template is a silent delivery failure**, which is the failure mode
  this whole channel exists to avoid, and there is no notification-failure
  counter to catch it (2026-09-19, earlier entry).

**Resolution:**
- The embed **title** is the lock-screen line: severity emoji, alert name,
  instance — enough to know what broke and where without unlocking. The **body**
  is the summary sentence and one italic line of timing. Two lines, from
  fifteen.
- Timing uses Discord's own timestamps (`<t:unix:R>` → "3 hours ago"), rendered
  client-side in the reader's timezone. That form is also the right one for a
  `repeat_interval: 4h` re-send, where the question is how long this has been
  broken, not when it started.
- Resolved alerts keep their summary but strike it through, so a mixed group —
  one container back, one still down — reads correctly line by line instead of
  taking a single status for the whole group.
- `homelab.line` falls back to the rule name when `summary` is absent, so a
  future rule that forgets one degrades to something readable rather than an
  empty Discord message.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/monitoring/docker-compose.yml restart grafana
```
Then Alerting → Contact points → `homelab` → **Test**, which exercises both the
firing and resolved paths. Provisioning is read at startup, so without the
restart the repo says one thing and `#alerts` keeps showing the other — the
same trap as every other file under `grafana/provisioning/`.

**Notes / next steps:**
- Validated by rendering, not by reading: `go run` over the extracted template
  against a mock `ExtendedData` caught the shape of every case before it could
  fail as a missing notification. Worth repeating for any future template edit;
  the harness is ten lines of struct and a `template.ParseFiles`.
- The nightly `repo-sync.sh` report in the same channel is formatted separately,
  by hand, in the script. It is a report rather than an alert, and was left
  alone — but it is the other thing in `#alerts` worth a second look.
- Deliberately not included: the Silence URL. It is the longest line in the
  default message by some distance, and silencing an alert from a phone is not
  a thing this lab has ever wanted to do — the embed title links to Grafana for
  the times it does.

---

## 2026-09-19 — Every image was months to years old, and the drift report was silent about it

**Goal:** Work out why `sablier` was still on `1.8.1`, and make the answer
impossible to reach again.

**Steps:**
1. Listed image ages on CT 100. Every image was 3–23 months old. The two
   **pinned** ones were the two **stalest** — `sablierapp/sablier:1.8.1` at 23
   months, `ghcr.io/gethomepage/homepage:v1.5.0` at 12 — while nothing on
   `:latest` was worse than 9.
2. Traced both halves. Floating tags never moved because `:latest` is a name,
   not an instruction: Compose's default pull policy is `missing`, so `up -d`
   finds the tag on disk and stops, and nothing in this repo runs `docker
   compose pull`. Pinned tags never moved because nothing opened the PRs.
3. Found the same hole one level down in builds. `docker/proxy/Dockerfile` is
   `FROM caddy:2-alpine`, and `up -d --build` serves a cached base —
   `caddy-sablier:local` was 22 minutes old on a three-month-old Caddy.
4. Wrote [`docs/design/tsd-dependency-updates.md`](design/tsd-dependency-updates.md)
   (#67), then shipped its two mechanism-independent parts.

**Issues encountered:**
- `repo-sync.sh` is structurally blind to the build case: it measures built
  stacks by image creation time, so a rebuild resets the clock while the base
  underneath keeps ageing. It read 22 minutes and called `proxy` fresh.
- The obvious fix — pin everything — is what produced the two worst offenders.
  Pinning only beats floating when something is opening PRs; where nothing is,
  it drifts *slower* to float. So the fix could not be a tagging convention.

**Resolution:**
- Added a second drift axis to `scripts/repo-sync.sh`: any running image older
  than `HL_MAX_IMAGE_AGE_DAYS` (default 90) is named in the existing Discord
  report, under the existing Healthchecks ping. Age, not availability —
  no registry calls, no credentials, works offline.
- Added `--pull` to the `up -d --build` path, so a rebuild refreshes the base.
- Then the other half, once the watchdog existed to catch it failing:
  `repo-sync.sh` now also **pulls** for the stacks where a bad version is cheap,
  recording each replaced image's digest to `HL_DIGEST_LOG` first — a floating
  tag cannot be rolled back to, only forward.
- `HL_NO_AUTOPULL` defaults to `proxy core monitoring`, which is **wider than
  the TSD first proposed**, and the reason is the useful part. The draft argued
  those stacks "fail visibly"; the Grafana Angular-panel incident recorded two
  entries below is the counter-example — 10 of 11 and 32 of 35 panels blank for
  months after an ordinary `:latest` restart, nothing errored. Invisible
  breakage is the failure this design exists to prevent, so the design's own
  criterion excludes monitoring. `core` is out because Portainer's migrations
  are one-way: reverting the tag is not a rollback.
- `renovate.json5` confines Renovate to `proxy`, weekly, no automerge, plus a
  custom manager for the xcaddy Sablier plugin that no built-in manager sees.
  AdGuard pinned to `v0.107.79` — Renovate cannot track `:latest`, so floating
  the tag is precisely what keeps it out of the review loop.
- CI validates `renovate.json5`. It earned that immediately: the first draft
  used `"a" + "b"` to wrap a long description, which JSON5 does not support. A
  broken config does not fail loudly, it just stops opening PRs — which looks
  exactly like "nothing needed updating".

**Notes / next steps:**
- This is the third instance of the class that produced the uninstalled cron job
  and the three-week-stale tree — invisible because no signal existed that would
  ever have said so. Same fix each time: make silence the alarm.
- AdGuard's pin is a version bump the box has not taken yet: `proxy` is on both
  `HL_NO_AUTOHEAL` and `HL_NO_AUTOPULL`, so it lands only when someone runs the
  rebuild. Do it while watching, with a second resolver configured.
- **Shipped broken, caught on the first real report.** The rebuild command was
  `up -d --build --pull`, which is invalid: `docker compose build --pull` is a
  boolean but `docker compose up --pull` takes `always|missing|never`, so bare
  `--pull` either swallows the next argument or fails with "flag needs an
  argument". It reached Discord as a copy-paste command that cannot run, and
  `assistant` — the other stack with a `build:` key, and not on
  `HL_NO_AUTOHEAL` — would have had every auto-rebuild fail. Now `--pull
  always`, verified against real `docker compose` rather than by reading.
  `sh -n` cannot catch this class: the script is syntactically perfect and the
  error lives inside a string it hands to another program.
- The stale report also told you to `pull` images whose tags are pinned, where a
  pull does nothing — and pinning is exactly why the oldest entries are old, so
  it was useless precisely where it mattered. It now says so.
- Widening `HL_NO_AUTOPULL` back out is one word. Letting `monitoring` in wants
  Grafana pinned to a major first, so a pull cannot cross one.
- Out of scope and tracked in the TSD: Homepage v1 → v2, Portainer STS → LTS,
  the Sablier bump (coupled to `sablier-caddy-plugin@v1.0.2`), orphan images.

---

## 2026-09-19 — `/deploy`, which works out what to recreate instead of being told

**Goal:** Turn the deploy steps for the day's monitoring changes into a command,
so the sequence stops living in a chat transcript.

**Steps:**
1. Wrote the obvious version first: pull, `install-cron.sh`, then
   `up -d --force-recreate prometheus blackbox-exporter node-exporter`, restart
   Grafana.
2. Noticed that list was already wrong once. The deploy instructions given out
   an hour earlier named `prometheus` and `node-exporter` and omitted
   `blackbox-exporter`, whose `blackbox.yml` had also changed — which would have
   left it serving a config without the new `dns_home` / `dns_upstream` modules,
   scored both DNS probes as failures, and paged `#alerts` at critical severity
   claiming the house had no name resolution. A false critical on the channel
   the whole day was spent making trustworthy.

**Issues encountered:**
- **A hardcoded recreate list is a second copy of something Docker already
  knows.** Same shape as the Caddyfile-vs-tiles and probe-vs-site-block drift
  that `check-dashboard.sh` and `check-probes.sh` exist to guard, and it rotted
  within a day of being written. Writing it down more carefully was not the fix.

**Resolution:**
- `/deploy` detects drift rather than reciting names: for every running
  container, every bind mount whose source is a *file* inside this repo gets
  copied out with `docker cp` and compared to the repo's copy. Whatever differs
  is recreated, resolved back to its stack and service through Docker's own
  compose labels, so nothing is typed by hand. The detection is re-run
  afterwards and must come back empty.
- `docker cp` rather than checksumming inside the container, for the reason
  `repo-sync.sh` already uses it: half these images have no shell, and a check
  that skips the containers it cannot run a binary in reports all-clear forever.
- Directory mounts are excluded, since they do not go stale — but config read
  only at *startup* still needs a restart, so the command diffs the pull against
  the pre-pull HEAD and restarts Grafana when `grafana/provisioning/` changed.
  It also flags a provisioning resource removed without a `deleteRules:` /
  `deleteContactPoints:` directive, which upserts would otherwise leave behind.
- `proxy` is excluded from unattended deploys, the same boundary
  `HL_NO_AUTOHEAL=proxy` draws for `repo-sync.sh`: it holds AdGuard, and a
  recreate that does not come back takes DNS down along with every tool you
  would use to diagnose it. It is reported with the command to run, and
  deploying it verifies `dig +short stats.home` before claiming success.
- Generalised to any stack rather than monitoring-only, because the inode trap
  is not specific to one.

**Notes / next steps:**
- The detection block was tested three ways against a stubbed `docker`: a stale
  copy is flagged with its path, an identical copy is not, and a non-repo volume
  mount is ignored.
- Worth noticing the pattern across today: every fix that held was one that
  asked the running system a question. Every one that needed fixing again was
  one that asserted an answer — three guesses at a metric name, and a recreate
  list written from memory.

---

## 2026-09-19 — Two of the four new Grafana tiles were querying metrics that do not exist

**Goal:** Confirm, against the running instance, that the self-scrape panels
shipped an hour earlier actually query metrics this Grafana emits.

**Steps:**
1. `grafana version 13.2.2` — well past the Grafana 12 that removed Angular, so
   the diagnosis behind the dashboard rebuild is confirmed rather than inferred.
   Worth noting on its own: **nothing in this repo ever chose 13.x.** The
   compose file says `:latest`, and a major version that removed a panel plugin
   class arrived on an ordinary `up -d`.
2. Dumped the full metric list off the box — 695 names — and checked each panel
   expression against it.

**Issues encountered:**
- **`grafana_datasource_request_total` does not exist.** Datasource queries run
  through the plugin path in 13.x; the real metric is `grafana_plugin_request_total`,
  with labels `{endpoint, plugin_id, plugin_version, status, status_source, target}`.
- **No notification delivery counter exists at all.** Neither
  `grafana_alerting_notifications_failed_total` nor the unprefixed
  `alertmanager_` form is present. The prefix theory was sound — every other
  upstream Alertmanager metric appears under `grafana_alerting_` (`nflog_*`,
  `silences_*`, `dispatcher_*`, `notification_latency_seconds`) — and still
  wrong. `notification_latency_seconds_count` had already observed a send, so
  the counters are not merely unregistered; 13.2 exports the histogram and not
  the counters.
- So `hl-notifications-failing` **could never fire**, and the tile beside it
  could never leave zero. Neither was broken in any way Grafana reports: the
  rule evaluated a query matching nothing, stayed Normal forever, and read as a
  delivery path in perfect health. That is the exact failure the whole day's
  work was about, shipped by me, inside the change arguing against it.
- **The verification command was itself unsafe.** `docker exec grafana wget
  -qO- .../metrics | grep …` returned empty, and `-q` silences connection
  failures too — so a missing metric and a failed request are indistinguishable.
  A check that cannot fail loudly is not a check.
- A counter appeared to vanish between two runs. It was a Grafana restart:
  `rule_evaluation_failures_total` registers once the scheduler has evaluated,
  not at startup, so a fresh process briefly has no such series. **No rule had
  been failing** — the earlier suspicion was wrong.

**Resolution:**
- Datasource tile moved to `grafana_plugin_request_total{status="error"}`.
  `status="error"` rather than `!="ok"` on purpose: `cancelled` is someone
  navigating away from a dashboard mid-query, not a fault.
- The notification tile now shows **sends** (`notification_latency_seconds_count`),
  coloured neutrally because there is nothing to threshold on, and the Alerting
  pipeline panel gained `grafana_alerting_active_alerts` on a right axis.
  Alerts climbing while sends stay flat is delivery being stuck — the same
  signal, read off two curves, since no single counter carries it.
- `hl-notifications-failing` removed, **with a `deleteRules:` directive**.
  Dropping it from the file alone would have left it in Grafana's database
  forever: file provisioning upserts, and the UI will not delete a provisioned
  resource. This repo has been caught by that once already with the ntfy
  receiver, and the Triage tile counting evaluating rules exists partly to
  surface it.
- README's Verify section now checks each metric name by count, from the host
  with `curl`, so a zero is unambiguous.

**Notes / next steps:**
- `up{job="grafana"} = 1` — the scrape itself works.
- The pattern to take from this: **three guesses at a metric name, two wrong.**
  The `__name__` regex hedge felt like rigour and was not; it covered two names
  when the real answer was "no such metric". Query the box before writing the
  panel, not after.
- Pinning Grafana is now clearly right rather than arguable. A major version
  landed here unchosen and removed things; `repo-sync.sh` reports images past
  90 days as of #69, so the objection that a pin would rot silently is gone.

---

## 2026-09-19 — Nothing was watching the watchman: Grafana now scrapes itself

**Goal:** Decide whether any Grafana feature toggles were worth enabling. Ended
up somewhere else, which is the useful part.

**Steps:**
1. Looked at the toggles. `grafanaAdvisor` (periodic checks over datasources and
   plugins) is the one adjacent to how three dashboards died here — a plugin
   class removed from under a `:latest` image. Worth having; not worth enabling
   blind, since turning experimental features on *while* the image floats is the
   exact combination that caused the original problem.
2. Checked what was actually being scraped before recommending anything, and
   found the real gap: **Grafana was never scraped.** `prometheus.yml` probes
   `http://grafana:3000/api/health` through blackbox, and that is the only thing
   that ever asked Grafana a question.

**Issues encountered:**
- **A liveness probe and a health check are not the same question.**
  `/api/health` says Grafana is up. It says nothing about whether the thing
  Grafana exists to do is working — and the two come apart in the sharpest
  version of the failure class this lab keeps meeting: **a provisioned alert
  rule whose query errors does not fire and does not warn. It stops
  evaluating.** A renamed metric, a datasource that stopped answering, a typo
  that survived a restart — any of them silently retires a rule, and a retired
  rule is indistinguishable from a rule with nothing to report. Sixteen rules
  are provisioned here. Nothing could have told you one had died.
- **Discord is the only delivery path, and nothing watched it.** ntfy was
  removed, `DISCORD_ALERT_WEBHOOK` is `:?required` so the stack cannot start
  unable to page — and none of that helps if the webhook starts rejecting
  sends. Every rule would fire correctly into nothing, and the first symptom is
  noticing you have not been paged in a while.
- **Two metric names, and no way to check from here.** Grafana has used both
  `grafana_alerting_notifications_failed_total` and
  `alertmanager_notifications_failed_total` for the embedded Alertmanager's
  counters, and grafana.com is unreachable from the dev sandbox. Guessing would
  have produced a panel that renders blank — the precise failure the last change
  was written to prevent.

**Resolution:**
- A `grafana` scrape job. One target, no new container, no auth (Grafana serves
  `/metrics` unauthenticated by default and nothing sets
  `GF_METRICS_BASIC_AUTH_*`); `:3000` was already published to LAN/tailnet, so
  this changes no exposure.
- Four tiles on **Triage** — rules erroring, notifications failing, datasource
  errors, and rules evaluating — plus one **Alerting pipeline** history panel,
  because a rule erroring since a provisioning change three days ago and one
  that broke ten minutes ago want different responses. Evaluations flat at zero
  means the scheduler itself stopped.
- Two alert rules: `hl-alert-eval-failing` and `hl-notifications-failing`.
- The name ambiguity is matched rather than guessed: both panels and the rule
  use a `{__name__=~"(grafana_alerting|alertmanager)_notifications_failed_total"}`
  selector, which is correct under either spelling. The README's Verify section
  has the one command to find out which, and says to collapse it afterwards.
- `grafanaAdvisor` is in the compose file commented out, with the two commands
  to check whether this version needs it at all.

**Notes / next steps:**
- The layering is now explicit, and it is the point: Grafana watches the lab,
  Prometheus watches Grafana, `up{job="grafana"}` watches that scrape, and
  `heartbeat.sh` watches all of it from off the box. `hl-notifications-failing`
  is delivered by the channel it monitors — that covers the partial failures
  (rate limiting, a rotated webhook, one integration of several), which are the
  common case, and the total outage stays heartbeat's job.
- Confirm the metric names on first deploy. A blank tile here means the metric
  is called something else in this version, not that the lab is quiet.
- Pinning the Grafana image is worth revisiting once `tsd-dependency-updates.md`
  §1 lands — the argument against pinning was that a pinned-and-forgotten image
  is its own silence, and a staleness watchdog removes that objection.

---

## 2026-09-19 — The dashboards were watching the hardware; the failures were all in the deployment layer

**Goal:** Review the Grafana dashboards, which had started to feel arbitrary,
and work out whether they were actually monitoring this lab or just the software
that happens to run on it.

**Steps:**
1. Audited all seven dashboards. Six of them existed because a `grafana.com` ID
   had been pasted into `fetch-dashboards.sh` — the set was organised around
   *which exporters we run*, not around *which questions we need answered*. Only
   `homelab-capacity.json` had been written for this lab.
2. Checked panel types against the Grafana version actually running. Three
   dashboards were built at schemaVersion 16-26 on Angular panels:

   | Dashboard | schema | Angular panels |
   |---|---|---|
   | blackbox (7587) | 16 | 10 of 11 |
   | postgresql (9628) | 19 | 32 of 35 |
   | loki-logs (13639) | 26 | 1 of 2 |

3. Cross-checked the incident history in this log against what any dashboard
   could show. The three-week deploy gap, the six-day stale config inode, the
   never-installed cron entry: **none of them were visible in Grafana.** Every
   one was found by hand, and every fix reports to Discord and then discards
   what it computed.
4. Checked coverage: AdGuard — the household's resolver, and the one service
   `repo-sync.sh` deliberately refuses to auto-restart — had no probe and no
   scrape of any kind. Nor did the assistant bot, which has no port to probe.

**Issues encountered:**
- **Grafana runs on `:latest`.** Grafana 11 disabled Angular by default and
  Grafana 12 removed it, so those three dashboards had been blank since some
  ordinary restart. Nothing errored. A blank dashboard reads exactly like a
  quiet lab, which is why it went unnoticed for an unknown length of time.
- **`loki-logs` was broken a second way, independent of Angular.** Its "App"
  selector was `label_values(job)`, but `config.alloy` only ever sets `job` to
  `docker` or `systemd-journal` — so the two choices on offer were "every
  container in the lab at once" and "the journal". The `container` label Alloy
  does set went unused.
- **`fetch-dashboards.sh` pulled `revisions/latest`,** so re-running it
  rewrote committed files with whatever upstream had published since. The JSON
  in git described whenever the script last ran, not a version anyone chose.
- **There was no way for a cron job to leave a metric behind** — node-exporter
  had no `--collector.textfile.directory`, which is the standard plumbing for
  exactly that.
- **`pg-backup.sh` had no dead man's switch,** despite `AGENTS.md` requiring one
  for anything scheduled. It is the lab's only protection against an accidental
  `DROP`, and a silently uninstalled cron entry would have looked like a quiet,
  healthy month.
- **Caught while testing:** adding the Healthchecks read to `pg-backup.sh`
  broke it outright on a box with no `.env`. That script runs under
  `set -euo pipefail`, so `sed` exiting 2 on a missing file propagated through
  the pipeline and killed the backup before it started — monitoring destroying
  the job it was bolted onto. Fixed with `|| true`, and the failure path is now
  the one that gets tested first.

**Resolution:**
- **Plumbing.** `scripts/metrics.sh`: a shared helper for writing Prometheus
  textfile metrics from shell, where every function degrades to a no-op so
  emitting a metric can never fail the job. node-exporter mounts
  `/var/lib/node_exporter/textfile` (a *directory* mount — the .prom files are
  replaced by atomic rename, so a file mount would pin one frozen scrape).
- **The three cron jobs now publish what they already knew.** `heartbeat.sh`
  every 5 minutes: every container declared in `docker/*/docker-compose.yml` and
  whether it is running, plus whether each job has a crontab entry.
  `repo-sync.sh` nightly: commits behind origin, per-stack deploy drift, and —
  new — a `docker cp` comparison of every bind-mounted config file against the
  repo's copy, which detects the six-day inode bug automatically for every
  container in the lab. `pg-backup.sh`: dump age, size, count and result, all
  measured from the files on disk so a failed run cannot erase the record of the
  last good one.
- **Dashboards, rebuilt around questions.** Four new committed dashboards —
  **Triage** (is anything broken right now), **Deployment & Drift** (is the lab
  running what git says), **Scheduled Jobs & Backups** (did the work happen, is
  what it produced any good), and a rewritten **Logs** filtered by `container`.
  **Capacity** was extended to cover CT 100, which is where the disk actually
  fills and which the original omitted entirely. **Endpoints & DNS** and
  **PostgreSQL** replace the two dead community dashboards with about six panels
  each that someone will actually read. The community dashboards that still
  render moved to a separate `Reference` folder, and `fetch-dashboards.sh` now
  pins revisions in `reference/REVISIONS` (`--update` to bump).
- **AdGuard is monitored.** Two blackbox DNS probes — one for the `*.home`
  rewrite, one proving upstream recursion still works, since those look
  identical from a laptop and have different fixes. Probed over DNS rather than
  HTTP deliberately: AdGuard's admin UI redirects to a login page, so an
  `http_2xx` probe would score a healthy AdGuard as down, which is the exact
  failure that made `#alerts` untrustworthy in August.
- **Nine new alert rules** for the failure modes that had none: DNS down,
  container missing, container restart loop, cron job not installed, repo sync
  stale, backup stale, config drift, stack running old code, and a
  `predict_linear` warning for a filesystem heading for full.
  `HEALTHCHECKS_PG_BACKUP_URL` closes the last unguarded job.
- **CI.** `scripts/check-observability.sh` fails the build on a removed panel
  type, a duplicate dashboard uid, an unresolved `${DS_*}` input, a `homelab_*`
  metric no script writes, or the job lists in `heartbeat.sh` and
  `install-cron.sh` drifting apart.

**Notes / next steps:**
- Deploy needs a recreate, not a restart: node-exporter has a new mount and a
  new flag, and Grafana needs `restart` for the provisioning change.
  `./scripts/install-cron.sh` creates the metrics directory and must run before
  the new dashboards have anything to show.
- `heartbeat.prom` appears within 5 minutes. `repo_sync.prom` and
  `pg_backup.prom` only after their first nightly run — run both by hand once
  rather than waiting a day to find out whether this works.
- Textfile metrics persist until their writer runs again, so a `config_drift` or
  `stack_drift` alert keeps firing until the next 04:00 sync even once fixed.
  Re-run `./scripts/repo-sync.sh` to clear it.
- Still not covered, and worth being explicit about: Homepage is not probed.
  It rejects requests whose `Host` header it does not recognise, so a probe by
  container name returns 400, and the real hostname is deliberately not in this
  public repo. It is covered by `homelab_container_running` instead.
- `tsd-backups-and-monitoring.md` remains parked. Everything above watches the
  jobs that exist; there is still **no image backup and no offsite copy**, so a
  green Scheduled Jobs dashboard means "the one backup we have ran", not "the
  lab is recoverable".
- **The drift dashboard measures one axis of two.** It compares the repo against
  what is deployed; it says nothing about what is deployed against upstream, so
  a stack rebuilt an hour ago on a three-month-old cached base image reads as
  perfectly fresh. `tsd-dependency-updates.md` (drafted the same day) covers
  that second axis, and the textfile plumbing added here is the vehicle its
  staleness watchdog would use — a `homelab_image_age_seconds` alongside the
  rest. The panel says so rather than implying coverage it does not have.

---

## 2026-09-19 — The dashboard's mark was the design `life` had already thrown away

**Goal:** Bring the dashboard's page icon onto the icon standard the `life`
repo settled a few days ago, so the two sites read as one family in a tab
group rather than as two unrelated experiments.

**Steps:**
1. Read what `life` actually does: `public/favicon.svg` is the real mark —
   **no ground**, and a `prefers-color-scheme` pair using the `mark` token's
   two amber steps — while `scripts/gen-icons.mjs` composites a separate
   `icon-source.svg` onto an ink ground for every raster a platform probes.
2. Redrew the homelab mark to match: three rack units, rectilinear and
   horizontal, a sibling of life's three ascending steps and the trips range
   and tellable apart from both at 16px.
3. `scripts/gen-dashboard-icons.py` writes `apple-touch-icon.png` (180),
   `favicon-32.png` and a 16/32/48 `favicon.ico`, all on the ink ground.
4. `config/custom.js` repoints the apple-touch link at the raster and adds the
   PNG and `.ico` fallbacks; the Caddyfile rewrites `/favicon.ico` and
   `/apple-touch-icon.png` onto the same directory.

**Issues encountered:**
- **The first mark was the exact thing `life` had replaced.** An amber figure
  on a near-black tile, which on a dark tab strip reads as nothing at all. It
  was drawn from the tokens and still off-standard, because the standard is not
  only which hue — it is that identity is an open figure, not a filled square.
  The lesson was already written down one repo over.
- **Homepage declares two tags and iOS wants a third thing.** `favicon:` emits
  `rel="icon"` and `rel="apple-touch-icon"` from one path: an SVG is right for
  the tab and useless for the home screen, a PNG the other way round. Safari
  reads the live DOM when someone taps Add to Home Screen, so `custom.js`
  repairing the link after hydration is honoured.
- **Safari never sees that markup for half of what it draws.** Its address bar,
  suggestion list and Favorites tiles probe `/favicon.ico` and
  `/apple-touch-icon.png` at the root, which is Homepage's stock logo. Those
  are rewritten in the proxy rather than by shadowing the image's
  `/app/public`.

**Resolution:**
- Verified in a browser: the mark at 16px and 32px in both colour schemes
  against life's and trips' marks (the three are tellable apart, which is the
  job), every raster on its ink ground, and the four declared icon links
  landing on the right files after `custom.js` runs.
- `check-dashboard.sh` now also checks the paths named in `custom.js` and the
  Caddyfile's rewrites, so a mark that never got generated fails the build
  rather than 404ing quietly into a globe.

**Notes / next steps:**
- Three equal bars read as a hamburger menu at 16px; the last unit is short for
  that reason. Checked, not assumed.
- Favicon caches ignore `Cache-Control`. These files changed names, which is
  its own cache bust — if one ever sticks, add `?v=2` in `settings.yaml`,
  `custom.js` and the Caddyfile. An iOS home-screen icon is baked in at add
  time and needs removing and re-adding.


---

## 2026-09-19 — The box was three weeks behind main and nothing said so

**Goal:** Deploy the dashboard changes, and then work out why deploying them
needed a person at all.

**Steps:**
1. Checked the box before blaming the change: `git log` on CT 100 read
   `aebfb68`, the merge of **#61**, from 2026-08-30. Twenty days of commits had
   never been pulled — which is also why `/icons/homelab.png` 404ed and the
   container still had only its `config` mount.
2. `git pull --ff-only` then refused outright:
   ```
   error: The following untracked working tree files would be overwritten by merge:
           docker/dashboard/config/custom.css
           docker/dashboard/config/custom.js
   ```
3. Both were 0 bytes, and not ours: Homepage copies a skeleton into the config
   directory for every config file it knows about — `custom.css` and
   `custom.js` included — whenever one is missing. Removing them let the pull
   through.
4. Then fixed the three layers underneath, rather than the symptom:
   `scripts/install-cron.sh` (schedule the jobs, idempotent, `--check`),
   a Healthchecks ping in `repo-sync.sh` (`HEALTHCHECKS_REPO_SYNC_URL`), and
   the empty-skeleton case cleared automatically before the pull.

**Issues encountered:**
- **The sync job had never been installed.** Its cron line lived in a comment
  in its own header, so installing it was a ritual someone had to remember on a
  box they were already busy fixing. Nothing referenced it — not the README,
  not a script.
- **And it could not report that.** `repo-sync.sh` speaks only when there is
  something to say, which makes "never ran" indistinguishable from three weeks
  of quiet, healthy days. `heartbeat.sh` cannot cover it either: the box is
  alive the whole time. Same lesson as 2026-08-30 one level up — the thing that
  would have told you was the thing that was not running.
- **One empty file stopped every stack from updating.** Not just the dashboard:
  a refused `git pull` is the whole box frozen, over a file containing nothing.
  The container wrote it, the repo later started tracking the same path, and
  git did exactly the right thing at exactly the wrong moment.

**Resolution:**
- `./scripts/install-cron.sh` installs `heartbeat.sh` (*/5), `repo-sync.sh`
  (04:00) and `pg-backup.sh` (02:00), never rewriting an entry that is already
  there, so a schedule someone moved on purpose stays moved.
- `repo-sync.sh` pings Healthchecks with its exit code on every run, so "ran and
  failed" and "never ran" are different signals. Until
  `HEALTHCHECKS_REPO_SYNC_URL` is set it says so in every report — an
  unconfigured switch is the same silence, and the nagging stops the moment it
  is set.
- Before pulling, it clears untracked *empty* files that an incoming commit
  adds, and reports what it cleared. Anything with a byte in it still stops the
  pull, which is the point: that one is somebody's work.
- Exercised both paths against a throwaway repo: an empty skeleton is removed
  and the pull applies; a file with content in it blocks the pull, is left
  untouched, and is reported. `install-cron.sh` was run against a stub crontab
  for the missing, fresh-install, already-present and no-trailing-newline cases.

**Notes / next steps:**
- `--check` on CT 100 confirmed it exactly: `heartbeat.sh` and `pg-backup.sh`
  were both scheduled, `repo-sync.sh` was not. One missing line, twenty days of
  drift, and the job that would have reported it was the missing one.
- Each installed entry now appends to `/var/log/<script>.log`. Cron mails a
  job's output to a local mailbox nobody reads and no MTA delivers, which loses
  exactly the failures worth keeping: `repo-sync.sh` reports to Discord, but a
  run that cannot *reach* Discord says so on stderr and nowhere else. The
  hand-written `pg-backup` entry on the box already did this; it is the default
  now rather than something each line has to remember.
- The second Healthchecks check wants period 1d, grace 6h.
- Worth considering later: `--check` from something that runs *off* the box, so
  a missing crontab is caught the same way a missing heartbeat is.

---

## 2026-09-19 — Tile icons are vendored, and Portainer's had been invisible all along

**Goal:** Decide what the design system does and does not get a vote on, now
that the dashboard follows it — starting with the icons on the tiles and the
favicons of the services behind them.

**Steps:**
1. Left every service's own favicon alone. Grafana's orange G, Portainer's
   whale and AdGuard's shield are already unmistakable *from each other*, which
   is the actual job in a tab group. Five marks in one house style would undo
   that. The dashboard needed its own mark because it had none; these do not.
2. `scripts/update-tile-icons.sh` vendors each tile icon from
   [dashboard-icons](https://github.com/homarr-labs/dashboard-icons), pinned to
   one commit in `docker/dashboard/icons/SOURCE`, and `services.yaml` now names
   them as `/icons/...` paths.
3. The brignano.io tile carries the `A|B` monogram from the site's own favicon
   instead of Vercel's logo, which named the host rather than the site.
4. `check-dashboard.sh` now checks every absolute path the dashboard's YAML
   names, not just the favicon, so a service added without its icon fails CI.

**Issues encountered:**
- **Portainer's tile has been invisible since it was added.** Its mark is a
  black P, the dashboard's card is near-black, and nobody notices a missing
  icon the way they notice a wrong one. Open WebUI's black disc was the same
  story in a milder form. Upstream ships a second drawing of each for dark
  backgrounds, but Homepage renders a tile icon as an `<img>` — its own
  document, which cannot see the page's theme — so it cannot pick between two
  files.
- **An SVG can see the colour scheme even when it cannot see the page.** So
  both drawings go into one file, each in a nested `<svg>` keeping its own
  coordinate system, switched by a media query
  (`scripts/compose-icon.py`). Ids are prefixed on the way in, because the two
  drawings are usually the same file with different fills and collide
  otherwise. The switch follows the OS rather than Homepage's toggle — the
  same `<img>` limit — and the tile still has its label when they disagree.
- **The icons were being fetched from a CDN by the browser, on every load.**
  For the page you open *because* the internet broke, and one AdGuard rule from
  a grid of blank squares.

**Resolution:**
- Verified by rendering all seven icons in a real browser under both colour
  schemes, on the card colours the dashboard actually uses: each one reads in
  both, including the two composites and the monogram.
- Deploy is `git pull` and a restart; `icons/` is a directory mount.

**Notes / next steps:**
- Adding a service is now: a Caddyfile block, a tile with `icon: /icons/<name>.svg`,
  and `./scripts/update-tile-icons.sh`. CI fails on any of the three being missed.
- Not done, and worth its own decision: the Grafana dashboards are imported
  community JSON, so recolouring them to `tokens.chart.css` would be undone by
  the next import. The custom one (`homelab-capacity.json`) is the only
  candidate.

---

## 2026-09-19 — The dashboard now wears the design system, and pulls it from npm

**Goal:** Make `home.$HOMELAB_DOMAIN` look like the rest of the brignano
surfaces rather than like a Homepage install, and make that follow
[`@brignano/design`](https://github.com/brignano/design) when the package moves
— without the dashboard keeping a private copy of the palette.

**Steps:**
1. `scripts/update-design-tokens.sh` vendors `tokens.css` from the npm registry
   into `docker/dashboard/assets/`, pinned to a version recorded beside it, and
   vendors Geist (latin, variable) from `@fontsource-variable/geist`. Both are
   committed: nothing on the box runs npm, so `git pull` is the install.
2. The same script *generates* `assets/homepage-palette.css` from those tokens.
3. `config/custom.css` imports both and bridges the rest — type, shape, state —
   through Tailwind v4's own theme variables rather than through class names.
4. `config/custom.js` mirrors Homepage's `light`/`dark` class onto
   `<html data-theme>`, which is what the tokens key their dark values off.
5. CI runs `./scripts/update-design-tokens.sh --check` (offline), and
   `check-dashboard.sh` now verifies every local path named in `settings.yaml`
   or `custom.css` exists in the repo and is mounted.

**Issues encountered:**
- **Homepage has no token layer, and the two can't be wired directly.** It
  themes itself from ten variables holding *RGB channels* — `--color-800: 39 39
  42` — while the design system ships hex. CSS cannot convert between them, so
  something has to translate, and a translation done by hand is a second copy of
  the palette that drifts silently. Hence the generator, and hence CI diffing
  it: a hand edit to the generated file is the only drift left, and it fails the
  build.
- **The ramps run in opposite directions.** Homepage's goes light → dark in
  *both* themes (in dark it takes the page background from `--color-800` and its
  ink from `--color-200`); the design system's inverts between themes. A
  step-for-step mapping would have put dark ink on a dark page, so each step is
  mapped by what Homepage *does* with it. The reasoning is in the generator,
  next to the values.
- **Two theme systems that cannot see each other.** Homepage ignores the OS and
  remembers its own toggle; the tokens key off `prefers-color-scheme` unless
  told otherwise. A phone in light mode on a dashboard pinned to dark got the
  light ramp painted on a dark page. `custom.js` mirrors one onto the other, so
  Homepage stays the single source of truth for which theme is on.
- **Geist had to come with it.** `--sans` names it first and nothing on a phone
  has it installed, so without the file the dashboard fell back to the system
  face — the same tokens, a different-looking family of site.

**Resolution:**
- Verified in a real browser before shipping, since no CI check covers the
  cascade: Homepage's `theme.css` and the compiled form of the classes it
  actually uses, served alongside this `custom.css`, driven through all four
  combinations of Homepage dark/light against OS dark/light, asserting the
  computed colours equal the token values. Also with the stylesheet order
  reversed, because `custom.css` is a `<link>` in `_document` and nothing
  guarantees it lands after Next's own CSS — the selectors carry an attribute
  match (`html[class*="theme-"]`) so they win either way.
- Deploy: `git pull` then a restart of the dashboard. `assets/` and `config/`
  are directory mounts, so replacements inside them are visible without a
  recreate — unlike the single-file mounts that caused 2026-08-30.

**Notes / next steps:**
- To move the look, bump the package and run the script. Editing a colour in
  `custom.css` is the thing not to do; there are no colour values in it, only
  token references, and that is deliberate.
- IBM Plex Mono is deliberately not vendored — Homepage uses `font-mono` in four
  minor places and the token's fallback chain lands on the platform mono face.
- `color: zinc` in `settings.yaml` no longer decides anything visible, but it
  still has to be *a* colour: it is what puts the `theme-` class on `<html>`
  that the generated palette hangs off.

---

## 2026-09-19 — The dashboard got a mark, because a default favicon is unfindable in a tab group

**Goal:** Make `home.$HOMELAB_DOMAIN` identifiable at 16px. The dashboard shipped
with Homepage's stock logo, which is the tab you scroll past in a Safari tab
group on a phone — the surface the dashboard is actually used from.

**Steps:**
1. Drew the mark to the shared design system
   ([brignano/design](https://github.com/brignano/design)), which already names
   `homelab` as a tool-tier consumer: its `mark` hue (larch amber `#e0a44f`,
   identity only, and only ever inking a graphic) on its `n-900` neutral
   (`#111111`). A house over a rack slot, sized so the silhouette and the colour
   are all that has to survive the tab strip.
2. Committed both `docker/dashboard/icons/homelab.svg` (source) and a 512×512
   `homelab.png` (what is served). No build step runs on the box, so the render
   is checked in.
3. Pointed `settings.yaml` at it with `favicon: /icons/homelab.png`, and mounted
   `./icons` at `/app/public/icons` — the only directory Homepage serves local
   images from.
4. Extended `scripts/check-dashboard.sh` (already run by CI) to fail if
   `settings.yaml` names an icon that is not in the repo, or if the compose file
   stops mounting the directory that serves it.

**Issues encountered:**
- **An SVG favicon would have broken the one platform this was for.** Setting
  `favicon:` makes Homepage emit `rel="icon"` *and* `rel="apple-touch-icon"`
  from the same path, and iOS will not take an SVG for the latter: Safari
  substitutes a screenshot of the page for the home-screen icon. The fix for an
  unidentifiable tile would have been an unidentifiable tile, visible only on a
  phone.
- **Rounded corners are Apple's to draw.** iOS masks its own radius onto a
  home-screen icon, so a pre-rounded icon shows dark notches inside the mask.
  The mark is full-bleed and square for that reason.
- **The icons mount is a directory, deliberately.** A single-file bind mount
  would repeat 2026-08-30: `git pull` gives the path a new inode, the container
  keeps the old one, and the box serves the previous icon while the repo and CI
  both look correct.

**Resolution:**
- Deploy is `git pull && docker compose up -d --force-recreate dashboard` in
  `docker/dashboard/`. The force-recreate is needed once, to pick up the new
  mount — not because of the icon.

**Notes / next steps:**
- The colours are literal hexes in the SVG. A standalone favicon has no
  stylesheet to read tokens from, so that is the one place the design system's
  "never hardcode a hex" rule cannot hold — if the mark hue moves there, it has
  to be moved here by hand.
- Homepage's `/site.webmanifest` is baked into the image and still lists its own
  logo, so an Android "install app" would use that. iOS reads `apple-touch-icon`
  first, so the phone this was drawn for is covered.
- Still open: whether the dashboard's *page* should follow the design system too
  (Homepage supports a `custom.css`, which is the only hook it gives). The tab
  is fixed; the page is still Homepage's zinc dark theme — which is at least the
  same cool-leaning neutral the system specifies.

---

## 2026-08-30 — The Caddy probe fix had been on disk for six days and never reached the container

**Goal:** Find out why `#alerts` was still firing about Caddy after two correct
fixes had been merged. Answered on the box this time, not from the repo.

**Steps:**
1. Ran `scripts/probe-status.sh` on CT 100. It printed the answer in three
   lines: `http://caddy/health` **NOT SCRAPED**, `http://caddy:80/` still a live
   series with **0 state changes in 6h**, and a live probe of
   `http://caddy/health` returning **HTTP 200**.
2. Compared the container's config against the host's:
   ```
   $ docker exec prometheus grep -n caddy /etc/prometheus/prometheus.yml
   61:          - http://caddy:80/                  # the file from 2026-08-24
   $ grep -n caddy docker/monitoring/prometheus/prometheus.yml
   65:          - http://caddy/health               # the file on disk
   $ curl -i -X POST http://localhost:9090/-/reload
   HTTP/1.1 200 OK
   ```
3. `docker compose up -d --force-recreate prometheus grafana`.

**Issues encountered:**
- **A stale single-file bind mount, and every signal said the deploy had
  worked.** `git pull` reported "Already up to date", `/-/reload` returned 200,
  CI was green, and Prometheus went on scraping a target that had been replaced
  six days earlier. Docker resolves a *file* bind mount to an inode when the
  container is created; git does not edit files in place, it writes a new file
  and renames it over the old one. So `git pull` gave the path a new inode and
  left the container mapped to the original, now unlinked. The reload was
  honest — it re-read the file the container still had.
- **`docker compose restart` does not fix it either.** Same container, same
  mounts. Only recreating re-resolves them, and nothing in the deploy notes
  (including the ones written the same day, in the entry below) said so.
- This is the whole reason the last two fixes looked wrong. Neither was.

**Resolution:**
- Recreated Prometheus and Grafana. `http://caddy/health` is scraped, the probe
  passes, and `http://caddy:80/` is gone.
- `scripts/probe-status.sh` — when the target list is stale it now diffs the
  container's `prometheus.yml` against the one on disk and prints the command
  that actually applies: a reload when the container has the right file, a
  `--force-recreate` when its mount is stale. Its previous advice was "reload
  it", which is precisely what does not work here and had already been tried
  twice.
- `docker/monitoring/README.md` — a "`restart` and `reload` are not enough after
  a `git pull`" section with the transcript above, and the note that
  `grafana/provisioning/` is a *directory* mount and therefore exempt.
- `AGENTS.md` — recreate, don't restart, for single-file config mounts; prefer a
  directory mount for new config where the directory holds no secrets.

**Notes / next steps:**
- The remaining single-file mounts have the same trap: `blackbox.yml`,
  `loki-config.yml`, `config.alloy` and the Caddyfile. The first three sit in
  directories containing nothing else and could become directory mounts, which
  removes the failure mode rather than documenting it. The Caddyfile cannot —
  `docker/proxy/` holds `.env`.
- Worth internalising, because it has now cost three sessions: when a fix is
  merged and the symptom persists, the next question is not "was the fix wrong"
  but "is the container running it".

---

## 2026-08-30 — Caddy alerts still arriving: grouping, and the question of whether the fix is running

**Goal:** `#alerts` is still filling with Caddy alerts a week after the probe fix
in [#60](https://github.com/brignano/homelab/pull/60) landed. Establish whether
that fix is wrong, or is simply not the config the box is running — and stop
guessing at this, because it is the second time the same symptom has cost a
full re-diagnosis.

**Steps:**
1. Re-verified the merged fix rather than trusting it. Ran a real Caddy 2.10
   against a Caddyfile carrying the same routing shape (HTTPS sites, which add
   HTTP→HTTPS redirect routes on the HTTP port; the `http://caddy` health site;
   the `*.home` redirects) and probed it the way blackbox does:

   | Request | Result |
   |---|---|
   | `Host: caddy`, `GET /health` | **200** |
   | `Host: caddy`, `GET /` | 404 |
   | `Host: caddy:8080`, `GET /health` | **200** |

   The site block and the probe target are correct. The fix is not the problem.
2. Read the notification policy against the symptom. `group_by: ["alertname"]`
   put every failing endpoint in the lab into one "Service endpoint unreachable"
   group, and a group is re-sent whenever its membership changes — throttled to
   `group_interval` (5m), not to `repeat_interval` (4h).
3. Wrote `scripts/probe-status.sh` to ask the running stack the question the
   repo cannot answer.

**Issues encountered:**
- **Grouping was amplifying unrelated churn into Caddy alerts.** With one group
  holding every probe, any single flapping target — a container restarting, Loki
  coming up, a desktop scaling to zero — re-sent the whole group every 5
  minutes, and each re-send listed every other firing endpoint again. So one
  unrelated flap reads as *Caddy* alerting non-stop. Grafana's own default
  (`[grafana_folder, alertname]`) has the same problem here: there is only one
  folder.
- **Nothing could tell us whether the fix was deployed.** `prometheus.yml`, the
  Caddyfile and `grafana/provisioning/` are all bind-mounted, so `git pull`
  changes the files on disk while the containers keep serving the old config
  until Caddy is restarted, Prometheus reloaded and Grafana restarted — three
  different actions, none of which `docker compose up -d` performs, because the
  container spec has not changed. The repo looks correct, CI is green, and
  Discord keeps firing the old alert. That gap, not either bug, is what made
  both of these expensive.

**Resolution:**
- `grafana/provisioning/alerting/policies.yml` — `group_by` is now
  `["alertname", "instance"]`. Every rule in `rules.yml` is per-target, so each
  failing thing now gets its own message on its own schedule. Five endpoints
  down is five messages rather than one; that is the right way round, because a
  message naming one thing is actionable and the merged one had to be re-read
  every time to work out what had changed.
- `scripts/probe-status.sh` — new runtime diagnostic. Prints (1) whether
  Prometheus is scraping the targets this checkout declares, (2) what each target
  answers when probed right now, via `blackbox-exporter`, and (3) `changes()`
  per probe over 6h. Together those separate *not deployed* from *stuck probe*
  from *genuinely flapping service*, which are indistinguishable from the
  Discord channel and have opposite fixes. It is the runtime sibling of
  `check-probes.sh`, which does the static half in CI.
- `docker/monitoring/README.md` — a "When `#alerts` is noisy" section with the
  symptom → meaning → fix table.

**Notes / next steps:**
- Deploy, all three, because each config is read at a different moment:
  ```bash
  cd docker/monitoring && docker compose restart grafana   # provisioning is read at startup only
  curl -X POST http://localhost:9090/-/reload              # prometheus.yml
  cd ../proxy && docker compose restart caddy              # Caddyfile
  ```
- Then run `./scripts/probe-status.sh` and expect every target `up` and
  `http://caddy/health` listed as `scraping`. If Caddy still shows `DOWN` with 0
  state changes after that, the probe is stuck on something new and the live
  HTTP status in that output says what.
- A repeating Discord message is not evidence of a repeating failure — noted in
  the previous entry, and it is what made *this* one look like a flap too.

---

## 2026-08-24 — Caddy was never down; the probe was asking the wrong question

**Goal:** Stop `#alerts` filling with "Service endpoint unreachable — Probe
failing for http://caddy/" several times a day. Caddy was up the whole time and
every service behind it was reachable, so the alert was pure noise — and noise
in the only channel that pages you is worse than no channel at all.

**Steps:**
1. Traced the probe end to end. `prometheus.yml` pointed blackbox at
   `http://caddy:80/`. blackbox reaches Caddy over the Docker network, so the
   Host header it sends is the literal container name, `caddy`.
2. Read the Caddyfile against that. Every site block is either
   `<name>.{$HOMELAB_DOMAIN}` or `http://<name>.home` — nothing matches a Host
   of `caddy`, and Caddy answers an unmatched Host with an empty 404.
3. Read the blackbox config against *that*. `http_2xx` has
   `valid_status_codes: []`, which means only a 2xx passes. 404 → `probe_success 0`
   → the rule's `for: 2m` elapses → firing, permanently.
4. Added a `http://caddy` site block serving `/health` as a 200, with `handle`
   blocks rather than bare `respond`s so the match order is explicit rather than
   inherited from directive sorting.
5. Repointed the probe at `http://caddy/health`, bringing it in line with every
   other target in the job — all of which already ask for a real health endpoint
   (`/api/health`, `/-/healthy`, `/ready`).
6. Added `scripts/check-probes.sh` and wired it into the `caddy` CI job ahead of
   the image build.

**Issues encountered:**
- The alert *looked* intermittent — "a few times a day" — which sent the first
  guess toward flapping, restarts or DNS. It was not intermittent at all. The
  alert had been firing continuously since the probe was added; what arrived a
  few times a day was the notification policy's `repeat_interval: 4h`
  re-notifying the same never-resolving alert. Six a day, evenly spaced. Worth
  remembering: a repeating Discord message is not evidence of a repeating
  failure.
- Caddy is the only probe target that is a *router* rather than an application.
  The others answer on any Host because they only serve one thing; Caddy
  deliberately answers nothing it was not told to serve. The bare `/` that works
  fine for Portainer and Ollama could never have worked here.

**Resolution:**
- `docker/proxy/Caddyfile` — new "Health check" section: `http://caddy` serving
  `/health` 200, everything else 404. Declared `http://` so Caddy never attempts
  ACME for a container name, and unreachable from the LAN or tailnet, where
  requests always carry a real hostname.
- `docker/monitoring/prometheus/prometheus.yml` — target is now
  `http://caddy/health`.
- `scripts/check-probes.sh` — fails if a blackbox target aimed at `caddy` has no
  `handle <path>` answering 200 in the Caddyfile. This is the same
  two-files-one-fact drift `check-dashboard.sh` guards, so it is guarded the same
  way: delete the health block or move the probe, and CI goes red instead of
  Discord.

**Notes / next steps:**
- Deploy: `cd docker/proxy && docker compose restart caddy`, then reload
  Prometheus (`curl -X POST http://localhost:9090/-/reload`).
- Verify from the box before trusting it:
  `docker exec blackbox-exporter wget -qSO- 'http://localhost:9115/probe?target=http://caddy/health&module=http_2xx' | grep probe_success`
  → must be `1`. The Discord "Resolved" message should follow within ~2 minutes.
- The old alert will resolve on its own: `instance` is part of the alert's
  identity and it changed, so Grafana retires `http://caddy:80/` rather than
  transitioning it.
- Open question for another day: `repeat_interval: 4h` is what turned one bad
  probe into ~180 messages a month. It is the right setting for an alert you
  must not miss, but it means every false positive is amplified six-fold. Worth
  revisiting only if a second one shows up — the fix for a wrong alert is a right
  alert, not a quieter one.

---

## 2026-08-24 — The ntfy contact point outlived its deletion

**Goal:** Finish the job the previous entry claimed was done. Removing the ntfy
receiver from `contactpoints.yml` turned out not to remove it from Grafana, so
the running instance still held a receiver pointing at a container that no
longer exists.

**Steps:**
1. Added a `deleteContactPoints:` block to `contactpoints.yml` naming
   `uid: ntfy_webhook` — the directive that actually deletes, as opposed to
   just ceasing to mention.
2. Documented both traps in `docker/monitoring/README.md`, plus a
   `curl`-the-provisioning-API check for what Grafana actually holds.

**Issues encountered:**
- **File provisioning upserts; it does not sync.** Removing a resource from a
  provisioning file leaves it in Grafana's database, shown as "Unused". The UI
  will not let you delete it either, because provisioned resources have the
  Delete button greyed out — so the state is reachable only by config, and the
  config that created it no longer mentions it. Deletion requires
  `deleteContactPoints:` with the uid.
- **`docker compose up -d` was a no-op on Grafana, and looked like a success.**
  `provisioning/` is bind-mounted and only read at startup. Editing files under
  it does not change the container's config hash, so compose printed
  `✔ Container grafana  Running` and moved on. The deploy appeared clean while
  changing nothing about alerting. `docker compose restart grafana` is required.
- **Neither trap was visible from CI.** Every check passed on the PR, because
  every check validates *files* — YAML parses, compose interpolates, Caddy
  adapts. Nothing asserts anything about the state of a live Grafana, so a
  provisioning change that is syntactically perfect and semantically inert is
  exactly the class of bug this repo's CI cannot see.

**Resolution:**
- `docker compose restart grafana`, then confirm via
  `/api/v1/provisioning/contact-points` that `discord_webhook` is the only uid.
- Deleting by *receiver* uid rather than contact-point name matters here:
  `policies.yml` routes to the name `homelab`, and Grafana refuses to delete a
  contact point a route still points at. Dropping one of two receivers leaves
  the name intact, backed by `discord_webhook`.

**Notes / next steps:**
- The `deleteContactPoints` block is safe to keep: deleting an absent uid is a
  no-op, so a fresh install converges to the same place. It can go once every
  provisioned instance has restarted with it at least once.
- Worth remembering for the parked backups work, which will provision alert
  rules: the same upsert semantics apply to `deleteRules:`.
- Open question not chased here: `docker volume rm monitoring_ntfy_data`
  returned "no such volume" on CT 100, and no orphan container was removed
  either, which suggests ntfy was not running under this compose project at the
  time. Harmless — nothing to clean up — but the name is worth confirming
  against `docker volume ls` before assuming the data is gone.

---

## 2026-08-24 — Alerting consolidated to Discord; ntfy removed

**Goal:** Drop the ntfy phone app. Alerts had been fanning out to both ntfy
(push over the tailnet) and Discord `#alerts` since the off-box alerting work,
and everything was in practice being read in Discord — so ntfy was a container,
a volume, a Caddy route and an app on the phone all serving a path nobody
looked at.

**Steps:**
1. Removed the `ntfy_webhook` receiver from `contactpoints.yml`, leaving the
   `homelab` contact point with Discord alone. `policies.yml` was untouched —
   it targets the contact point, not the receiver.
2. Deleted the `ntfy` service and its `ntfy_data` volume from
   `docker/monitoring/docker-compose.yml`, the `alerts.*` site block from
   `docker/proxy/Caddyfile`, the tile from `docker/dashboard/config/services.yaml`,
   and `NTFY_BASE_URL` / `NTFY_PORT` from `.env.example`.
3. Promoted `DISCORD_ALERT_WEBHOOK` from `${VAR:-}` to `${VAR:?required}`.
4. Swapped the `NTFY_BASE_URL` stub in `.github/workflows/ci.yml` for a
   `DISCORD_ALERT_WEBHOOK` one, since the required-var set changed.
5. Marked the **ntfy stays** decision in
   [`tsd-alerting-off-box.md`](design/tsd-alerting-off-box.md) superseded rather
   than editing it out.

**Issues encountered:**
- **The webhook had been optional on purpose, and that stopped being safe.**
  The old comment in `docker-compose.yml` said an empty `DISCORD_ALERT_WEBHOOK`
  was allowed because "ntfy still works". Remove ntfy and that same default
  turns into a monitoring stack that starts cleanly, evaluates every rule, and
  delivers nothing — the worst failure mode available, because the dashboards
  all look fine. Hence step 3.
- **CI would have caught it, one commit too late.** The compose job supplies
  throwaway values for exactly the `:?required` vars; adding a new one without
  listing it there fails the run. That is the check working as designed, but it
  meant the required-var change and the CI change had to land together.

**Resolution:**
- Grafana → Alerting → Contact points → test `homelab` is now the single check
  that matters, and it is the one to re-run after any webhook change.
- Deployment on the box is not just `up -d`: the ntfy container and its volume
  outlive the compose change and have to be reaped explicitly (see below).

**Notes / next steps:**
- On CT 100: `docker compose up -d --remove-orphans` in `docker/monitoring/`,
  then `docker volume rm monitoring_ntfy_data` once the container is gone.
  Reload Caddy for the dropped `alerts.*` route. Then delete the phone app and
  the `alerts` DNS entry if one was pinned outside the wildcard.
- **Discord is now a single point of delivery**, which is a real reduction in
  redundancy and worth being honest about. The mitigation is that the failure it
  most plausibly hides — the box or its uplink dying — is precisely what
  `heartbeat.sh` catches from off-box, and Healthchecks alerts the same channel
  by an independent path. A Discord-wide outage would still be silent; accepted.
- The parked backups plan (`tsd-backups-and-monitoring.md`) assumed job alerts
  would reuse ntfy. It should use the `#alerts` webhook instead; the note in
  `AGENTS.md` now says so.

---

## 2026-08-23 — A landing page, and CI to stop it drifting

**Goal:** Seven subdomains and growing, none of them memorable. One URL to start
from.

**Steps:**
1. Added `docker/dashboard/` — [gethomepage](https://gethomepage.dev), config
   bind-mounted from the repo rather than kept in a volume.
2. Served at the bare `{$HOMELAB_DOMAIN}` — the parent of every service name, so
   it is the only URL worth memorising.
3. Added [`scripts/check-dashboard.sh`](../scripts/check-dashboard.sh) and a CI
   job: every Caddyfile site must have a tile, and every tile must point at a
   real site block.

**Decisions:**
- **One dashboard, not two.** Splitting homelab from other projects just moves
  the problem to "which dashboard was it on". Grouped sections instead, with the
  real distinction being *services* (on this box, status-checked) versus
  *bookmarks* (elsewhere, just links).
- **No Docker socket.** Homepage can auto-discover services from container
  labels, but that needs the socket — root-equivalent on this host — to avoid
  maintaining a list CI already keeps honest. The same trade rejected for
  `/deploy`, and it comes out the same way.
- **No credentialed widgets.** A Grafana or AdGuard widget means putting an
  admin password into this stack to render a number that is one click away.
  `siteMonitor` gives up/down and response time and needs nothing.
- **No `siteMonitor` on Kali.** Polling it would boot the container on every
  dashboard refresh, defeating Sablier's scale-to-zero.
- **Where the bookmark list stops: active repositories.** Archived ones are
  excluded, which is a line GitHub already maintains — so the list stays correct
  without anyone making a recurring taste call about what still counts. Archiving
  a repo removes it from here; that is the same decision, made once.
- **Grouped by what a repo *is*, not how active it is.** The first attempt split
  private-personal from public-projects, which put `design` next to `life` and
  `homelab` next to `hoststats` — both wrong. The distinction that holds:
  *Personal* (yours, ongoing, not shipped), *Core* (persistent things simply
  maintained — the site, the lab, the design system; they have no "done", so
  they are not projects), *Projects* (discrete work with a scope and an end).
- **Links go as deep as the URL is stable.** Cloudflare uses `?to=/:account/...`
  so it resolves the account id and lands on the zone's DNS page; Discord links
  into the guild rather than the app root. A bookmark that lands on a product's
  marketing page has saved nothing.
- **brignano.io is a service, not a bookmark.** It is off-box but externally
  reachable, so unlike a bookmark it can carry a real status check, and "is my
  site up?" is worth answering at a glance. The coverage check ignores hrefs
  without `{{HOMEPAGE_VAR_DOMAIN}}`, so an external service is not mistaken for
  a tile pointing at a deleted site block.

**Issues encountered:**
- **A DNS wildcard does not match its own parent.** `*.home` covers
  `stats.home.<zone>` but never `home.<zone>`, so the dashboard needs its own
  `A` record. Missing it looks exactly like a Caddy fault and is not.
- **Homepage rejects unrecognised Host headers.** Without `HOMEPAGE_ALLOWED_HOSTS`
  it answers "Invalid Host header" rather than serving a page — again, looks
  like a proxy problem.
- **The tile list is a second copy of the Caddyfile's list**, hand-maintained,
  in another file. That is the same drift shape as the deployment gap, except a
  stale dashboard never breaks — it just silently stops being complete, so you
  go on trusting it. Hence the CI check, in both directions: a site with no tile
  fails, and a tile pointing at a deleted site fails too.

**Verification:** the check was tested by breaking it on purpose — adding a
Caddyfile site with no tile (`MISSING vault`, exit 1) and a tile pointing at a
non-existent site (`STALE gone`, exit 1). All nine compose files still validate,
the four config files parse as YAML, and stock Caddy parses past the new site
block (failing later on the sablier plugin it does not have), so the block
itself is sound.

- **Then it shipped with exactly the bug it warned about.**
  `HOMEPAGE_ALLOWED_HOSTS` was set to `home.$HOMELAB_DOMAIN`, but the Caddyfile
  serves the dashboard at the *bare* `$HOMELAB_DOMAIN` — so with
  `HOMELAB_DOMAIN=home.brignano.io` the allowlist read `home.home.brignano.io`
  and every request was rejected as "Invalid Host header". The container starts
  fine and Caddy proxies fine; only the page is wrong, which is why it reads as a
  proxy fault. Two files that must hold the same string, in different syntaxes,
  with no check between them — the same shape as the tile drift, introduced in
  the commit that added the check for it. `check-dashboard.sh` now compares them
  too, and was verified by reintroducing the bug.

- **And then a second one, from the same cause: I never ran the container.**
  The config mount was `:ro`, which is right — it is the codified part and
  nothing inside the container should be able to drift it away from what CI
  checks. But Homepage writes its log file to `config/logs`, so the mkdir failed
  on every render and the page 500'd with the config perfectly valid. Fixed with
  a named volume nested inside the read-only bind — which failed too, and worse:
  runc has to *create* the mountpoint inside the bind before mounting over it,
  and the bind is read-only, so the container would not start at all. Settled on
  a plain writable bind. `:ro` there was defensive polish rather than a boundary
  — Homepage never writes its own config, the container has no Docker socket and
  sits on one network, and CI is what actually keeps the directory honest.
  `config/logs/` is gitignored.

  All three dashboard failures were runtime behaviour of an image that was never
  started before it shipped: static checks passed every time. Worth remembering
  next time a stack looks finished because CI is green.

**Notes / next steps:**
- Adding a service is now three edits: a Caddyfile block, a dashboard tile, and
  `docker compose restart caddy`. CI enforces the middle one.

---

## 2026-08-23 — repo-sync heals instead of nagging

**Goal:** The drift report's answer was always "run this command", so stop
printing it and run it — without handing a cron job the power to take the
network down.

**Steps:**
1. `repo-sync.sh` now restarts stale stacks itself, verifies each came back, and
   reports what changed.
2. `HL_NO_AUTOHEAL` (default `proxy`) lists stacks that are only ever reported.
   `HL_AUTOHEAL=no` restores report-only behaviour.
3. Reworked the output: one section per outcome (restarted / failed / needs you)
   and the remaining commands collected into a single block instead of repeated
   after every line.

**Issues encountered:**
- **`proxy` is the exception, and it is not a close call.** AdGuard runs in that
  stack, so auto-restarting it is auto-restarting the household's DNS. Nothing
  guards that failure the way `heartbeat.sh` guards Grafana and Prometheus, and
  a failure at 4am leaves no working name resolution to debug through.
- **A restart without verification is worse than no restart**, because it turns
  "stale but working" into "broken and unattended" while reporting success.
  Every heal is followed by a check that at least as many containers are running
  as before and none are stuck restarting.
- **One failure mode turned out to already be safe:** `up -d --build` builds
  *before* it recreates, so a broken build leaves the previous container
  serving. The case worth catching is a build that succeeds and then crashes.
- **Auto-healing is skipped entirely when the pull failed.** A tree that could
  not fast-forward is in an unknown state and is not one to deploy from.
- Real rollback was considered and rejected: `--build` discards the previous
  image unless it is tagged first, so a genuine rollback needs a tagging scheme
  and a retention policy. Verify-and-shout is the honest version of the 90%.

**Verification:** stubbed `docker`, a throwaway origin and a local webhook
receiver, covering each path — clean heal (exit 0); a stack that comes back with
fewer containers (reported under RESTART FAILED as `1/2 running`, exit 1);
`proxy` routed to "Needs you" with its command; a failed pull suppressing all
healing.

---

## 2026-08-23 — Drift report cried wolf on its first run

**Goal:** Fix a 50% false-positive rate in `repo-sync.sh`, found the first time
it ran for real.

**Steps:**
1. Excluded `**/*.md`, `tests/**` and `.env.example` from the "newest commit
   touching this stack" calculation.

**Issues encountered:**
- **Docs and tests live inside the stack directories but are never deployed.**
  The first real run flagged four stacks; two were changes that could not
  possibly affect them — `mcp` for a `README.md`, `assistant` partly for a
  `README.md` and `tests/smoke.py`, neither of which the Dockerfile copies (it
  takes `requirements.txt`, `app/` and `guild.yml`). A report that is wrong half
  the time is one you learn to ignore, which is worse than not having it.
- **`:(exclude)` silently does nothing with `**`.** Git pathspec needs glob
  magic for `**` to expand, so the exclusion must be written
  `:(exclude,glob)`. Written the obvious way it fails open — the filter appears
  to work and changes nothing. Caught by comparing filtered against unfiltered
  output rather than assuming.

**Verification:**
- Throwaway repo with a stubbed `docker`: a doc-only commit after container
  start does not flag; a compose change after container start does.
- Against the real repo the filter moves `mcp` from "10 hours ago" back to
  "3 months ago" — older than its container, correctly clearing it.

**Notes / next steps:**
- What the first run *did* correctly catch: `monitoring` had been up 24 days
  while its alerting provisioning was written 10 hours earlier. Grafana reads
  alerting provisioning only at startup, so the codified contact points had
  never been loaded — the working alerts were hand-made in the UI. Exactly the
  invisible gap the report exists to surface.

---

## 2026-08-23 — Repo drift: a daily pull, a report, and a CI gate

**Goal:** Stop the working tree on CT 100 silently falling days behind GitHub —
without turning that into an unattended deploy pipeline aimed at the box that
serves the household's DNS.

**Steps:**
1. Added [`scripts/repo-sync.sh`](../scripts/repo-sync.sh) — daily
   `git pull --ff-only` plus a deployment-drift report, posting to `#alerts`
   only when there is something to do.
2. Added `.github/workflows/ci.yml` — the first CI this repo has had. Four jobs:
   assistant smoke tests, `docker compose config` on all eight stacks, `sh -n`
   on the cron scripts, and `caddy adapt` inside the real proxy image.
3. Added [`docker/assistant/tests/smoke.py`](../docker/assistant/tests/smoke.py)
   — 22 offline checks, no pytest, no network.

**Issues encountered:**
- **A pull cron on its own would have made things worse.** Nothing on the box
  runs from the working tree; every service runs from a built image or read its
  config when its container started. Pulling silently leaves the repo *ahead* of
  what is running, so `git log` says you are current when you are not — a
  visible gap converted into an invisible one. The pull is only safe because the
  drift report ships with it.
- **"Is this stack stale?" is two different questions.** A stack that builds its
  own image (assistant, proxy) has to be compared against the *image* creation
  time, because a restart does not rebuild — and a host reboot restarts
  everything, which would otherwise read as fresh. A stack that pulls upstream
  images and bind-mounts its config from the repo only needs a restart, so
  *container start* time is the right basis. The script picks per stack by
  looking for a `build:` key in the compose file.
- **`set -e` plus `[ -n "$X" ] && VAR=…` is a trap.** When the test fails the
  AND-list returns non-zero and the whole script exits — and the test failing is
  the *normal* case when building an optional report. Caught by running it;
  rewritten as `if` blocks.
- **A pull is not inert.** Seven repo files are bind-mounted into running
  containers, and Grafana polls its dashboard provisioning directory. An
  automatic pull can therefore change dashboards with no restart from you.
- **Stock Caddy cannot check this Caddyfile at all.** `acme_dns cloudflare`
  fails with "module not registered" unless the plugin is compiled in, so CI
  builds the real proxy image and checks inside it. Checking against stock Caddy
  would have proved nothing about what actually deploys.
- **`caddy validate` was the wrong verb, and CI proved it.** The first run went
  red: `validate` does not stop at parsing — it *provisions* every module, and
  the cloudflare DNS provider checks its API token's format while doing so, so
  the throwaway `ci` token was rejected. A real `validate` would need a
  credential-shaped secret in CI to say anything at all, which means either
  putting a live Cloudflare token there or testing a fake. `caddy adapt` stops
  at Caddyfile → JSON, which is the right boundary: it still catches syntax
  errors and missing plugin directives, and needs no *valid* secrets. Worth
  noting what the failure *did* prove — the log shows `adapted config to JSON`
  before the provisioning error, so the Caddyfile itself was never in question.
- **Then it went red a second time, for the opposite reason.** Having decided
  the token was not needed, dropping it entirely fails earlier still:
  `acme_dns cloudflare {$CLOUDFLARE_API_TOKEN}` expands to nothing and the
  directive is rejected at *adapt* time with "missing API token". So the token
  must be present but need not be well-formed — presence is checked by the
  adapter, format by the provisioner. Two failures, two different stages, and
  the first run's log was what proved the fix: it had already adapted cleanly
  with exactly this value.

**Verification:**
- `repo-sync.sh` exercised against a throwaway origin and a stubbed `docker`,
  covering all three paths: clean (silent, exit 0), pull-and-stale (correct
  stack flagged, correct restart hint), and pull failure (reported, exit 1).
- Smoke tests mutation-checked: leaking `num_thread` into a request payload and
  deleting the do-not-volunteer guard from the chat prompt each turn the suite
  red, so the checks are load-bearing rather than decorative.
- Three of four CI jobs run locally and pass; the `caddy` job needs a Docker
  daemon and was verified only as far as a stock binary allows — it failed on
  its first real run and was fixed (see above), which is roughly the point of
  having CI.

**Install the cron entry** (on CT 100, as root):

```bash
crontab -e
```

```
0 4 * * * /root/homelab/scripts/repo-sync.sh
```

Silence means the tree is current and every stack is running that code. Run it
by hand once first — it prints the same report it would post.

**Notes / next steps:**
- A `/deploy` slash command is the obvious sequel, and deliberately not built
  yet: it needs the Docker socket in the assistant container (root-equivalent on
  that host), and it is only defensible now that CI gates `main`. The model must
  never be the thing that chooses — Discord's own enumerated choices are the
  picker, Python does the work.
- CI does not yet lint shell beyond `sh -n`; shellcheck on the existing scripts
  is unverified and would need a pass before being made blocking.

---

## 2026-08-23 — #chat can see the homelab

**Goal:** First real conversation in `#chat` produced *"I can't access anything
on the homelab or assess the current load or performance metrics."* True — and
explicitly instructed — but useless, and the data was two seconds away in the
same bot. `/digest` reads Prometheus and Loki; `#chat` could not.

**Steps:**
1. Added `Facts.compact()` — one dense line of readings for a chat prompt,
   terser than the digest's block.
2. Replaced the fixed `CHAT_SYSTEM` with `build_system(facts_line)`: with
   readings it states them as measured fact; without, it falls back to saying it
   cannot see live data.
3. Added `_live_metrics()` to the bot, with a TTL cache.
4. New knobs: `CHAT_LIVE_METRICS` (default true), `CHAT_METRICS_TTL_S` (60).

**Issues encountered:**
- **Tool-calling was the obvious answer and the wrong one.** A 3B is unreliable
  at it — the reason `tsd-ai-homelab-assistant.md` shelved itself — and the same
  hardware constraints still apply.
- **The old prompt made the limitation too salient.** "You cannot see any live
  data" as a standing declaration meant a 3B led with it on every question,
  related or not — the same failure as the persona bug earlier today.
- **Six queries per message** would be wasteful in a fast back-and-forth.
- **A stale reading is worse than no reading.** Numbers presented as current but
  measured minutes ago during an outage would actively mislead.

**Resolution:**
- **Injection, not tools.** Facts are gathered deterministically on every turn;
  the model never decides whether to look, it simply always has them. Python
  measures — including the "needs attention" verdict — and the model only reads
  the numbers out. Same rule that makes the digest trustworthy.
- The no-live-data clause now appears *only* when collection actually failed.
  The no-internet caveat stays, scoped to things genuinely outside the box.
- Cached for 60s; a homelab does not change meaningfully between two messages
  typed seconds apart.
- **Failure yields no readings, never stale ones** — the cache is not served past
  its TTL when a fresh collection fails, and failures are not cached.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
No config change needed — it is on by default. Ask "how's the server doing?" in
`#chat`; expect real numbers. `docker logs assistant` shows the collection.

**Notes / next steps:**
- Web search was **not** added. The 2026-06-07 decision still holds: a 3B
  ignored retrieved sources and answered from its prior, which a better search
  engine does not fix.
- Filesystem/shell access was **not** added. Debugging needs multi-step
  reasoning over tool results — the 3B's weakest area — and `mcp.home` already
  gives Claude that job with a read-only ceiling.
- If replies slow noticeably, the readings cost ~50-60 tokens per turn;
  `CHAT_LIVE_METRICS=false` removes them.

## 2026-08-23 — Conversational #chat, and a layout that means something

**Goal:** Two complaints. The channels felt like stock Discord with no
intention behind them, and talking to the model meant typing `/ask` every single
time — no continuity, no follow-ups, no conversation.

**Steps:**
1. Reworked `guild.yml` into two categories split by **direction**: `HOMELAB`
   (digest, alerts — the lab reporting to you) and `ASSISTANT` (chat — you
   talking to it), with real topics explaining what each is for.
2. Renamed `#ask` to `#chat` and made it conversational: `on_message` answers
   every message, no command needed.
3. Added `app/chat.py` and `Ollama.chat()` (`/api/chat`), factoring the shared
   request handling out of `generate()` into `_post()`.
3b. **Context now follows Discord's own primitives** rather than rolling channel
   history: a plain message is a one-off, a reply walks its reply chain, and a
   message in a thread reads the whole thread. Threads (and forum posts, which
   are threads) are therefore the persistence unit — named conversations you can
   return to. Pointing `DISCORD_CHAT_CHANNEL_ID` at a forum channel gives a
   browsable list of them with no code change.
4. New optional settings: `DISCORD_CHAT_CHANNEL_ID` (blank = feature off),
   `CHAT_HISTORY_TURNS`, `CHAT_HISTORY_CHARS`, `CHAT_NUM_PREDICT`.

**Issues encountered:**
- **A bot that answers every message will answer itself, forever.** The first
  thing `_should_handle` checks is whether the author is a bot.
- **Reading plain messages needs the Message Content privileged intent.** PR #34
  listed "no privileged intents" as a security property, so this walks one back.
- **Conversation memory needs somewhere to live**, and any in-process store is
  lost on the restarts that happen constantly during setup.
- **Rolling channel history was the wrong default.** Two unrelated questions
  typed into the same channel contaminate each other's context, and there is no
  way to end a conversation short of waiting for it to scroll away.
- **Unbounded history would get slower every turn** — prompt evaluation is
  CPU-bound and roughly linear in tokens.
- **Concatenating turns into one prompt** would feed the model a format it was
  never trained on.

**Resolution:**
- **Discord *is* the store**, and *where you type* decides what is read back:
  plain message → itself only; reply → the reply chain; thread → the whole
  thread. No command, no state, and the context is always visibly implied by
  where the message sits. No database, no state: restart-safe, threads get
  their own context for free, and deleting a message actually removes it from
  the model's memory. What you see in the channel is what it sees.
- The intent is requested **only when a chat channel is configured**, and
  narrowed in code — one channel (plus its threads), allowlisted users only.
- Both a turn cap and a character budget, trimming oldest-first while always
  keeping the newest message, since that is the one being answered.
- `/api/chat` applies the model's own chat template to role-tagged turns.
- `//` prefix: no reply, and excluded from context — for notes and asides.

**On the box (apply after merge):**
```bash
# 1. Developer Portal -> Bot -> Privileged Gateway Intents -> Message Content ON
# 2. Rename #ask to #chat IN DISCORD (preserves history), then copy its ID
cd ~/homelab && git pull
nano docker/assistant/.env      # DISCORD_CHAT_CHANNEL_ID=<the #chat id>

docker compose -f docker/assistant/docker-compose.yml run --rm --build \
  assistant --provision                     # review, then --apply
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
Then just type in `#chat`. Expect ~10-30s per reply depending on how much
history is in context.

**Notes / next steps:**
- The bot's display name is now declared too (`bot.nickname` in `guild.yml`,
  applied as a per-server nickname). Deliberately the nickname rather than the
  global username: Discord rate-limits username changes to 2/hour, while a
  nickname is server-scoped and free to change. Renamed `spotter` to `Otto` —
  a role-description read as cold in practice; a plain name reads like a
  participant in the conversation.
- Rename channels in Discord rather than in `guild.yml` — the provisioner never deletes,
  so changing the name in the file creates a second, empty channel instead.
- If replies get slow, lower `CHAT_HISTORY_TURNS` before anything else; context
  length is the dominant cost on this hardware.
- `#alerts` stays webhook-fed and is untouched by any of this — deliberately, so
  it keeps working when the bot is down.

## 2026-08-23 — Real domain + real certificates, still not exposed

**Goal:** Replace the `*.home` pseudo-TLD with a real domain and publicly-trusted
certificates, **without publishing anything**. The obvious reading of "put it on
my domain" is a tunnel or a port forward; that was not wanted, and `AGENTS.md`
rules it out for admin services.

**Steps:**
1. Added `--with github.com/caddy-dns/cloudflare` to the Caddy build (it was
   already an `xcaddy` build for the Sablier plugin).
2. Rewrote the Caddyfile: sites are now `<name>.{$HOMELAB_DOMAIN}` with
   `acme_dns cloudflare` in the global block.
3. Kept every `*.home` name as an `http://` site that redirects to its real
   counterpart, so bookmarks, `hl-*` aliases and the dev machines' MCP config
   keep working.
4. Added `HOMELAB_DOMAIN` and `CLOUDFLARE_API_TOKEN` (both `:?required`) to the
   proxy stack; documented the exact token scope in `.env.example`.
5. `shell/aliases.sh` gained `HL_DOMAIN` (defaults to `home`, so the aliases work
   unchanged and just take the redirect).
6. Wrote [`docs/design/tsd-real-domain-private-tls.md`](design/tsd-real-domain-private-tls.md)
   and annotated the original proxy TSD as superseded in part.

**Issues encountered:**
- **HTTP-01 cannot work here.** It needs port 80 reachable from the internet —
  precisely what is being refused.
- **`.home` must never be sent to a CA.** No public CA will issue for it, and an
  attempt would produce repeated failures in the log.
- **Caddy rejects single-line site blocks.** `addr { directive }` on one line is
  a syntax error; the closing brace needs its own line. Caught by validating
  with a real Caddy binary rather than by eye.

**Resolution:**
- **ACME DNS-01**: Caddy proves ownership by writing a TXT record to the
  Cloudflare zone, never by receiving a connection. That is what makes real
  HTTPS possible on a host the internet cannot reach — and it retires the
  internal-CA trust prompt `kali.home` needed.
- Legacy blocks are declared `http://` so Caddy never attempts issuance for them.
- Config validated with `caddy validate` (plugin directives stubbed, since the
  stock binary lacks them) and formatted with `caddy fmt`. All 14 hostnames —
  7 real, 7 redirects — adapt correctly.

**On the box (apply after merge):**
```bash
# 1. Cloudflare DNS: add ONE record, proxy OFF (grey cloud):
#      Type A   Name *.home   Content 10.0.0.201   Proxy status: DNS only
#    (adjust "home" to whatever subdomain you chose)
# 2. Cloudflare API token: dash.cloudflare.com/profile/api-tokens ->
#    Create Token -> Custom -> Zone | DNS | Edit, scoped to this zone only.
#    Do NOT use the Global API Key.
cd ~/homelab && git pull
nano docker/proxy/.env     # HOMELAB_DOMAIN=, CLOUDFLARE_API_TOKEN=

docker compose -f docker/proxy/docker-compose.yml up -d --build
docker logs -f caddy       # watch certificate issuance; ~1-2 min for all seven
```
Then verify: `https://stats.<domain>` loads with a valid padlock, and
`http://stats.home` redirects to it.

**Notes / next steps:**
- **Proxy status must be DNS only.** An orange cloud would route through
  Cloudflare's edge, which cannot reach a private address.
- Update each dev machine's `.mcp.json` to `https://mcp.<domain>/mcp`. The old
  URL still works via redirect, but MCP clients may not follow redirects.
- Once nothing uses `*.home`, delete that Caddyfile section and the AdGuard
  rewrites. AdGuard stays for ad blocking and as the tailnet resolver.
- First issuance is ~7 sequential DNS-01 challenges; subsequent renewals are
  automatic and staggered.

## 2026-08-23 — Alerting that survives the box going down

**Goal:** Close the hole found the hard way — the server went offline and
nothing said so. Grafana evaluates the rules, ntfy delivers the push, and the
assistant posts the digest, all inside CT 100. When the box dies, all three die
with it. The one failure most worth hearing about was guaranteed to be silent.

**Steps:**
1. Added a `discord` receiver alongside the existing ntfy webhook in
   `contactpoints.yml` — one contact point, two receivers, so `policies.yml`
   only changed its target name (`ntfy` → `homelab`).
2. Passed `DISCORD_ALERT_WEBHOOK` into the Grafana container so provisioning can
   interpolate it; documented it in `.env.example`.
3. Added [`scripts/heartbeat.sh`](../scripts/heartbeat.sh) — a dead man's switch
   that pings Healthchecks.io from cron every 5 minutes.
4. Updated `#alerts` in `guild.yml` to say what actually feeds it, and added
   `hl-heartbeat` to `shell/lib.sh`.
5. Wrote [`docs/design/tsd-alerting-off-box.md`](design/tsd-alerting-off-box.md).

**Issues encountered:**
- **A monitoring system cannot report its own death.** No alert rule and no
  extra container on CT 100 can fix this; anything hosted on the watched machine
  inherits the same failure.
- **An unconditional ping would only prove cron ran**, not that monitoring works.
- **Routing alerts through the assistant bot** would have reintroduced exactly
  the dependency being removed.

**Resolution:**
- Inverted the logic: the box pings *out*, and **silence is the signal**. That's
  the only shape that survives the failure it's meant to catch — and it needs no
  inbound access, so still no port, no tunnel, no public endpoint.
- The heartbeat pings only while `grafana` and `prometheus` are running, so
  "host up, Docker wedged" is caught too. If either is missing it pings `/fail`
  and alerts immediately rather than waiting out the grace period.
- `#alerts` is fed by **webhooks only**. A Discord webhook needs no bot process,
  so alerts arrive even when the whole `ai` stack is down.
- Kept ntfy. Redundancy at the notification layer is cheap, and it preserves a
  path that doesn't depend on Discord or on having internet at all.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull

# 1. Discord webhook: Server Settings -> Integrations -> Webhooks ->
#    New Webhook -> channel #alerts -> Copy Webhook URL
# 2. Healthchecks.io: create a check, period 5m, grace 15m, and add its
#    Discord integration pointed at the same webhook. Copy the ping URL.
nano docker/monitoring/.env      # DISCORD_ALERT_WEBHOOK=, HEALTHCHECKS_PING_URL=

docker compose -f docker/monitoring/docker-compose.yml up -d   # reload provisioning
./scripts/heartbeat.sh && echo ok                              # verify by hand

# 3. Schedule it (alongside the 02:00 pg-backup entry):
crontab -e
*/5 * * * * /root/homelab/scripts/heartbeat.sh
```
Verify end to end: in Grafana, **Alerting → Contact points → homelab → Test** —
a message should land in `#alerts`. Then stop the monitoring stack for 15
minutes and confirm Healthchecks alerts. Testing the failure path is the whole
point; an untested dead man's switch is an assumption.

**Notes / next steps:**
- Healthchecks itself going down is uncovered and accepted.
- The same Healthchecks account can later monitor `pg-backup.sh`, which is what
  `tsd-backups-and-monitoring.md` wanted it for. Still ⏸ parked on a USB SSD.
- 5m period / 15m grace = two consecutive misses before alerting. Widen the
  grace before weakening the check if false alarms appear.

## 2026-08-22 — Discord server layout codified (guild.yml + `--provision`)

**Goal:** Start the Discord side from scratch — no server, no bot, no channels —
without the layout ending up as undocumented clicks in a UI. Everything else in
this lab is config-in-git; the channels the digest depends on should be too.

**Steps:**
1. Added `docker/assistant/guild.yml` — declarative categories, channels, topics
   and permissions.
2. Added `docker/assistant/app/provision.py` and a `--provision` mode:
   `--provision` prints a plan and changes nothing; `--provision --apply`
   executes it.
3. Layout: category `HOMELAB` with `#digest` (bot posts only), `#ask`
   (interactive), `#alerts` (reserved for a future ntfy bridge).
4. Documented the manual-vs-codified split in `docker/assistant/README.md`.
5. Added PyYAML to the pinned requirements; `guild.yml` is copied into the image.

**Issues encountered:**
- **Locking `#digest` would have silently broken the digest.** Denying
  `@everyone` Send Messages also denies the bot — it's a member like any other.
  Without an explicit self-allow, the daily post would fail into a channel the
  bot itself created.
- **Chicken-and-egg on `DISCORD_DIGEST_CHANNEL_ID`.** It can't be known until
  the channel exists, but `Config.from_env()` requires it, so provisioning
  couldn't reuse the normal config path.
- **A bot token cannot do everything.** Creating a server and restricting a
  slash command to a channel both need a *user* OAuth token.
- **Deleting channels to converge would be unrecoverable** — `#digest` is the
  health history.

**Resolution:**
- Every locked channel gets a paired overwrite: deny `@everyone`, explicitly
  allow the bot — `send_messages` only, since Discord rejects an overwrite
  granting a permission the acting bot doesn't itself hold.
- `--provision` uses a minimal config path needing only `DISCORD_TOKEN` and
  `DISCORD_GUILD_ID`, and prints the `.env` line to paste when it finishes.
- The two bot-impossible steps are documented as clicks rather than
  half-automated.
- The provisioner is additive only: it creates and fixes drift, never deletes.
  Channels not in `guild.yml` are reported and left alone.
- Login-only (no gateway connect) so it starts and exits in about a second.

**On the box (apply after merge):**
```bash
# 1. Create the server + bot by hand (see docker/assistant/README.md).
#    Invite with View Channels + Send Messages + Manage Channels + Manage Roles
#    (permissions=268438544).
# 2. Fill DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_ALLOWED_USER_IDS, TZ.
cd ~/homelab && git pull
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --selftest
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --provision
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --provision --apply
# 3. Paste the printed DISCORD_DIGEST_CHANNEL_ID into .env, then:
docker compose -f docker/assistant/docker-compose.yml up -d --build
```
Manage Channels / Manage Roles can be removed afterwards — the bot needs only
View Channels + Send Messages to run.

**Notes / next steps:**
- Still not deployed; no Docker daemon was available. The diff engine and the
  apply path were verified offline against fakes, and every discord.py call was
  checked against the pinned 2.4.0 API, but nothing has run against a real guild.
- `#alerts` is created but nothing writes to it yet — ntfy still serves alerts at
  `alerts.home`. Bridging it is the obvious next step.
- Restricting `/ask` to `#ask` (Server Settings → Integrations) is worth doing
  once, or `#digest` stops being a clean log.

## 2026-08-22 — Local LLM moved from pull (chat page) to push (Discord jobs)

**Goal:** Actually use the local `llama3.2:3b` instead of reaching for Claude by
default. The blocker was never the model — it was that Open WebUI is a *pull*
interface: you go to `chat.home`, type, and watch ~16 tok/s. Against Claude that
comparison is lost before it starts, so the box idled.

**Steps:**
1. Added the `assistant` stack (`docker/assistant/`) — a small Python container
   running a Discord bot. No `ports:`, no Caddy route, no DNS rewrite: it opens
   an *outbound* websocket to Discord, so it adds zero inbound surface and works
   off-tailnet, which `chat.home` can't.
2. Scheduled daily digest (`DIGEST_AT`, default 07:30): services up/down,
   CPU/RAM/disk, container restarts, and log-error counts by container, queried
   from Prometheus + Loki.
3. Interactive surfaces: `/ask`, `/summarize`, right-click → *Summarize message*,
   plus `/digest` and `/status`.
4. All LLM work funnels through a single-worker priority queue
   (`app/jobqueue.py`), interactive ahead of scheduled.
5. Wrote `docs/design/tsd-local-llm-discord-jobs.md`; reframed
   `docs/ai-strategy.md` around "is anyone waiting on the answer?"; noted in
   `tsd-ai-homelab-assistant.md` that its canned-summary half now ships.
6. Added `assistant` to `HL_STACKS` in `shell/lib.sh` so `hl-up` includes it.

**Issues encountered:**
- **Concurrency would have made things worse, not better.** The tuned model pins
  `num_thread 4` of the LXC's 6-core quota, so two generations at once contend
  for the same cores and memory bandwidth — both crawl *and* the monitoring/proxy
  stacks lose their remaining 2 cores. Ollama accepts the parallel requests
  happily and thrashes.
- **Request-time options override the Modelfile.** Passing `num_thread` on
  `/api/generate` would silently undo the pin that took generation from
  ~0.5 tok/s back to ~16 (the 2026-06-07 fix below).
- **A digest that can silently say "all clear" is worse than none.** Naive error
  handling would render an unreachable Prometheus as zero problems.

**Resolution:**
- Single worker for every LLM call; supporting caps on context, output length,
  input size, backlog depth, and per-job timeout.
- `num_thread` deliberately never sent; documented as a rule in `AGENTS.md`.
- Python queries *and thresholds* every fact, including the "is this fine?"
  verdict — the model only restates a finished facts block. If Ollama fails the
  digest still posts without prose; an unreadable backend is reported as
  **Incomplete**, never as healthy.
- Container healthcheck watches a 60s heartbeat file, since a Discord client can
  lose its gateway socket while the process stays alive.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
cp docker/assistant/.env.example docker/assistant/.env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_DIGEST_CHANNEL_ID,
# DISCORD_ALLOWED_USER_IDS, TZ

# check the backends before involving Discord — needs no token:
docker compose -f docker/assistant/docker-compose.yml run --rm assistant --selftest

docker compose -f docker/assistant/docker-compose.yml up -d --build
docker logs -f assistant     # expect "connected as <bot> (guild ...)"
```
Discord app setup (bot token, guild install with `bot` + `applications.commands`,
**no** privileged intents) is in
[`docker/assistant/README.md`](../docker/assistant/README.md).

**Notes / next steps:**
- Not yet deployed — the image has not been built or run against real Discord,
  Prometheus, Loki or Ollama. Logic was verified offline (parsers against real
  API payload shapes, queue serialization/priority and load-shedding, and the full
  digest path over stub HTTP servers); `--selftest` is the first real check.
- Grafana-managed alert state is deliberately not in the digest — the rules live
  in Grafana, not Prometheus, so `ALERTS` isn't queryable. Could be added later
  via the Grafana API.
- If a second container ever drives Ollama, the single-worker guarantee breaks —
  the queue would need to move behind a shared gateway.

## 2026-06-07 — Reverted web search + 7B; consolidated to single local model

**Goal:** Walk back the SearXNG + `qwen2.5:7b` web-search experiment (below). In
practice the 7B was too slow and RAM-hungry on this CPU-only / 16 GB box, and a
3B can't faithfully use retrieved sources anyway — so the whole web-augmented
path added latency and confidently-wrong answers for no real gain.

**Steps:**
1. Removed the `searxng` service + `docker/ai/searxng/settings.yml`, the
   web-search env on `open-webui`, and `SEARXNG_SECRET` from `.env.example`.
2. Removed `docker/ai/models/qwen2.5.Modelfile`; back to a single `llama3.2:3b`.
3. Restored `OLLAMA_KEEP_ALIVE=-1` (one small model, keep it resident — no
   cold-load lag).
4. Rewrote the README AI section; recorded the rationale + local-vs-Claude split
   in `docs/ai-strategy.md`; shelved `docs/design/tsd-ai-homelab-assistant.md`.

**On the box (apply after merge):**
```bash
cd ~/homelab && git pull
docker compose -f docker/ai/docker-compose.yml up -d --remove-orphans   # drops searxng
docker exec ollama ollama rm qwen2.5:7b                                  # reclaim ~5 GB
# remove the leftover bind-mount dir + the now-unused SEARXNG_SECRET line in .env
rm -rf docker/ai/searxng
```
In Open WebUI: delete the "🔎 Research" preset; keep "⚡ Quick Chat" (llama3.2:3b,
web search off). Web-search settings persist in the `open_webui_data` volume but
are harmless once the engine/container is gone.

**Notes:**
- Decision recorded in `docs/ai-strategy.md` → Decision log. Live-data / reasoning
  tasks (incl. trip & climbing-weather planning) go to Claude.

---

## 2026-06-07 — Self-hosted web search for Open WebUI (SearXNG)

> ⚠️ **Superseded** by the entry above — this setup was reverted the same day.

**Goal:** Give the local Llama model working web search. DuckDuckGo (DDGS) via
Open WebUI's built-in engine kept returning "no sources found" (DuckDuckGo
rate-limits/blocks the scraped queries), so answers silently fell back to stale
training data.

**Steps:**
1. Added a `searxng` service to `docker/ai/` (same `ai` network as Open WebUI,
   **no host port** — internal-only). Hardened with `cap_drop: ALL` + minimal
   `cap_add`, healthcheck on `/healthz`.
2. Committed `docker/ai/searxng/settings.yml` with `use_default_settings: true`,
   `limiter: false`, and the critical `search.formats: [html, json]` — Open WebUI
   talks to SearXNG over JSON; without it every query 403s.
3. Wired Open WebUI to it via env (`WEB_SEARCH_ENGINE=searxng`,
   `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>`) and added
   `SEARXNG_SECRET` to `.env.example` (entrypoint injects it into `secret_key`).

**Issues encountered:**
- DDGS "no sources found" → DuckDuckGo throttling, not a config bug.
- Existing Open WebUI volume persists web-search config (PersistentConfig), so the
  new env vars don't override it on an already-running instance.

**Resolution:**
- On the box: set `SEARXNG_SECRET` in `.env`, `docker compose up -d`, then in
  Open WebUI **Admin > Settings > Web Search** switch the engine to **searxng**
  and set the Query URL to `http://searxng:8080/search?q=<query>`.

**Follow-up — model upgrade for RAG faithfulness:**
- With SearXNG working, search retrieved the *correct* sources (Proxmox VE 9.0),
  but `llama3.2:3b` ignored them and still answered "7.0.18" from its training
  prior — a 3B model is too small to faithfully use retrieved context.
- Added `qwen2.5:7b` (pinned `num_thread 4`) in `docker/ai/models/`. Select it in
  Open WebUI when Web Search is on; keep `llama3.2:3b` for quick chats.
- Changed `OLLAMA_KEEP_ALIVE` from `-1` to `5m`: pinning both models resident is
  ~8 GB and crowds the 14 GB LXC. Ollama now loads whichever model the chat
  selects and frees it after idle (cost: ~10-40s reload on switch).
- Apply on the box: `./docker/ai/load-models.sh` (pulls + tunes qwen2.5:7b).

**Notes / next steps:**
- Result count 3 + fetch length capped to keep prompts small on the CPU-only LXC
  (long context = slow time-to-first-token on the 7730U).
- Verify with a current-events query ("latest stable Proxmox VE version" → should
  return **9.0 with source citations** when using qwen2.5:7b + Web Search).

---

## 2026-06-07 — Observability stack deployed & verified in production

**Goal:** Deploy the monitoring buildout (exporters, Loki/Alloy, dashboards, ntfy
alerting) to the LXC and confirm it works end to end.

**Steps:**
1. On host **m5**: created read-only PVE token (`monitoring@pve!grafana`, role
   `PVEAuditor`); installed `prometheus-node-exporter` on the bare metal.
2. In the LXC: created read-only Postgres role (`monitoring`, `pg_monitor`);
   filled `.env`; `docker compose up -d` (all 11 containers up).
3. Follow-ups (PR #11): switched the ntfy contact point to `?template=grafana`
   for readable pushes; fronted ntfy with Caddy as `alerts.home`; baked the
   Proxmox host IP (`10.0.0.200`) into `prometheus.yml`. Added
   `GF_SERVER_ROOT_URL=http://stats.home` so alert links work from the phone.

**Verified:**
- All Prometheus targets `UP` — host, LXC, containers, `pve`, `postgres`, all 7
  blackbox probes, loki, alloy.
- Grafana provisioning loaded cleanly (datasources, 6 dashboards, 5 alert rules).
- Loki shows live `{job="docker"}` logs via Alloy.
- Test alert delivered to the ntfy phone app, formatted by the Grafana template.

**Notes / next steps:**
- Grafana admin password was reset on the box via
  `grafana cli admin reset-admin-password` (env var doesn't change an already-
  initialised instance).
- ntfy web UI shows a harmless "notifications only over HTTPS" banner — browser
  API limitation only; phone app + Grafana delivery are unaffected.
- Optional later: custom ntfy template to drop markdown / add severity+host.

---

## 2026-06-07 — `.home` names stopped resolving (wedged Tailscale subnet session)

**Goal:** `chat.home` (and all `*.home`) stopped loading from the MacBook, while the
direct `http://10.0.0.201:3010` still worked. Determine whether DNS was broken and fix it.

**Diagnosis:**
1. Confirmed the DNS record itself was fine: `dig @10.0.0.201 chat.home` → `10.0.0.201`,
   Caddy `:80` open, Open WebUI up. But `dig @100.100.100.100 chat.home` (Tailscale
   resolver) timed out and `curl http://chat.home/` hung (HTTP 000).
2. Ruled out config: AdGuard rewrite `*.home → 10.0.0.201` correct, `allowed_clients: []`
   (allows all), Caddy route correct, m5 `ip_forward=1` (persisted), `ts-forward`/masquerade
   chains intact, m5 → `10.0.0.201` on LAN fine. Nothing mis-set.
3. Found the fault in the tailnet path: `tailscale status` showed m5 as
   `relay "nyc", tx … rx 0` — sending but receiving nothing. SSH to m5's *own* tailnet IP
   (`100.116.69.120`) worked, but traffic *forwarded through* m5 to the subnet-routed
   `10.0.0.201` (how every device resolves `.home`) died. Only 49 packets had ever hit the
   subnet masquerade.

**Root cause:**
- A stale, half-open WireGuard session to m5 stuck on the DERP relay and never re-formed a
  direct path (the UPnP-based direct path had lapsed). The node stayed reachable, but
  subnet-router forwarding to `10.0.0.201` was effectively dead — so split-DNS lookups for
  `*.home`, which forward to `10.0.0.201`, timed out. **Operational fault, not config or
  architecture.**

**Resolution:**
- Restarted `tailscaled` on m5 (detached `systemd-run` so it survived the Tailscale-SSH drop).
  The session immediately re-formed **direct** (`10.0.0.200:41641`, `rx > 0`); `tailscale ping
  10.0.0.201` went from DERP-40ms/timeout → direct 3ms; `chat.home` → **HTTP 200 in 17ms**.
- Hardened inside the existing single-gateway design (did **not** add Tailscale to the LXC —
  consistent with the `/dev/net/tun` constraint): added a static **UDP `41641` →
  `10.0.0.200`** port-forward on the Xfinity router + DHCP reservation for m5, so the direct
  path is deterministic instead of depending on UPnP lease renewal. Verified m5's tailscaled
  listens on `:41641` and it now advertises `73.143.128.196:41641` as a peer endpoint.
  (Closes the long-pending DHCP-reservation item for `10.0.0.200`.)

**Notes / next steps:**
- **Runbook:** if `*.home` goes flaky again, first check `tailscale status | grep m5` on any
  device — `relay` instead of `direct` = this same failure; fix is `systemctl restart
  tailscaled` on m5. The router port-forward should now prevent the relay-wedge recurring.
- The true off-LAN test (direct handshake inbound on `41641`) happens next time a device
  connects from outside the home network — it should go direct instead of relay.

---

## 2026-06-07 — Observability buildout: exporters, logs, dashboards, alerting

**Goal:** Prometheus + Grafana were running but observing nothing — no exporters
beyond node/cadvisor, no dashboards, no logs, no alerts. Stand up full coverage
(Proxmox host, LXC, containers, PostgreSQL, endpoint uptime) plus log aggregation
and push alerting, within the 16 GB RAM budget.

**Steps (all in `docker/monitoring/`, branch `feat/observability-stack`):**
1. Added exporters to the compose: `pve-exporter` (Proxmox API, read-only
   `PVEAuditor` token via `PVE_*` env), `postgres-exporter` (read-only `pg_monitor`
   role, joins `core_core`), `blackbox-exporter` (HTTP probes, joins
   `core`/`ai`/`proxy`).
2. Added logs: `loki` (filesystem store, 30-day retention) + `alloy` (ships Docker
   container logs via the read-only socket + host journal → Loki).
3. Added `ntfy` for push alerts (`NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` so iOS
   gets instant APNs delivery; only a wake-up poke leaves the box).
4. Provisioned Grafana as code: Prometheus + Loki datasources, a dashboard file
   provider, and unified alerting (ntfy webhook contact point + default policy +
   5 alert rules: target down, disk >85%, mem <10%, probe down, postgres down).
5. Extended `prometheus.yml` with jobs: `node-proxmox`, `pve`, `postgres`,
   `blackbox`, `loki`, `alloy` (+ `--web.enable-lifecycle` for hot reload).
6. `scripts/fetch-dashboards.sh` downloads community dashboards (1860, 19792,
   10347, 9628, 7587, 13639) and pins datasource inputs to the fixed UIDs.

**Notes / next steps:**
- **Manual host steps before deploy:** `apt install prometheus-node-exporter` on
  the Proxmox host; set `PROXMOX_HOST_IP` (×2) in `prometheus.yml`; create the PVE
  token and Postgres `monitoring` role; fill `.env`; run `fetch-dashboards.sh`.
- Decision: kept secrets in `.env` (`${VAR:?required}`) per repo convention rather
  than secret files — the PG role is read-only and the DB is internal-only.
- Validated locally: `docker compose config` and YAML parse all pass. `promtool`
  and live target/alert verification must run on the host (Docker daemon not on
  the Mac). See `docker/monitoring/README.md` for the verify checklist.
- Follow-up: front ntfy with Caddy as `alerts.home`; consider a relay for prettier
  alert message formatting (currently raw Grafana JSON).

---

## 2026-06-07 — Doc sync: networking reality + Ollama model loader

**Goal:** Bring `AGENTS.md` back in line with the deployed setup and reduce the per-model tuning toll.

**Steps:**
1. Fixed the stale `AGENTS.md` networking docs: it still claimed "all ports bind to `127.0.0.1`," untrue since the rebind of Portainer/Grafana/Prometheus/Ollama to all interfaces. Rewrote the networking rules (subnet route, `*.home` split-DNS, the loopback-vs-all-interfaces split) and added the missing **proxy stack** (Caddy + AdGuard) to the stack overview.
2. Added `docker/ai/load-models.sh` — runs `ollama create` for every `docker/ai/models/*.Modelfile`, rebuilding each tag in place. Adding a tuned model is now "drop a Modelfile, run the loader."

**Notes / next steps:**
- New-service guidance in `AGENTS.md` now says: default to `127.0.0.1`, open to all interfaces + a Caddy `*.home` route only when LAN/tailnet access is needed.

---

## 2026-06-07 — Ollama still slow after the num_thread commit: it was never applied + cold-load tax

**Goal:** The `num_thread 4` Modelfile was committed but Open WebUI was still slow. Find out why.

**Diagnosis:**
1. `ollama show llama3.2:3b --modelfile | grep num_thread` returned **empty** — the *committed* Modelfile had never been applied to the *running* model. Committing the file does nothing; the tag must be rebuilt with `ollama create` on the box.
2. `load-models.sh` couldn't apply it either: Ollama runs as a **Docker container** (`ollama`), not on the LXC PATH, so `pct exec 100 -- ollama …` fails with `Failed to exec "ollama"`. Must go through `docker exec ollama …`.
3. `ollama create -f -` (Modelfile via stdin) is **not supported** on this version — needs a real file path. Used `docker cp` to land the Modelfile in the container, then `ollama create -f /tmp/…`.
4. After rebuild, `ollama show … | grep num_thread` → `PARAMETER num_thread 4`. A timed `ollama run … --verbose`: **eval rate 16.25 tok/s** (generation fixed). But `load duration: 42s` dominated total time — the model reload into RAM.

**Root cause of the *lingering* slowness:**
- Two separate things: (a) the tuned params were never live, and (b) Ollama's default `keep_alive` is 5 min, so after any idle gap the next chat pays a ~40s cold load before the first token — felt as "still slow" even though generation now runs at ~16 tok/s.

**Resolution:**
- Rebuilt the live model in the container (`docker cp` Modelfile → `docker exec ollama ollama create llama3.2:3b -f …`).
- Set `OLLAMA_KEEP_ALIVE: "-1"` on the ollama container in `docker/ai/docker-compose.yml` so the model stays resident (3B ≈ 2-3 GB, fits the 14 GB LXC).
- Rewrote `load-models.sh` to operate on the container (`docker cp` + `docker exec ollama ollama create`), since the CLI isn't on the LXC PATH.
- Open WebUI's per-model `num_thread` left on **Default** (not 0): Default = not sent, so the Modelfile's value wins. `0` would mean "auto-detect" and re-trigger the 16-thread oversubscription.

**Notes / next steps:**
- Standing gotcha: editing a Modelfile is inert until `./docker/ai/load-models.sh` rebuilds the tag *inside the container*. Commit ≠ deploy.
- Apply the keep_alive change: `cd <repo-on-lxc>/docker/ai && docker compose up -d ollama`.

---

## 2026-06-07 — Fixed pathologically slow Ollama (LXC thread oversubscription)

**Goal:** Open WebUI chat responses were extremely slow; find out why and fix it.

**Diagnosis:**
1. Queried the Ollama API directly (`/api/ps`, `/api/generate`). Model `llama3.2:3b` (Q4_K_M) runs **CPU-only** (`size_vram: 0` — expected, no GPU).
2. Benchmarked generation: **~0.5 tok/s** by default — about 30× slower than this CPU should manage for a 3B Q4 model. An 80-token request even timed out.
3. Re-ran with an explicit thread count: `num_thread=4` → **16.1 tok/s**, `num_thread=6` → **17.0 tok/s**. Explicit threads = ~30× faster.

**Root cause:**
- Ollama auto-detects the **host's** logical CPU count (16), not the LXC's **6-core cgroup quota**. It spawns more inference threads than the quota allows; the kernel CFS-throttles them (scheduled → hit quota → stall), so throughput collapses. Capping threads ≤ the quota removes the throttling.

**Resolution:**
- Added `docker/ai/models/llama3.2.Modelfile` (`FROM llama3.2:3b` + `PARAMETER num_thread 4`) — version-controlled, reproducible.
- Apply on the box: `ollama create llama3.2:3b -f docker/ai/models/llama3.2.Modelfile` (rebuilds the same tag in place, so Open WebUI needs no change).
- Chose `num_thread 4` over 6: same throughput (~16 vs 17 tok/s) while leaving 2 cores for the other stacks.
- Documented the constraint as a standing convention in `AGENTS.md` (every new model must pin `num_thread`).

**Notes / next steps:**
- No global Ollama thread env var exists, so this is per-model — repeat the Modelfile pattern for every model added.
- `AGENTS.md` networking rules still say "all ports bind to 127.0.0.1"; that's stale since today's rebind of Portainer/Grafana/Prometheus/Ollama to all interfaces — worth a separate doc cleanup.

---

## 2026-06-07 — Memorable service names (proxy stack: Caddy + AdGuard)

**Goal:** Reach services at memorable, port-free names (`chat.home`, `stats.home`, `apps.home`, `dns.home`) that resolve on any tailnet device, anywhere.

**Steps:**
1. Added a `docker/proxy/` stack: **Caddy** (reverse proxy on `:80`, routes by Host header, `auto_https off` — plain HTTP since Tailscale encrypts) attached to the `core`/`monitoring`/`ai` networks, and **AdGuard Home** (DNS on `:53` + ad-blocking).
2. Configured AdGuard via its install API (admin on `:80` behind Caddy, DNS on `:53`) and added a `*.home → 10.0.0.201` rewrite.
3. Tailscale admin → DNS → **Split DNS**: custom nameserver `10.0.0.201` restricted to domain `home`, so every tailnet device resolves `*.home` via AdGuard (reached over the existing subnet route).

**Issues encountered:**
- **AdGuard setup port 3000 collided with Grafana** (now published on `:3000`). Moved the first-run wizard mapping to `3001`.
- Dropped the original plan to run **Tailscale inside the LXC** — `/dev/net/tun` isn't exposed to the container. Pointing split-DNS at the subnet-routed `10.0.0.201` instead is simpler and needs no Proxmox device config.

**Resolution:**
- Verified end to end: AdGuard resolves all `*.home → 10.0.0.201`, Caddy routes each name to the right service (HTTP 200), and the Mac resolves the names on its own via split-DNS.

**Notes / next steps:**
- AdGuard admin password stored in password manager; reachable at `dns.home`.
- Optional: set AdGuard (`10.0.0.201`) as the router's DHCP DNS so `.home` + ad-blocking apply to *all* home-LAN devices, not just tailnet ones.
- The `3001` setup-wizard port mapping can be removed from the compose now that AdGuard is configured.

---

## 2026-06-07 — Tailscale subnet routing + first stacks brought up

**Goal:** Make the LXC reachable from the MacBook over Tailscale, then clone the repo and bring up the `core`, `monitoring`, and `ai` stacks.

**Steps:**
1. Logged the MacBook into Tailscale (already installed; was logged out) with `--accept-routes`. Tailnet domain `tail58e272.ts.net`; host is `m5.tail58e272.ts.net` (`100.116.69.120`).
2. On the host: enabled IP forwarding persistently (`/etc/sysctl.d/99-tailscale.conf`) and ran `tailscale set --advertise-routes=10.0.0.0/24`. Approved the route + disabled key expiry for `m5` in the admin console. The LXC (`10.0.0.201`) is now reachable from any tailnet device.
3. Installed the MacBook's SSH key on the host (`ssh-copy-id root@10.0.0.200`) for passwordless management via `pct exec 100`.
4. Cloned `brignano/homelab` into the LXC, generated random secrets into `docker/*/.env` (`chmod 600`), and brought up `core` → `monitoring` → `ai`. All 8 containers running.

**Issues encountered:**
- **Open WebUI never started.** The Ollama healthcheck ran `curl`, which isn't in the `ollama/ollama` image (`exec: "curl": not found`), so Ollama never went healthy and Open WebUI (which waits on `service_healthy`) never came up.
- **Services unreachable from the Mac.** Every port was bound to `127.0.0.1` inside the LXC, so the new subnet route still couldn't reach them.

**Resolution:**
- Changed the healthcheck to `ollama list` (in-image binary). Ollama → healthy, Open WebUI started. ([#3](https://github.com/brignano/homelab/pull/3))
- Rebound Portainer, Grafana, Prometheus, and the Ollama API to all interfaces; kept Postgres on `127.0.0.1` (apps use the internal Docker network). Services are now reachable over LAN + tailnet, but not public.
- Pulled `llama3.2:3b` into Ollama so Open WebUI has a model to chat with.

**Notes / next steps:**
- `tunnel` (cloudflared) still not deployed — needs a Cloudflare Zero Trust tunnel token for public access.
- Still pending: DHCP reservation on the router (`84-47-09-86-96-A4` → `10.0.0.200`), Jellyfin media stack.

---

## 2026-06-07 — Bare-metal Proxmox install + Docker LXC provisioned

**Goal:** Stand up the GMKtec M5 Ultra as the Proxmox host and create the privileged Docker LXC per the VM→LXC decision, ending with a working Docker + Compose foundation.

**Steps:**
1. Installed **Proxmox VE 9.2** bare metal, wiping the preinstalled Windows 11. Node FQDN `m5.homelab.lan`, static IPv4 `10.0.0.200/24`, gateway `10.0.0.1`, DNS `1.1.1.1`.
2. Disabled the two enterprise APT repos, added `pve-no-subscription`, ran `apt dist-upgrade` (new kernel `7.0.6-2-pve` + AMD microcode), rebooted onto the new kernel.
3. Installed **Tailscale** on the host (`tailscale up --ssh`); host tailnet IP `100.116.69.120`.
4. Downloaded the `debian-13-standard` LXC template and created **CT 100** (`docker`) via `pct create`: privileged (`--unprivileged 0`), `--features nesting=1`, 14 GB RAM limit, 6 cores, 400 GB thin rootfs on `local-lvm`, static `10.0.0.201/24`, `--onboot 1`.
5. Inside the container: installed **Docker CE 29.5.3** + Compose v2 (`v5.1.4`) via `get.docker.com`. `docker run hello-world` succeeded → Docker-in-LXC via nesting confirmed working.
6. Generated `en_US.UTF-8` locale to clear the perl/locale warnings.

**Issues encountered:**
- **Container had no internet (DNS).** Tailscale rewrote the host's `/etc/resolv.conf` to MagicDNS (`100.100.100.100`); the LXC inherited it via "use host settings," but MagicDNS is unreachable from inside the container (Tailscale only runs on the host). Raw-IP routing worked; name resolution hung.
- **Thin pool overprovisioned.** `pve/data` thin pool is only **~348 GiB**, but the rootfs is provisioned at 400 GiB, and the VG has just 16 GiB free (pool can't auto-extend). Fine for containers/configs (currently <1% used), but the real ceiling is ~348 GiB.
- **Create CT wizard hid the privileged/nesting toggles** (require "Advanced" mode); used `pct create` on the CLI instead.

**Resolution:**
- DNS fixed with `pct set 100 --nameserver 1.1.1.1` (plus a live `echo` to `/etc/resolv.conf` to unblock the running container). Persistence verified — `nameserver: 1.1.1.1` is in the container config, so it survives restarts.
- Thin pool: left as-is (thin provisioning is the chosen tradeoff). Keep large media (Jellyfin) off this pool or monitor `lvs` pool usage so actual data stays under ~348 GiB.

**Notes / next steps:**
- Add a DHCP reservation on the Xfinity router (`10.0.0.1`) for the host NIC MAC `84-47-09-86-96-A4` → `10.0.0.200`.
- Decide how Tailscale reaches the LXC services: host subnet router (`--advertise-routes=10.0.0.0/24`) vs. Tailscale inside the LXC vs. a Tailscale sidecar container.
- Clone `brignano/homelab` into the container and bring up stacks in order: `core` → `monitoring` → `ai` → `tunnel`; populate `.env` from `.env.example`; supply the Cloudflare Tunnel token.
- Fill in the Tailscale hostname placeholder in `README.md`.

---

## 2026-06-07 — Switched planned Docker host from VM to LXC

**Goal:** Pick the right host type for Docker workloads on the 16 GB / 512 GB GMKtec M5 Ultra without starving Proxmox.

**Steps:**
1. Compared three host options for the Docker workload.
2. Selected a Proxmox LXC container and updated `AGENTS.md` (`## LXC Configuration`) accordingly.

**Options considered:**
- **Proxmox + VM:** Strong isolation, but the 12 GB RAM reservation is a hard carve-out — on a 16 GB host that left Proxmox only ~2 GB of headroom.
- **Proxmox + LXC (chosen):** RAM is a limit rather than a hard reservation and disk is thin-provisioned, so the host keeps real headroom while still running under Proxmox.
- **Bare-metal Debian:** Maximum performance, but loses Proxmox snapshots/management and the ability to run other VMs/containers on the box.

**Resolution:**
- Going with a **Proxmox LXC container**. The 12 GB VM reservation left Proxmox only ~2 GB on the 16 GB host; an LXC's 14 GB limit plus thin-provisioned disk leaves usable host headroom.

**Notes / next steps:**
- The LXC must be **privileged** with **`nesting=1`** enabled (required for Docker-in-LXC).
- Workload runs identically inside the LXC — no changes to compose files or the bootstrap script.

---

## 2026-06-06 — Pre-provisioning hardening and tooling

**Goal:** Make initial server setup smoother before the Proxmox VM is provisioned.

**Steps:**
1. Added healthcheck to `ollama` service in `docker/ai/docker-compose.yml` (polls `http://localhost:11434/` every 30s).
2. Updated `open-webui` `depends_on` to use `condition: service_healthy` so it waits for Ollama to be ready.
3. Added `## VM Configuration` section to `AGENTS.md` documenting planned Proxmox VM specs (12GB RAM, 6 cores, 400GB VirtIO disk, Debian).
4. Created `.claude/commands/preflight.md` — checks `.env` presence, required vars, external Docker networks, and Tailscale connectivity before any stack is brought up.
5. Created `.claude/commands/bootstrap-stack.md` — brings stacks up in dependency order (core → monitoring → ai → tunnel) with health polling between each step.
6. Added `## Tailscale Hostname` placeholder to `README.md` to fill in post-provisioning.

**Notes / next steps:**
- Provision Debian VM in Proxmox with the specs in `AGENTS.md`.
- Connect VM to Tailscale, then fill in hostname in `README.md`.
- Run `/preflight` before first `docker compose up` on the new VM.

---

## 2026-06-06 — Initial repo created

**Goal:** Scaffold the homelab repository and document the hardware.

**Steps:**
1. Created GitHub repo `brignano/homelab`.
2. Added Docker Compose stacks for monitoring, core services, and local AI.
3. Added `scripts/bootstrap-docker.sh` for fresh host setup.

**Notes / next steps:**
- Install Proxmox VE on the GMKtec M5 Ultra.
- Provision a Debian VM inside Proxmox for Docker workloads.
- Run `bootstrap-docker.sh` on that VM.
- Connect host to Tailscale before exposing any service ports.
