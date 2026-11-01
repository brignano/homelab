# Video review page

A web page for reviewing the videos in the iCloud mirror without hunting for each
one in Photos. `https://review.home.brignano.io` (LAN + tailnet, via Caddy).

**Every video in the mirror is listed**, with filters for *Climbing clips*,
*Other videos* and *Duplicates*, plus month. Two kinds:

- **Climbing clips**: videos with a verdict in
  `/root/climb-judge/climb-verdicts-claude-*.csv`. Claude's verdict, confidence and
  reason sit beside the video and you label it send / fall / none (or unsure).
- **Other videos**: no climb verdict, so you decide where they live: **keep in
  iCloud** (hot) or **cold storage** (the 5 TB mirror stays as the copy; the
  video leaves iCloud). Byte-identical copies (same size, same first and last
  MiB) are grouped and linked to each other, and queued first, then the biggest.
  The `.MOV` half of a Live Photo (a photo of the same name beside it) is part of
  the photo and is not listed.

The stage is sized to each clip's own shape, so portrait phone video fills its box
with no black bars. Phone clips are sometimes sideways: the browser honours the
rotation metadata (Safari on the Mac does; `.MOV`/HEVC may not play elsewhere),
and **R** rotates by hand if one still lands wrong. The look is
[brignano/design](https://github.com/brignano/design): the tokens and Geist are
the dashboard's own files (`docker/dashboard/assets/`, mounted read-only), so
`scripts/update-design-tokens.sh` updates both.

| Key | Does |
|-----|------|
| S / F / N | climbing clip: send / fall / none, appended to `/root/climb-judge/labels.csv`, the format `climb-judge.sh --labels` reads |
| X | climbing clip: **not climbing** (the cat, a pet, a trip video). Recorded in `review-log.csv` only, never in `labels.csv`; the clip then joins *Other videos* and takes keep / cold storage |
| K / C | other video: keep in iCloud / cold storage, appended to `video-review.csv` |
| U | unsure: recorded only (`review-log.csv` for clips, `video-review.csv` for videos) |
| ← → (or J / K on a climbing clip) | next / previous |
| Space | play / pause |

The verdicts CSV may carry a bare file name; the page finds it under `YYYY/MM/` in the mirror.

Only videos already on the drive are shown; the header says how many verdicts are still waiting for the sync to download them.

The queue shows clips still to decide first, `none` and low confidence at the
top; unsure clips come back. Month filter and "hide reviewed" are in the header.

## Removal

The page never deletes anything. The header links export the **candidates**. For climbing clips:
clips you labelled fall or none that are confirmed present in the mirror
(`+ unreviewed Claude calls` adds Claude's **fall** calls at confidence ≥ 0.8 you
did not review; Claude's `none` is never a candidate on its own, since it also
means "not climbing"). To act on them, on the Mac:

```bash
uv run tools/photos/to-album.py --url https://review.$HOMELAB_DOMAIN
```

It finds those clips in Photos by file name and adds them to one album, "Climb
clips to delete" (favourites skipped, `--dry-run` to preview). Then in Photos:
open the album, Cmd-A, Delete. For the other list, `to-album.py --set cold` builds the album "Videos to move to
cold storage" from the videos you marked cold (`/export.csv?set=cold`).
Photos is where deletion has to happen
(`docs/design/tsd-photo-library.md`).

## Deploy

`cd docker/clipreview && docker compose up -d --build`. The mirror mount refuses
to start without `/media/photos/icloud`. The proxy stack needs a Caddy reload
for the new route (`/deploy` covers it; a Caddy *recreate* stays confirm-first).

## Duplicate photos (`/dupes`)

A second tab for `tools/photos/duplicates-to-album.py`. The Mac exports sets
(a keeper plus its extras) and a thumbnail per photo to `/data/dupes/`; the page
adds whether each original is in the read-only mirror (same file name in the
photo's month, same byte size), records your decision per set in
`/data/dupes/decisions.csv`, and serves the accepted removals at
`/api/dupes/accepted.json` for `--from-review` on the Mac. A same-moment shot
(a different file) is only accepted if its original is confirmed in the mirror;
exact copies are not held back. Nothing here deletes or writes to the mirror.

**Confidence and agreement.** Each set is rated high / fairly sure / check this one
(`assess()` in `app/dupes.py`): exact copies and your favourites are high; for
same-moment shots it is the gap between the keeper's Apple score and the runner-up's
(>= 0.10 high, >= 0.03 fairly sure, else check). The card says why. *Accept high
confidence* accepts every undecided high set (similar shots only if all originals are
in the mirror). The line under the header reports how often you agreed with the
proposed keeper, per tier, so the thresholds can be tuned from your own decisions
instead of guessed. Nothing is deleted by any of this.
