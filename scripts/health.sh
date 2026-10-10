#!/usr/bin/env sh
#
# Is the lab healthy, in one command. Run on CT 100, from anywhere:
#
#   ./scripts/health.sh          # one line per check, then a summary
#
# Exit 0 when nothing FAILed (WARNs included), 1 otherwise, so it can gate a
# deploy or sit in a loop.
#
# Why this exists
# ---------------
# Every answer here already exists somewhere — Grafana's alert rules, the
# Prometheus target list, install-cron.sh --check, git. Asking them one at a
# time is what nobody does at 11pm, and each has its own failure mode that
# reads as healthy: a Grafana that is down fires no alerts, a cron entry that
# was never installed sends no messages, a checkout behind main looks current
# from inside. So this asks all of them, and treats "could not ask" as a
# finding rather than a pass.
#
# It reports; it never fixes. The hint under a FAIL names the script that does.
#
# Deliberately not duplicated here: probe-status.sh (why a probe is down) and
# deploy-plan.sh (what a pull needs). Those are the next command once this one
# has said where to look.
#
set -u

REPO=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$REPO/docker/monitoring/.env"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
# Same list, same reason as heartbeat.sh: Sablier scales kali to zero by
# design, and cloudflared has never been deployed.
OPTIONAL_CONTAINERS="${HL_OPTIONAL_CONTAINERS:-kali-linux cloudflared}"
DISK_WARN=75
DISK_FAIL=85

