#!/usr/bin/env sh
#
# autoheal.sh against a fake `docker`: restarts an unhealthy container, leaves
# excluded stacks and gluetun alone, gives up after the cap instead of looping,
# and honours the kill switch. Run by CI and by hand: ./scripts/test-autoheal.sh
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
mkdir -p "$WORK/bin" "$WORK/state" "$WORK/metrics"

fail=0
check() { _n=$1; shift; if "$@"; then echo "ok    $_n"; else echo "FAIL  $_n"; fail=1; fi; }
has() { grep -q -- "$2" "$1" 2>/dev/null; }
lacks() { ! grep -q -- "$2" "$1" 2>/dev/null; }

# $W/unhealthy holds "<container> <stack>" lines; restarts land in $W/restarts.
cat > "$WORK/bin/docker" <<'FAKE'
#!/bin/sh
W=${FAKE_WORK:?}
case "$1" in
  ps) cut -d' ' -f1 "$W/unhealthy" ;;
  inspect) for a in "$@"; do n=$a; done; awk -v n="$n" '$1 == n {print $2}' "$W/unhealthy" ;;
  restart) echo "$2" >> "$W/restarts" ;;
esac
FAKE
chmod +x "$WORK/bin/docker"

run() {
  FAKE_WORK=$WORK PATH="$WORK/bin:$PATH" HL_AUTOHEAL_STATE_DIR="$WORK/state" \
    HL_TEXTFILE_DIR="$WORK/metrics" "$@" "$HERE/autoheal.sh" >> "$WORK/out" 2>&1
}
: > "$WORK/out"; : > "$WORK/restarts"
printf 'sonarr downloads\nproxy-caddy proxy\ngluetun downloads\n' > "$WORK/unhealthy"

run env
check "restarts an unhealthy container" has "$WORK/restarts" '^sonarr$'
check "leaves a HL_NO_AUTOHEAL stack alone" lacks "$WORK/restarts" 'proxy-caddy'
check "never restarts gluetun" lacks "$WORK/restarts" 'gluetun'
check "restart is a metric" has "$WORK/metrics/autoheal.prom" 'restarts_in_window{container="sonarr"} 1'

run env; run env
check "restarts up to the cap" test "$(grep -c '^sonarr$' "$WORK/restarts")" = 3
run env
check "gives up past the cap, no fourth restart" test "$(grep -c '^sonarr$' "$WORK/restarts")" = 3
check "giving up is a metric" has "$WORK/metrics/autoheal.prom" 'gave_up{container="sonarr"} 1'

: > "$WORK/restarts"; rm -f "$WORK/state"/*
run env HL_AUTOHEAL=no
check "HL_AUTOHEAL=no is report only" test ! -s "$WORK/restarts"

[ "$fail" = 0 ] || exit 1
