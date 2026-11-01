"""Review page for climb-judge verdicts.

Reads verdict CSVs from DATA_DIR, plays the clips from PHOTOS_DIR, and appends
labels to DATA_DIR/labels.csv in the format `climb-judge.sh --labels` reads.

It never writes to, moves or deletes anything under PHOTOS_DIR (which the
compose file also mounts read-only). Videos are only served by an id that came
from the verdicts file, never by a path from the request.
"""
import csv
import fcntl
import glob
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dupes, ui
from .dupes_page import PAGE as DUPES_PAGE

PHOTOS = Path(os.environ.get("PHOTOS_DIR", "/photos"))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
ASSETS = Path(os.environ.get("ASSETS_DIR", "/srv/assets"))
VERDICT_GLOB = os.environ.get("VERDICT_GLOB", "climb-verdicts-claude-*.csv")
HI_CONF = float(os.environ.get("EXPORT_CLAUDE_CONFIDENCE", "0.8"))

HUMAN = ("send", "fall", "none")  # what labels.csv accepts
CLEAR = "clear"  # a row that voids the clip's earlier decision (Undo, Clear decision)
NOTCLIMB = "notclimb"  # review-log.csv only: labels.csv (climb-judge --labels) rejects it
ALL_LABELS = HUMAN + ("unsure", NOTCLIMB)
MONTH_RE = re.compile(r"(?:^|/)(\d{4})/(\d{2})/")

_lock = threading.Lock()


# --- data ---------------------------------------------------------------------

def verdict_files():
    files = sorted(glob.glob(str(DATA / VERDICT_GLOB)))
    return files or sorted(glob.glob(str(DATA / "climb-verdicts*.csv")))


def stem_matches(key: str, stem: str) -> bool:
    return stem == key or stem.startswith(key + "_")


def read_verdicts():
    """file (relative) -> {claude, conf, why}; later files win."""
    out = {}
    for path in verdict_files():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("file") or "").strip()
                if not name:
                    continue
                verdict = (row.get("claude") or row.get("verdict") or "").strip().lower()
                try:
                    conf = float(row.get("claude_confidence") or row.get("confidence") or "")
                except ValueError:
                    conf = None
                out[name] = {"claude": verdict, "conf": conf,
                             "why": row.get("claude_why") or row.get("reason") or ""}
    return out


def read_log():
    """stem -> label, newest event wins. review-log.csv is the UI's own record
    (it also holds `unsure`, which labels.csv cannot); labels.csv is read too,
    so labels made by hand count."""
    labels = {}
    p = DATA / "labels.csv"
    if p.exists():
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                lab = (row.get("label") or "").strip().lower()
                if row.get("file") and lab in HUMAN:
                    labels[row["file"].strip()] = lab
    p = DATA / "review-log.csv"
    if p.exists():
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                lab = (row.get("label") or "").strip().lower()
                if row.get("file") and lab == CLEAR:   # Undo / Clear decision: back to undecided
                    labels.pop(row["file"].strip(), None)
                elif row.get("file") and lab in ALL_LABELS:
                    labels[row["file"].strip()] = lab
    return labels


_index = {"at": 0.0, "files": {}, "by_name": {}, "dupes": {}}
_fp_cache = {}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".heic", ".jpg", ".jpeg", ".png", ".dng"}
VIDEO_LABELS = ("keep", "cold", "unsure")  # keep in iCloud / move to cold storage
LIVE_MARK = "_HEVC"  # icloudpd names a Live Photo's motion half IMG_1234_HEVC[_id].MOV


def is_live_half(name: str, image_stems) -> bool:
    stem = os.path.splitext(name)[0]
    return stem in image_stems or stem.upper().endswith(LIVE_MARK) or LIVE_MARK + "_" in stem.upper()


def _fingerprint(rel: str, size: int, mtime: float) -> str:
    """Cheap content identity: size plus the first and last MiB. Only computed
    for files whose size already collides, so it reads little."""
    key = (rel, size, mtime)
    if key not in _fp_cache:
        h = hashlib.sha1(str(size).encode())
        with open(PHOTOS / rel, "rb") as f:
            h.update(f.read(1 << 20))
            if size > 2 << 20:
                f.seek(size - (1 << 20))
                h.update(f.read(1 << 20))
        _fp_cache[key] = h.hexdigest()
    return _fp_cache[key]


