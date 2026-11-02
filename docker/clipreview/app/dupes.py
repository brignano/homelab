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
HIDDEN = Path(os.environ.get("HIDDEN_PHOTOS_DIR", "/photos-hidden"))
DIR = DATA / "dupes"
THUMBS = DIR / "thumbs"
UUID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
MAX_SETS_BYTES = 64 << 20
MAX_THUMB_BYTES = 2 << 20
_listing = {}  # "YYYY/MM" -> (time, [(name, stem, size)])


# --- mirror -------------------------------------------------------------------

def _month_dir(ym: str, root: Path = None):
    root = root or PHOTOS
    now = time.time()
    key = (str(root), ym)
    hit = _listing.get(key)
    if hit and now - hit[0] < 60:
        return hit[1]
    out = []
    d = root / ym
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(".xmp") or not e.is_file():
                    continue
                out.append((e.name, os.path.splitext(e.name)[0], e.stat().st_size))
    except OSError:
        pass
    _listing[key] = (now, out)
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


def mirror_state(member: dict, root: Path = None) -> str:
    """ok: a mirror file has this name and exactly this many bytes.
    size: the name matches but the size differs (not trusted).
    missing: no such file in the photo's month (+-1)."""
    key = Path(member.get("filename") or "").stem
    size = int(member.get("bytes") or 0)
    if not key:
        return "missing"
    named = False
    for ym in _months(member.get("date") or ""):
        for _name, stem, sz in _month_dir(ym, root):
            if stem == key or stem.startswith(key + "_"):
                if sz == size:
                    return "ok"
                named = True
    return "size" if named else "missing"


def mirror_state_any(member: dict) -> str:
    """mirror_state, then the hidden mirror (/photos-hidden). ok-hidden: the
    original is in the hidden mirror with exactly this name and size."""
    st = mirror_state(member)
    if st == "ok":
        return st
    h = mirror_state(member, HIDDEN)
    if h == "ok":
        return "ok-hidden"
    return h if st == "missing" else st


def confirmed(state: str) -> bool:
    return state in ("ok", "ok-hidden")


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
                       "favorite": bool(m.get("favorite")),
                       # Photos' sub-scores, kept only so the agreement report can
                       # test whether a better blend than the overall score exists
                       "sub": {str(k)[:40]: round(float(v), 3) for k, v in (m.get("sub") or {}).items()
                               if isinstance(v, (int, float))} if isinstance(m.get("sub"), dict) else {},
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


def parse_keep(keep: str):
    """The decision for a set: "" undecided, "all" keep everything, "none" keep nothing
    (remove every photo in the set), or one or more member uuids to keep, comma
    separated; every other member is removed."""
    if keep in ("", "all", "none"):
        return keep, []
    return "some", [k for k in keep.split(",") if k]


def decide(set_id: str, keep: str):
    sets = {s["id"]: s for s in load_sets()["sets"]}
    s = sets.get(set_id)
    if not s:
        raise KeyError("no such set")
    kind, ids = parse_keep(keep)
    uuids = {m["uuid"] for m in s["members"]}
    if kind == "some" and (not ids or len(set(ids)) != len(ids) or not set(ids) <= uuids or set(ids) == uuids):
        raise ValueError("keepers must be distinct members of the set, and not all of them (use all)")
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


# --- confidence ---------------------------------------------------------------
# How sure are we that the proposed keeper really is the best of the set? The
# thresholds are a first guess on Apple's overall score; report() below measures
# how often Anthony agrees per tier, and that is what should move them.
HIGH_MARGIN = 0.10
MEDIUM_MARGIN = 0.03


def assess(kind: str, members: list):
    """(tier, why): tier is high, medium or low."""
    keeper = next(m for m in members if m["keep"])
    others = [m for m in members if not m["keep"]]
    if kind == "exact":
        return "high", "Byte-identical copies; kept the largest"
    if keeper.get("favorite") and not any(o.get("favorite") for o in others):
        return "high", "Your favourite"
    if keeper["score"] is None or any(o["score"] is None for o in others):
        return "low", "No Apple score for some of these shots, so the pick is a guess"
    gap = keeper["score"] - max(o["score"] for o in others)
    txt = "Apple score %.2f vs %.2f" % (keeper["score"], max(o["score"] for o in others))
    if gap >= HIGH_MARGIN:
        return "high", txt + ": clear winner"
    if gap >= MEDIUM_MARGIN:
        return "medium", txt + ": fairly close"
    return "low", txt + ": too close to call, look at them"


