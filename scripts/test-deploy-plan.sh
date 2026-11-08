#!/usr/bin/env sh
#
# deploy-plan.sh --machine, against a throwaway git repo. Run by CI and by hand:
#
#   ./scripts/test-deploy-plan.sh
#
# auto-deploy.sh acts on these lines without a person reading them first, so the
# one thing worth pinning down is that each kind of change produces the action
# /deploy would have taken, and that the human plan still says the same thing.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

fail=0
expect() { # <name> <expected> <actual>
  if [ "$2" = "$3" ]; then
    echo "ok    $1"
  else
    echo "FAIL  $1"
    echo "      expected: $(printf '%s' "$2" | tr '\n' '|')"
    echo "      actual:   $(printf '%s' "$3" | tr '\n' '|')"
    fail=1
  fi
}

cd "$WORK"
git init -q
git config user.email test@example.invalid
git config user.name test
mkdir -p scripts scripts/host
cp "$HERE/deploy-plan.sh" scripts/
for s in monitoring dashboard assistant proxy media; do
  mkdir -p "docker/$s"
  printf 'services: {}\n' > "docker/$s/docker-compose.yml"
done
mkdir -p docker/monitoring/grafana/provisioning/alerting docker/dashboard/icons docker/assistant/app
printf 'apiVersion: 1\ngroups:\n  - uid: keep-me\n  - uid: drop-me\n' \
  > docker/monitoring/grafana/provisioning/alerting/rules.yml
git add -A && git commit -qm base
BASE=$(git rev-parse HEAD)

plan() { ./scripts/deploy-plan.sh --machine "$1"; }

# Docs only: nothing to do.
echo "# notes" > docker/media/README.md
git add -A && git commit -qm docs
expect "docs-only change plans nothing" "" "$(plan HEAD~1)"

# A compose change: up.
printf 'services: {a: {image: x}}\n' > docker/media/docker-compose.yml
git add -A && git commit -qm media
expect "compose change is up" "up media" "$(plan HEAD~1)"

# Source baked into an image: build.
echo "x = 1" > docker/assistant/app/main.py
git add -A && git commit -qm app
expect "image source change is build" "build assistant" "$(plan HEAD~1)"

# Grafana provisioning: up + restart grafana.
printf 'apiVersion: 1\ngroups:\n  - uid: keep-me\n' \
  > docker/monitoring/grafana/provisioning/alerting/rules.yml
git add -A && git commit -qm grafana
expect "provisioning restarts grafana, flags the dropped uid" "up monitoring
restart monitoring grafana
deletion docker/monitoring/grafana/provisioning/alerting/rules.yml drop-me" "$(plan HEAD~1)"

# A new dashboard icon: up + recreate.
echo "<svg/>" > docker/dashboard/icons/new.svg
git add -A && git commit -qm icon
expect "new dashboard icon recreates" "up dashboard
recreate dashboard" "$(plan HEAD~1)"

# proxy, scripts and host: never acted on directly.
echo "# x" > docker/proxy/Caddyfile
echo "echo hi" > scripts/thing.sh
echo "echo hi" > scripts/host/hook
git add -A && git commit -qm risky
expect "proxy, scripts and host" "cron
manual proxy
manual host" "$(plan HEAD~1)"

# The whole range at once: every action, each once.
expect "range from base" "build assistant
up dashboard
up media
up monitoring
restart monitoring grafana
recreate dashboard
cron
manual proxy
manual host
deletion docker/monitoring/grafana/provisioning/alerting/rules.yml drop-me" "$(plan "$BASE")"

# The human plan is the same rules: spot-check it still reads as before.
human=$(./scripts/deploy-plan.sh "$BASE")
case "$human" in
  *"docker compose -f docker/assistant/docker-compose.yml up -d --build"*"restart grafana"*"docker/proxy changed"*)
    echo "ok    human plan unchanged in shape" ;;
  *) echo "FAIL  human plan lost a line:"; printf '%s\n' "$human"; fail=1 ;;
esac

rc=0; ./scripts/deploy-plan.sh --machine nosuchrev >/dev/null 2>&1 || rc=$?
expect "bad revision fails" "1" "$rc"

exit $fail
