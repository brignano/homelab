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

PHOTOS = Path(os.environ.get("PHOTOS_DIR", "/photos"))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
VERDICT_GLOB = os.environ.get("VERDICT_GLOB", "climb-verdicts-claude-*.csv")
HI_CONF = float(os.environ.get("EXPORT_CLAUDE_CONFIDENCE", "0.8"))

HUMAN = ("send", "fall", "none")  # what labels.csv accepts
ALL_LABELS = HUMAN + ("unsure",)
KEYS = {"s": "send", "f": "fall", "n": "none", "u": "unsure"}
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


def clip_id(rel: str) -> str:
    return hashlib.sha1(rel.encode()).hexdigest()[:12]


def build():
    verdicts = read_verdicts()
    labels = read_log()
    clips = []
    for rel, v in verdicts.items():
        path = PHOTOS / rel
        present = path.is_file() and PHOTOS.resolve() in path.resolve().parents
        stem = Path(rel).stem
        label = ""
        for key, lab in labels.items():
            if stem_matches(key, stem):
                label = lab
        m = MONTH_RE.search(rel)
        clips.append({
            "id": clip_id(rel), "file": rel, "name": path.name, "stem": stem,
            "month": "%s-%s" % m.groups() if m else "",
            "claude": v["claude"], "conf": v["conf"], "why": v["why"],
            "label": label, "present": present,
            "size": path.stat().st_size if present else 0,
        })
    # Queue: not yet reviewed first (unsure counts as not decided), then
    # "none" and low confidence ahead of confident verdicts.
    def key(c):
        done = c["label"] in HUMAN
        rank = -1.0 if c["claude"] == "none" else (c["conf"] if c["conf"] is not None else -0.5)
        return (done, rank, c["file"])
    clips.sort(key=key)
    return clips


def append_label(clip: dict, label: str):
    DATA.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    _append(DATA / "review-log.csv", ["ts", "file", "label"], [now, clip["stem"], label])
    if label in HUMAN:
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


