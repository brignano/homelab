# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70"]
# ///
"""Put the clip review page's removal candidates into one Photos album.

Run on the Mac whose Photos app holds the library:

    uv run tools/photos/to-album.py --url https://review.YOUR_DOMAIN

It downloads the candidate list from the review page (the "csv" link: clips you
labelled fall or none, present in the mirror), finds each one in the Photos
library by its original file name, and adds them to an album called
"Climb clips to delete". Then in Photos: open the album, Cmd-A, Delete.

It only creates an album and adds to it. It never deletes a photo, empties the
album or touches iCloud, so the worst a wrong match costs is a clip you remove
from the album. Re-running adds only what is not there yet. Favourites are
skipped, and a candidate Photos does not have is listed, not guessed at.

    --set cold         the other list: videos you marked "cold storage" (they stay
                       on the 5 TB mirror; delete them from iCloud via the album)
    --include-claude   also add unreviewed clips Claude called fall/none with
                       high confidence (the review page's "+ unreviewed" csv)
    --dry-run          print what would be added; change nothing
"""
import argparse
import csv
import io
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="base URL of the review page")
    ap.add_argument("--set", choices=("climb", "cold"), default="climb",
                    help="climb: clips labelled fall/none; cold: other videos you marked cold storage")
    ap.add_argument("--album", help="default: 'Climb clips to delete' or 'Videos to move to cold storage'")
    ap.add_argument("--include-claude", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.album:
        args.album = "Videos to move to cold storage" if args.set == "cold" else "Climb clips to delete"
    query = "?set=cold" if args.set == "cold" else ("?include=claude" if args.include_claude else "")
    url = args.url.rstrip("/") + "/export.csv" + query
    with urllib.request.urlopen(url, timeout=30) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode())))
    stems = {Path(r["file"]).stem: r["file"] for r in rows}
    print("%d candidates from %s" % (len(stems), url))

    import osxphotos
    db = osxphotos.PhotosDB()
    found, taken = {}, set()
    for p in db.photos(movies=True, images=False):
        orig = Path(p.original_filename or "").stem
        if not orig:
            continue
        for stem in stems:
            if stem == orig or stem.startswith(orig + "_"):
                if p.favorite:
                    print("skipped (favourite): %s" % stem)
                    taken.add(stem)
                else:
                    found.setdefault(stem, p.uuid)
                    taken.add(stem)
    for stem in stems:
        if stem not in taken:
            print("not found in Photos: %s" % stems[stem])
    print("%d matched in Photos" % len(found))
    if args.dry_run or not found:
        return 0

    import photoscript
    lib = photoscript.PhotosLibrary()
    album = lib.album(args.album) or lib.create_album(args.album)
    have = {p.uuid for p in album.photos()}
    new = [u for u in found.values() if u not in have]
    if new:
        album.add(lib.photos(uuid=new))
    print("album '%s': added %d, already there %d. Open it in Photos, Cmd-A, Delete."
          % (args.album, len(new), len(found) - len(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
