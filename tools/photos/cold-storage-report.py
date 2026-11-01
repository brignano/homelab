# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70"]
# ///
"""Report who Photos has named, and how many photos would go to cold storage.

Read-only. Run on the Mac whose Photos app holds the library. It changes
nothing in Photos and writes only the CSV you name.

    uv run tools/photos/cold-storage-report.py --cold Lauren --cold Cora \
        [--csv cold-report.csv]

Prints:
  1. every person/pet name Photos knows, with photo counts (hidden people
     included), so you can see who is missing or still unnamed;
  2. how many photos have faces that are not named yet;
  3. how the library splits by the rules:
       cold    only --cold names are in the photo (and it is not a favourite)
       keep    a favourite of a cold person, or no cold name at all
       mixed   a cold name together with any other named person or pet
                (stays in iCloud; only reviewed if you ask)
     A photo with unnamed faces next to a cold name is counted as "mixed"
     (we cannot tell who the unnamed face is).
  4. the same for hidden photos and photos already in Recently Deleted
     (counted separately, never in cold).
"""
import argparse
import csv
from collections import Counter

import osxphotos


def classify(photo, cold):
    names = set(photo.persons or [])
    named = {n for n in names if n and n != "_UNKNOWN_"}
    unnamed = any(
        (getattr(f, "name", None) in (None, "", "_UNKNOWN_"))
        for f in (photo.face_info or [])
    )
    cold_hit = named & cold
    if not cold_hit:
        return "keep", named, unnamed
    if photo.favorite:
        return "keep-favourite", named, unnamed
    if (named - cold) or unnamed:
        return "mixed", named, unnamed
    return "cold", named, unnamed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cold", action="append", default=[], help="name in Photos to move to cold storage (repeatable)")
    ap.add_argument("--csv", help="write one row per cold/mixed photo to this file")
    args = ap.parse_args()
    cold = set(args.cold)

    db = osxphotos.PhotosDB()
    photos = db.photos(intrash=False) + db.photos(intrash=True)

    counts = Counter()
    for p in photos:
        for n in set(p.persons or []):
            counts[n] += 1
    print("People and pets Photos knows (photos each, incl. hidden):")
    for n, c in counts.most_common():
        tag = "  <- cold" if n in cold else ""
        print("  %-30s %6d%s" % (n, c, tag))
    for n in sorted(cold - set(counts)):
        print("  WARNING: no photos found for %r (misspelled, hidden, or unnamed in Photos?)" % n)

    result = Counter()
    rows = []
    unnamed_photos = 0
    for p in photos:
        kind, named, unnamed = classify(p, cold)
        if unnamed:
            unnamed_photos += 1
        where = "recently-deleted" if p.intrash else ("hidden" if p.hidden else "library")
        if where == "recently-deleted" and kind == "cold":
            kind = "cold(already deleted)"
        result[(where, kind)] += 1
        if kind in ("cold", "mixed"):
            rows.append([p.uuid, p.original_filename, p.date.isoformat() if p.date else "",
                         kind, where, ";".join(sorted(named))])

    print("\nPhotos with at least one unnamed face: %d" % unnamed_photos)
    print("\nBy rule (cold names: %s):" % (", ".join(sorted(cold)) or "none given"))
    for (where, kind), c in sorted(result.items()):
        print("  %-17s %-22s %6d" % (where, kind, c))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["uuid", "filename", "date", "kind", "where", "people"])
            w.writerows(rows)
        print("\nwrote %d rows to %s" % (len(rows), args.csv))


if __name__ == "__main__":
    main()
