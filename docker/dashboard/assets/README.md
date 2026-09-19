# Dashboard assets

Everything `config/custom.css` imports, served at `/assets/...` by the
`./assets:/app/public/assets` mount in `docker-compose.yml`.

| File | What it is |
|---|---|
| `design-tokens.css` | `tokens.css` from [`@brignano/design`](https://github.com/brignano/design), **verbatim**. The source of truth for colour, type, space, shape and motion. |
| `design-tokens.version` | The version that file came from. |
| `homepage-palette.css` | **Generated** from the tokens. Do not edit. |
| `fonts/` | Geist (latin, variable) from `@fontsource-variable/geist`, and its licence. |

All of it is committed, because nothing on the box runs npm: `git pull` is the
whole install.

## Updating

```sh
./scripts/update-design-tokens.sh            # refresh at the pinned versions
./scripts/update-design-tokens.sh 0.2.0      # move to a version, or `latest`
./scripts/update-design-tokens.sh --check    # what CI runs; offline
```

The script fetches both packages from the npm registry, writes the versions it
actually took, and regenerates `homepage-palette.css`. Then
`docker compose up -d --force-recreate dashboard` on the box — or just a
restart, since `assets/` is a directory mount and file replacements inside it
are visible to the running container.

## Why the palette is generated

Homepage has no design-token layer. It themes itself from ten CSS variables,
`--color-50` … `--color-900`, each holding **RGB channels** rather than a
colour:

```css
.theme-zinc { --color-800: 39 39 42; }          /* Homepage */
:root       { --n-900: #111111; }               /* the design system */
```

CSS cannot turn one into the other, so something has to translate. Do it by
hand and the dashboard owns a private copy of the palette, which is the drift
this repo keeps building checks against — so the translation is generated from
the vendored tokens instead, and CI diffs it on every push.

The mapping is not step-for-step, and the reasoning is in the generator:
Homepage's ramp runs light → dark in *both* themes (in dark it takes the page
background from `--color-800` and its ink from `--color-200`), while the design
system's ramp inverts between them. Each step is therefore chosen from what
Homepage *does* with it.

## What is deliberately not here

- **IBM Plex Mono.** `--mono` names it, but Homepage uses `font-mono` in four
  places, none of them prominent. The token's fallback chain lands on the
  platform's mono face, which is not worth another vendored file to avoid.
- **Silkscreen.** Marketing tier only. On the tool tier `--display` aliases to
  `--sans`, so there is nothing to load.
