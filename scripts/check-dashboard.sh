#!/usr/bin/env sh
#
# Every service reachable through Caddy must have a tile on the home page, and
# every tile must point at things that exist.
#
# Why this exists
# ---------------
# docker/proxy/Caddyfile and docker/dashboard/site/services.json are two lists
# of the same thing, maintained by hand, in different files. That is the exact
# shape of drift this repo keeps running into: the home page does not break
# when it falls behind, it just quietly stops being the complete picture —
# which is worse, because you go on trusting it.
#
# So CI compares them, and the other lists a tile leans on:
#   - every Caddyfile site has a tile, and every tile's site still exists
#   - every tile's icon file is in docker/dashboard/icons (an -on-dark mark
#     needs its -on-light twin, named on the same tile)
#   - every tile's `probe` is a blackbox target in prometheus.yml, or its
#     Up/Down would say "No check" forever
#   - every tile's `container` is a container_name in some compose file
#   - every /icons/ path index.html or the web app manifest names exists
#
# Run by hand from the repo root:
#   ./scripts/check-dashboard.sh
#
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
exec python3 - "$REPO" <<'PY'
import json, pathlib, re, sys

repo = pathlib.Path(sys.argv[1])
caddy = (repo / "docker/proxy/Caddyfile").read_text()
site = repo / "docker/dashboard/site"
icons = repo / "docker/dashboard/icons"
services = json.loads((site / "services.json").read_text())
prom = (repo / "docker/monitoring/prometheus/prometheus.yml").read_text()
compose = "\n".join(p.read_text() for p in (repo / "docker").glob("*/docker-compose.yml"))

# Sites that intentionally have no tile. Each needs a reason, not just a name.
#   mcp, budget-mcp   API endpoints, not pages: they answer MCP over HTTP and
#                     return 401 to a browser without a bearer token.
EXEMPT = {"mcp", "budget-mcp"}

bad = []
def ok(msg): print("ok       " + msg)
def fail(msg): print("MISSING  " + msg); bad.append(msg)

tiles = [t for s in services["sections"] for t in s["tiles"]]
sites = set(re.findall(r"^([a-z0-9-]+)\.\{\$HOMELAB_DOMAIN\}[ ,]", caddy, re.M))
linked = {m.group(1) for t in tiles for m in [re.match(r"https://([a-z0-9-]+)\.\{domain\}", t["href"])] if m}

for s in sorted(sites - EXEMPT):
    (ok if s in linked else fail)(f"{s} has a tile" if s in linked else f"{s}: Caddyfile site with no tile in services.json")
for s in sorted(linked - sites):
    fail(f"{s}: tile links to a site with no Caddyfile block")

for t in tiles:
    for key in ("icon", "iconLight"):
        f = t.get(key)
        if f and not (icons / f).is_file():
            fail(f"{t['name']}: icon {f} is not in docker/dashboard/icons")
    if t.get("icon", "").endswith("-on-dark.svg") and t.get("iconLight") != t["icon"].replace("-on-dark", "-on-light"):
        fail(f"{t['name']}: {t['icon']} needs iconLight {t['icon'].replace('-on-dark', '-on-light')}")
    if "probe" in t:
        if re.search(r"^\s*-\s*" + re.escape(t["probe"]) + r"\s*$", prom, re.M):
            ok(f"{t['name']} is probed ({t['probe']})")
        else:
            fail(f"{t['name']}: probe {t['probe']} is not a blackbox target in prometheus.yml")
    if "container" in t and not re.search(r"container_name:\s*" + re.escape(t["container"]) + r"\s*$", compose, re.M):
        fail(f"{t['name']}: no compose file names a container {t['container']}")
    if "probe" not in t and "container" not in t:
        fail(f"{t['name']}: needs a probe or a container, or it can never say Up")

refs = set(re.findall(r'"/icons/([A-Za-z0-9._-]+)"', (site / "index.html").read_text()))
manifest = json.loads((icons / "site.webmanifest").read_text())
refs |= {i["src"].split("/icons/")[-1] for i in manifest.get("icons", []) if "/icons/" in i.get("src", "")}
for r in sorted(refs):
    if not (icons / r).is_file():
        fail(f"/icons/{r} is named by index.html or the manifest but not in docker/dashboard/icons")

if bad:
    print("\nThe home page's tile list is out of step: see MISSING above.", file=sys.stderr)
    sys.exit(1)
print("home page covers every proxied site")
PY
