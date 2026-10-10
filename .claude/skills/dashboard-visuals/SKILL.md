---
name: dashboard-visuals
description: Visual rules for this homelab — the brignano/design system, page icons, favicons and the web app manifest, vendored tile icons, design tokens, and the Homepage dashboard's config directory. Use when touching anything under docker/dashboard/, adding a service tile, changing an icon or colour, or making any visual choice in this repo.
---

# Dashboard and visuals

- **Anything with a visual choice in it follows [brignano/design](https://github.com/brignano/design)** — the shared design system, which already names `homelab` as a tool-tier consumer. Take colour from its tokens rather than picking one: identity is its `mark` hue (larch amber), and it only ever inks a graphic, never a control. `docker/dashboard/icons/` is the worked example, including why a favicon is the one place its "never hardcode a hex" rule cannot hold.
- **Page icons follow the standard `life` sets** (`scripts/gen-icons.mjs` there, `scripts/gen-dashboard-icons.py` here): the SVG is the real mark — transparent ground, `prefers-color-scheme` step, because a filled tile disappears against a tab strip that matches it — and the rasters (`.ico`, `apple-touch`, 32px PNG) take an ink ground for the surfaces that put the icon somewhere we do not control. Safari probes `/favicon.ico` and `/apple-touch-icon.png` at the root without reading markup, so those paths have to answer; the Caddyfile points them at the mark. **An installed app reads none of that** — Chrome draws its window titlebar, taskbar entry and install dialog from the web app *manifest*, never from `rel="icon"` — so `/site.webmanifest` is rewritten onto ours the same way, and it lists rasters only (192, 512, and a 512 inset 10% a side for `maskable`), for the same ink-ground reason. Fixing the tab icon and fixing the app window are two separate pieces of work; check both.
- **Third-party marks stay as their projects draw them.** The dashboard's tile icons are vendored into `docker/dashboard/icons/` by `scripts/update-tile-icons.sh` rather than fetched from a CDN by the browser — a dashboard that needs the internet to render is useless on the day the internet is what broke. Adding a service means `icon: /icons/<name>.svg` and a run of that script; CI fails if the file is not there.
- **The dashboard wears that system at runtime.** `scripts/update-design-tokens.sh` vendors `tokens.css` (and Geist) from npm into `docker/dashboard/assets/` and generates the RGB-channel palette Homepage themes itself from; `config/custom.css` bridges the rest through Tailwind v4's own theme variables, never through class names. Update it by re-running the script, not by editing generated files or typing a colour — CI diffs the generated palette against the vendored tokens.
- **A new file under `docker/dashboard/icons/` needs a container restart to be served.** Homepage enumerates `/app/public` once at startup, so a new filename is a 404 however current the mount is. The "Config mounts" section of `AGENTS.md` has the whole story; it is how the dashboard's mark shipped, passed CI and was never served once.
- **A container that owns a bind-mounted config directory writes to it.**
  Homepage drops 0-byte skeletons (`custom.css`, `custom.js`, `docker.yaml`, …)
  into `docker/dashboard/config/` whenever they are missing. The day the repo
  starts tracking one of those paths, `git pull` refuses to overwrite the empty
  local copy and the box stops pulling *entirely* — every stack, over a file
  with nothing in it. `repo-sync.sh` clears that narrow case (untracked, empty,
  and added by an incoming commit) and reports it; anything with content in it
  still stops the pull. The unused skeletons are named one by one in
  `.gitignore`.
