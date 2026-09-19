#!/usr/bin/env sh
#
# Every dashboard renders, and every metric a dashboard or alert asks for is one
# something actually writes.
#
# Why this exists
# ---------------
# Two failures, both of which had already happened here before this was written.
#
# The first is a dashboard that stops rendering without anyone noticing. Three
# of the seven dashboards in this repo were published at schemaVersion 16-26,
# when `graph` and `singlestat` were Angular plugins. Grafana 11 disabled Angular
# by default and Grafana 12 removed it — and because the compose file pins
# `grafana/grafana:latest`, that arrived on an ordinary restart. 10 of 11 panels
# on the blackbox dashboard, and 32 of 35 on the Postgres one, had been dead for
# an unknown length of time. Nothing failed. Nothing said anything. The
# dashboards were simply blank, and a blank dashboard reads like a quiet lab.
#
# The second is the shape this repo keeps hitting: two hand-maintained lists of
# the same thing in different files (see check-dashboard.sh and check-probes.sh
# for the other two instances). The `homelab_*` metrics are the newest case —
# written by scripts/*.sh, consumed by dashboards and alert rules, with nothing
# connecting the two. Rename a metric in one place and the panel silently goes
# empty, which looks exactly like "nothing is wrong".
#
# So: read the committed dashboards and alert rules, and prove each of the
# following, in CI, offline.
#
#   1. Every dashboard file is valid JSON with a uid and a title, and no two
#      share a uid (Grafana provisioning resolves a collision by overwriting).
#   2. No Angular panel types anywhere.
#   3. Every datasource reference is a pinned uid, not an unresolved ${DS_*}
#      template input — fetch-dashboards.sh rewrites those, and a miss produces
#      a dashboard that prompts for an import on every load.
#   4. Every homelab_* metric a dashboard or an alert rule uses is emitted by a
#      script in scripts/.
#   5. heartbeat.sh's cron job list matches install-cron.sh's.
#
# Run by hand from the repo root:
#   ./scripts/check-observability.sh
#
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
export REPO

python3 - <<'PY'
import json, os, pathlib, re, sys

repo = pathlib.Path(os.environ["REPO"])
dash_dir = repo / "docker/monitoring/grafana/dashboards"
rules = repo / "docker/monitoring/grafana/provisioning/alerting/rules.yml"
scripts = repo / "scripts"

fail = []
def bad(msg):
    fail.append(msg)
    print("FAIL  " + msg)

# Panel types that Grafana 11 disabled and Grafana 12 removed. A dashboard
# containing one of these does not warn — it renders an empty box.
ANGULAR = {
    "graph", "singlestat", "table-old", "grafana-piechart-panel",
    "grafana-singlestat-panel", "grafana-worldmap-panel", "natel-discrete-panel",
    "briangann-gauge-panel", "heatmap-old",
}

def walk_panels(panels):
    for p in panels or []:
        yield p
        yield from walk_panels(p.get("panels"))

files = sorted(dash_dir.rglob("*.json"))
if not files:
    bad("no dashboards found under %s" % dash_dir)

uids = {}
dash_text = ""
for f in files:
    rel = f.relative_to(repo)
    try:
        d = json.loads(f.read_text())
    except Exception as e:
        bad("%s is not valid JSON: %s" % (rel, e))
        continue
    dash_text += f.read_text()

    uid, title = d.get("uid"), d.get("title")
    if not uid:
        bad("%s has no uid — provisioning would assign a new one on every restart" % rel)
    if not title:
        bad("%s has no title" % rel)
    if uid in uids:
        bad("%s reuses the uid %r, already used by %s" % (rel, uid, uids[uid]))
    else:
        uids[uid] = rel

    angular = sorted({p["type"] for p in walk_panels(d.get("panels"))
                      if p.get("type") in ANGULAR})
    if angular:
        bad("%s uses panel types Grafana no longer ships: %s"
            % (rel, ", ".join(angular)))

    unresolved = sorted(set(re.findall(r"\$\{DS_[A-Z0-9_]+\}", json.dumps(d))))
    if unresolved:
        bad("%s has unresolved datasource inputs: %s" % (rel, ", ".join(unresolved)))

    # A panel with nothing to query renders as an empty box, which is the same
    # thing a healthy lab looks like. Checked only for the dashboards written
    # here: the upstream ones are somebody else's editorial decisions.
    if rel.parts[-2] == "homelab":
        for p in walk_panels(d.get("panels")):
            if p.get("type") in ("row", "text", "alertlist"):
                continue
            targets = p.get("targets") or []
            if not targets:
                bad("%s: panel %r has no query" % (rel, p.get("title")))
            for t in targets:
                if not t.get("expr"):
                    bad("%s: panel %r has an empty query" % (rel, p.get("title")))

    n = sum(1 for p in walk_panels(d.get("panels")) if p.get("type") != "row")
    print("ok    %s (%s, %d panels)" % (rel, uid, n))

# --- metrics both ends -------------------------------------------------------
rules_text = rules.read_text() if rules.exists() else ""
if not rules_text:
    bad("%s not found" % rules.relative_to(repo))

used = set(re.findall(r"\bhomelab_[a-z0-9_]+\b", dash_text + rules_text))

emitted = set()
for s in sorted(scripts.glob("*.sh")):
    for m in re.findall(r"\bhomelab_[a-z0-9_]+\b", s.read_text()):
        emitted.add(m)

missing = sorted(used - emitted)
if missing:
    for m in missing:
        bad("%s is used by a dashboard or alert but no script in scripts/ writes it" % m)
else:
    print("ok    all %d homelab_* metrics used are emitted by scripts/" % len(used))

unused = sorted(emitted - used)
if unused:
    # Not a failure. A metric can be worth collecting before anything reads it,
    # and the cost of an unread series here is nil. Worth saying out loud so it
    # is a decision rather than an accident.
    print("note  emitted but unused: %s" % ", ".join(unused))

# --- the two cron job lists --------------------------------------------------
def first_match(path, pattern):
    text = (scripts / path).read_text()
    m = re.search(pattern, text, re.M)
    return set(m.group(1).split()) if m else set()

hb_jobs = first_match("heartbeat.sh", r"HL_CRON_JOBS:-([^}]*)\}")
ic_jobs = first_match("install-cron.sh", r"^\s*for script in (.+?);")

if not hb_jobs:
    bad("could not read the cron job list out of heartbeat.sh")
elif not ic_jobs:
    bad("could not read the cron job list out of install-cron.sh")
elif hb_jobs != ic_jobs:
    bad("heartbeat.sh and install-cron.sh disagree about the scheduled jobs: "
        "heartbeat has %s, install-cron has %s"
        % (sorted(hb_jobs), sorted(ic_jobs)))
else:
    print("ok    heartbeat.sh and install-cron.sh agree on %d scheduled jobs"
          % len(hb_jobs))

if fail:
    print()
    print("%d problem(s)." % len(fail), file=sys.stderr)
    sys.exit(1)
print()
print("dashboards render, metrics line up, job lists agree")
PY
