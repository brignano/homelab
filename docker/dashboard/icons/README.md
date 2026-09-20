# Dashboard icons

Served at `/icons/...` — `docker-compose.yml` mounts this directory at
`/app/public/icons`, the only place Homepage serves local files from.

> **A NEW FILE HERE IS A 404 UNTIL THE CONTAINER IS RECREATED.** Homepage is
> Next.js, and a production Next server walks `public/` once at startup into a
> Set it never refreshes (`setupFsCheck`; the watcher that would refresh it is
> behind `if (opts.dev)`). Requests are matched against that frozen list, so a
> path that was not on disk when the process booted is a 404 however current the
> bind mount is. Editing a file that was already there is fine — contents are
> read per request.
>
> `docker compose up -d` does **not** do it. Compose recreates by hashing the
> service definition, the bytes behind a mount are not in that hash, and with
> the image pinned it finds nothing to do and exits 0. Deploying a new icon is
> `docker compose -f docker/dashboard/docker-compose.yml up -d --force-recreate`
> — which is what `scripts/repo-sync.sh` now runs for every bind-mount stack.
>
> This is not hypothetical. Every file below was added after the last change to
> `docker-compose.yml`, so from the afternoon the mark was drawn until this was
> found, the dashboard served a globe in Chrome and blank tiles, with CI green
> and the repo correct.

## The dashboard's own mark

| File | What it is |
|---|---|
| `favicon.svg` | **The real mark.** Hand-written: transparent ground, `prefers-color-scheme` step. What a tab shows. |
| `icon-source.svg` | The same figure with no media query, for rasterizing. |
| `apple-touch-icon.png` | 180px, ink ground. iOS home screen. |
| `favicon-32.png` | 32px, ink ground. Browsers that decline the SVG. |
| `favicon.ico` | 16/32/48, ink ground. Safari's root probe. |
| `site.webmanifest` | The web app manifest. What Chrome draws an **installed** app from. |
| `icon-192.png`, `icon-512.png` | Ink ground. The manifest's `any` icons. |
| `icon-maskable-512.png` | Ink ground, figure inset 10% a side. The manifest's `maskable`. |

The rasters are generated — `python3 scripts/gen-dashboard-icons.py`, which
needs `cairosvg` and `Pillow`. Edit `icon-source.svg`, re-run, commit the
output. Everything is committed because nothing on the box runs a build.

This follows the standard `life` sets in its own `scripts/gen-icons.mjs`, and
the two halves of it are worth stating:

**The SVG has no ground.** A filled tile dies against a tab strip that happens
to match it — homelab's first mark was an amber figure on a near-black square,
which is the exact thing `life` had already replaced for reading as nothing in
dark mode. An open amber figure carries on white and on near-black alike, and
the light/dark pair mirrors the `mark` token's own two steps.

**The rasters do take one.** They land where a transparent figure cannot be
trusted: Safari's Favorites tiles and an iOS home screen put the icon on a
surface the file does not control, and a thin amber bar on an unknown
background is a coin toss. Ink ground, `n-900`.

**The figure is three rack units**, rectilinear and horizontal — a family with
`life`'s three ascending steps and the trips range, tellable apart from both at
16px, which is the job: these sites sit in the same tab group. The last unit is
short because three equal bars read as a hamburger menu at that size.

### Why this many files, when Homepage declares two

Setting `favicon:` emits exactly `rel="icon"` and `rel="apple-touch-icon"`,
both pointing at the one path. Three things fill the gap:

- `config/custom.js` repoints the apple-touch link at the raster (iOS will not
  take an SVG — it substitutes a screenshot of the page) and adds the `.png`
  and `.ico` fallbacks. Safari reads the live DOM when someone taps Add to Home
  Screen, so patching after hydration is honoured.
- `docker/proxy/Caddyfile` rewrites `/favicon.ico` and `/apple-touch-icon.png`
  onto these files. Safari's address bar, suggestions and Favorites tiles probe
  those root paths *without parsing any markup*, and would otherwise find
  Homepage's stock logo.
- the same Caddyfile rewrites `/site.webmanifest`, for the surface below.

`scripts/check-dashboard.sh` fails if any of those places names a file that is
not here. A favicon that 404s is not an error anywhere — the browser just draws
a globe.

### An installed app reads none of that

Chrome takes the window titlebar, the taskbar entry and the install dialog from
the **web app manifest**, not from `rel="icon"`. Every link tag above is
invisible to it, which is why "the tab icon is fixed" and "the app window icon
is fixed" are two separate pieces of work.

Homepage hard-codes `<link rel="manifest" href="/site.webmanifest?v=4">` in its
`_document` and ships a manifest listing its own logo, baked into the image
where no setting reaches it. So the override is the same trick as the root
probes — `rewrite /site.webmanifest /icons/site.webmanifest` — rather than
bind-mounting a single file over `/app/public`, which is the stale-inode
problem the compose file documents. Dropping that rewrite does not 404; it
silently serves Homepage's logo again, so CI checks for the line.

**The manifest lists only rasters.** Not `favicon.svg`, deliberately, and this
is the ground rule again rather than an omission: a manifest icon lands on
window chrome and a launcher background this file does not control, so it gets
the ink ground for the same reason `apple-touch-icon.png` does. The SVG is for
the tab strip, where being transparent is the point.

192 and 512 are not conventional sizes here, they are required: Chrome will not
offer to install a site without at least a 192, and uses the 512 for the splash
and the larger chrome. The maskable copy is inset 10% a side because an Android
adaptive icon is cropped to a shape the page does not choose and only the middle
80% survives — this mark is near full-bleed so it holds at 16px, which is
exactly the drawing a launcher would crop the bars off.

