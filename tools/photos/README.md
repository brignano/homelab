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
- several shots of the same moment (taken within 20 seconds of each other):
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
   that number, in GB, below.
3. In Terminal, from a checkout of this repo:
   ```bash
   brew install uv            # once
   uv run tools/photos/estimate.py --icloud-used-gb 1500 --csv ~/Desktop/photo-candidates.csv
   ```
   Without Homebrew: `python3 -m pip install --user osxphotos`, then
   `python3 tools/photos/estimate.py --icloud-used-gb 1500`.

Add `--videos-csv ~/Desktop/videos.csv` for every video, largest first, with
its resolution, frame rate and length: a quick way to find the 4K clips worth
trimming or compiling.

A big library takes a few minutes to read. The report ends with what would
be left and which iCloud plan that fits. The CSV lists every candidate with
its reasons (and, for a similar shot, which one it would keep), so you can spot-check what a rule would catch before trusting it.

Tune it with `--similar-seconds`, `--low-quality-percent`, `--screenshot-months`, `--video-years`,
`--include-albums` and `--include-people`. `--help` lists them all.
