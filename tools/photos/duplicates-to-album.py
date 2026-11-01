# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70", "photoscript"]
# ///
"""Find duplicate photos, review them on the homelab review page, and put the
ones you accepted into one Photos album.

Run on the Mac whose Photos app holds the library. Three steps:

  1. uv run tools/photos/duplicates-to-album.py --export https://review.YOUR_DOMAIN
       Sends the sets to the review page (the keeper and the extras of each,
       with a thumbnail of every photo). Nothing in Photos changes.

  2. Open https://review.YOUR_DOMAIN/dupes and go through the sets. Enter
     accepts "remove the others", 1-9 picks a different keeper, K keeps them all.
     A same-moment shot (a different file, not a copy) is only accepted if its
     original is confirmed in the iCloud mirror on the 5 TB drive.

  3. uv run tools/photos/duplicates-to-album.py --from-review https://review.YOUR_DOMAIN
       Adds what you accepted to the album "Duplicates to delete". Then in
       Photos: open the album, Cmd-A, Delete.

Without the review page:

  uv run tools/photos/duplicates-to-album.py --exact-only [--dry-run --csv FILE]
       Adds only exact extra copies straight to the album (no review needed: the
       keeper is the same picture). Same-moment shots are never added this way.

This only creates an album and adds to it. It never deletes a photo, empties the
album or touches iCloud. Re-running adds only what is not there yet. Never
included: favourites, photos in an album you made, photos with a named person,
shared-album photos (--include-albums / --include-people loosen the middle two).
A photo that became a favourite after the export is skipped at step 3.

    --similar-seconds N   window for "same moment" shots (default 20)
    --look-distance N     a same-moment shot must also LOOK like the keeper:
                          picture fingerprints at most N of 64 bits apart
                          (default 10; --look-distance -1 turns the check off)
    --sweep               print how many sets/photos each window gives, with
                          and without the look-alike check; change nothing
    --max-sets N          export only the N sets with the most extras
    --album NAME          album to fill (default "Duplicates to delete")
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dupes import dhash_from_bmp, find_dupes, hamming  # noqa: E402


def http(url, data=None, method=None, ctype="application/json"):
    """curl when there is one: on macOS it runs under the terminal's Local Network
    permission, which a uv-managed Python may not have ("No route to host")."""
    if shutil.which("curl"):
        cmd = ["curl", "-fsS", "--max-time", "120", "-X", method or ("POST" if data else "GET")]
        if data is not None:
            cmd += ["-H", "Content-Type: " + ctype, "--data-binary", "@-"]
        r = subprocess.run(cmd + [url], input=data, capture_output=True)
        if r.returncode != 0:
            raise SystemExit("request to %s failed: %s" % (url, r.stderr.decode().strip()))
        return r.stdout
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


CACHE = Path.home() / ".cache" / "homelab-photos-dhash.json"


class Looks:
    """Picture fingerprints (64-bit difference hash) of a photo's preview, made
    with macOS' sips and cached by uuid, so a re-run is fast."""

    def __init__(self, max_distance):
        self.max = max_distance
        try:
            self.cache = {k: int(v, 16) for k, v in json.loads(CACHE.read_text()).items()}
        except (OSError, ValueError):
            self.cache = {}
        self.new = 0

    def hash(self, photo):
        if photo.uuid in self.cache:
            return self.cache[photo.uuid]
        h = None
        srcs = [Path(p) for p in (getattr(photo, "path_derivatives", None) or [])]
        if photo.path:
            srcs.append(Path(photo.path))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "h.bmp"
            for src in srcs:
                if src.exists():
                    try:
                        r = subprocess.run(["sips", "-s", "format", "bmp", "-z", "8", "9", str(src), "--out", str(out)],
                                           capture_output=True, timeout=30)
                    except subprocess.TimeoutExpired:
                        continue
                    if r.returncode == 0 and out.exists():
                        try:
                            h = dhash_from_bmp(out.read_bytes())
                            break
                        except ValueError:
                            pass
        self.cache[photo.uuid] = h
        self.new += 1
        return h

    def same(self, a, b):
        ha, hb = self.hash(a), self.hash(b)
        if ha is None or hb is None:  # no preview to compare: do not guess
            return False
        return hamming(ha, hb) <= self.max

    def save(self):
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({k: format(v, "x") for k, v in self.cache.items() if v is not None}))