fails=0
warns=0
ok()   { printf '  ok    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; warns=$((warns + 1)); }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }
hint() { printf '        -> %s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

in_words() {
  for _w in $2; do [ "$_w" = "$1" ] && return 0; done
  return 1
}

# Instant query against Prometheus; prints "<label value> <sample value>" per
# series. Same shallow parse as probe-status.sh, for the same reason: the shape
# is fixed and flat, and jq is not on the box.
prom() {
  _resp=$(curl -fsS -m 10 -G "$PROM_URL/api/v1/query" --data-urlencode "query=$1" 2>/dev/null) || return 1
  printf '%s' "$_resp" \
    | sed 's/}[[:space:]]*,[[:space:]]*{[[:space:]]*"metric"/\n{"metric"/g' \
    | awk -v lbl="$2" '
        /"value"[[:space:]]*:/ {
          name = "-"
          if (match($0, "\"" lbl "\"[[:space:]]*:[[:space:]]*\"[^\"]*\"")) {
            name = substr($0, RSTART, RLENGTH)
            sub(/^[^:]*:[[:space:]]*"/, "", name); sub(/"$/, "", name)
          }
          val = $0
          sub(/.*"value"[[:space:]]*:[[:space:]]*\[[^,]*,[[:space:]]*"/, "", val)
          sub(/".*/, "", val)
          print name, val
        }'
}

env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -n1 | sed 's/^"\(.*\)"$/\1/; s/^'"'"'\(.*\)'"'"'$/\1/'
}

# ── 1. Containers ────────────────────────────────────────────────────────────
section "Containers"
if ! docker info >/dev/null 2>&1; then
  fail "docker is not reachable — nothing below about containers can be trusted"
else
  running=$(docker ps --format '{{.Names}}')
  missing=""
  declared=0
  for compose in "$REPO"/docker/*/docker-compose.yml; do
    [ -f "$compose" ] || continue
    # Parsed from the compose files rather than listed here, as heartbeat.sh
    # does, so a new service is checked the moment it is committed.
    for name in $(sed -n 's/^[[:space:]]*container_name:[[:space:]]*"\{0,1\}\([A-Za-z0-9_.-]\{1,\}\)"\{0,1\}[[:space:]]*$/\1/p' "$compose"); do
      declared=$((declared + 1))
      in_words "$name" "$OPTIONAL_CONTAINERS" && continue
      printf '%s\n' "$running" | grep -qx "$name" || missing="$missing $name"
    done
  done
  if [ "$declared" -eq 0 ]; then
    fail "no container_name found under $REPO/docker — is this the repo?"
  elif [ -n "$missing" ]; then
    fail "declared but not running:$missing"
    hint "docker compose -f docker/<stack>/docker-compose.yml up -d, or /debug-container"
  else
    ok "all $declared declared containers running (optional: $OPTIONAL_CONTAINERS)"
  fi

  bad=$(docker ps -a --format '{{.Names}} {{.Status}}' \
        | grep -E '\(unhealthy\)|^[^ ]+ Restarting' || true)
  if [ -n "$bad" ]; then
    printf '%s\n' "$bad" | while read -r line; do printf '  FAIL  %s\n' "$line"; done
    fails=$((fails + $(printf '%s\n' "$bad" | wc -l)))
    hint "docker logs --tail 50 <name>, or /debug-container"
  else
    ok "none unhealthy or restarting"
  fi
fi

# ── 2. Firing alerts ─────────────────────────────────────────────────────────
# The rules already encode every threshold this lab cares about (backup age,
# cron, drift, probes, disk); asking Grafana beats re-implementing them here.
section "Grafana alerts"
pw=$(env_value GRAFANA_ADMIN_PASSWORD)
user=$(env_value GRAFANA_ADMIN_USER); user=${user:-admin}
if [ -z "$pw" ]; then
  warn "no GRAFANA_ADMIN_PASSWORD in $ENV_FILE — cannot ask Grafana"
else
  # Credentials on stdin, not argv, so they do not show up in `ps`.
  resp=$(printf 'user = "%s:%s"\n' "$user" "$pw" \
    | curl -fsS -m 10 -K - \
        "$GRAFANA_URL/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false" \
        2>/dev/null) || resp=""
  if [ -z "$resp" ]; then
    fail "Grafana did not answer at $GRAFANA_URL — and a dead Grafana fires no alerts"
  else
    firing=$(printf '%s' "$resp" \
      | sed 's/}[[:space:]]*,[[:space:]]*{[[:space:]]*"annotations"/\n{"annotations"/g' \
      | awk '
          /"labels"/ {
            s = ""
            if (match($0, /"summary"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"/)) {
              s = substr($0, RSTART, RLENGTH); sub(/^"summary"[[:space:]]*:[[:space:]]*"/, "", s); sub(/"$/, "", s)
              gsub(/\\"/, "\"", s)
            } else if (match($0, /"alertname"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
              s = substr($0, RSTART, RLENGTH); sub(/^"alertname"[[:space:]]*:[[:space:]]*"/, "", s); sub(/"$/, "", s)
            }
            if (s != "") print s
          }')
    if [ -n "$firing" ]; then
      printf '%s\n' "$firing" | while read -r line; do printf '  FAIL  %s\n' "$line"; done
      fails=$((fails + $(printf '%s\n' "$firing" | wc -l)))
      hint "same messages as #alerts; ./scripts/probe-status.sh for a probe that looks wrong"
    else
      ok "nothing firing (silenced alerts excluded)"
    fi
  fi
fi

# ── 3. Scrape targets ────────────────────────────────────────────────────────
section "Prometheus targets"
down=$(prom 'up == 0' instance) || down="__unreachable__"
if [ "$down" = "__unreachable__" ]; then
  fail "Prometheus did not answer at $PROM_URL"
elif [ -n "$down" ]; then
  fail "not scraping: $(printf '%s\n' "$down" | awk '{print $1}' | paste -sd ' ' -)"
  hint "./scripts/probe-status.sh"
else
  ok "every target up"
fi

# ── 4. Disk ──────────────────────────────────────────────────────────────────
# Two numbers, because AGENTS.md "Disk": the guest's view is optimistic, and the
# thin pool under it is the ceiling that actually exists.
section "Disk"
pct=$(df -P / | awk 'NR == 2 { sub(/%/, "", $5); print $5 }')
if [ "$pct" -ge "$DISK_FAIL" ]; then fail "guest / is ${pct}% full"
elif [ "$pct" -ge "$DISK_WARN" ]; then warn "guest / is ${pct}% full"
else ok "guest / is ${pct}% full"; fi

pool=$(prom 'pve_disk_usage_bytes{id=~"storage/.+/local-lvm"} / pve_disk_size_bytes{id=~"storage/.+/local-lvm"} * 100' id 2>/dev/null \
       | awk 'NR == 1 { printf "%d", $2 }')
if [ -z "$pool" ]; then
  warn "thin pool usage unknown — metric not found; see the pve-exporter grep in AGENTS.md"
elif [ "$pool" -ge "$DISK_FAIL" ]; then fail "thin pool local-lvm is ${pool}% full — the guest will not see this coming"
elif [ "$pool" -ge "$DISK_WARN" ]; then warn "thin pool local-lvm is ${pool}% full"
else ok "thin pool local-lvm is ${pool}% full"; fi

# ── 5. Repo ──────────────────────────────────────────────────────────────────
# A checkout behind main is the failure that looks most like health: GitHub
# says the fix merged, and the box is not running it.
section "Repo"
if timeout 20 git -C "$REPO" fetch --quiet origin main 2>/dev/null; then
  behind=$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
  # Being behind is normal for up to a day: repo-sync.sh pulls at 04:00. It is
  # only a finding once the oldest missing commit has lived through a sync.
  oldest=$(git -C "$REPO" log --format=%ct HEAD..origin/main 2>/dev/null | tail -n1)
  age_h=$(( ($(date +%s) - ${oldest:-0}) / 3600 ))
  if [ "$behind" = "0" ]; then ok "checkout is current with origin/main"
  elif [ -n "$oldest" ] && [ "$age_h" -lt 26 ]; then
    ok "checkout is $behind commit(s) behind origin/main, oldest ${age_h}h old — the 04:00 repo-sync will pull it"
  elif [ -z "$oldest" ]; then
    warn "checkout is $behind commit(s) behind origin/main"
    hint "tail /var/log/repo-sync.log, then /deploy"
  else
    warn "checkout is $behind commit(s) behind origin/main, oldest ${age_h}h old — a nightly sync has missed it"
    hint "tail /var/log/repo-sync.log, then /deploy"
  fi
else
  warn "could not fetch origin/main — cannot say whether the checkout is current"
fi
dirty=$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null | wc -l)
if [ "$dirty" -gt 0 ]; then
  warn "$dirty tracked file(s) modified locally — the next pull may refuse"
  hint "git -C $REPO status"
fi

# ── 6. Scheduled jobs ────────────────────────────────────────────────────────
section "Cron"
if "$REPO/scripts/install-cron.sh" --check >/dev/null 2>&1; then
  ok "every scheduled job is installed"
else
  fail "scheduled jobs missing or duplicated"
  hint "./scripts/install-cron.sh --check, then without --check to install"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
printf '\n'
if [ "$fails" -gt 0 ]; then
  printf 'UNHEALTHY: %d fail, %d warn\n' "$fails" "$warns"
  exit 1
fi
if [ "$warns" -gt 0 ]; then printf 'healthy, %d warn\n' "$warns"
else printf 'healthy\n'; fi
exit 0
