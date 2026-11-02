#!/bin/sh
# One command on the Mac that builds the review page's Photos albums in a single
# go, so Photos is started hidden once and put away at the end:
#
#   tools/photos/albums.sh                 # climb candidates + cold storage
#   tools/photos/albums.sh --dry-run       # print what would be added; change nothing
#
# Review page: HOMELAB_REVIEW_URL, default https://review.home.brignano.io.
# Duplicates have their own flow (tools/photos/duplicates-to-album.py): they need
# decisions made in Photos first.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
url="${HOMELAB_REVIEW_URL:-https://review.home.brignano.io}"

open -g -j -a Photos 2>/dev/null || true
for set in climb cold; do
  echo "== $set =="
  uv run "$here/to-album.py" --url "$url" --set "$set" "$@"
done
osascript -e 'tell application "System Events" to set visible of process "Photos" to false' 2>/dev/null || true
