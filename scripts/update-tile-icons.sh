#!/usr/bin/env sh
#
# Vendor the service-tile icons the dashboard names.
#
# Why this exists
# ---------------
# Homepage resolves a bare `icon: grafana.png` against a CDN, in the browser, on
# every load. That makes the tiles depend on the internet and on AdGuard not
# blocking the CDN — for the one page you open *because* something is wrong.
# A WAN outage would leave the dashboard rendering a grid of blank squares.
#
# So the icons are fetched once, committed, and named as local paths
# (`icon: /icons/grafana.svg`), which Homepage serves from /app/public/icons.
# They are third-party marks and stay exactly as their projects draw them:
# recognising Grafana's G at a glance is the entire job of a tile icon.
#
# Usage:
#   ./scripts/update-tile-icons.sh             # fetch anything referenced but missing
#   ./scripts/update-tile-icons.sh --refresh   # re-resolve upstream and re-fetch all
#
# check-dashboard.sh (and so CI) fails if a tile names an icon that is not here,
# which is what turns "I added a service" into "fetch its icon" rather than into
# a blank square nobody notices.
set -eu

SOURCE_REPO="homarr-labs/dashboard-icons"
REPO=$(cd "$(dirname "$0")/.." && pwd)
ICONS="$REPO/docker/dashboard/icons"
CONFIG="$REPO/docker/dashboard/config"
SOURCE_FILE="$ICONS/SOURCE"

# Drawn in this repo, not fetched from anywhere. If one of these goes missing
# it is a deleted file, not a missing download.
LOCAL="favicon icon-source brignano"

# Marks whose default drawing is inked for a light background and vanishes on
# this dashboard's dark card — Portainer's black P is invisible there today.
# Upstream ships a second drawing for dark backgrounds, so for these the two are
# composed into one file that switches on the viewer's colour scheme, rather
# than picking a theme to be wrong in. `<name>:<upstream variant>`.
COMPOSITES="portainer:portainer-dark open-webui:open-webui-light"

refresh=""
case "${1:-}" in
  "")         ;;
  --refresh)  refresh=yes ;;
  *)          echo "usage: $0 [--refresh]" >&2; exit 2 ;;
esac

command -v curl >/dev/null || { echo "update-tile-icons: curl is required" >&2; exit 1; }

# One ref for the whole set, so the icons are all from the same upstream commit
# rather than from whenever each one happened to be added.
if [ -n "$refresh" ] || [ ! -f "$SOURCE_FILE" ]; then
  ref=$(git ls-remote "https://github.com/$SOURCE_REPO" main | cut -f1)
  [ -n "$ref" ] || { echo "update-tile-icons: could not resolve $SOURCE_REPO main" >&2; exit 1; }
  printf '%s %s\n' "$SOURCE_REPO" "$ref" > "$SOURCE_FILE"
else
  ref=$(cut -d' ' -f2 "$SOURCE_FILE")
fi
echo "source   $SOURCE_REPO@$ref"

# Every local icon path the dashboard's config names — the tiles' `icon:` and
# the page's own `favicon:`, which is drawn here and so only ever checked.
refs=$(grep -hoE '(icon|favicon): /icons/[A-Za-z0-9._-]+' "$CONFIG"/*.yaml | sed 's|.*/icons/||' | sort -u)
[ -n "$refs" ] || { echo "update-tile-icons: no /icons/... references in $CONFIG" >&2; exit 1; }

missing=""
for file in $refs; do
  name=${file%.*}
  ext=${file##*.}
  dest="$ICONS/$file"

  drawn_here=""
  for l in $LOCAL; do
    [ "$l" = "$name" ] && drawn_here=yes
  done

  if [ -n "$drawn_here" ]; then
    if [ -f "$dest" ]; then
      echo "ok       $file (drawn in this repo)"
    else
      echo "MISSING  $file is drawn in this repo and is gone — restore it from git" >&2
      missing="$missing $file"
    fi
    continue
  fi

  if [ -f "$dest" ] && [ -z "$refresh" ]; then
    echo "ok       $file"
    continue
  fi

  variant=""
  for c in $COMPOSITES; do
    [ "${c%%:*}" = "$name" ] && variant=${c#*:}
  done

  base="https://raw.githubusercontent.com/$SOURCE_REPO/$ref/$ext"

  if [ -n "$variant" ]; then
    tmp=$(mktemp -d)
    if curl -fsSL "$base/$file" -o "$tmp/light.svg" && curl -fsSL "$base/$variant.$ext" -o "$tmp/dark.svg"; then
      LIGHT="$tmp/light.svg" DARK="$tmp/dark.svg" OUT="$dest" \
        NAME="$name" VARIANT="$variant" SOURCE="$SOURCE_REPO@$ref" python3 "$REPO/scripts/compose-icon.py"
      echo "composed $file (with $variant.$ext)"
    else
      echo "FAILED   $file — $base/$file or $base/$variant.$ext" >&2
      missing="$missing $file"
    fi
    rm -rf "$tmp"
    continue
  fi

  if curl -fsSL "$base/$file" -o "$dest.tmp"; then
    mv "$dest.tmp" "$dest"
    echo "fetched  $file"
  else
    rm -f "$dest.tmp"
    echo "FAILED   $file — $base/$file" >&2
    missing="$missing $file"
  fi
done

[ -z "$missing" ] || { echo >&2; echo "Could not vendor:$missing" >&2; exit 1; }
