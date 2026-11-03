"""Photos' own knowledge about every photo and clip (who is in it, where, what
Photos thinks it shows, albums, favourites), kept on the drive so it survives
the item leaving iCloud. The Mac sends it (tools/photos/metadata-export.py);
this side stores it, searches it, and links each record to its original in the
mirror by file name and size. Nothing here touches a photo.
"""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from . import dupes

DIR = dupes.DATA / "meta"
LIB = DIR / "library.json"
MAX_BODY = 16 << 20
MAX_RECORDS = 200000
MAX_ZIP = 300
LIST_FIELDS = ("persons", "labels", "albums", "keywords")
# what the iPhone camera roll calls "Media Types": id -> label (the Mac export sends the ids)
TYPES = {"screenshot": "Screenshots", "selfie": "Selfies", "raw": "RAW", "live": "Live Photos", "portrait": "Portraits",
         "panorama": "Panoramas", "hdr": "HDR", "burst": "Bursts", "slowmo": "Slo-mo", "timelapse": "Time-lapse",
         "screenrec": "Screen recordings", "cinematic": "Cinematic"}
RAW_EXT = {".dng", ".nef", ".cr2", ".cr3", ".arw", ".raf", ".orf", ".rw2"}
VIEWABLE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
            ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4"}
PREVIEWS = Path(os.environ.get("PREVIEWS_DIR", "/photos-previews"))  # JPEGs of the HEICs (scripts/photos-previews.sh)
_cache = {"mtime": None, "rows": {}}


def _s(v, n=200):
    return str(v or "")[:n]


HIDDEN = DIR / "hidden-people.json"


