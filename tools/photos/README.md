# Photo cleanup tools (run on the Mac)

These run on the Mac whose Photos app holds the library, never on the homelab:
deletions have to happen in Photos, so the decisions are made there
(`docs/design/tsd-photo-library.md`, Half 2).

## `estimate.py`: how much would cleaning up save?

Read-only. It changes nothing in Photos and deletes nothing. It reads what
Photos already knows about every photo and prints how much space each kind
of junk takes:

- screenshots and screen recordings older than 3 months
- burst frames you never picked
- extra copies of the same photo
- several shots of the same moment (all within 20 seconds of the first):
  all but the best one, picked by Photos' own score, or your favourite if
  you marked one
- receipts, documents and photos of screens, from Photos' own labels
- the lowest-scored 10% of photos, by Photos' own quality score
- optionally, old videos that aren't favourites (`--video-years 3`)

It also breaks down where the video space goes: 4K, 4K at 60 fps, clips
Photos labelled as climbing or bouldering, and roughly what keeping the 4K
clips at 1080p would save. Those are information, not candidates: whether a
clip is a send or a fall isn't something Photos records.

Favourites, photos in an album you made, photos with a named person and
shared-album photos are never counted.

### Run it

1. Give Terminal access to the library: System Settings → Privacy & Security
   → Full Disk Access → turn on Terminal. (Turn it off again afterwards if you
   like.)
2. Check what iCloud says Photos uses: Settings → your name → iCloud. Use
   that number, in GB, in place of YOUR_GB below (not a guess: a wrong figure
   scales every size in the report).
3. In Terminal, from a checkout of this repo:
   ```bash
   brew install uv            # once
   uv run tools/photos/estimate.py --icloud-used-gb YOUR_GB --csv ~/Desktop/photo-candidates.csv
   ```
   Without Homebrew: `python3 -m pip install --user osxphotos`, then
   `python3 tools/photos/estimate.py --icloud-used-gb YOUR_GB`.

Add `--videos-csv ~/Desktop/videos.csv` for every video, largest first, with
its resolution, frame rate and length: a quick way to find the 4K clips worth
trimming or compiling.

A big library takes a few minutes to read. The report ends with what would
be left and which iCloud plan that fits. The CSV lists every candidate with
its reasons (and, for a similar shot, which one it would keep), so you can spot-check what a rule would catch before trusting it.

Tune it with `--similar-seconds`, `--low-quality-percent`, `--screenshot-months`, `--video-years`,
`--include-albums` and `--include-people`. `--help` lists them all.

## `duplicates-to-album.py`: review and remove duplicates in Photos

