# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70", "photoscript"]
# ///
"""Put duplicate photos (all but the best of each set) into one Photos album.

Run on the Mac whose Photos app holds the library:

    uv run tools/photos/duplicates-to-album.py --dry-run --csv ~/Desktop/dupes.csv
    uv run tools/photos/duplicates-to-album.py

It finds the same candidates as estimate.py's "extra copies" and "same moment,
several shots" rules (tools/photos/dupes.py), and adds each one that is not the
keeper to an album called "Duplicates to delete". Then in Photos: open the
album, look it over, Cmd-A, Delete.

It only creates an album and adds to it. It never deletes a photo, empties the
album or touches iCloud, so the worst a wrong match costs is a photo you drag
out of the album. Re-running adds only what is not there yet.

Never added: favourites, photos in an album you made, photos with a named
person, shared-album photos (--include-albums / --include-people loosen the
middle two). The keeper of each set is never added.

    --dry-run          print what would be added; change nothing
    --csv FILE         write every candidate, the reason and the shot it keeps
    --similar-seconds  window for "same moment" shots (default 20, 0 = exact
                       copies only)
    --album NAME       album to fill (default "Duplicates to delete")
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dupes import find_dupes  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--album", default="Duplicates to delete")
    ap.add_argument("--similar-seconds", type=float, default=20.0)
    ap.add_argument("--include-albums", action="store_true")
    ap.add_argument("--include-people", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import osxphotos
    print("Reading the Photos library (a few minutes for a big one)...", file=sys.stderr)
    db = osxphotos.PhotosDB()
    photos = [p for p in db.photos() if not p.shared and not getattr(p, "syndicated", False)]

    def protected(p):
        if p.favorite:
            return "favourite"
        if not args.include_albums and p.albums:
            return "in an album"
        if not args.include_people and [n for n in p.persons if n and n != "_UNKNOWN_"]:
            return "has a named person"
        return None

    extras, keep_of = find_dupes(photos, protected, args.similar_seconds)
    size = sum(p.original_filesize or 0 for p, _ in extras.values())
    print("%d photos to remove, %.1f GB by osxphotos' count" % (len(extras), size / 1e9))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["uuid", "filename", "date", "bytes", "reasons", "keeps_instead"])
            for u, (p, rs) in sorted(extras.items(), key=lambda kv: kv[1][0].date):
                w.writerow([u, p.original_filename, p.date.isoformat(),
                            p.original_filesize or 0, " ".join(rs), keep_of.get(u, "")])
        print("Candidates written to %s" % args.csv)
    if args.dry_run or not extras:
        print("Dry run: nothing changed." if args.dry_run else "Nothing to add.")
        return 0

    import photoscript
    lib = photoscript.PhotosLibrary()
    album = lib.album(args.album) or lib.create_album(args.album)
    have = {p.uuid for p in album.photos()}
    new = [u for u in extras if u not in have]
    for i in range(0, len(new), 200):
        album.add(lib.photos(uuid=new[i:i + 200]))
    print("album '%s': added %d, already there %d. Open it in Photos, look it over, Cmd-A, Delete."
          % (args.album, len(new), len(extras) - len(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
