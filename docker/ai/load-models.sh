#!/usr/bin/env bash
#
# Create/refresh tuned Ollama models from the Modelfiles in ./models/.
#
# Each <name>.Modelfile pins num_thread so Ollama doesn't oversubscribe the
# LXC's CPU quota (see AGENTS.md → "Ollama / AI tuning"). This script builds
# each one in place, under the tag named on its `FROM` line, so Open WebUI
# needs no change.
#
# Ollama runs as a Docker container ("ollama"), NOT directly in the LXC, so the
# `ollama` CLI isn't on the LXC PATH. We copy each Modelfile into the container
# and run `ollama create` there. (`ollama create -f -` / stdin is not supported
# on this version, hence the docker cp.)
#
# Run inside the Docker LXC (where the `docker` CLI is available):
#   ./docker/ai/load-models.sh
#
set -euo pipefail

container="ollama"
models_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/models" && pwd)"

if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
  echo "error: container '$container' is not running" >&2
  exit 1
fi

shopt -s nullglob
found=0
for mf in "$models_dir"/*.Modelfile; do
  found=1
  # Tag = the base model on the FROM line; we tune it in place.
  tag="$(awk 'tolower($1) == "from" { print $2; exit }' "$mf")"
  if [ -z "$tag" ]; then
    echo "skip: no FROM line in $mf" >&2
    continue
  fi
  remote="/tmp/$(basename "$mf")"
  echo "==> docker exec $container ollama create $tag (from $(basename "$mf"))"
  docker cp "$mf" "$container:$remote"
  docker exec "$container" ollama create "$tag" -f "$remote"
  docker exec "$container" rm -f "$remote"
done

if [ "$found" -eq 0 ]; then
  echo "No *.Modelfile found in $models_dir" >&2
  exit 1
fi

echo
echo "Done. Installed models:"
docker exec "$container" ollama list