def mirror_index():
    """Every video in the mirror, rebuilt at most once a minute.

    files:   rel -> {size, mtime, live}. `live` marks the .MOV half of a Live
             Photo (a photo of the same name beside it): part of the photo, not
             a video to review on its own.
    by_name: basename -> rel, for verdict CSVs that record only the file name
             while the mirror keeps clips under YYYY/MM/.
    dupes:   rel -> [rel, ...] byte-identical videos (size, then head+tail hash).
    Sidecars and non-video files are ignored."""
    if time.time() - _index["at"] <= 60:
        return _index
    files, by_name, by_size = {}, {}, {}
    for root, _dirs, names in os.walk(PHOTOS):
        stems = {os.path.splitext(n)[0] for n in names if os.path.splitext(n)[1].lower() in IMAGE_EXT}
        for n in names:
            if os.path.splitext(n)[1].lower() not in VIDEO_EXT:
                continue
            full = os.path.join(root, n)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, PHOTOS)
            files[rel] = {"size": st.st_size, "mtime": st.st_mtime,
                          "live": is_live_half(n, stems)}
            # prefer a YYYY/MM location if a name appears twice
            if n not in by_name or MONTH_RE.search(rel):
                by_name[n] = rel
            if st.st_size and not files[rel]["live"]:
                by_size.setdefault(st.st_size, []).append(rel)
    groups = {}
    for size, rels in by_size.items():
        if len(rels) < 2:
            continue
        for rel in rels:
            try:
                fp = _fingerprint(rel, size, files[rel]["mtime"])
            except OSError:
                continue
            groups.setdefault(fp, []).append(rel)
    dupes = {rel: [o for o in g if o != rel] for g in groups.values() if len(g) > 1 for rel in g}
    _index.update(at=time.time(), files=files, by_name=by_name, dupes=dupes)
    return _index


def locate(rel: str) -> str:
    """The mirror-relative path for a CSV `file` value, or "" if absent."""
    p = PHOTOS / rel
    if p.is_file() and PHOTOS.resolve() in p.resolve().parents:
        return rel
    return mirror_index()["by_name"].get(Path(rel).name, "")


def clip_id(rel: str) -> str:
    return hashlib.sha1(rel.encode()).hexdigest()[:12]


def read_decisions():
    """mirror-relative path -> keep / cold / unsure, newest event wins."""
    out = {}
    p = DATA / "video-review.csv"
    if p.exists():
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                lab = (row.get("decision") or "").strip().lower()
                if row.get("file") and lab == CLEAR:
                    out.pop(row["file"].strip(), None)
                elif row.get("file") and lab in VIDEO_LABELS:
                    out[row["file"].strip()] = lab
    return out


def read_triage():
    """mirror-relative path -> {category, suggest, conf, sig, why} from
    tools/photos/triage.py (video-triage.csv); a later row for a file wins."""
    out = {}
    p = DATA / "video-triage.csv"
    if p.exists():
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("file") or "").strip()
                if not name or row.get("error") or row.get("suggest") not in ("judge", "keep", "cold"):
                    continue
                try:
                    conf = float(row.get("confidence") or "")
                    sig = int(row.get("significance") or 0)
                except ValueError:
                    continue
                out[name] = {"category": row.get("category", ""), "suggest": row["suggest"],
                             "conf": conf, "sig": sig, "why": row.get("reason", "")}
    return out


