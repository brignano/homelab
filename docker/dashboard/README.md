# dashboard — the home page

The landing page at the **bare** `$HOMELAB_DOMAIN`, the parent of every service
name, so it is the one URL that does not have to be remembered. Built to show
people what the box runs (homelab#395): a plain-English hero with a live
services-up ring, the box's stats with tap-for-details, the open-work totals,
and every app with a live Up/Down.

**No container.** Caddy serves these files and three read-only routes
(`docker/proxy/Caddyfile`, the bare-domain block). It replaced Homepage, whose
look could only be changed by overriding its markup, which broke on its updates.

| Path | What it is | Served at |
|---|---|---|
| `site/index.html`, `app.css`, `app.js` | The page. | `/` |
| `site/services.json` | Every tile, in order, by section, plus the footer links. | `/services.json` |
| `icons/` | The page's mark and every tile icon. [README](icons/README.md) | `/icons/...` |
| `assets/` | The design system: vendored tokens and Geist. [README](assets/README.md) | `/assets/...` |
| `.env` | Weather coordinates and Healthchecks id (see `.env.example`). Read by the proxy. | `/api/home.json` |

## Where the numbers come from

| On the page | Source |
|---|---|
| CPU, memory, disks, network, uptime | node-exporter (`job="node"`), via `/api/prom/` |
| Temperature | node-exporter hwmon sensors, if the box exposes any |
| Busiest and biggest apps, containers count, desktops Asleep/Running | cAdvisor |
| Up/Down and response time per tile | the blackbox probe named by the tile's `probe` |
| "What's on it" and "TB of movies and shows" | `homelab_media_folder_bytes`, written daily by `scripts/media-usage.sh` |
| Open-work totals | the Work page's `data.js` over the same GitHub route, so the totals match it |
| Weather | Open-Meteo, from the browser, no key |

Prometheus is reachable on the LAN already; `/api/prom/` passes only GET
`/api/v1/query` and `/api/v1/query_range`, the two read endpoints.

## Adding a tile

1. A site block in `docker/proxy/Caddyfile`.
2. A tile in `site/services.json`: `name`, `href` (`https://<name>.{domain}`),
   `icon`, a plain-words `desc`, and a `probe` that is a blackbox target in
   `docker/monitoring/prometheus/prometheus.yml` (or `container` for a
   scale-to-zero desktop, which must not be probed).
3. `./scripts/update-tile-icons.sh` to vendor the icon.

`scripts/check-dashboard.sh` (CI) fails if any of those is missing.

## Deploying a change

Files under `site/`, `icons/` and `assets/` are directory mounts into Caddy, so
a pull is the whole deploy. A Caddyfile or proxy compose change needs
`/deploy proxy`.