def export(include_claude: bool):
    rows = []
    for c in build():
        if not c["present"]:
            continue
        why = ""
        if c["label"] in ("fall", "none"):
            why = "labelled " + c["label"]
        elif include_claude and not c["label"] and c["claude"] in ("fall", "none") \
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
        if u.path == "/api/clips":
            with _lock:
                clips = build()
            return self._send(200, json.dumps(clips), "application/json")
        if u.path.startswith("/video/"):
            return self._video(u.path[len("/video/"):])
        if u.path in ("/export.csv", "/export.txt"):
            with _lock:
                rows = export(q.get("include", [""])[0] == "claude")
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

    def do_POST(self):
        if urlparse(self.path).path != "/api/label":
            return self._send(404, "not found")
        # A page on another site must not be able to label clips from the
        # browser on the LAN: same-origin JSON only.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self._send(403, "cross-origin")
        if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
            return self._send(415, "json only")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)))
            cid, label = str(body["id"]), str(body["label"])
        except (ValueError, KeyError, TypeError):
            return self._send(400, "bad request")
        if label not in ALL_LABELS:
            return self._send(400, "bad label")
        with _lock:
            clip = next((c for c in build() if c["id"] == cid), None)
            if not clip:
                return self._send(404, "no such clip")
            append_label(clip, label)
        self._send(200, json.dumps({"ok": True, "label": label}), "application/json")

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
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clip review</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}
body{margin:0;display:flex;flex-direction:column;height:100vh}
header,footer{padding:.5rem 1rem;display:flex;gap:.75rem;align-items:center;flex-wrap:wrap}
header{border-bottom:1px solid #8884}
main{flex:1;display:flex;gap:1rem;padding:1rem;min-height:0}
#stage{flex:1;display:flex;align-items:center;justify-content:center;background:#000;border-radius:8px;overflow:hidden;min-width:0}
video{max-width:100%;max-height:100%;transition:transform .2s}
#side{width:22rem;overflow:auto}
button{font:inherit;padding:.4rem .8rem;border-radius:6px;border:1px solid #8886;background:#8881;cursor:pointer}
button b{opacity:.6;margin-left:.3rem}
.tag{padding:.1rem .5rem;border-radius:999px;border:1px solid #8886}
.send{background:#2a74}.fall{background:#c224}.none{background:#8884}.unsure{background:#fa04}
#why{white-space:pre-wrap}
small{opacity:.7}
</style></head><body>
<header>
 <b>Clip review</b>
 <label>Month <select id="month"><option value="">all</option></select></label>
 <label><input type="checkbox" id="hide" checked> hide reviewed</label>
 <span id="count"></span>
 <span style="flex:1"></span>
 <a href="/export.txt" target="_blank">candidates (names)</a>
 <a href="/export.csv" target="_blank">csv</a>
 <a href="/export.csv?include=claude" target="_blank">csv + unreviewed Claude calls</a>
</header>
<main>
 <div id="stage"><video id="v" controls autoplay muted playsinline></video></div>
 <div id="side">
  <h3 id="name"></h3>
  <p><small id="meta"></small></p>
  <p>Claude: <span id="verdict" class="tag"></span> <span id="conf"></span></p>
  <p id="why"></p>
  <p>Your label: <span id="mine" class="tag"></span></p>
  <p id="warn" style="color:#c33"></p>
 </div>
</main>
<footer>
 <button data-l="send">Send<b>S</b></button>
 <button data-l="fall">Fall<b>F</b></button>
 <button data-l="none">None<b>N</b></button>
 <button data-l="unsure">Unsure<b>U</b></button>
 <span style="width:1rem"></span>
 <button id="prev">Prev<b>←</b></button>
 <button id="next">Next<b>→</b></button>
 <button id="rot">Rotate<b>R</b></button>
 <small>Space plays/pauses. Labels go to labels.csv; nothing is ever deleted here.</small>
</footer>
<script>
let all=[],list=[],i=0,rot=0;
const $=id=>document.getElementById(id), v=$('v');
async function load(keepId){
  all=await (await fetch('/api/clips')).json();
  const months=[...new Set(all.map(c=>c.month).filter(Boolean))].sort();
  const sel=$('month'), cur=sel.value;
  sel.innerHTML='<option value="">all</option>'+months.map(m=>`<option>${m}</option>`).join('');
  sel.value=cur; refilter(keepId);
}
function refilter(keepId){
  const m=$('month').value, hide=$('hide').checked;
  list=all.filter(c=>(!m||c.month===m)&&(!hide||!['send','fall','none'].includes(c.label)||c.id===keepId));
  i=Math.max(0,list.findIndex(c=>c.id===keepId)); show();
}
function show(){
  const done=all.filter(c=>['send','fall','none'].includes(c.label)).length;
  $('count').textContent=`${list.length?i+1:0}/${list.length} · ${done}/${all.length} labelled`;
  const c=list[i];
  if(!c){v.removeAttribute('src');$('name').textContent='Nothing left in this view';return}
  $('name').textContent=c.name;
  $('meta').textContent=`${c.file} · ${(c.size/1e6).toFixed(1)} MB`;
  $('verdict').textContent=c.claude||'-'; $('verdict').className='tag '+c.claude;
  $('conf').textContent=c.conf==null?'':`confidence ${c.conf.toFixed(2)}`;
  $('why').textContent=c.why;
  $('mine').textContent=c.label||'-'; $('mine').className='tag '+c.label;
  $('warn').textContent=c.present?'':'Not in the mirror (yet?): cannot play, will not be exported.';
  if(c.present){v.src='/video/'+c.id;v.play().catch(()=>{})}else v.removeAttribute('src');
  v.style.transform=`rotate(${rot}deg)`;
}
async function label(l){
  const c=list[i]; if(!c)return;
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:c.id,label:l})});
  if(!r.ok){$('warn').textContent='Save failed: '+r.status;return}
  c.label=l; const next=list[i+1];
  all=all.map(x=>x.id===c.id?c:x);
  if($('hide').checked&&['send','fall','none'].includes(l)){list.splice(i,1)}else{i++}
  if(i>=list.length)i=Math.max(list.length-1,0);
  show();
}
const go=d=>{i=Math.min(Math.max(i+d,0),list.length-1);show()};
document.querySelectorAll('[data-l]').forEach(b=>b.onclick=()=>label(b.dataset.l));
$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);
$('rot').onclick=()=>{rot=(rot+90)%360;v.style.transform=`rotate(${rot}deg)`};
$('month').onchange=()=>refilter();$('hide').onchange=()=>refilter();
addEventListener('keydown',e=>{
  if(e.target.tagName==='SELECT'||e.metaKey||e.ctrlKey)return;
  const k=e.key.toLowerCase();
  if(k==='s')label('send');else if(k==='f')label('fall');else if(k==='n')label('none');else if(k==='u')label('unsure');
  else if(k==='arrowright'||k==='j')go(1);else if(k==='arrowleft'||k==='k')go(-1);
  else if(k==='r')$('rot').click();
  else if(k===' '){e.preventDefault();v.paused?v.play():v.pause()}
});
load();
</script></body></html>
"""
