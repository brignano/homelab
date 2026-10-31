"""Duplicate-photo review: sets come from the Mac (tools/photos/duplicates-to-album.py
--export), thumbnails too; this side adds the one thing only CT 100 can know,
whether each original is in the iCloud mirror, records the decisions, and hands
the accepted removals back to the Mac.

Nothing here touches a photo: the mirror is read-only, and the Photos album
(and the delete) stay on the Mac.
"""
import csv
import fcntl
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

PHOTOS = Path(os.environ.get("PHOTOS_DIR", "/photos"))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
DIR = DATA / "dupes"
THUMBS = DIR / "thumbs"
UUID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
MAX_SETS_BYTES = 64 << 20
MAX_THUMB_BYTES = 2 << 20
_listing = {}  # "YYYY/MM" -> (time, [(name, stem, size)])


# --- mirror -------------------------------------------------------------------

def _month_dir(ym: str):
    now = time.time()
    hit = _listing.get(ym)
    if hit and now - hit[0] < 60:
        return hit[1]
    out = []
    d = PHOTOS / ym
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(".xmp") or not e.is_file():
                    continue
                out.append((e.name, os.path.splitext(e.name)[0], e.stat().st_size))
    except OSError:
        pass
    _listing[ym] = (now, out)
    return out


def _months(date_iso: str):
    try:
        d = datetime.fromisoformat(date_iso)
    except ValueError:
        return []
    y, m = d.year, d.month
    res = []
    for off in (0, -1, 1):
        mm = m + off
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        res.append("%04d/%02d" % (yy, mm))
    return res


def mirror_state(member: dict) -> str:
    """ok: a mirror file has this name and exactly this many bytes.
    size: the name matches but the size differs (not trusted).
    missing: no such file in the photo's month (+-1)."""
    key = Path(member.get("filename") or "").stem
    size = int(member.get("bytes") or 0)
    if not key:
        return "missing"
    named = False
    for ym in _months(member.get("date") or ""):
        for _name, stem, sz in _month_dir(ym):
            if stem == key or stem.startswith(key + "_"):
                if sz == size:
                    return "ok"
                named = True
    return "size" if named else "missing"


# --- storage ------------------------------------------------------------------

def load_sets():
    try:
        return json.loads((DIR / "sets.json").read_text())
    except (OSError, ValueError):
        return {"generated": "", "sets": []}


def validate_sets(payload) -> list:
    sets = []
    if not isinstance(payload, dict) or not isinstance(payload.get("sets"), list):
        raise ValueError("expected {sets: [...]}")
    for s in payload["sets"]:
        sid, kind, members = str(s["id"]), s["kind"], s["members"]
        if not UUID_RE.match(sid) or kind not in ("exact", "similar") or not 2 <= len(members) <= 200:
            raise ValueError("bad set")
        ms = []
        for m in members:
            if not UUID_RE.match(str(m["uuid"])):
                raise ValueError("bad uuid")
            ms.append({"uuid": str(m["uuid"]), "filename": str(m["filename"])[:200],
                       "date": str(m.get("date") or "")[:40], "bytes": int(m.get("bytes") or 0),
                       "score": m.get("score") if isinstance(m.get("score"), (int, float)) else None,
                       "keep": bool(m.get("keep"))})
        if sum(1 for m in ms if m["keep"]) != 1:
            raise ValueError("a set needs exactly one default keeper")
        sets.append({"id": sid, "kind": kind, "members": ms})
    return sets


def save_sets(payload) -> dict:
    sets = validate_sets(payload)
    DIR.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(parents=True, exist_ok=True)
    doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "sets": sets}
    tmp = DIR / "sets.json.tmp"
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, DIR / "sets.json")
    need = sorted({m["uuid"] for s in sets for m in s["members"]
                   if not (THUMBS / (m["uuid"] + ".jpg")).exists()})
    return {"ok": True, "sets": len(sets), "need_thumbs": need}


def save_thumb(uuid: str, data: bytes):
    if not UUID_RE.match(uuid) or not data.startswith(b"\xff\xd8"):
        raise ValueError("bad thumbnail")
    THUMBS.mkdir(parents=True, exist_ok=True)
    tmp = THUMBS / (uuid + ".jpg.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, THUMBS / (uuid + ".jpg"))


def thumb_path(uuid: str):
    if not UUID_RE.match(uuid):
        return None
    p = THUMBS / (uuid + ".jpg")
    return p if p.is_file() else None


def read_decisions() -> dict:
    out = {}
    p = DIR / "decisions.csv"
    if p.exists():
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("set"):
                    out[row["set"]] = row.get("keep", "")
    return out


def decide(set_id: str, keep: str):
    sets = {s["id"]: s for s in load_sets()["sets"]}
    s = sets.get(set_id)
    if not s:
        raise KeyError("no such set")
    if keep not in ("", "all") and keep not in {m["uuid"] for m in s["members"]}:
        raise ValueError("keeper is not in the set")
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / "decisions.csv"
    with open(p, "a+", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0, os.SEEK_END)
        new = f.tell() == 0
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "set", "keep"])
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), set_id, keep])


# --- views --------------------------------------------------------------------

def view() -> dict:
    decisions = read_decisions()
    out = []
    for s in load_sets()["sets"]:
        ms = []
        for m in s["members"]:
            ms.append(dict(m, mirror=mirror_state(m), thumb=thumb_path(m["uuid"]) is not None))
        d = decisions.get(s["id"], "")
        out.append({"id": s["id"], "kind": s["kind"], "members": ms, "decision": d})
    # exact copies first (safe), then similar by date of the first member.
    out.sort(key=lambda s: (s["kind"] != "exact", min(m["date"] for m in s["members"])))
    return {"sets": out}


def accepted() -> dict:
    """Removals the human accepted. A similar shot is a different file, so it is
    only listed if its original is confirmed in the mirror; an exact copy has an
    identical keeper in the library, so it is not held back."""
    uuids, skipped, kept_all = [], [], 0
    for s in view()["sets"]:
        keep = s["decision"]
        if not keep:
            continue
        if keep == "all":
            kept_all += 1
            continue
        for m in s["members"]:
            if m["uuid"] == keep:
                continue
            if s["kind"] == "similar" and m["mirror"] != "ok":
                skipped.append({"uuid": m["uuid"], "filename": m["filename"],
                                "reason": "original not confirmed in the mirror (%s)" % m["mirror"]})
            else:
                uuids.append(m["uuid"])
    return {"uuids": uuids, "skipped": skipped, "kept_all_sets": kept_all}