def thumbnail(photo, out: Path) -> bool:
    """A small JPEG via macOS' own sips: no extra dependency, and HEIC is fine."""
    srcs = [Path(p) for p in (getattr(photo, "path_derivatives", None) or [])]
    if photo.path:
        srcs.append(Path(photo.path))
    for src in srcs:
        if src.exists():
            try:
                r = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "800", str(src), "--out", str(out)],
                                   capture_output=True, timeout=30)
            except subprocess.TimeoutExpired:
                continue  # a preview that will not convert must not stall the whole export
            if r.returncode == 0 and out.exists():
                return True
    return False


def build_sets(extras, keeper_of):
    sets = {}
    for u, (p, reasons) in extras.items():
        kind = "exact" if "duplicates" in reasons else "similar"
        k = keeper_of[u]
        sid = k.uuid + ("-1" if kind == "exact" else "-2")
        s = sets.setdefault(sid, {"id": sid, "kind": kind, "photos": {k.uuid: (k, True)}})
        s["photos"][p.uuid] = (p, False)
    return sets


SUB_SCORES = ("sharply_focused_subject", "well_timed_shot", "noise", "failure",
              "pleasant_composition", "well_framed_subject", "pleasant_lighting")


def member(p, keep):
    sub = {k: getattr(p.score, k) for k in SUB_SCORES
           if p.score is not None and isinstance(getattr(p.score, k, None), (int, float))}
    return {"uuid": p.uuid, "filename": p.original_filename, "date": p.date.isoformat(),
            "bytes": p.original_filesize or 0, "keep": keep, "favorite": bool(p.favorite), "sub": sub,
            "score": p.score.overall if p.score and p.score.overall is not None else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", metavar="URL", help="send the sets and thumbnails to the review page")
    ap.add_argument("--from-review", metavar="URL", help="add what you accepted on the review page to the album")
    ap.add_argument("--exact-only", action="store_true", help="no review page: add exact extra copies only")
    ap.add_argument("--album", default="Duplicates to delete")
    ap.add_argument("--similar-seconds", type=float, default=20.0)
    ap.add_argument("--look-distance", type=int, default=10)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--max-sets", type=int, default=0)
    ap.add_argument("--include-albums", action="store_true")
    ap.add_argument("--include-people", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not (args.export or args.from_review or args.exact_only or args.sweep):
        ap.error("choose one of --export URL, --from-review URL, --exact-only or --sweep")

    import osxphotos
    print("Reading the Photos library (a few minutes for a big one)...", file=sys.stderr)
    db = osxphotos.PhotosDB()

    if args.from_review:
        res = json.loads(http(args.from_review.rstrip("/") + "/api/dupes/accepted.json"))
        uuids, skipped, gone = [], 0, 0
        for u in res["uuids"]:
            p = db.get_photo(u)
            if p is None:
                print("not found in Photos: %s" % u)
            elif p.intrash:
                gone += 1   # already deleted (Recently Deleted): nothing to add, and Photos rejects its id
            elif p.favorite:
                print("skipped (favourite now): %s" % p.original_filename)
            else:
                uuids.append(u)
        for s in res["skipped"]:
            skipped += 1
            print("held back: %s (%s)" % (s["filename"], s["reason"]))
        print("%d accepted, %d held back, %d sets kept whole" % (len(uuids), skipped, res["kept_all_sets"]))
        if gone:
            print("%d accepted photos are already in Recently Deleted; left alone" % gone)
        return to_album(uuids, args)

    photos = [p for p in db.photos() if not p.shared and not getattr(p, "syndicated", False)]

    def protected(p):
        if p.favorite:
            return "favourite"
        if not args.include_albums and p.albums:
            return "in an album"
        if not args.include_people and [n for n in p.persons if n and n != "_UNKNOWN_"]:
            return "has a named person"
        return None

    looks = Looks(args.look_distance) if args.look_distance >= 0 else None
    if args.sweep:
        lk = looks or Looks(10)
        print("%-9s %-8s %8s %8s" % ("window", "look", "sets", "photos"))
        for secs in (2, 5, 10, 20):
            for use in (False, True):
                ex, _, kp = find_dupes(photos, protected, secs, lk.same if use else None)
                print("%-9s %-8s %8d %8d" % ("%ds" % secs, "yes" if use else "no", len(build_sets(ex, kp)), len(ex)),
                      flush=True)
        lk.save()
        print("Nothing was sent or changed.")
        return 0
    seconds = 0 if args.exact_only else args.similar_seconds
    if looks and seconds:
        print("Comparing pictures of same-moment shots (first run takes a while)...", file=sys.stderr)
    extras, keep_of, keeper_of = find_dupes(photos, protected, seconds, looks.same if looks and seconds else None)
    if looks:
        looks.save()
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

    if args.export:
        base = args.export.rstrip("/")
        sets = build_sets(extras, keeper_of)
        if args.max_sets and len(sets) > args.max_sets:
            keep = sorted(sets, key=lambda i: len(sets[i]["photos"]), reverse=True)[:args.max_sets]
            print("%d sets found; sending the %d with the most photos" % (len(sets), args.max_sets))
            sets = {i: sets[i] for i in keep}
        doc = {"sets": [{"id": s["id"], "kind": s["kind"],
                         "members": [member(p, k) for p, k in s["photos"].values()]}
                        for s in sets.values()]}
        print("%d sets, %d photos to review" % (len(sets), sum(len(v["photos"]) - 1 for v in sets.values())))
        if args.dry_run:
            print("Dry run: nothing sent.")
            return 0
        res = json.loads(http(base + "/api/dupes/sets", json.dumps(doc).encode(), "POST"))
        by_uuid = {p.uuid: p for s in sets.values() for p, _ in s["photos"].values()}
        need = res["need_thumbs"]
        print("sending %d thumbnails..." % len(need))
        failed = 0
        with tempfile.TemporaryDirectory() as td:
            for n, u in enumerate(need, 1):
                out = Path(td) / (u + ".jpg")
                print("  %d/%d %s" % (n, len(need), by_uuid[u].original_filename), flush=True)
                if thumbnail(by_uuid[u], out):
                    try:
                        http(base + "/api/dupes/thumb/" + u, out.read_bytes(), "PUT", "image/jpeg")
                    except SystemExit as e:  # one bad upload must not end the run
                        failed += 1
                        print("    upload failed (%s)" % e)
                else:
                    print("    no local preview (shows as 'no thumbnail')")
        if failed:
            print("%d uploads failed: run the same command again to send just those." % failed)
        print("Done. Review at %s/dupes, then run --from-review %s" % (base, base))
        return 0

    # --exact-only: straight to the album.
    exact = [u for u, (_p, rs) in extras.items() if "duplicates" in rs]
    print("%d are exact copies" % len(exact))
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0
    return to_album(exact, args)


def to_album(uuids, args) -> int:
    if not uuids:
        print("Nothing to add.")
        return 0
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0
    import photoscript
    lib = photoscript.PhotosLibrary()
    album = lib.album(args.album) or lib.create_album(args.album)
    have = {p.uuid for p in album.photos()}
    new = [u for u in uuids if u not in have]
    for i in range(0, len(new), 200):
        album.add(lib.photos(uuid=new[i:i + 200]))
    print("album '%s': added %d, already there %d. Open it in Photos, look it over, Cmd-A, Delete."
          % (args.album, len(new), len(uuids) - len(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