### If a browser gets stuck on the old icon

Favicon caches are a separate long-lived store that largely ignores
`Cache-Control`; new bytes at an old URL are simply not seen. These files moved
to new names, which is its own cache bust. If it happens again, add a `?v=2` to
the paths in `settings.yaml`, `custom.js` and the Caddyfile — and note that an
iOS home-screen icon is baked in when the page is added, so that one needs
removing and re-adding.

**Rule out the server first, though.** The two failures look identical from a
tab — old icon or no icon — and the cheap check separates them in one request:
open `/icons/favicon.svg` directly. If it 404s, nothing is cached and no `?v=`
will help; the container is serving a file list from before that name existed
and needs `up -d --force-recreate` (see the top of this file). Reaching for the
cache bust first is the wrong order, because renaming a file is precisely what
the frozen list cannot serve — the fix makes the symptom worse.

## Tile icons

| File | What it is |
|---|---|
| `brignano.svg` | The `A\|B` monogram, the mark brignano.io carries. Drawn here — one ink, re-inked from the page by `config/custom.css`. |
| `grafana.svg`, `adguard-home.svg`, `kali-linux.svg` | Vendored verbatim from [dashboard-icons](https://github.com/homarr-labs/dashboard-icons). |
| `portainer-on-light.svg`, `portainer-on-dark.svg`, and the same pair for `open-webui` | **Generated**: each upstream drawing on its own. What the tiles actually show — `config/custom.css` picks one by the dashboard's theme. |
| `portainer.svg`, `open-webui.svg` | **Generated**: both drawings in one file, switched by the OS. The name the tile still points at, and the fallback. |
| `SOURCE` | The upstream repo and commit every vendored icon came from. |

```sh
./scripts/update-tile-icons.sh             # fetch anything referenced but missing
./scripts/update-tile-icons.sh --refresh   # re-resolve upstream and rebuild all
```

`icon: grafana.png` would make the *browser* fetch each one from a CDN on every
load — a poor dependency for the page you open because the internet broke, and
one AdGuard rule from a grid of blank squares. They are fetched once and
committed, and they stay exactly as their projects draw them: recognising
Grafana's G at a glance is the whole job of a tile icon.

### A tile icon cannot see the page

An `<img>` is its own document, so a `prefers-color-scheme` step inside a tile
icon follows the **OS**, while the dashboard follows Homepage's `theme:` setting
and header toggle. Those two disagree by default: `theme: dark` is pinned, so a
browser in light mode asks each icon for its light-ground drawing and puts it on
a near-black card.

Not *only* the OS, strictly. Blink resolves `prefers-color-scheme` inside an
embedded SVG against the embedder's used `color-scheme`, which the tokens do set
— measured here in headless Chromium, where a combined icon follows the page.
Nothing is built on that: it is one engine, it depends on `custom.js` having run
to put `color-scheme: dark` on `<html>`, and the mark that started all this was
invisible on a real phone. The swap below works the same everywhere.

`brignano.svg` is the icon we draw, so it does not guess. It carries one ink
(`n-900`, as the site draws it) and `config/custom.css` inverts it when
`data-theme` is `dark` — the attribute `custom.js` mirrors from Homepage, and the
one the tokens already read. The mark follows the dashboard; the phone does not
get a vote. It only works on a single-colour mark, which is a reason to keep it
one.

Two vendor marks are black on a near-black card — Portainer's was invisible from
the day the tile was added. Upstream draws each of those twice, once per
background, so `scripts/compose-icon.py` writes three files from the pair:
`<name>-on-light.svg` and `<name>-on-dark.svg`, each a single drawing with
nothing in it that reads the viewer, plus `<name>.svg` with both nested and a
media query between them. Ids are prefixed on the way in, because the two
drawings are usually the same file with different fills and collide otherwise.
Naming is by the card the drawing lands **on**: upstream's own `portainer-dark`
is the drawing *for* a dark background and `open-webui-light` the one for a
light background, so its suffixes mean opposite things across the set.

`custom.css` swaps the tile between the two singles with `content: url(...)`,
which replaces what an `<img>` draws — the element-level `content`, not the
pseudo-element one, which WebKit and Blink shipped for real elements and Gecko
followed. Same `data-theme` key as the monogram, so all three tiles answer to
the dashboard.

A browser that declines the swap still loads `<name>.svg` and gets the OS
answer, which is what was here before: right while the phone and the dashboard
agree, wrong exactly where it was already wrong. That is the whole reason the
combined file is still generated. `scripts/check-dashboard.sh` fails if a
`-on-dark.svg` here is not named in `custom.css`, or if `custom.css` names one
that is not here — composing a third of these and forgetting the stylesheet
would look precisely like the bug it replaced.

## Coverage

| Surface | Reads | Served by |
|---|---|---|
| Chrome/Firefox tab strip | `rel="icon"` | `settings.yaml` → `favicon.svg`, plus `custom.js` fallbacks |
| Safari address bar, Favorites | `/favicon.ico` root probe | Caddyfile rewrite |
| iOS home screen | `rel="apple-touch-icon"`, `/apple-touch-icon.png` | `custom.js` + Caddyfile rewrite |
| Chrome installed app: titlebar, taskbar, install dialog | the manifest | Caddyfile rewrite → `site.webmanifest` |
| Android installed app | the manifest | as above, `maskable` included |

All five now point at this directory. The one thing none of it survives is the
container not being recreated — see the top of this file.
