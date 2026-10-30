#!/bin/sh
# Runs tools/photos/climb_judge.py on CT 100 in a throwaway container, so the
# box needs no Python, uv or OpenCV of its own. The mirror is mounted
# read-only; verdicts land in /root/climb-judge (mounted as /out).
#
#   tools/photos/climb-judge.sh --labels /out/labels.csv
#   tools/photos/climb-judge.sh --labels /out/labels.csv --methods local
#
# The Claude half needs ANTHROPIC_API_KEY in the environment; it is passed
# through by name, never written anywhere. Put labels.csv in /root/climb-judge.
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT="${CLIMB_JUDGE_OUT:-/root/climb-judge}"
LIBRARY="${CLIMB_JUDGE_LIBRARY:-/media/photos/icloud}"
mkdir -p "$OUT" "$OUT/cache"

# libgl1 and libglib2.0-0: mediapipe pulls in the full (not headless) OpenCV,
# which will not import without them. uv's cache sits in $OUT so the second
# run does not download ~300 MB of wheels again.
exec docker run --rm \
  --cpus 2 --memory 3g \
  -e ANTHROPIC_API_KEY \
  -e UV_CACHE_DIR=/out/cache \
  -v "$REPO/tools/photos:/tool:ro" \
  -v "$LIBRARY:/photos:ro" \
  -v "$OUT:/out" \
  ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim \
  sh -c 'apt-get update -qq >/dev/null && apt-get install -y -qq libgl1 libglib2.0-0 >/dev/null && exec uv run --quiet /tool/climb_judge.py "$@"' \
  climb-judge "$@"
