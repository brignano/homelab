# /// script
# requires-python = ">=3.10"
# dependencies = ["osxphotos>=0.70"]
# ///
"""Save what Photos knows about every photo and clip, so it survives the item
leaving iCloud: who is in it, where, what Photos thinks it shows ("beach",
"dog"), albums, keywords, favourite, hidden, date.

Read-only. Run on the Mac whose Photos app holds the library. It changes
nothing in Photos or iCloud and deletes nothing.

    uv run tools/photos/metadata-export.py --url https://review.home.brignano.io
    uv run tools/photos/metadata-export.py --out ~/Desktop/photos-metadata.json   # a local copy only

It includes the Recently Deleted album: Photos keeps people, labels and albums
for those items until it purges them (about 30 days), so run this BEFORE that
window ends and never empty Recently Deleted by hand. Re-running is safe: the
server merges by photo id and never drops a record, so an item Photos has since
purged keeps the context saved earlier.

Search it at <url>/meta. Each record carries the original file name and size,
which is how the server finds the file in the iCloud mirror (the Hidden mirror
counts).
"""
import argparse
import json
import shutil
import subprocess
import sys
import urllib.request

import osxphotos

CHUNK = 1500


def media_types(p):
    """The iPhone camera roll's media types. Missing attributes (older osxphotos) count as no."""
    flags = (("screenshot", "screenshot"), ("selfie", "selfie"), ("raw", "israw"), ("live", "live_photo"),
             ("portrait", "portrait"), ("panorama", "panorama"), ("hdr", "hdr"), ("burst", "burst"),
             ("slowmo", "slow_mo"), ("timelapse", "time_lapse"), ("screenrec", "screen_recording"),
             ("cinematic", "cinematic"))
    out = []
    for name, attr in flags:
        try:
            if getattr(p, attr, False):
                out.append(name)
        except Exception:
            pass
    return out


def record(p, trashed):
    place = ""
    try:
        place = p.place.name if p.place else ""
    except Exception:
        pass
    loc = p.location if p.location and p.location[0] is not None else (None, None)
    return {
        "uuid": p.uuid,
        "filename": p.original_filename or "",
        "bytes": p.original_filesize or 0,
        "date": p.date.isoformat() if p.date else "",
        "kind": "video" if p.ismovie else "photo",
        "types": media_types(p),
        "persons": [n for n in (p.persons or []) if n and n != "_UNKNOWN_"],
        "labels": list(p.labels or []),
        "albums": list(p.albums or []),
        "keywords": list(p.keywords or []),
        "place": place or "",
        "lat": loc[0], "lon": loc[1],
        "title": p.title or "",
        "fav": bool(p.favorite), "hidden": bool(p.hidden), "trashed": trashed,
    }


def post(url, payload):
    """curl when there is one (macOS Local Network permission), else urllib."""
    data = json.dumps(payload).encode()
    if shutil.which("curl"):
        r = subprocess.run(["curl", "-fsS", "--max-time", "300", "-X", "POST", "-H",
                            "Content-Type: application/json", "--data-binary", "@-", url],
                           input=data, capture_output=True)
        if r.returncode != 0:
            raise SystemExit("request to %s failed: %s" % (url, r.stderr.decode().strip()))
        return json.loads(r.stdout)
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="review server, e.g. https://review.home.brignano.io")
    ap.add_argument("--out", help="also (or only, without --url) write everything to this JSON file")
    args = ap.parse_args()
    if not args.url and not args.out:
        ap.error("give --url, --out, or both")

    db = osxphotos.PhotosDB()
    live = db.photos(intrash=False)
    trash = db.photos(intrash=True)
    recs = {}
    for p in live:
        recs[p.uuid] = record(p, False)
    for p in trash:
        recs[p.uuid] = record(p, True)
    rows = list(recs.values())
    named = sum(1 for r in rows if r["persons"])
    print("read %d items (%d in Recently Deleted); %d have a named person, %d have labels, %d have a place"
          % (len(rows), len(trash), named, sum(1 for r in rows if r["labels"]), sum(1 for r in rows if r["place"])))

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"photos": rows}, f)
        print("wrote", args.out)
    if args.url:
        endpoint = args.url.rstrip("/") + "/api/meta/import"
        for i in range(0, len(rows), CHUNK):
            res = post(endpoint, {"photos": rows[i:i + CHUNK]})
        print("saved on the drive: %d records known (%d new on the last batch). Search: %s/meta"
              % (res["total"], res["added"], args.url.rstrip("/")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
