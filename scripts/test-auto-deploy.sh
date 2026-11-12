#!/usr/bin/env sh
#
# auto-deploy.sh end to end, against a throwaway origin, clone and a fake
# `docker` on PATH. Run by CI and by hand:
#
#   ./scripts/test-auto-deploy.sh
#
# What it pins down is the policy, since that is what runs with nobody watching:
# quiet when nothing merged, deploys what the plan says, never touches proxy or
# a stopped stack, stops on a dirty tree and says so once, honours the pause.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

fail=0
check() { # <name> <condition...>
  _n=$1; shift
  if "$@"; then echo "ok    $_n"; else echo "FAIL  $_n"; fail=1; fi
}
has() { grep -q -- "$2" "$1" 2>/dev/null; }
lacks() { ! grep -q -- "$2" "$1" 2>/dev/null; }

# --- a fake docker ----------------------------------------------------------------
# Running stacks are named in $WORK/running, unhealthy ones in $WORK/unhealthy;
# every compose action is appended to $WORK/compose.log.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/docker" <<'EOF'
#!/bin/sh
W=${FAKE_WORK:?}
if [ "$1" = compose ]; then
  case " $* " in
    *" config --services "*) echo svc; exit 0 ;;
  esac
  echo "$*" >> "$W/compose.log"
  exit 0