Uses the same detection as `estimate.py` (`dupes.py`): exact extra copies
(keeps the largest) and shots within 20 seconds of each other (keeps your
favourite, else Photos' best score). Reviewed on the homelab review page, so
you are not hunting through Photos:

```bash
uv run tools/photos/duplicates-to-album.py --export https://review.YOUR_DOMAIN       # 1. send sets + thumbnails
# 2. https://review.YOUR_DOMAIN/dupes : Enter accepts, 1-9 picks another keeper, K keeps all
uv run tools/photos/duplicates-to-album.py --from-review https://review.YOUR_DOMAIN  # 3. fill the album
```

Then open the "Duplicates to delete" album in Photos, Cmd-A, Delete. Nothing
here deletes. Exact copies are the same picture as the keeper, so they need no
backup. A same-moment shot is a *different file*, so the review page only lets
you accept it when its original is confirmed in the iCloud mirror (matched by
file name, month and exact size). That mirror is the 5 TB drive and is not
backed up offsite (decided 2026-10-31), so it is the only second copy:
photos deleted from Photos stay recoverable for 30 days in Recently Deleted.

Same-moment shots must also *look alike*: each preview is reduced to a 64-bit
picture fingerprint (made with macOS' `sips`, cached in
`~/.cache/homelab-photos-dhash.json`), and a shot only joins a set if it is within
`--look-distance` (default 10) bits of the keeper. Photos has no exact duplicates
in most libraries, so time alone produced thousands of sets (a different pose seconds
later looked the same to it). Before a real run, `--sweep` prints how many sets each
window gives with and without the look check, and `--max-sets N` sends only the N sets
with the most photos. Requests go through `curl` when present, which uses the terminal's
Local Network permission (a uv Python can be blocked on macOS).

RAW files (NEF, CR2, DNG and so on) are never proposed for removal, and a RAW and its
same-name JPEG are not treated as duplicates of each other.

Without the page: `--exact-only [--dry-run --csv FILE]` adds only exact copies
straight to the album. Favourites, photos in your albums, photos with a named
person and shared-album photos are never included. Logic test:
`python tools/photos/test_dupes.py`; the page and its API are covered by
`docker/clipreview/tests/smoke.py`.

## `climb_judge.py`: did this climbing clip end in a send or a fall?

Runs on CT 100 against the mirror, not on the Mac. Read-only: it writes one
CSV of verdicts and changes nothing in iCloud or Photos. It compares two ways
of telling, so you can see which one to trust before it judges every clip:

- **local**: body tracking (MediaPipe) on the box. Free and private. A send
  is a pause at the high point, hands up, before any drop; a fall is a fast
  drop with no pause. Shaky handheld clips are hard on it.
- **claude**: about 12 frames per clip, with timestamps, sent to the Claude
  API (`claude-opus-5-5`, about $0.02 a clip). More context, but the frames
  leave the house.

### Test it on clips you have labelled

1. Pick ~20 climbing clips that are already in the mirror (the backfill
   starts with the newest), about half sends and half falls. In Photos,
   ⌘I on a clip shows its file name, e.g. `IMG_5069.MOV`.
2. On CT 100, write them to `/root/climb-judge/labels.csv`:
   ```
   file,label
   IMG_5069,send
   IMG_5102,fall
   ```
   (`none` for a clip with no attempt in it.)
3. Run both methods:
   ```bash
   export ANTHROPIC_API_KEY=...        # for the claude half only
   cd /root/homelab && tools/photos/climb-judge.sh --labels /out/labels.csv
   ```
   Leave out the key and add `--methods local` to try the local half alone.

It prints a line per clip, then a score per method: how many it got right
and, the number that matters, how many sends it called falls (those are the
clips that would be offered for removal). Every verdict, with the reason, is
in `/root/climb-judge/climb-verdicts.csv`.

The first run downloads its Python packages (~300 MB, cached in
`/root/climb-judge/cache`). The container is limited to two cores and 3 GB.

## `triage.py`: sort every video before you review any

Most videos in the mirror are not climbing, and reviewing thousands by hand
does not work. `triage.py` looks at four frames of each video (plus its length)
with a cheap model (`claude-haiku-5-5`, set with `--model`) and writes
`/root/climb-judge/video-triage.csv`: a category (climbing, pet, people,
travel, event, screen, other), how much the clip matters (1 to 5), a
suggestion and a confidence.

- **climbing** is suggested for the Opus send/fall pass (`climb_judge.py --list`
  with the page's "looks like climbing" export); the triage never judges
  send or fall itself.
- everything else is suggested **keep** (stay in iCloud) or **cold** (the
  mirror is the copy that stays).

It only suggests. The review page shows each suggestion, can filter by it, and
"Accept N keep / N cold" records the suggestions at confidence 0.7 and up as
the same decisions the buttons write. Low-confidence ones are the "Needs a
look" list, which is the only part to review by hand. Nothing is deleted.

```bash
export ANTHROPIC_API_KEY=...
tools/photos/triage.sh --limit 50          # a trial; prints tokens and the real cost
tools/photos/triage.sh                     # everything not triaged yet (resumable)
```

Live Photo motion halves (`IMG_1234_HEVC.MOV`) and clips that already have a
climb-judge verdict are skipped. `python tools/photos/test_triage.py` runs the
offline tests.

## `cold-storage-report.py`: who is named, and what would go to cold storage

Read-only dry run for moving photos of people/pets you no longer want in
iCloud (exes, an ex's dog) to cold storage. Lists every name Photos knows
(hidden included) with counts, how many photos have unnamed faces, and the
cold / keep / mixed split. Mixed photos (a cold name next to anyone else) and
favourites stay in iCloud.

```bash
uv run tools/photos/cold-storage-report.py --cold Lauren --cold Cora --csv cold-report.csv
```

Add `--check https://review.home.brignano.io` to confirm each cold photo's
original is in the mirror (the hidden mirror counts), `--save-manifest` to store
the confirmed photos' people/albums/dates on the drive (`/root/climb-judge/cold/`),
and `--album "Cold storage"` to add only the confirmed ones to a Photos album.
It never deletes anything; you delete from the album in Photos.

## Building the Photos albums without Photos jumping to the front

Adding to an album has to go through the Photos app, so it launches. The
scripts now start Photos in the background (`open -g -j`) and hide it again
when done (`photos_bg.py`); macOS may still raise a window, so this is best
effort. To build every review-page album in one run, so Photos starts once:

```bash
tools/photos/albums.sh --dry-run    # preview, changes nothing
tools/photos/albums.sh              # climb candidates + cold storage
```
