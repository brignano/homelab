# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70"]
# ///
"""How much of the Photos library is junk, before anything is touched.

Run on the Mac whose Photos app holds the library (docs/design/tsd-photo-library.md,
Phase 2, "dry-run report"):

    uv run tools/photos/estimate.py --icloud-used-gb YOUR_GB
    uv run tools/photos/estimate.py --csv ~/Desktop/photo-candidates.csv

Read-only. It opens a copy of the library's database through osxphotos, reads
what Photos has already worked out about every photo (screenshot or not,
which burst frame you kept, its quality score, its labels) and prints how much
each kind of junk takes up. It creates no album, changes no photo and deletes
nothing. Deciding comes later, from these numbers.

Never suggested, whatever the rules say: favourites, photos in an album you
made, photos with a named person in them, and shared-album photos (those do
not count against your iCloud storage anyway). --include-albums and
--include-people loosen the first two of those.

About the sizes: osxphotos knows each original's size, but not the video half
of a Live Photo, so its total is lower than what iCloud reports. Pass
--icloud-used-gb (Settings, your name, iCloud) and each category is also shown
as its share of that number, which is the figure that decides the plan.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict

from dupes import find_dupes

# Photos' own labels, lower-cased, matched exactly. These are the "random"
# photos: pictures of paper, receipts and screens, taken to remember something
# once. Exact rather than substring, so "text" does not catch "textile".
DOC_LABELS = {"document", "receipt", "text", "whiteboard", "handwriting",
              "paper", "screenshot", "menu", "poster", "computer monitor",
              "monitor", "screen", "computer"}

# iCloud+ plans (US, per month). The library must fit under a tier to use it.
TIERS = [(50, 0.99), (200, 2.99), (2000, 9.99), (6000, 29.99), (12000, 59.99)]

CATEGORIES = [
    ("screenshots", "Screenshots and screen recordings"),
    ("bursts", "Burst frames you never picked"),
    ("duplicates", "Extra copies of the same photo"),
    ("similar", "Same moment, several shots: all but the best"),
    ("documents", "Receipts, documents, screens (by Photos' labels)"),
    ("low_quality", "Lowest-scored photos (by Photos' own score)"),
    ("old_videos", "Videos, not favourites, older than --video-years"),
]


def gb(n: float) -> str:
    return "%.1f GB" % (n / 1e9)


def tier_for(used_gb: float) -> tuple[int, float] | None:
    for size, price in TIERS:
        if used_gb <= size:
            return size, price
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help="path to a .photoslibrary (default: the one Photos last opened)")
    ap.add_argument("--icloud-used-gb", type=float,
                    help="what iCloud says Photos uses, in GB, to scale the estimate "
                         "(a total far above the library is treated as the whole account)")
    ap.add_argument("--low-quality-percent", type=float, default=10.0,
                    help="treat the lowest-scored N%% of photos as low quality (default 10)")
    ap.add_argument("--screenshot-months", type=int, default=3,
                    help="only count screenshots older than this many months (default 3)")
    ap.add_argument("--video-years", type=int, default=0,
                    help="also count non-favourite videos older than N years (default 0 = off)")
    ap.add_argument("--similar-seconds", type=float, default=20.0,
                    help="photos taken within N seconds of a group's first shot join it; "
                         "the best of each group is kept (default 20, 0 = off)")
    ap.add_argument("--videos-csv", help="write every video, largest first, with resolution, fps and length")
    ap.add_argument("--include-albums", action="store_true",
                    help="allow photos that are in one of your albums")
    ap.add_argument("--include-people", action="store_true",
                    help="allow photos with a named person in them")
    ap.add_argument("--csv", help="write every candidate with its reasons to this file")
    args = ap.parse_args(argv)

    try:
        import osxphotos
    except ImportError:
        print("osxphotos is not installed. Run this with:  uv run tools/photos/estimate.py", file=sys.stderr)
        return 2

    import datetime as dt

    print("Reading the Photos library (a few minutes for a big one)...", file=sys.stderr)
    db = osxphotos.PhotosDB(dbfile=args.library) if args.library else osxphotos.PhotosDB()
    photos = [p for p in db.photos() if not p.shared and not getattr(p, "syndicated", False)]
    if not photos:
        print("No photos found. Is this the right library?", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc)

    def age_days(p) -> float:
        d = p.date
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return (now - d).total_seconds() / 86400

    def protected(p) -> str | None:
        if p.favorite:
            return "favourite"
        if not args.include_albums and p.albums:
            return "in an album"
        if not args.include_people and [n for n in p.persons if n and n != "_UNKNOWN_"]:
            return "has a named person"
        return None

    # The score cut-off, as a percentile of this library's own photos, so it
    # does not depend on what scale Apple's scores happen to use.
    scored = sorted(p.score.overall for p in photos
                    if not p.ismovie and p.score and p.score.overall is not None)
    cut = None
    if scored and args.low_quality_percent > 0:
        idx = min(len(scored) - 1, int(len(scored) * args.low_quality_percent / 100))
        cut = scored[idx]

    reasons: dict[str, list[str]] = defaultdict(list)

    for p in photos:
        if protected(p):
            continue
        if (p.screenshot or getattr(p, "screen_recording", False)) \
                and age_days(p) > args.screenshot_months * 30:
            reasons[p.uuid].append("screenshots")
        if p.burst and not p.burst_selected and not p.burst_key:
            reasons[p.uuid].append("bursts")
        labels = [l.lower() for l in (p.labels or [])]
        if not p.ismovie and DOC_LABELS.intersection(labels):
            reasons[p.uuid].append("documents")
        if cut is not None and not p.ismovie and p.score and p.score.overall is not None \
                and p.score.overall <= cut:
            reasons[p.uuid].append("low_quality")
        if args.video_years and p.ismovie and age_days(p) > args.video_years * 365:
            reasons[p.uuid].append("old_videos")

    # Exact copies and same-moment shots: see dupes.py (shared with
    # duplicates-to-album.py). Proposes only; the CSV says which shot it keeps.
    extras, keep_of, _ = find_dupes(photos, protected, args.similar_seconds)
    for u, (_p, rs) in extras.items():
        reasons[u].extend(rs)

    by_uuid = {p.uuid: p for p in photos}
    total = sum(p.original_filesize or 0 for p in photos)
    videos = sum(p.original_filesize or 0 for p in photos if p.ismovie)
    scale = (args.icloud_used_gb * 1e9 / total) if args.icloud_used_gb and total else None
    # Live Photo videos explain a gap of a few tens of percent, not 3x. A figure
    # far above the library is the whole iCloud account (backups, iCloud Drive,
    # Messages), and scaling by it would triple every category. Keep sizes as
    # they are and carry the rest of iCloud into the plan sum unchanged.
    other = 0.0
    if scale and scale > 1.5:
        other = args.icloud_used_gb * 1e9 - total
        scale = None

    def line(label: str, count: int, size: int) -> str:
        s = "  %-54s %8d  %10s" % (label, count, gb(size))
        if scale:
            s += "   ~%s of iCloud" % gb(size * scale)
        return s

    print()
    print("Library: %d items, %s by osxphotos' count (videos %s, %.0f%%)"
          % (len(photos), gb(total), gb(videos), 100 * videos / total if total else 0))
    if scale:
        print("iCloud says %s, so sizes below are also scaled by %.2fx" % (gb(args.icloud_used_gb * 1e9), scale))
    elif other:
        print("iCloud says %s, %s more than the library: that is iCloud's other data"
              % (gb(args.icloud_used_gb * 1e9), gb(other)))
        print("(device backups, iCloud Drive, Messages), which cleaning Photos does not touch.")
        print("Settings, your name, iCloud shows the breakdown.")
    print()
    print("  %-54s %8s  %10s" % ("Category (a photo can be in several)", "items", "size"))
    for key, label in CATEGORIES:
        if key == "old_videos" and not args.video_years:
            continue
        uu = [u for u, r in reasons.items() if key in r]
        print(line(label, len(uu), sum(by_uuid[u].original_filesize or 0 for u in uu)))
    union = sum(by_uuid[u].original_filesize or 0 for u in reasons)
    print("  " + "-" * 74)
    print(line("All candidates, each counted once", len(reasons), union))

    # Videos: where the space actually is. Information, not candidates:
    # whether a clip is a send or a fall is not something Photos records.
    vids = [p for p in photos if p.ismovie]
    if vids:
        def res(p):
            return max(p.width or 0, p.height or 0)

        def fps(p):
            e = p.exif_info
            return (e.fps or 0) if e else 0

        def is_climb(p):
            return any(k in l.lower() for l in (p.labels or [])
                       for k in ("climb", "boulder"))

        uhd = [p for p in vids if res(p) >= 3840]
        uhd60 = [p for p in uhd if fps(p) >= 50]
        climb = [p for p in vids if is_climb(p)]
        print()
        print("  %-54s %8s  %10s" % ("Videos (information, not counted above)", "items", "size"))
        print(line("All videos", len(vids), sum(p.original_filesize or 0 for p in vids)))
        print(line("4K", len(uhd), sum(p.original_filesize or 0 for p in uhd)))
        print(line("4K at 50-60 fps", len(uhd60), sum(p.original_filesize or 0 for p in uhd60)))
        print(line("Labelled climbing or bouldering by Photos", len(climb),
                   sum(p.original_filesize or 0 for p in climb)))
        # 1080p30 HEVC is roughly a fifth of 4K60, a quarter of 4K30: a rule of
        # thumb for "what if the raw footage were kept smaller", not a promise.
        shrink = sum((p.original_filesize or 0) * (0.8 if fps(p) >= 50 else 0.75) for p in uhd)
        print(line("Saved if the 4K clips were kept at 1080p (rough)", len(uhd), int(shrink)))

    left = (total - union) * (scale or 1) + other
    left_gb = left / 1e9
    print()
    print("Left after removing all of them: about %s" % gb(left))
    t = tier_for(left_gb)
    if not args.icloud_used_gb:
        print("(Pass --icloud-used-gb for the plan this would fit.)")
    elif t:
        size, price = t
        print("That fits the %s plan ($%.2f/month)." % ("%d GB" % size if size < 1000 else "%d TB" % (size // 1000), price))
        cur = tier_for(args.icloud_used_gb)
        if cur and cur[1] > price:
            print("Saving $%.2f/month, $%.2f/year, compared with now." % (cur[1] - price, 12 * (cur[1] - price)))
        else:
            print("Same plan as now: this would tidy the library, not lower the bill.")
    print()
    print("Nothing was changed. Protected and never counted: favourites%s%s, shared-album photos."
          % ("" if args.include_albums else ", photos in your albums",
             "" if args.include_people else ", photos with a named person"))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["uuid", "filename", "date", "bytes", "video", "reasons", "keeps_instead"])
            for u, r in sorted(reasons.items(), key=lambda kv: by_uuid[kv[0]].date):
                p = by_uuid[u]
                w.writerow([u, p.original_filename, p.date.isoformat(), p.original_filesize or 0,
                            int(p.ismovie), " ".join(r), keep_of.get(u, "")])
        print("Candidates written to %s" % args.csv)

    if args.videos_csv:
        with open(args.videos_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["uuid", "filename", "date", "bytes", "width", "height", "fps",
                        "seconds", "favourite", "labels"])
            for p in sorted((p for p in photos if p.ismovie),
                            key=lambda p: p.original_filesize or 0, reverse=True):
                e = p.exif_info
                w.writerow([p.uuid, p.original_filename, p.date.isoformat(),
                            p.original_filesize or 0, p.width, p.height,
                            (e.fps if e else "") or "", (e.duration if e else "") or "",
                            int(p.favorite), "; ".join(p.labels or [])])
        print("Videos written to %s" % args.videos_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
