#!/usr/bin/env bash
# Download the community Grafana dashboards into the `Reference` folder, pinning
# datasource template inputs to the fixed UIDs declared in
# grafana/provisioning/datasources/datasources.yml (prometheus / loki).
#
# Run on the host, then restart Grafana:
#   ./scripts/fetch-dashboards.sh            # fetch the pinned revisions
#   ./scripts/fetch-dashboards.sh --update   # move the pins to latest upstream
#   docker compose restart grafana
#
# What changed, and why the list got shorter (2026-09-19)
# -------------------------------------------------------
# This script used to fetch six dashboards, which between them were most of what
# the lab had. Two problems, found on review:
#
#   Three of them no longer rendered. `blackbox` (7587), `postgresql` (9628) and
#   `loki-logs` (13639) were published at schemaVersion 16-26, back when `graph`
#   and `singlestat` were Angular plugins. Grafana 11 disabled Angular by default
#   and Grafana 12 removed it, so 10 of 11, 32 of 35 and 1 of 2 panels
#   respectively were dead — on a `grafana/grafana:latest` image that had moved
#   underneath them without anyone noticing. They are replaced by committed
#   dashboards in grafana/dashboards/homelab/, which is where anything the lab
#   actually relies on belongs.
#
#   `revisions/latest` is not a pin. Re-running this rewrote committed files with
#   whatever upstream had published since, so the JSON in git described "whenever
#   someone last ran the script" rather than any version a human had chosen —
#   and the change arrived as a 400 KB diff nobody was going to read.
#
# So the revisions are now recorded in `REVISIONS` next to the dashboards, and
# this fetches exactly those. Upstream changes become a deliberate act with a
# one-line diff attached: `--update` moves the pins and rewrites the JSON, and
# the REVISIONS diff says what moved and to what.
#
# The first run on a fresh checkout has nothing pinned, so it resolves latest and
# writes the pins — commit that file along with the dashboards.
#
# What is left is reference material: three dashboards that are genuinely good at
# what they do (140 panels of node_exporter, 92 of cAdvisor) and that nobody
# should start a diagnosis from. Hence the folder.
set -euo pipefail

DEST="$(cd "$(dirname "$0")/../grafana/dashboards/reference" && pwd)"
PINS="$DEST/REVISIONS"

update=""
case "${1:-}" in
  "")        ;;
  --update)  update=yes ;;
  *)         echo "usage: $0 [--update]" >&2; exit 2 ;;
esac

echo "Writing dashboards to: $DEST"

# name -> grafana.com dashboard id
dashboards="
node-exporter-full:1860
docker-cadvisor:19792
proxmox:10347
"

pinned_rev() {
  [ -f "$PINS" ] || return 0
  awk -v n="$1" '$1 == n { print $3 }' "$PINS" | tail -n1
}

latest_rev() {
  # The dashboard metadata endpoint reports the current revision. Parsed with
  # sed rather than jq so this keeps working on a box where jq is not installed,
  # which is the box it runs on.
  curl -fsSL "https://grafana.com/api/dashboards/$1" \
    | tr ',' '\n' \
    | sed -n 's/.*"revision"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' \
    | head -n1
}

tmp_pins=$(mktemp)
trap 'rm -f "$tmp_pins"' EXIT
printf '# Pinned grafana.com revisions. Written by scripts/fetch-dashboards.sh.\n' >> "$tmp_pins"
printf '# Bump with --update, and read the JSON diff before committing it.\n' >> "$tmp_pins"
printf '# name  id  revision\n' >> "$tmp_pins"

for entry in $dashboards; do
  name="${entry%%:*}"
  id="${entry##*:}"

  rev=""
  [ -n "$update" ] || rev=$(pinned_rev "$name")
  if [ -z "$rev" ]; then
    rev=$(latest_rev "$id")
    [ -n "$rev" ] || { echo "could not resolve a revision for $name (id=$id)" >&2; exit 1; }
    echo "  - $name (id=$id) -> pinning revision $rev"
  else
    echo "  - $name (id=$id rev=$rev)"
  fi

  curl -fsSL "https://grafana.com/api/dashboards/${id}/revisions/${rev}/download" \
    | sed -E \
        -e 's/\$\{DS_PROMETHEUS[^}]*\}/prometheus/g' \
        -e 's/\$\{DS_LOKI[^}]*\}/loki/g' \
        -e 's/"datasource"[[:space:]]*:[[:space:]]*"\$\{[^}]*[Pp]rometheus[^}]*\}"/"datasource": {"type":"prometheus","uid":"prometheus"}/g' \
        -e 's/"datasource"[[:space:]]*:[[:space:]]*"\$\{[^}]*[Ll]oki[^}]*\}"/"datasource": {"type":"loki","uid":"loki"}/g' \
    > "${DEST}/${name}.json"

  printf '%s %s %s\n' "$name" "$id" "$rev" >> "$tmp_pins"
done

mv "$tmp_pins" "$PINS"
trap - EXIT

echo
echo "Pins written to $PINS:"
grep -v '^#' "$PINS"
echo
echo "Now: docker compose restart grafana  (and commit the diff)"
