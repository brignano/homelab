# Dashboard icons

Served at `/icons/...` — `docker-compose.yml` mounts this directory at
`/app/public/icons`, which is the only place Homepage serves local images from.

| File | What it is |
|---|---|
| `homelab.svg` | The mark. **Source of truth** — edit this one. |
| `homelab.png` | 512×512 render of it, and the file `settings.yaml` points at. |

## Why a PNG is what ships

Setting `favicon:` makes Homepage emit *both* tags from the same path:

```html
<link rel="icon" href="/icons/homelab.png" />
<link rel="apple-touch-icon" sizes="180x180" href="/icons/homelab.png" />
```

iOS will not take an SVG for `apple-touch-icon`. Point that at the `.svg` and
Safari silently substitutes a screenshot of the page for the home-screen icon —
which is the unidentifiable tile the mark exists to replace, and it fails in
exactly the place it is hardest to notice from a desktop browser. So the PNG is
committed rather than built on the box: there is no build step in this stack,
and an icon that only exists after someone remembers to run a command is an
icon that is missing.

Regenerate it after any edit to the SVG, from this directory:

```sh
# pip install cairosvg
python3 -c "import cairosvg; cairosvg.svg2png(url='homelab.svg', write_to='homelab.png', output_width=512, output_height=512)"

# or, with librsvg installed:
rsvg-convert -w 512 -h 512 homelab.svg -o homelab.png
```

`scripts/check-dashboard.sh` fails if `settings.yaml` names an icon that is not
here, or if the compose file stops mounting this directory. It cannot tell you
the PNG is stale — that one is on you.

## Known limit

Homepage's `/site.webmanifest` is baked into the image and still lists its own
logo, so an Android "install app" uses that rather than this mark. Overriding it
would mean bind-mounting a single file into `/app/public`, which is the stale
inode problem the compose file documents. iOS reads `apple-touch-icon` first, so
the surface this was drawn for is covered.