def report(sets: list, decisions: dict) -> dict:
    """Agreement between the proposed keeper and what was actually decided, per tier."""
    tiers = {t: {"decided": 0, "agreed": 0, "picked_other": 0, "kept_all": 0}
             for t in ("high", "medium", "low")}
    for s in sets:
        d = decisions.get(s["id"], "")
        if not d:
            continue
        t = tiers[s["confidence"]]
        t["decided"] += 1
        if d == "all":
            t["kept_all"] += 1
        elif next(m["uuid"] for m in s["members"] if m["keep"]) == d:  # exactly the proposed keeper
            t["agreed"] += 1
        else:
            t["picked_other"] += 1
    tot = {k: sum(t[k] for t in tiers.values()) for k in ("decided", "agreed", "picked_other", "kept_all")}
    return {"tiers": tiers, "total": tot}


# --- views --------------------------------------------------------------------

def view() -> dict:
    decisions = read_decisions()
    out = []
    for s in load_sets()["sets"]:
        ms = []
        for m in s["members"]:
            ms.append(dict(m, mirror=mirror_state_any(m), thumb=thumb_path(m["uuid"]) is not None))
        d = decisions.get(s["id"], "")
        conf, why = assess(s["kind"], ms)
        out.append({"id": s["id"], "kind": s["kind"], "members": ms, "decision": d,
                    "confidence": conf, "why": why,
                    # safe to accept without looking: high confidence and, for a
                    # similar shot, every original confirmed in the mirror
                    "auto_ok": conf == "high" and (s["kind"] == "exact" or all(confirmed(m["mirror"]) for m in ms))})
    # exact copies first (safe), then similar by date of the first member.
    out.sort(key=lambda s: (s["kind"] != "exact", min(m["date"] for m in s["members"])))
    return {"sets": out, "report": report(out, decisions)}


def accepted() -> dict:
    """Removals the human accepted. A similar shot is a different file, so it is
    only listed if its original is confirmed in the mirror. An exact copy has an
    identical keeper in the library, so it is not held back, unless nothing in the
    set is kept: then there is no other copy and the mirror must have it."""
    uuids, skipped, kept_all = [], [], 0
    for s in view()["sets"]:
        kind, keepers = parse_keep(s["decision"])
        if not kind:
            continue
        if kind == "all":
            kept_all += 1
            continue
        for m in s["members"]:
            if m["uuid"] in keepers:
                continue
            needs_mirror = s["kind"] == "similar" or kind == "none"
            if needs_mirror and not confirmed(m["mirror"]):
                skipped.append({"uuid": m["uuid"], "filename": m["filename"],
                                "reason": "original not confirmed in the mirror (%s)" % m["mirror"]})
            else:
                uuids.append(m["uuid"])
    return {"uuids": uuids, "skipped": skipped, "kept_all_sets": kept_all}


# --- cold storage ---------------------------------------------------------------

COLD_DIR = DATA / "cold"
MAX_COLD = 20000


def cold_check(payload) -> dict:
    """Mirror check for photos proposed for cold storage (tools/photos/
    cold-storage-report.py). Each member needs its original in the mirror
    (/photos) or in the hidden mirror (/photos-hidden) with exactly the same
    name and size. With save=true the confirmed members (with their people,
    albums and so on) are written to a manifest on the drive, so the context
    Photos holds survives the photo leaving iCloud. Nothing is deleted."""
    if not isinstance(payload, dict) or not isinstance(payload.get("members"), list):
        raise ValueError("expected {members: [...]}")
    members = payload["members"]
    if len(members) > MAX_COLD:
        raise ValueError("too many")
    states, ok = {}, []
    for m in members:
        uuid = str(m["uuid"])
        if not UUID_RE.match(uuid):
            raise ValueError("bad uuid")
        st = mirror_state(m)
        if st != "ok":
            h = mirror_state(m, HIDDEN)
            if h == "ok":
                st = "ok-hidden"
            elif st == "missing":
                st = h
        states[uuid] = st
        if st in ("ok", "ok-hidden"):
            ok.append(dict(m, mirror=st))
    saved = None
    if payload.get("save") and ok:
        batch = re.sub(r"[^0-9A-Za-z_-]", "", str(payload.get("batch") or ""))[:40] or "batch"
        COLD_DIR.mkdir(parents=True, exist_ok=True)
        path = COLD_DIR / ("manifest-%s.json" % batch)
        old = {}
        try:
            old = {x["uuid"]: x for x in json.loads(path.read_text())["photos"]}
        except (OSError, ValueError, KeyError):
            pass
        old.update({x["uuid"]: x for x in ok})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"batch": batch, "photos": list(old.values())}, indent=1))
        tmp.replace(path)
        saved = path.name
    return {"states": states, "confirmed": len(ok), "manifest": saved}
