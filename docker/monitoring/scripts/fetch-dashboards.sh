#!/usr/bin/env bash
# Download community Grafana dashboards into the provisioned dashboards folder,
# pinning datasource template inputs to the fixed UIDs declared in
# grafana/provisioning/datasources/datasources.yml (prometheus / loki).
#
# Run once on the host before (or any time after) `docker compose up`, then
# restart Grafana:  docker compose restart grafana
set -euo pipefail

DEST="$(cd "$(dirname "$0")/../grafana/dashboards" && pwd)"
echo "Writing dashboards to: $DEST"

# name -> grafana.com dashboard id
dashboards="
node-exporter-full:1860
docker-cadvisor:19792
proxmox:10347
postgresql:9628
blackbox:7587
loki-logs:13639
"

for entry in $dashboards; do
  name="${entry%%:*}"
  id="${entry##*:}"
  echo "  - $name (id=$id)"
  curl -fsSL "https://grafana.com/api/dashboards/${id}/revisions/latest/download" \
    | sed -E \
        -e 's/\$\{DS_PROMETHEUS[^}]*\}/prometheus/g' \
        -e 's/\$\{DS_LOKI[^}]*\}/loki/g' \
        -e 's/"datasource"[[:space:]]*:[[:space:]]*"\$\{[^}]*[Pp]rometheus[^}]*\}"/"datasource": {"type":"prometheus","uid":"prometheus"}/g' \
        -e 's/"datasource"[[:space:]]*:[[:space:]]*"\$\{[^}]*[Ll]oki[^}]*\}"/"datasource": {"type":"loki","uid":"loki"}/g' \
    > "${DEST}/${name}.json"
done

echo "Done. Now: docker compose restart grafana"
