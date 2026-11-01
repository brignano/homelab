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
                if row.get("file") and lab in ALL_LABELS:
                    labels[row["file"].strip()] = lab
    return labels


_index = {"at": 0.0, "files": {}, "by_name": {}, "dupes": {}}
_fp_cache = {}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".heic", ".jpg", ".jpeg", ".png", ".dng"}
VIDEO_LABELS = ("keep", "cold", "unsure")  # keep in iCloud / move to cold storage


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
                          "live": os.path.splitext(n)[0] in stems}
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
                if row.get("file") and lab in VIDEO_LABELS:
                    out[row["file"].strip()] = lab
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


def append_label(clip: dict, label: str):
    DATA.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if clip["kind"] == "video":
        _append(DATA / "video-review.csv", ["ts", "file", "decision"], [now, clip["file"], label])
        return
    _append(DATA / "review-log.csv", ["ts", "file", "label"], [now, clip["stem"], label])
    if label in HUMAN:  # not "unsure" or "notclimb": --labels rejects them
        _append(DATA / "labels.csv", ["file", "label"], [clip["stem"], label])


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


def export(include_claude: bool, which: str = "climb"):
    rows = []
    for c in build():
        if not c["present"]:
            continue
        why = ""
        if which == "cold":
            if c["kind"] == "video" and c["label"] == "cold":
                why = "cold storage"
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
        if path not in ("/api/label", "/api/dupes/sets", "/api/dupes/decide"):
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
            if label not in (ALL_LABELS if clip["kind"] == "climb" else VIDEO_LABELS):
                return self._send(400, "bad label")
            append_label(clip, label)
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
 <select id="month" aria-label="Month"><option value="">All months</option></select>
 <label><input type="checkbox" id="hide" checked> hide decided</label>
 <span id="count" class="muted"></span>
 <span class="grow"></span>
 <small>Export: <a href="/export.txt" target="_blank">climb</a> · <a href="/export.csv?include=claude" target="_blank">climb + unreviewed Claude calls</a> · <a href="/export.csv?set=cold" target="_blank">cold storage</a></small>
</header>
<main>
 <div id="stagebox"><div id="stage"><video id="v" controls autoplay muted playsinline></video></div><p id="empty" hidden></p></div>
 <aside id="side" class="panel">
  <h2 id="name"></h2>
  <p class="muted" id="meta" style="margin:0"></p>
  <div class="row"><span id="kindchip" class="chip"></span> <span>Your call:</span> <span id="mine" class="chip"></span></div>
  <div class="g-climb group"><div class="row"><span>Claude:</span> <span id="verdict" class="chip"></span> <small id="conf"></small></div>
   <p id="why"></p></div>
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
 <button id="prev">Prev <kbd>←</kbd></button>
 <button id="next">Next <kbd>→</kbd></button>
 <button id="rot">Rotate <kbd>R</kbd></button>
 <small class="grow">Space plays. Nothing is deleted here: cold-storage picks go to a Photos album from the Mac, and the mirror stays as the cold copy.</small>
</footer>
<script>
let all=[],list=[],i=0,rot=0,ratio=16/9;const seen={};
const DONE=['send','fall','none','keep','cold'];
const $=id=>document.getElementById(id), v=$('v'), stage=$('stage'), box=$('stagebox');
const mb=n=>(n/1e6).toFixed(n>1e8?0:1)+' MB';
async function load(keepId){
  all=await (await fetch('/api/clips')).json();
  const months=[...new Set(all.map(c=>c.month).filter(Boolean))].sort().reverse();
  const sel=$('month'), cur=sel.value;
  sel.innerHTML='<option value="">All months</option>'+months.map(m=>`<option>${m}</option>`).join('');
  sel.value=cur; refilter(keepId);
}
function refilter(keepId){
  const m=$('month').value, hide=$('hide').checked, view=$('view').value;
  list=all.filter(c=>c.present&&(!m||c.month===m)
    &&(view==='all'||view===c.kind||(view==='dupes'&&c.dupes.length))
    &&(!hide||!DONE.includes(c.label)||c.id===keepId));
  i=Math.max(0,list.findIndex(c=>c.id===keepId)); show();
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
  stage.hidden=!c; $('empty').hidden=!!c;
  if(!c){v.removeAttribute('src');$('name').textContent='';$('empty').textContent='Nothing left in this view.';return}
  document.body.dataset.kind=c.kind;
  $('name').textContent=c.name;
  $('meta').textContent=`${c.file} · ${mb(c.size)}`;
  $('kindchip').textContent=c.kind==='climb'?'Climbing clip':c.notclimb?'Video (not climbing)':'Video';
  $('verdict').textContent=c.claude||'–'; $('verdict').className='chip '+c.claude;
  $('conf').textContent=c.conf==null?'':`confidence ${c.conf.toFixed(2)}`;
  $('why').textContent=c.why;
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
  $('view').value='all';$('hide').checked=false;$('month').value='';refilter(a.dataset.id)};
async function label(l){
  const c=list[i]; if(!c)return;
  const ok=c.kind==='climb'?['send','fall','none','unsure','notclimb']:['keep','cold','unsure'];
  if(!ok.includes(l))return;
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:c.id,label:l})});
  if(!r.ok){$('warn').textContent='Save failed: '+r.status;return}
  if(l==='notclimb'){c.kind='video';c.notclimb=true;c.label='';show();return}   // now the keep / cold question
  c.label=l;
  if($('hide').checked&&DONE.includes(l)){list.splice(i,1)}else{i++}
  if(i>=list.length)i=Math.max(list.length-1,0);
  show();
}
const go=d=>{i=Math.min(Math.max(i+d,0),list.length-1);show()};
document.querySelectorAll('[data-l]').forEach(b=>b.onclick=()=>label(b.dataset.l));
$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
$('rot').onclick=()=>{rot=(rot+90)%360;fit()};
for(const id of ['month','hide','view'])$(id).onchange=()=>refilter();
addEventListener('keydown',e=>{
  if(e.target.tagName==='SELECT'||e.metaKey||e.ctrlKey)return;
  const k=e.key.toLowerCase(), c=list[i];
  const m={s:'send',f:'fall',n:'none',u:'unsure',c:'cold'}; if(c&&c.kind==='climb')m.x='notclimb'; if(c&&c.kind==='video')m.k='keep';
  if(m[k])label(m[k]);
  else if(k==='arrowright'||k==='j')go(1);else if(k==='arrowleft'||k==='k')go(-1);
  else if(k==='r')$('rot').click();
  else if(k===' '){e.preventDefault();v.paused?v.play():v.pause()}
});
load();
</script></body></html>
""".replace("__HEAD__", ui.HEAD).replace("__BRAND__", ui.brand()).replace("__NAV__", ui.nav("videos"))
