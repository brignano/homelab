# Dashboard icons

Served at `/icons/...` — `docker-compose.yml` mounts this directory at
`/app/public/icons`, the only place Homepage serves local images from.

| File | What it is |
|---|---|
| `homelab.svg` | The dashboard's own mark. **Source of truth** — edit this one. |
| `homelab.png` | 512×512 render of it, and the file `settings.yaml` points at. |
| `brignano.svg` | The `A\|B` monogram, the same mark brignano.io carries. Drawn here. |
| `grafana.svg`, `adguard-home.svg`, `kali-linux.svg` | Vendored verbatim from [dashboard-icons](https://github.com/homarr-labs/dashboard-icons). |
| `portainer.svg`, `open-webui.svg` | **Generated**: two upstream drawings in one file. See below. |
| `SOURCE` | The upstream repo and commit every vendored icon came from. |

```sh
./scripts/update-tile-icons.sh             # fetch anything referenced but missing
./scripts/update-tile-icons.sh --refresh   # re-resolve upstream and rebuild all
```

`scripts/check-dashboard.sh` — and so CI — fails if the config names an icon
that is not here, or if this directory stops being mounted.

## Why the tile icons are vendored

`icon: grafana.png` makes *the browser* fetch the icon from a CDN on every load.
That is a poor dependency for the page you open because the internet is the
thing that broke, and one AdGuard rule away from a grid of blank squares. They
are fetched once, committed, and named as `/icons/...` paths instead.

They stay exactly as their projects draw them. Recognising Grafana's G at a
glance is the entire job of a tile icon, so the design system does not get a
vote here — its job is the surfaces this repo owns.

## Why two of them are generated

Portainer's mark is a black P. On this dashboard's dark card that is nothing at
all — and it has been invisible for as long as the tile has existed. Upstream
ships a second drawing for dark backgrounds, and Homepage cannot choose between
two files by theme: it renders a tile as an `<img>`, which is its own document
and cannot see the page.

An SVG document *can* see the viewer's colour scheme. So both drawings go into
one file, each in a nested `<svg>` that keeps its own coordinate system, and a
media query shows one. `scripts/compose-icon.py` does it, ids prefixed on the
way in because the two drawings are usually the same file with different fills.

The switch follows the **OS**, not Homepage's theme toggle — an `<img>` cannot
see that either. They agree unless the dashboard is pinned to a theme the phone
is not in, and in that case the tile still has its label.

## Why the mark ships as a PNG

Setting `favicon:` makes Homepage emit *both* tags from the same path:

```html
<link rel="icon" href="/icons/homelab.png" />
<link rel="apple-touch-icon" sizes="180x180" href="/icons/homelab.png" />
```

iOS will not take an SVG for `apple-touch-icon`. Point that at the `.svg` and
Safari silently substitutes a screenshot of the page for the home-screen icon —
the unidentifiable tile the mark exists to replace, failing in the place it is
hardest to notice from a desktop browser. So the PNG is committed rather than
built on the box: there is no build step in this stack, and an icon that only
exists after someone remembers to run a command is an icon that is missing.

Regenerate it after any edit to `homelab.svg`, from this directory:

```sh
# pip install cairosvg
python3 -c "import cairosvg; cairosvg.svg2png(url='homelab.svg', write_to='homelab.png', output_width=512, output_height=512)"

# or, with librsvg installed:
rsvg-convert -w 512 -h 512 homelab.svg -o homelab.png
```

Nothing checks that the PNG matches the SVG — that one is on you.

## Known limit

Homepage's `/site.webmanifest` is baked into the image and still lists its own
logo, so an Android "install app" uses that rather than this mark. Overriding it
would mean bind-mounting a single file into `/app/public`, which is the stale
inode problem the compose file documents. iOS reads `apple-touch-icon` first, so
the surface this was drawn for is covered.
