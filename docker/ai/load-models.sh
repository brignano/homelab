#!/usr/bin/env bash
#
# Create/refresh tuned Ollama models from the Modelfiles in ./models/.
#
# Each <name>.Modelfile pins num_thread so Ollama doesn't oversubscribe the
# LXC's CPU quota (see AGENTS.md → "Ollama / AI tuning"). This script builds
# each one in place, under the tag named on its `FROM` line, so Open WebUI
# needs no change.
#
# Run inside the Docker LXC where Ollama is installed:
#   ./docker/ai/load-models.sh
#
set -euo pipefail

models_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/models" && pwd)"

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
  echo "==> ollama create $tag -f $mf"
  ollama create "$tag" -f "$mf"
done

if [ "$found" -eq 0 ]; then
  echo "No *.Modelfile found in $models_dir" >&2
  exit 1
fi

echo
echo "Done. Installed models:"
ollama list
