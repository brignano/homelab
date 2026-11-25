#!/usr/bin/env bash
# Redraws docs/architecture/ from the Compose files and the Caddyfile, with
# Driftwood (https://github.com/brignano/driftwood). Reads files only: no
# credentials, nothing on the box. Run from anywhere; needs Node 20+.
set -euo pipefail
cd "$(dirname "$0")/.."

out=docs/architecture
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Driftwood reads routes from a JSON list. Pull them from the Caddyfile: each
# site block's host, and the first reverse_proxy to a Compose service in it.
mkdir -p "$out"
awk '
  /^[^ \t#}].*\{[ \t]*$/ { host=$1; sub(/,$/, "", host); next }
  /reverse_proxy [a-z0-9_-]+:[0-9]+/ && host != "" && !(host in seen) {
    for (i = 1; i <= NF; i++) if ($i == "reverse_proxy") up = $(i+1)
    seen[host] = 1
    printf "%s{\"host\":\"%s\",\"upstream\":\"%s\"}", (n++ ? "," : "["), host, up
  }
  END { print (n ? "]" : "[]") }
' docker/proxy/Caddyfile > "$out/routes.json"

npx --yes @brignano/driftwood@latest scan --out-dir "$tmp/scan" >/dev/null
cp "$tmp/scan/diagram-flow.svg" "$out/request-flow.svg"
cp "$tmp/scan/diagram.svg" "$out/services.svg"
cp "$tmp/scan/summary.md" "$out/summary.md"
echo "wrote $out/ (routes.json, request-flow.svg, services.svg, summary.md)"
