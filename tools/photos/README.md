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
- receipts, documents and photos of screens, from Photos' own labels
- the lowest-scored 10% of photos, by Photos' own quality score
- optionally, old videos that aren't favourites (`--video-years 3`)

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

A big library takes a few minutes to read. The report ends with what would
be left and which iCloud plan that fits. The CSV lists every candidate with
its reasons, so you can spot-check what a rule would catch before trusting it.

Tune it with `--low-quality-percent`, `--screenshot-months`, `--video-years`,
`--include-albums` and `--include-people`. `--help` lists them all.
