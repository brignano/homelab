#!/bin/sh
# Runs tools/photos/triage.py on CT 100 in a throwaway container. The mirror is
# read-only; results land in /root/climb-judge/video-triage.csv (mounted as /out).
#
#   tools/photos/triage.sh --limit 50
#   tools/photos/triage.sh --month 2026/09
#
# Needs ANTHROPIC_API_KEY in the environment; it is passed through by name and
# never written anywhere.
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT="${CLIMB_JUDGE_OUT:-/root/climb-judge}"
LIBRARY="${CLIMB_JUDGE_LIBRARY:-/media/photos/icloud}"
mkdir -p "$OUT" "$OUT/cache"

exec docker run --rm \
  --cpus 2 --memory 2g \
  -e ANTHROPIC_API_KEY \
  -e UV_CACHE_DIR=/out/cache \
  -v "$REPO/tools/photos:/tool:ro" \
  -v "$LIBRARY:/photos:ro" \
  -v "$OUT:/out" \
  ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim \
  sh -c 'apt-get update -qq >/dev/null && apt-get install -y -qq libgl1 libglib2.0-0 >/dev/null && cd /tool && exec uv run --quiet /tool/triage.py "$@"' \
  triage "$@"
