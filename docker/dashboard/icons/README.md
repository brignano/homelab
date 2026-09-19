# Dashboard icons

Served at `/icons/...` — `docker-compose.yml` mounts this directory at
`/app/public/icons`, the only place Homepage serves local files from.

## The dashboard's own mark

| File | What it is |
|---|---|
| `favicon.svg` | **The real mark.** Hand-written: transparent ground, `prefers-color-scheme` step. What a tab shows. |
| `icon-source.svg` | The same figure with no media query, for rasterizing. |
| `apple-touch-icon.png` | 180px, ink ground. iOS home screen. |
| `favicon-32.png` | 32px, ink ground. Browsers that decline the SVG. |
| `favicon.ico` | 16/32/48, ink ground. Safari's root probe. |

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

### Why five files, when Homepage declares two

Setting `favicon:` emits exactly `rel="icon"` and `rel="apple-touch-icon"`,
both pointing at the one path. Two things fill the gap:

- `config/custom.js` repoints the apple-touch link at the raster (iOS will not
  take an SVG — it substitutes a screenshot of the page) and adds the `.png`
  and `.ico` fallbacks. Safari reads the live DOM when someone taps Add to Home
  Screen, so patching after hydration is honoured.
- `docker/proxy/Caddyfile` rewrites `/favicon.ico` and `/apple-touch-icon.png`
  onto these files. Safari's address bar, suggestions and Favorites tiles probe
  those root paths *without parsing any markup*, and would otherwise find
  Homepage's stock logo.

`scripts/check-dashboard.sh` fails if any of those three places names a file
that is not here. A favicon that 404s is not an error anywhere — the browser
just draws a globe.

### If a browser gets stuck on the old icon

Favicon caches are a separate long-lived store that largely ignores
`Cache-Control`; new bytes at an old URL are simply not seen. These files moved
to new names, which is its own cache bust. If it happens again, add a `?v=2` to
the paths in `settings.yaml`, `custom.js` and the Caddyfile — and note that an
iOS home-screen icon is baked in when the page is added, so that one needs
removing and re-adding.

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

## Known limit

Homepage's `/site.webmanifest` is baked into the image and still lists its own
logo, so an Android "install app" uses that rather than this mark. Overriding
it would mean bind-mounting a single file into `/app/public`, which is the
stale inode problem the compose file documents. iOS reads `apple-touch-icon`
first, so the surface this was drawn for is covered.
