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
     Unnamed faces (strangers, background) do not make a photo mixed unless
     --unnamed-blocks is given. --ignore NAME (default: Anthony Brignano)
     never counts as "another person". Typed names match "Lauren" to
     "Lauren <emoji>".
  4. the same for hidden photos and photos already in Recently Deleted
     (counted separately, never in cold).
"""
import argparse
import csv
from collections import Counter

import osxphotos


def resolve(wanted, known):
    """Match typed names to Photos names: exact, or the typed name followed by
    a space (so "Lauren" finds "Lauren \U0001f970")."""
    out = set()
    for w in wanted:
        hits = {k for k in known if k == w or k.startswith(w + " ")}
        out |= hits
    return out


def classify(photo, cold, ignore, unnamed_blocks):
    names = set(photo.persons or [])
    named = {n for n in names if n and n != "_UNKNOWN_"}
    unnamed = "_UNKNOWN_" in names
    cold_hit = named & cold
    if not cold_hit:
        return "keep", named, unnamed
    if photo.favorite:
        return "keep-favourite", named, unnamed
    if (named - cold - ignore) or (unnamed and unnamed_blocks):
        return "mixed", named, unnamed
    return "cold", named, unnamed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cold", action="append", default=[], help="name in Photos to move to cold storage (repeatable)")
    ap.add_argument("--ignore", action="append", default=["Anthony Brignano"],
                    help="name that never makes a photo 'mixed' (you; default Anthony Brignano)")
    ap.add_argument("--unnamed-blocks", action="store_true",
                    help="a photo with an unnamed face next to a cold name counts as mixed")
    ap.add_argument("--csv", help="write one row per cold/mixed photo to this file")
    args = ap.parse_args()
    db = osxphotos.PhotosDB()
    photos = db.photos(intrash=False) + db.photos(intrash=True)
    known = {n for p in photos for n in (p.persons or [])}
    cold = resolve(args.cold, known)
    ignore = resolve(args.ignore, known)
    print("Cold names resolved to: %s" % ", ".join(sorted(cold)))
    print("Ignored in mixed check: %s\n" % ", ".join(sorted(ignore)))

    counts = Counter()
    for p in photos:
        for n in set(p.persons or []):
            counts[n] += 1
    print("People and pets Photos knows (photos each, incl. hidden):")
    for n, c in counts.most_common():
        tag = "  <- cold" if n in cold else ""
        print("  %-30s %6d%s" % (n, c, tag))
    for w in args.cold:
        if not resolve([w], known):
            print("  WARNING: no name matches %r (misspelled, or unnamed in Photos?)" % w)

    result = Counter()
    rows = []
    unnamed_photos = 0
    for p in photos:
        kind, named, unnamed = classify(p, cold, ignore, args.unnamed_blocks)
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