def build():
    """Every video in the mirror, plus verdicts whose clip has not synced yet.

    A video with a climb-judge verdict is kind "climb" and takes send / fall /
    none; any other video is kind "video" and takes keep (stay in iCloud) /
    cold (cold storage: the mirror is the copy that stays) / unsure."""
    idx = mirror_index()
    verdicts = read_verdicts()
    labels = read_log()
    decisions = read_decisions()
    triage = read_triage()
    items = {}
    for key, v in verdicts.items():
        actual = locate(key)
        items[actual or key] = (key, v)
    for rel, meta in idx["files"].items():
        if rel not in items and not meta["live"]:
            items[rel] = (rel, None)
    clips = []
    for rel, (key, v) in items.items():
        notclimb = False
        claude = v  # Claude's call is still shown after the clip leaves the climbing flow
        meta = idx["files"].get(rel)
        present = meta is not None
        stem = Path(key).stem
        label = ""
        if v is not None:
            for k, lab in labels.items():
                if stem_matches(k, stem):
                    label = lab
            if label == NOTCLIMB:  # not a climb after all: the Other videos flow decides
                v, label = None, decisions.get(rel, "")
                notclimb = True
        else:
            label = decisions.get(rel, "")
        m = MONTH_RE.search(rel)
        clips.append({
            "id": clip_id(rel), "file": rel, "name": Path(rel).name, "stem": stem,
            "kind": "climb" if v is not None else "video", "notclimb": notclimb,
            "month": "%s-%s" % m.groups() if m else "",
            "claude": claude["claude"] if claude else "", "conf": claude["conf"] if claude else None,
            "why": claude["why"] if claude else "",
            "label": label, "present": present,
            "tri": triage.get(rel) if v is None else None,
            "size": meta["size"] if present else 0,
            "dupes": [{"id": clip_id(o), "file": o, "size": idx["files"][o]["size"]}
                      for o in idx["dupes"].get(rel, [])],
        })
    # Queue: not yet decided first (unsure counts as not decided). Climb clips
    # lead, "none" and low confidence ahead of confident verdicts; then other
    # videos, duplicated ones first and the biggest (dearest to keep hot) next.
    def key(c):
        done = c["label"] in HUMAN + ("keep", "cold")
        if c["kind"] == "climb":
            rank = -1.0 if c["claude"] == "none" else (c["conf"] if c["conf"] is not None else -0.5)
            return (done, 0, rank, 0, c["file"])
        return (done, 1, 0 if c["dupes"] else 1, -c["size"], c["file"])
    clips.sort(key=key)
    return clips


def append_label(clip: dict, label: str, as_climb: bool = False):
    """as_climb: write to the climbing log even though the clip now reads as a
    video (undoing "not climbing" voids that row in review-log.csv)."""
    DATA.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if clip["kind"] == "video" and not as_climb:
        _append(DATA / "video-review.csv", ["ts", "file", "decision"], [now, clip["file"], label])
        return
    _append(DATA / "review-log.csv", ["ts", "file", "label"], [now, clip["stem"], label])
    if label in HUMAN:  # not "unsure" or "notclimb": --labels rejects them
        _append(DATA / "labels.csv", ["file", "label"], [clip["stem"], label])


