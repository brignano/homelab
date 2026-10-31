# Clip review page

A web page for judging climb-judge verdicts without hunting for each clip in
Photos. `https://review.$HOMELAB_DOMAIN` (LAN + tailnet, via Caddy).

For each clip in `/root/climb-judge/climb-verdicts-claude-*.csv` it plays the
video from the iCloud mirror (read-only) beside Claude's verdict, confidence and
reason. Phone clips are often sideways: the browser honours the rotation
metadata (Safari on the Mac does; `.MOV`/HEVC may not play elsewhere), and **R**
rotates by hand if one still lands wrong.

| Key | Does |
|-----|------|
| S / F / N | label send / fall / none: appended to `/root/climb-judge/labels.csv`, the format `climb-judge.sh --labels` reads |
| U | unsure: recorded in `review-log.csv` only (`--labels` would reject it) |
| ← → (or K / J) | previous / next |
| Space | play / pause |

Only clips already on the drive are shown; the header says how many verdicts are still waiting for the sync to download them.

The queue shows clips still to decide first, `none` and low confidence at the
top; unsure clips come back. Month filter and "hide reviewed" are in the header.

## Removal

The page never deletes anything. The header links export the **candidates**:
clips you labelled fall or none that are confirmed present in the mirror
(`+ unreviewed Claude calls` adds fall/none at confidence ≥ 0.8 you did not
review). To act on them, on the Mac:

```bash
uv run tools/photos/to-album.py --url https://review.$HOMELAB_DOMAIN
```

It finds those clips in Photos by file name and adds them to one album, "Climb
clips to delete" (favourites skipped, `--dry-run` to preview). Then in Photos:
open the album, Cmd-A, Delete. Photos is where deletion has to happen
(`docs/design/tsd-photo-library.md`).

## Deploy

`cd docker/clipreview && docker compose up -d --build`. The mirror mount refuses
to start without `/media/photos/icloud`. The proxy stack needs a Caddy reload
for the new route (`/deploy` covers it; a Caddy *recreate* stays confirm-first).