def hidden_people() -> list:
    """People left out of results unless asked for (the page's "show hidden"
    switch). Kept on the drive so every device agrees."""
    try:
        return [str(x) for x in json.loads(HIDDEN.read_text())["people"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def set_hidden_people(payload) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get("people"), list) or len(payload["people"]) > 500:
        raise ValueError("expected {people: [...]}")
    people = sorted({_s(x, 120).strip() for x in payload["people"] if str(x).strip()})
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = HIDDEN.with_suffix(".tmp")
    tmp.write_text(json.dumps({"people": people}))
    tmp.replace(HIDDEN)
    return people


def _clean(r) -> dict:
    if not isinstance(r, dict) or not dupes.UUID_RE.match(str(r.get("uuid", ""))):
        raise ValueError("bad record")
    out = {"uuid": str(r["uuid"]), "filename": _s(r.get("filename")), "bytes": int(r.get("bytes") or 0),
           "date": _s(r.get("date"), 40), "place": _s(r.get("place")), "title": _s(r.get("title")),
           "kind": "video" if r.get("kind") == "video" else "photo",
           "fav": bool(r.get("fav")), "hidden": bool(r.get("hidden")), "trashed": bool(r.get("trashed"))}
    for k in ("lat", "lon"):
        v = r.get(k)
        out[k] = round(float(v), 5) if isinstance(v, (int, float)) else None
    types = {t for t in (r.get("types") or []) if t in TYPES}
    if Path(out["filename"]).suffix.lower() in RAW_EXT:
        types.add("raw")
    out["types"] = sorted(types)
    for k in LIST_FIELDS:
        v = r.get(k) or []
        if not isinstance(v, list):
            raise ValueError("bad " + k)
        out[k] = sorted({_s(x, 120) for x in v if x and x != "_UNKNOWN_"})[:100]
    return out


def rows() -> dict:
    try:
        m = LIB.stat().st_mtime_ns
    except OSError:
        return {}
    if _cache["mtime"] != m:
        _cache["rows"] = {x["uuid"]: x for x in json.loads(LIB.read_text())["photos"]}
        _cache["mtime"] = m
    return _cache["rows"]


def ingest(payload) -> dict:
    """Merge records by uuid (newest export wins). Records are never dropped:
    a photo Photos has since purged keeps its context here, which is the point."""
    if not isinstance(payload, dict) or not isinstance(payload.get("photos"), list):
        raise ValueError("expected {photos: [...]}")
    cur = dict(rows())
    new = [_clean(r) for r in payload["photos"]]
    if len(cur) + len(new) > MAX_RECORDS:
        raise ValueError("too many")
    added = sum(1 for r in new if r["uuid"] not in cur)
    for r in new:
        cur[r["uuid"]] = r
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIB.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": int(time.time()), "photos": list(cur.values())}))
    tmp.replace(LIB)
    return {"received": len(new), "added": added, "total": len(cur)}


def _find(rec: dict):
    """Path of the mirror original (main, then hidden), or (None, state)."""
    key = Path(rec["filename"]).stem
    if not key:
        return None, "missing"
    named = False
    for root, tag in ((dupes.PHOTOS, "ok"), (dupes.HIDDEN, "ok-hidden")):
        for ym in dupes._months(rec["date"]):
            for name, stem, sz in dupes._month_dir(ym, root):
                if stem == key or stem.startswith(key + "_"):
                    if sz == rec["bytes"]:
                        return root / ym / name, tag
                    named = True
    return None, "size" if named else "missing"


def _viewable(path):
    """(file, content type) a browser can show for this mirror original, or None.
    HEIC has no browser support, so its nightly JPEG preview stands in for it."""
    if path is None:
        return None
    ext = path.suffix.lower()
    if ext in VIEWABLE:
        return path, VIEWABLE[ext]
    if ext in (".heic", ".heif"):
        try:
            prev = PREVIEWS / path.relative_to(dupes.PHOTOS).with_suffix(".jpg")
        except ValueError:
            return None  # hidden mirror: no previews are made there
        if prev.is_file():
            return prev, "image/jpeg"
    return None


def _norm(s):
    return s.casefold()


def _match_name(want: str, have) -> bool:
    """"Lauren" finds "Lauren <emoji>"; case-blind."""
    w = _norm(want)
    return any(_norm(h) == w or _norm(h).startswith(w + " ") for h in have)


def _has(want: str, have) -> bool:
    w = _norm(want)
    return any(w in _norm(h) for h in have)


def search(q: dict, limit: int = 60) -> dict:
    """q keys (all optional, all must match): person, label, album, keyword,
    place, text, year, month (YYYY-MM), fav (1), kind (photo|video), type (see TYPES), exclude (names to leave out),
    where (library|hidden|trashed)."""
    data = rows().values()
    f = lambda k: str(q.get(k) or "").strip()
    persons = [x for x in re.split(r"\s*,\s*", f("person")) if x]  # several = together
    skip = [x for x in re.split(r"\s*,\s*", f("exclude")) if x]  # hidden people: only when not searched for
    skip = [x for x in skip if not any(_norm(x) == _norm(p) for p in persons)]
    hits = []
    for r in data:
        if skip and any(_match_name(x, r["persons"]) for x in skip):
            continue
        if any(not _match_name(p, r["persons"]) for p in persons):
            continue
        if f("label") and not _has(f("label"), r["labels"]):
            continue
        if f("album") and not _has(f("album"), r["albums"]):
            continue
        if f("keyword") and not _has(f("keyword"), r["keywords"]):
            continue
        if f("place") and _norm(f("place")) not in _norm(r["place"]):
            continue
        if f("text") and not (_has(f("text"), r["labels"] + r["albums"] + r["keywords"] + r["persons"])
                              or _norm(f("text")) in _norm(r["place"] + " " + r["title"] + " " + r["filename"])):
            continue
        if f("year") and not r["date"].startswith(f("year")):
            continue
        if f("month") and not r["date"].startswith(f("month")):
            continue
        if f("fav") == "1" and not r["fav"]:
            continue
        if f("type") and f("type") not in r.get("types", ()):
            continue
        if f("kind") in ("photo", "video") and r["kind"] != f("kind"):
            continue
        w = f("where")
        if w == "trashed" and not r["trashed"] or w == "hidden" and not r["hidden"] \
                or w == "library" and (r["trashed"] or r["hidden"]):
            continue
        hits.append(r)
    hits.sort(key=lambda r: r["date"], reverse=True)
    offset = max(0, int(q.get("offset") or 0))
    page = []
    for r in hits[offset:offset + limit]:
        path, state = _find(r)
        page.append(dict(r, mirror=state, viewable=_viewable(path) is not None))
    return {"total": len(hits), "offset": offset, "results": page}


def facets() -> dict:
    data = list(rows().values())
    top = lambda key, n: Counter(x for r in data for x in r[key]).most_common(n)
    return {"total": len(data),
            "persons": top("persons", 60), "labels": top("labels", 80), "albums": top("albums", 60),
            "years": sorted(Counter(r["date"][:4] for r in data if r["date"]).items(), reverse=True),
            "types": [(t, TYPES[t], n) for t, n in Counter(t for r in data for t in r.get("types", ())).most_common() if t in TYPES],
            "trashed": sum(1 for r in data if r["trashed"]), "hidden": sum(1 for r in data if r["hidden"])}


def file_for(uuid: str):
    """(path, content type) of a viewable mirror original, by uuid only."""
    r = rows().get(uuid) if dupes.UUID_RE.match(uuid) else None
    if not r:
        return None
    return _viewable(_find(r)[0])


def export_csv() -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["uuid", "filename", "date", "kind", "persons", "labels", "albums", "keywords", "place",
                "lat", "lon", "favourite", "hidden", "trashed", "title"])
    for r in sorted(rows().values(), key=lambda r: r["date"]):
        w.writerow([r["uuid"], r["filename"], r["date"], r["kind"], "; ".join(r["persons"]), "; ".join(r["labels"]),
                    "; ".join(r["albums"]), "; ".join(r["keywords"]), r["place"], r["lat"], r["lon"],
                    int(r["fav"]), int(r["hidden"]), int(r["trashed"]), r["title"]])
    return buf.getvalue()


def originals(uuids):
    """[(arcname, path, record)] for the mirror originals of these ids (any file type), skipping
    ones with no copy. Names are made unique so two photos with one name do not overwrite."""
    out, seen = [], set()
    for u in uuids:
        r = rows().get(u) if dupes.UUID_RE.match(u) else None
        path = _find(r)[0] if r else None
        if not path:
            continue
        name = path.name
        if name in seen:
            name = "%s_%s%s" % (path.stem, u[:8], path.suffix)
        seen.add(name)
        out.append((name, path, r))
    return out


def csv_for(recs) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "date", "kind", "types", "persons", "labels", "albums", "keywords", "place", "lat", "lon", "favourite"])
    for name, _path, r in recs:
        w.writerow([name, r["date"], r["kind"], "; ".join(r.get("types", [])), "; ".join(r["persons"]), "; ".join(r["labels"]),
                    "; ".join(r["albums"]), "; ".join(r["keywords"]), r["place"], r["lat"], r["lon"], int(r["fav"])])
    return buf.getvalue()
