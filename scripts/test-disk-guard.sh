#!/usr/bin/env bash
# Tests scripts/host/hl-disk-guard with stubbed lvs/pct/logger. No root needed.
set -u
here=$(cd "$(dirname "$0")" && pwd)
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
mkdir "$d/bin" "$d/prom" "$d/state"
cat > "$d/bin/lvs" <<S
#!/bin/sh
cat "$d/pool"
S
cat > "$d/bin/pct" <<S
#!/bin/sh
echo "pct \$*" >> "$d/calls"
if [ "\$1" = exec ]; then
  case "\$*" in *df*) printf 'Use%%\n %s%%\n' "\$(cat $d/guest)" ;; esac
  [ "\$1" = exec ] && case "\$*" in *"image prune"*) echo "image prune" >> "$d/calls";; esac
fi
S
echo '#!/bin/sh' > "$d/bin/logger"; chmod +x "$d/bin/"*
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: wanted '$3' got '$2'"; fails=1; fi; }
run() { PATH="$d/bin:$PATH" HL_TEXTFILE_DIR="$d/prom" HL_DISKGUARD_STATE="$d/state" "$here/host/hl-disk-guard" 2>/dev/null; }

echo 40 > "$d/pool"; echo 30 > "$d/guest"; : > "$d/calls"; run
check "under the line does nothing" "$(grep -c fstrim "$d/calls")" 0

echo 75 > "$d/pool"; : > "$d/calls"; run
check "pool over the line trims" "$(grep -c fstrim "$d/calls")" 1
check "pool over the line prunes images" "$(grep -c 'image prune -f' "$d/calls")" 1
check "never prune -a" "$(grep -c -- 'prune -a' "$d/calls")" 0
check "metric written" "$(grep -c homelab_disk_guard_cleanup_timestamp "$d/prom/hl_disk_guard.prom")" 1

: > "$d/calls"; run
check "cooldown stops a second run" "$(grep -c fstrim "$d/calls")" 0

rm -f "$d/state/last-run"; echo 10 > "$d/pool"; echo 80 > "$d/guest"; : > "$d/calls"; run
check "guest over the line trims" "$(grep -c fstrim "$d/calls")" 1
exit $fails