fi
if [ "$1" = ps ]; then
  wd=""; status=""; health=""
  for a in "$@"; do
    case "$a" in
      label=com.docker.compose.project.working_dir=*) wd=${a#*working_dir=} ;;
      status=*) status=${a#status=} ;;
      health=*) health=${a#health=} ;;
    esac
  done
  [ -n "$wd" ] || exit 0
  stack=$(basename "$wd")
  if [ -n "$health" ]; then
    grep -qx "$stack" "$W/unhealthy" 2>/dev/null && echo "$stack-1"
    exit 0
  fi
  [ "$status" = running ] || exit 0
  grep -qx "$stack" "$W/running" 2>/dev/null && echo "id-$stack"
  exit 0
fi
exit 0
EOF
chmod +x "$WORK/bin/docker"
# flock is util-linux; present on CT 100 and on the CI runner.
PATH="$WORK/bin:$PATH"; export PATH
FAKE_WORK=$WORK; export FAKE_WORK

# --- origin and the box's clone ---------------------------------------------------
git init -q --bare "$WORK/origin.git"
git -C "$WORK/origin.git" symbolic-ref HEAD refs/heads/main
git clone -q "$WORK/origin.git" "$WORK/dev" 2>/dev/null
cd "$WORK/dev"
git config user.email test@example.invalid; git config user.name test
git checkout -q -b main
mkdir -p scripts
for f in auto-deploy.sh deploy-plan.sh deploy-common.sh metrics.sh healthchecks.sh; do
  cp "$HERE/$f" scripts/
done
# install-cron.sh is real elsewhere; here it only has to succeed.
printf '#!/bin/sh\necho "nothing to do"\n' > scripts/install-cron.sh
chmod +x scripts/*.sh
for s in media proxy idle monitoring; do
  mkdir -p "docker/$s"; printf 'services: {}\n' > "docker/$s/docker-compose.yml"
done
printf 'A=\n' > docker/media/.env.example
git add -A && git commit -qm base && git push -q origin main 2>/dev/null

git clone -q "$WORK/origin.git" "$WORK/box" 2>/dev/null
printf 'A=1\n' > "$WORK/box/docker/media/.env"
mkdir -p "$WORK/box/docker/monitoring"
printf 'media\nproxy\nmonitoring\n' > "$WORK/running"

HL_REPO="$WORK/box"; export HL_REPO
HL_AUTO_DEPLOY_STATE="$WORK/state"; export HL_AUTO_DEPLOY_STATE
HL_AUTO_DEPLOY_PAUSE="$WORK/pause"; export HL_AUTO_DEPLOY_PAUSE
HL_DEPLOY_LOCK="$WORK/deploy.lock"; export HL_DEPLOY_LOCK
HL_TEXTFILE_DIR="$WORK/textfile"; export HL_TEXTFILE_DIR
mkdir -p "$HL_TEXTFILE_DIR"
HL_VERIFY_DELAY=0; export HL_VERIFY_DELAY
HL_VERIFY_TRIES=1; export HL_VERIFY_TRIES

run() { : > "$WORK/out"; "$WORK/box/scripts/auto-deploy.sh" > "$WORK/out" 2>&1 || echo "exit $?" >> "$WORK/out"; }
merge() { # <file> <content> <message>
  cd "$WORK/dev"; mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"
  git add -A && git commit -qm "$3" && git push -q origin main 2>/dev/null
  cd - >/dev/null
}
at_origin() { [ "$(git -C "$WORK/box" rev-parse HEAD)" = "$(git -C "$WORK/origin.git" rev-parse main)" ]; }

# 1. Nothing merged: quiet, no compose calls.
run
check "nothing merged is quiet" lacks "$WORK/out" "deployed"
check "nothing merged runs no compose" [ ! -s "$WORK/compose.log" ]
check "metrics written" has "$HL_TEXTFILE_DIR/auto_deploy.prom" "homelab_auto_deploy_behind 0"

# 2. A compose change to a running stack: pulled, up -d, verified, recorded.
merge docker/media/docker-compose.yml 'services: {a: {image: x}}' "media change"
run
check "pulled to origin" at_origin
check "media brought up" has "$WORK/compose.log" "docker/media/docker-compose.yml up -d"
check "last-good recorded" [ -f "$WORK/state/last-good" ]
check "no failure recorded" [ ! -f "$WORK/state/last-failed" ]

# 3. proxy and a stopped stack: reported, never run.
: > "$WORK/compose.log"
merge docker/proxy/Caddyfile '# x' "proxy change"
merge docker/idle/docker-compose.yml 'services: {b: {image: y}}' "idle change"
run
check "proxy not touched" lacks "$WORK/compose.log" "docker/proxy"
check "stopped stack not started" lacks "$WORK/compose.log" "docker/idle"
check "still pulled" at_origin

# 4. A new .env key: skipped and named.
: > "$WORK/compose.log"
merge docker/media/.env.example 'A=
B=' "new key"
merge docker/media/docker-compose.yml 'services: {a: {image: z}}' "uses B"
run
check "stack with a missing key not deployed" lacks "$WORK/compose.log" "docker/media"

# 5. Unhealthy after deploy: failed, recorded, exit non-zero.
printf 'B=2\n' >> "$WORK/box/docker/media/.env"
printf 'media\n' > "$WORK/unhealthy"
merge docker/media/docker-compose.yml 'services: {a: {image: w}}' "breaks health"
run
check "unhealthy fails verify" [ -f "$WORK/state/last-failed" ]
check "failed run exits non-zero" has "$WORK/out" "exit 1"
: > "$WORK/unhealthy"

# 6. The next merge retries what failed (plan from last-good, not last HEAD).
: > "$WORK/compose.log"
merge docker/monitoring/grafana/provisioning/x.yml 'a: 1' "grafana tweak"
run
check "failed stack retried with next merge" has "$WORK/compose.log" "docker/media/docker-compose.yml up -d"
check "grafana restarted" has "$WORK/compose.log" "restart grafana"
check "failure cleared" [ ! -f "$WORK/state/last-failed" ]

# 7. Dirty tree: refused once, not pulled, quiet next tick.
echo "# local edit" >> "$WORK/box/docker/media/docker-compose.yml"
merge docker/media/docker-compose.yml 'services: {a: {image: v}}' "while dirty"
run
check "dirty tree not pulled" sh -c "! [ \"\$(git -C '$WORK/box' rev-parse HEAD)\" = \"\$(git -C '$WORK/origin.git' rev-parse main)\" ]"
check "dirty tree recorded" [ -f "$WORK/state/last-failed" ]
check "dirty tree logged" has "$WORK/out" "uncommitted changes"
run
check "second tick is quiet" lacks "$WORK/out" "not deploying"
git -C "$WORK/box" checkout -q -- docker/media/docker-compose.yml

# 8. Paused: nothing happens.
touch "$WORK/pause"
merge docker/media/docker-compose.yml 'services: {a: {image: u}}' "while paused"
: > "$WORK/compose.log"
run
check "paused does nothing" [ ! -s "$WORK/compose.log" ]
check "paused metric" has "$HL_TEXTFILE_DIR/auto_deploy.prom" "homelab_auto_deploy_paused 1"
rm -f "$WORK/pause"

# 9. A manual /deploy in the last 30 minutes holds it off.
date +%s > "$WORK/state/manual-deploy"
run
check "manual deploy marker holds it off" [ ! -s "$WORK/compose.log" ]
echo 0 > "$WORK/state/manual-deploy"
run
check "expired marker lets it run" has "$WORK/compose.log" "docker/media"

# 10. The lock: held elsewhere, the tick skips.
: > "$WORK/compose.log"
merge docker/media/docker-compose.yml 'services: {a: {image: t}}' "while locked"
(
  exec 9>"$HL_DEPLOY_LOCK"; flock 9
  "$WORK/box/scripts/auto-deploy.sh" > "$WORK/out" 2>&1 || true
)
check "busy lock skips" [ ! -s "$WORK/compose.log" ]


# 11. Phase 2 died after the merge (reboot, hung compose): the next tick finishes it.
: > "$WORK/compose.log"
merge docker/media/docker-compose.yml 'services: {a: {image: s}}' "interrupted"
git -C "$WORK/box" fetch -q origin main && git -C "$WORK/box" merge -q --ff-only origin/main
run
check "unfinished deploy resumed" has "$WORK/compose.log" "docker/media/docker-compose.yml up -d"
check "resume logged" has "$WORK/out" "resuming an unfinished deploy"
: > "$WORK/compose.log"
run
check "resumed once, then quiet" [ ! -s "$WORK/compose.log" ]

# 12. A key that was already in .env.example before this range, and is blank
#     or absent in .env on purpose, does not block anything.
sed -i '/^A=/d' "$WORK/box/docker/media/.env"
: > "$WORK/compose.log"
merge docker/media/docker-compose.yml 'services: {a: {image: r}}' "later change"
run
check "old optional key does not block later deploys" has "$WORK/compose.log" "docker/media/docker-compose.yml up -d"

# 13. A key that blocked a stack stays owed: a later unrelated merge must not
#     deploy the stack while .env still lacks it.
merge docker/media/.env.example 'A=
B=
NEEDED=' "needs a key"
run
: > "$WORK/compose.log"
merge docker/media/docker-compose.yml 'services: {a: {image: q}}' "unrelated change"
run
check "owed key still blocks the next merge" lacks "$WORK/compose.log" "docker/media"
printf 'NEEDED=1\n' >> "$WORK/box/docker/media/.env"
merge docker/media/docker-compose.yml 'services: {a: {image: p}}' "after key added"
run
check "deploys once the key is in .env" has "$WORK/compose.log" "docker/media/docker-compose.yml up -d"

# 14. Phase 2 that dies every time: resumed once, then refused once, then quiet.
mv "$WORK/bin/docker" "$WORK/bin/docker.real"
printf '#!/bin/sh\ncase " $* " in *" up "*|*" restart "*) pkill -9 -f box/scripts/auto-deploy.sh; exit 137 ;; esac\nexec "%s/bin/docker.real" "$@"\n' "$WORK" > "$WORK/bin/docker"
chmod +x "$WORK/bin/docker"
merge docker/media/docker-compose.yml 'services: {a: {image: o}}' "crashes phase 2"
run   # pulls, dies in Phase 2
run   # resumes, dies again
run   # refuses
check "second crash is refused" has "$WORK/out" "did not finish either time"
check "refusal recorded" [ -f "$WORK/state/last-failed" ]
run
check "then quiet" lacks "$WORK/out" "resuming"
mv "$WORK/bin/docker.real" "$WORK/bin/docker"


# 15. Discord routing by colour (discord_webhook, deploy-common.sh): green to
# #deploys when DISCORD_REPORT_WEBHOOK is set, everything else to #alerts.
route() { # <env file contents> <colour>
  printf '%s' "$1" > "$WORK/route.env"
  ENV_FILE="$WORK/route.env" sh -c '. "$1/deploy-common.sh"; discord_webhook "$2"' _ "$HERE" "$2"
}
BOTH='DISCORD_ALERT_WEBHOOK="https://alert"
DISCORD_REPORT_WEBHOOK=https://report
'
ALERT='DISCORD_ALERT_WEBHOOK=https://alert
DISCORD_REPORT_WEBHOOK=
'
check "green goes to the report webhook" [ "$(route "$BOTH" 7323541)" = https://report ]
check "green falls back to #alerts when report is blank" [ "$(route "$ALERT" 7323541)" = https://alert ]
check "red stays on #alerts" [ "$(route "$BOTH" 14711391)" = https://alert ]
check "amber stays on #alerts" [ "$(route "$BOTH" 14722127)" = https://alert ]
check "nothing set is empty" [ -z "$(route '' 7323541)" ]

exit $fail