def drop_last_human_label(stem: str):
    """Undo for a climbing clip: remove the newest row for it from labels.csv,
    which climb-judge --labels reads, so an undone label does not train it.
    review-log.csv is never edited; the Undo is a new row there."""
    p = DATA / "labels.csv"
    if not p.exists():
        return
    with open(p, "r+", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        rows = list(csv.reader(f))
        for k in range(len(rows) - 1, 0, -1):
            if rows[k] and rows[k][0].strip() == stem:
                del rows[k]
                f.seek(0)
                f.truncate()
                csv.writer(f).writerows(rows)
                return


def _append(path: Path, header, row):
    with open(path, "a+", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0, os.SEEK_END)
        size = f.tell()
        lead = ""
        if size:
            f.seek(size - 1)
            if f.read(1) != "\n":  # a hand-edited file may lack its last newline
                lead = "\n"
        f.seek(0, os.SEEK_END)
        if lead:
            f.write(lead)
        w = csv.writer(f)
        if not size:
            w.writerow(header)
        w.writerow(row)


def accept_triage(suggest: str, min_conf: float, month: str = "", dry: bool = True) -> int:
    """Record the triage suggestion as the decision for every video nobody has
    decided yet (keep / cold; "judge" is not a decision). Returns how many.
    Same rows the buttons write, so one undo path: pick the other button."""
    if suggest not in ("keep", "cold"):
        raise ValueError("suggest")
    rows = [c for c in build()
            if c["kind"] == "video" and c["present"] and not c["label"] and c["tri"]
            and c["tri"]["suggest"] == suggest and c["tri"]["conf"] >= min_conf
            and (not month or c["month"] == month or c["month"].startswith(month + "-"))]
    if not dry and rows:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        _append_rows(DATA / "video-review.csv", ["ts", "file", "decision"],
                     [[now, c["file"], suggest] for c in rows])
    return len(rows)


def _append_rows(path: Path, header, rows):
    for r in rows:
        _append(path, header, r)


def export(include_claude: bool, which: str = "climb"):
    rows = []
    for c in build():
        if not c["present"]:
            continue
        why = ""
        if which == "cold":
            if c["kind"] == "video" and c["label"] == "cold":
                why = "cold storage"
        elif which == "judge":  # for climb_judge.py --list: probably climbing, no verdict yet
            if c["kind"] == "video" and not c["notclimb"] and c["tri"] and c["tri"]["category"] == "climbing":
                why = "triage: climbing %.2f" % c["tri"]["conf"]
        elif c["kind"] != "climb":
            pass
        elif c["label"] in ("fall", "none"):
            why = "labelled " + c["label"]
        # Claude's "none" is never a candidate on its own: it also means "not
        # climbing", and a cat video is not a clip to remove. Only a human
        # labelling it none, or a confident "fall", gets a clip listed.
        elif include_claude and not c["label"] and c["claude"] == "fall" \
                and (c["conf"] or 0) >= HI_CONF:
            why = "claude %s %.2f, not reviewed" % (c["claude"], c["conf"])
        if why:
            rows.append((c, why))
    return rows


# --- http ---------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "clipreview"

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), flush=True)

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if not any(k == "Cache-Control" for k, _ in extra):
            self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self._send(200, "ok")
        if u.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path.startswith("/assets/"):
            return self._asset(u.path[len("/assets/"):])
        if u.path in ui.ICON_ROUTES or u.path.startswith("/icons/"):
            return self._icon(u.path)
        if u.path == "/api/clips":
            with _lock:
                clips = build()
            return self._send(200, json.dumps(clips), "application/json")
        if u.path == "/dupes":
            return self._send(200, DUPES_PAGE, "text/html; charset=utf-8")
        if u.path == "/api/dupes":
            with _lock:
                return self._send(200, json.dumps(dupes.view()), "application/json")
        if u.path == "/api/dupes/accepted.json":
            with _lock:
                return self._send(200, json.dumps(dupes.accepted()), "application/json")
        if u.path.startswith("/dupes/thumb/"):
            p = dupes.thumb_path(u.path[len("/dupes/thumb/"):])
            if not p:
                return self._send(404, "no thumbnail")
            return self._send(200, p.read_bytes(), "image/jpeg")
        if u.path.startswith("/video/"):
            return self._video(u.path[len("/video/"):])
        if u.path in ("/export.csv", "/export.txt"):
            with _lock:
                rows = export(q.get("include", [""])[0] == "claude", q.get("set", ["climb"])[0])
            if u.path == "/export.txt":
                body = "".join(c["name"] + "\n" for c, _ in rows)
                ctype = "text/plain; charset=utf-8"
            else:
                import io
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["file", "month", "mirror_path", "size_mb", "why"])
                for c, why in rows:
                    w.writerow([c["name"], c["month"], c["file"], "%.1f" % (c["size"] / 1e6), why])
                body, ctype = buf.getvalue(), "text/csv; charset=utf-8"
            return self._send(200, body, ctype,
                              [("Content-Disposition", "inline; filename=%s" % u.path[1:])])
        self._send(404, "not found")

    def _same_origin_json(self, limit):
        """A page on another site must not be able to write from a browser on
        the LAN: same-origin only. The Mac script sends no Origin header."""
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._send(403, "cross-origin")
            return None
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            self._send(413, "too large")
            return None
        return self.rfile.read(n)

    def do_PUT(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/dupes/thumb/"):
            return self._send(404, "not found")
        body = self._same_origin_json(dupes.MAX_THUMB_BYTES)
        if body is None:
            return
        try:
            dupes.save_thumb(path[len("/api/dupes/thumb/"):], body)
        except ValueError:
            return self._send(400, "bad thumbnail")
        self._send(200, "ok")

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/label", "/api/dupes/sets", "/api/dupes/decide", "/api/triage/accept"):
            return self._send(404, "not found")
        if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
            return self._send(415, "json only")
        raw = self._same_origin_json(dupes.MAX_SETS_BYTES if path == "/api/dupes/sets" else 4096)
        if raw is None:
            return
        try:
            body = json.loads(raw)
            if path == "/api/dupes/sets":
                with _lock:
                    return self._send(200, json.dumps(dupes.save_sets(body)), "application/json")
            if path == "/api/triage/accept":
                with _lock:
                    n = accept_triage(str(body["suggest"]), float(body["min_conf"]),
                                      str(body.get("month") or ""), bool(body.get("dry", True)))
                return self._send(200, json.dumps({"count": n}), "application/json")
            if path == "/api/dupes/decide":
                with _lock:
                    dupes.decide(str(body["set"]), str(body["keep"]))
                return self._send(200, json.dumps({"ok": True}), "application/json")
            cid, label = str(body["id"]), str(body["label"])
        except KeyError:
            return self._send(404, "no such set")
        except (ValueError, TypeError):
            return self._send(400, "bad request")
        with _lock:
            clip = next((c for c in build() if c["id"] == cid), None)
            if not clip:
                return self._send(404, "no such clip")
            climb_ctx = clip["kind"] == "climb" or bool(body.get("climb"))
            if label not in ((ALL_LABELS if climb_ctx else VIDEO_LABELS) + (CLEAR,)):
                return self._send(400, "bad label")
            if body.get("undo") and climb_ctx and read_log().get(clip["stem"]) in HUMAN:
                drop_last_human_label(clip["stem"])
            append_label(clip, label, as_climb=bool(body.get("climb")))
        self._send(200, json.dumps({"ok": True, "label": label}), "application/json")

    def _icon(self, path):
        """The site's own mark (app/static): tab icon, touch icon, manifest.
        A fixed name list, so no path from the request is opened. Safari probes
        /favicon.ico and /apple-touch-icon.png at the root without reading the
        page, so those are answered here too."""
        name = ui.ICON_ROUTES.get(path) or (path[len("/icons/"):] if path.startswith("/icons/") else "")
        if name not in ui.ICON_FILES:
            return self._send(404, "not found")
        p = ui.STATIC / name
        if not p.is_file():
            return self._send(404, "not found")
        return self._send(200, p.read_bytes(), ui.ICON_FILES[name],
                          [("Cache-Control", "public, max-age=86400")])

    def _asset(self, name):
        """Design tokens and Geist, from the dashboard's assets (mounted read-only).
        Only these two shapes are served, so no path from the request is opened."""
        if name == "design-tokens.css":
            p = ASSETS / name
        elif re.fullmatch(r"fonts/[A-Za-z0-9.-]+\.woff2", name):
            p = ASSETS / name
        else:
            return self._send(404, "not found")
        if not p.is_file():
            return self._send(404, "not found")
        return self._send(200, p.read_bytes(), ui.ASSET_TYPES[p.suffix],
                          [("Cache-Control", "public, max-age=86400")])

    def _video(self, cid):
        with _lock:
            clip = next((c for c in build() if c["id"] == cid), None)
        if not clip or not clip["present"]:
            return self._send(404, "not in the mirror")
        path = PHOTOS / clip["file"]
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "video/quicktime"
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(size - int(m.group(2)), 0)
            end = min(end, size - 1)
            if start > end:
                return self._send(416, "bad range", extra=[("Content-Range", "bytes */%d" % size)])
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        try:
            with open(path, "rb") as f:  # read-only
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the player seeked away


def main():
    port = int(os.environ.get("PORT", "8000"))
    print("clipreview: photos=%s data=%s verdicts=%s" % (PHOTOS, DATA, verdict_files()), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


PAGE = r"""<!doctype html>
<html lang="en"><head>
<title>Videos · Review</title>
__HEAD__
<style>
#stagebox{flex:1;min-width:0;min-height:0;display:flex;align-items:center;justify-content:center}
#stage{position:relative;overflow:hidden;border-radius:var(--radius);background:var(--n-100);box-shadow:var(--shadow-2)}
#v{position:absolute;top:50%;left:50%;transform-origin:center;object-fit:contain;background:transparent;
  transition:transform var(--dur) var(--ease)}
#empty{color:var(--slate)}
#side{width:23rem;max-width:100%;overflow:auto;display:flex;flex-direction:column;gap:var(--s3)}
#name{margin:0;font-size:var(--text-xl);font-weight:650;letter-spacing:-.01em;overflow-wrap:anywhere}
#why{white-space:pre-wrap;margin:0}
.row{display:flex;gap:var(--s2);align-items:center;flex-wrap:wrap}
#dupes a{display:block;overflow-wrap:anywhere}
.group{display:none}
body[data-kind=climb] .g-climb,body[data-kind=video] .g-video{display:contents}
@media (max-width:760px){#stagebox{flex:none;height:56vh}#side{width:auto;overflow:visible}}
</style></head><body data-kind="video">
<header>
 __BRAND____NAV__
 <select id="view" aria-label="View">
  <option value="all">All videos</option><option value="climb">Climbing clips</option>
  <option value="video">Other videos</option><option value="dupes">Duplicates</option></select>
 <select id="sug" aria-label="Suggestion">
  <option value="">Any suggestion</option><option value="keep">Suggested: keep</option>
  <option value="cold">Suggested: cold</option><option value="judge">Looks like climbing</option>
  <option value="look">Needs a look</option><option value="none">Not triaged</option></select>
 __MF__
 <label><input type="checkbox" id="hide" checked> hide decided</label>
 <span id="count" class="muted"></span>
 <span class="grow"></span>
 <small class="exports">Export <a href="/export.txt" target="_blank">climb</a> <a href="/export.csv?include=claude" target="_blank">climb + Claude calls</a> <a href="/export.csv?set=cold" target="_blank">cold storage</a> <a href="/export.txt?set=judge" target="_blank">looks like climbing</a></small>
</header>
<main>
 <div id="stagebox"><div id="stage"><video id="v" controls autoplay muted playsinline></video></div><p id="empty" hidden></p></div>
 <aside id="side" class="panel">
  <h2 id="name"></h2>
  <p class="muted" id="meta" style="margin:0"></p>
  <div class="row"><span id="kindchip" class="chip"></span> <span>Your call:</span> <span id="mine" class="chip"></span></div>
  <div class="g-climb group"><div class="row"><span>Claude:</span> <span id="verdict" class="chip"></span> <small id="conf"></small></div>
   <p id="why"></p></div>
  <div id="tri" class="panel" style="padding:var(--s3)" hidden></div>
  <div id="bulk" class="panel" style="padding:var(--s3)" hidden>
   <b>Accept suggestions</b>
   <p class="muted" id="bulktxt" style="margin:var(--s1) 0"></p>
   <div class="row"><button id="bkeep">Accept keep</button><button id="bcold">Accept cold</button></div></div>
  <div id="dupes"></div>
  <p id="warn" class="warn" style="margin:0"></p>
 </aside>
</main>
<footer>
 <span class="g-climb group">
  <button data-l="send">Send <kbd>S</kbd></button>
  <button data-l="fall">Fall <kbd>F</kbd></button>
  <button data-l="none">None <kbd>N</kbd></button>
  <button data-l="notclimb">Not climbing <kbd>X</kbd></button></span>
 <span class="g-video group">
  <button data-l="keep" class="primary">Keep in iCloud <kbd>K</kbd></button>
  <button data-l="cold">Cold storage <kbd>C</kbd></button></span>
 <button data-l="unsure">Unsure <kbd>U</kbd></button>
 <span class="sep"></span>
 <button id="undo" disabled>Undo <kbd>Z</kbd></button>
 <button id="clear" disabled>Clear decision</button>
 <button id="prev">Prev <kbd>←</kbd></button>
 <button id="next">Next <kbd>→</kbd></button>
 <button id="rot">Rotate <kbd>R</kbd></button>
 <small class="grow">Space plays. Nothing is deleted here: cold-storage picks go to a Photos album from the Mac, and the mirror stays as the cold copy.</small>
</footer>
<script>
__MFJS__
const LOW=0.7;  // below this a suggestion goes to "Needs a look"
let all=[],list=[],i=0,rot=0,ratio=16/9,undo=null;const seen={};
const DONE=['send','fall','none','keep','cold'];
const $=id=>document.getElementById(id), v=$('v'), stage=$('stage'), box=$('stagebox');
const mb=n=>(n/1e6).toFixed(n>1e8?0:1)+' MB';
async function load(keepId){
  all=await (await fetch('/api/clips')).json();
  MF.set(all.filter(c=>c.present).map(c=>c.month)); refilter(keepId);
}
function refilter(keepId){
  const hide=$('hide').checked, view=$('view').value, sug=$('sug').value;
  list=all.filter(c=>c.present&&MF.match(c.month)&&sugOk(c,sug)
    &&(view==='all'||view===c.kind||(view==='dupes'&&c.dupes.length))
    &&(!hide||!DONE.includes(c.label)||c.id===keepId));
  i=Math.max(0,list.findIndex(c=>c.id===keepId)); show();
}
function sugOk(c,s){
  if(!s)return true;
  if(c.kind!=='video')return false;
  const t=c.tri;
  if(s==='none')return !t;
  if(s==='look')return !!t&&t.conf<LOW;
  if(s==='judge')return !!t&&t.category==='climbing';
  return !!t&&t.suggest===s&&t.conf>=LOW;
}
function bulkInfo(){
  const m=MF.val;
  const pool=all.filter(c=>c.present&&c.kind==='video'&&!c.label&&c.tri&&MF.match(c.month));
  const n=s=>pool.filter(c=>c.tri.suggest===s&&c.tri.conf>=LOW).length;
  $('bulk').hidden=!pool.length;
  $('bulktxt').textContent=`${n('keep')} keep and ${n('cold')} cold suggestions at confidence ${LOW}+ are undecided`+(m?` in ${MF.label()}`:'')+'. Accepting writes the same decision as the buttons; nothing is deleted.';
  $('bkeep').textContent=`Accept ${n('keep')} keep`;$('bcold').textContent=`Accept ${n('cold')} cold`;
}
async function accept(s){
  const m=$('month').value, body={suggest:s,min_conf:LOW,month:m,dry:true};
  const post=b=>fetch('/api/triage/accept',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
  const r=await post(body); if(!r.ok){$('warn').textContent='Accept failed: '+r.status;return}
  const n=(await r.json()).count; if(!n)return;
  if(!confirm(`Record "${s==='keep'?'keep in iCloud':'cold storage'}" for ${n} undecided videos${m?' in '+m:''}? You can change any one afterwards.`))return;
  body.dry=false; const r2=await post(body);
  if(!r2.ok){$('warn').textContent='Accept failed: '+r2.status;return}
  const id=(list[i]||{}).id; await load(id);
}
function fit(){
  const W=box.clientWidth,H=box.clientHeight; if(!W||!H)return;
  const turned=rot%180!==0, r=turned?1/ratio:ratio;   // shape of what is on screen
  let w=W,h=W/r; if(h>H){h=H;w=H*r}
  stage.style.width=w+'px';stage.style.height=h+'px';
  const iw=turned?h:w, ih=turned?w:h;                  // the video box before it is rotated
  v.style.width=iw+'px';v.style.height=ih+'px';
  v.style.transform=`translate(-50%,-50%) rotate(${rot}deg)`;
}
function show(){
  const done=all.filter(c=>DONE.includes(c.label)).length;
  const waiting=all.filter(c=>!c.present&&c.kind==='climb').length;
  $('count').textContent=`${list.length?i+1:0}/${list.length} · ${done}/${all.length} decided`+(waiting?` · ${waiting} not on the drive yet`:'');
  const c=list[i];
  $('undo').disabled=!undo; $('clear').disabled=!(c&&(c.label||c.notclimb));
  stage.hidden=!c; $('empty').hidden=!!c;
  if(!c){v.removeAttribute('src');$('name').textContent='';$('empty').textContent='Nothing left in this view.';return}
  document.body.dataset.kind=c.kind;
  $('name').textContent=c.name;
  $('meta').textContent=`${c.file} · ${mb(c.size)}`;
  $('kindchip').textContent=c.kind==='climb'?'Climbing clip':c.notclimb?'Video (not climbing)':'Video';
  $('verdict').textContent=c.claude||'–'; $('verdict').className='chip '+c.claude;
  $('conf').textContent=c.conf==null?'':`confidence ${c.conf.toFixed(2)}`;
  $('why').textContent=c.why;
  const t=c.tri, tr=$('tri'); tr.hidden=!t;
  if(t)tr.innerHTML=`<b>Triage: ${t.category}</b> <span class="chip ${t.suggest==='judge'?'':t.suggest}">${t.suggest==='judge'?'send to climb judge':'suggest '+t.suggest}</span> <small>confidence ${t.conf.toFixed(2)} · matters ${t.sig}/5</small><p style="margin:var(--s1) 0 0">${t.why.replace(/[<>&]/g,'')}</p>`;
  bulkInfo();
  const names={keep:'keep in iCloud',cold:'cold storage'};
  $('mine').textContent=names[c.label]||c.label||'–'; $('mine').className='chip '+c.label;
  $('dupes').innerHTML=c.dupes.length
    ?`<div class="panel" style="padding:var(--s3)"><b>${c.dupes.length+1} identical copies</b><small class="muted"> (same size and content)</small>`
     +c.dupes.map(d=>`<a href="#" data-id="${d.id}">${d.file}</a>`).join('')+'</div>':'';
  document.querySelectorAll('[data-l]').forEach(b=>b.classList.toggle('on',b.dataset.l===c.label));
  $('warn').textContent='';
  ratio=seen[c.id]||ratio; fit();
  v.src='/video/'+c.id;v.play().catch(()=>{});
}
v.onloadedmetadata=()=>{if(v.videoWidth&&v.videoHeight){ratio=v.videoWidth/v.videoHeight;const c=list[i];if(c)seen[c.id]=ratio;fit()}};
new ResizeObserver(fit).observe(box);
$('dupes').onclick=e=>{const a=e.target.closest('a[data-id]'); if(!a)return; e.preventDefault();
  $('view').value='all';$('hide').checked=false;MF.val='';MF.set(all.filter(c=>c.present).map(c=>c.month));refilter(a.dataset.id)};
async function label(l){
  const c=list[i]; if(!c)return;
  const ok=c.kind==='climb'?['send','fall','none','unsure','notclimb']:['keep','cold','unsure'];
  if(!ok.includes(l))return;
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:c.id,label:l})});
  if(!r.ok){$('warn').textContent='Save failed: '+r.status;return}
  undo={id:c.id,prev:c.label||'',was:l};
  if(l==='notclimb'){c.kind='video';c.notclimb=true;c.label='';show();return}   // now the keep / cold question
  c.label=l;
  if($('hide').checked&&DONE.includes(l)){list.splice(i,1)}else{i++}
  if(i>=list.length)i=Math.max(list.length-1,0);
  show();
}
// Undo puts the clip back as it was (its earlier label, or undecided) and returns to it.
// Clear decision does the same for the clip on screen, without needing a previous action.
async function post(c,l,extra){
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({id:c.id,label:l},extra))});
  if(!r.ok)$('warn').textContent='Save failed: '+r.status;
  return r.ok;
}
async function doUndo(){
  if(!undo)return; const u=undo, c=all.find(x=>x.id===u.id); if(!c)return;
  const climb=u.was==='notclimb';   // that decision lives in the climbing log
  if(!await post(c,u.prev||'clear',{undo:true,climb}))return;
  undo=null; await load(c.id);
}
async function doClear(){
  const c=list[i]; if(!c||!(c.label||c.notclimb))return;
  if(!await post(c,'clear',{undo:c.kind==='climb',climb:c.notclimb}))return;
  undo=null; await load(c.id);
}
const go=d=>{i=Math.min(Math.max(i+d,0),list.length-1);show()};
document.querySelectorAll('[data-l]').forEach(b=>b.onclick=()=>label(b.dataset.l));
$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);$('undo').onclick=doUndo;$('clear').onclick=doClear;
$('rot').onclick=()=>{rot=(rot+90)%360;fit()};
$('bkeep').onclick=()=>accept('keep');$('bcold').onclick=()=>accept('cold');
for(const id of ['hide','view','sug'])$(id).onchange=()=>refilter();
MF.cb=()=>refilter();
addEventListener('keydown',e=>{
  if(e.target.tagName==='SELECT'||e.target.closest('#mf')||e.metaKey||e.ctrlKey)return;
  const k=e.key.toLowerCase(), c=list[i];
  const m={s:'send',f:'fall',n:'none',u:'unsure',c:'cold'}; if(c&&c.kind==='climb')m.x='notclimb'; if(c&&c.kind==='video')m.k='keep';
  if(m[k])label(m[k]);
  else if(k==='arrowright'||k==='j')go(1);else if(k==='arrowleft'||k==='k')go(-1);
  else if(k==='r')$('rot').click();
  else if(k==='z')doUndo();
  else if(k===' '){e.preventDefault();v.paused?v.play():v.pause()}
});
load();
</script></body></html>
""".replace("__HEAD__", ui.HEAD).replace("__BRAND__", ui.brand()).replace("__NAV__", ui.nav("videos")).replace("__MF__", ui.MF_HTML).replace("__MFJS__", ui.MF_JS)
