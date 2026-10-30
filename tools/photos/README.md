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
