"""Offline smoke test: labels append in climb-judge's format, the queue order,
export only lists files present in the mirror, and video is served by id only."""
import csv
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
tmp = Path(tempfile.mkdtemp())
(tmp / "photos/2026/10").mkdir(parents=True)
(tmp / "data").mkdir()
for n in ("IMG_1_aaaaaaa.MOV", "IMG_2_bbbbbbb.MOV", "IMG_3_ccccccc.MOV"):
    (tmp / "photos/2026/10" / n).write_bytes(b"0123456789")
os.environ["PHOTOS_DIR"] = str(tmp / "photos")
os.environ["DATA_DIR"] = str(tmp / "data")
with open(tmp / "data/climb-verdicts-claude-2026-10.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["file", "seconds", "label", "local", "local_why", "active_seconds",
                "claude", "claude_confidence", "claude_why", "errors"])
    w.writerow(["2026/10/IMG_1_aaaaaaa.MOV", "9", "", "", "", "0", "send", "0.95", "topped", ""])
    w.writerow(["2026/10/IMG_2_bbbbbbb.MOV", "9", "", "", "", "0", "fall", "0.90", "dropped", ""])
    w.writerow(["2026/10/IMG_3_ccccccc.MOV", "9", "", "", "", "0", "none", "0.50", "empty", ""])
    w.writerow(["2026/10/IMG_9_missing.MOV", "9", "", "", "", "0", "fall", "0.99", "gone", ""])
(tmp / "data/labels.csv").write_text("file,label\nIMG_1,send")  # no trailing newline

from app import server  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_port


def get(p, **h):
    return urllib.request.urlopen(urllib.request.Request(base + p, headers=h))


def post(body, **h):
    h.setdefault("Content-Type", "application/json")
    return urllib.request.urlopen(urllib.request.Request(
        base + "/api/label", json.dumps(body).encode(), h))


clips = json.load(get("/api/clips"))
assert [c["name"][:5] for c in clips][:2] == ["IMG_3", "IMG_2"], clips  # none first, then low conf
assert clips[-1]["label"] == "send" and clips[-1]["name"].startswith("IMG_1")  # hand label seen
assert not [c for c in clips if "missing" in c["name"]][0]["present"]

by = {c["name"][:5]: c for c in clips}
post({"id": by["IMG_2"]["id"], "label": "fall"})
post({"id": by["IMG_3"]["id"], "label": "unsure"})
rows = list(csv.DictReader(open(tmp / "data/labels.csv")))
assert rows == [{"file": "IMG_1", "label": "send"},
                {"file": "IMG_2_bbbbbbb", "label": "fall"}], rows  # unsure never in labels.csv

assert get("/export.txt").read().decode() == "IMG_2_bbbbbbb.MOV\n"  # missing one excluded
assert "IMG_9" not in get("/export.csv?include=claude").read().decode()

r = get("/video/" + by["IMG_1"]["id"], Range="bytes=2-4")
assert r.status == 206 and r.read() == b"234"

for bad in (lambda: post({"id": "../../etc/passwd", "label": "fall"}),
            lambda: post({"id": by["IMG_1"]["id"], "label": "delete"}),
            lambda: post({"id": by["IMG_1"]["id"], "label": "fall"}, Origin="http://evil.example"),
            lambda: get("/video/../../etc/passwd")):
    try:
        bad()
        raise SystemExit("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 403, 404), e.code

# --- duplicate photos --------------------------------------------------------
(tmp / "photos/2026/10").mkdir(parents=True, exist_ok=True)
(tmp / "photos/2026/10/IMG_5_zzzzzzz.HEIC").write_bytes(b"x" * 100)
(tmp / "photos/2026/10/IMG_5_zzzzzzz.HEIC.xmp").write_bytes(b"<x/>")
(tmp / "photos/2026/10/IMG_6_yyyyyyy.HEIC").write_bytes(b"x" * 50)  # size differs from Photos
UA, UB, UC, UD, UE, UF = ("%08d-0000" % n for n in range(1, 7))
def mem(u, fn, size, keep=False):
    return {"uuid": u, "filename": fn, "date": "2026-10-15T12:00:00", "bytes": size,
            "score": 0.5, "keep": keep}
payload = {"sets": [
    {"id": "abc00001", "kind": "similar", "members": [mem(UA, "IMG_5.HEIC", 100, True), mem(UB, "IMG_6.HEIC", 60),
                                                     mem(UC, "IMG_7.HEIC", 60)]},
    {"id": "abc00002", "kind": "exact", "members": [mem(UD, "IMG_8.HEIC", 80, True), mem(UE, "IMG_9.HEIC", 80)]}]}
def jpost(path, body, **h):
    h.setdefault("Content-Type", "application/json")
    return urllib.request.urlopen(urllib.request.Request(base + path, json.dumps(body).encode(), h))
r = json.load(jpost("/api/dupes/sets", payload))
assert r["need_thumbs"] and UA in r["need_thumbs"], r
urllib.request.urlopen(urllib.request.Request(base + "/api/dupes/thumb/" + UA, b"\xff\xd8jpeg", method="PUT"))
assert get("/dupes/thumb/" + UA).read() == b"\xff\xd8jpeg"
v = json.load(get("/api/dupes"))["sets"]
assert [s["id"] for s in v] == ["abc00002", "abc00001"]  # exact first
states = {m["uuid"]: m["mirror"] for s in v for m in s["members"]}
assert states[UA] == "ok" and states[UB] == "size" and states[UC] == "missing", states
# nothing accepted yet
assert json.load(get("/api/dupes/accepted.json"))["uuids"] == []
jpost("/api/dupes/decide", {"set": "abc00001", "keep": UA})
jpost("/api/dupes/decide", {"set": "abc00002", "keep": UD})
acc = json.load(get("/api/dupes/accepted.json"))
# exact extra is accepted without a mirror copy; similar extras are held back (not confirmed)
assert acc["uuids"] == [UE] and sorted(x["uuid"] for x in acc["skipped"]) == [UB, UC], acc
# choosing the mirror-confirmed shot as the extra: keep UB means UA (ok) and UC (missing) removed
jpost("/api/dupes/decide", {"set": "abc00001", "keep": UB})
acc = json.load(get("/api/dupes/accepted.json"))
assert UA in acc["uuids"] and UC not in acc["uuids"], acc
jpost("/api/dupes/decide", {"set": "abc00001", "keep": "all"})
assert json.load(get("/api/dupes/accepted.json"))["uuids"] == [UE]
for bad in (lambda: jpost("/api/dupes/decide", {"set": "abc00001", "keep": "not-a-member"}),
            lambda: jpost("/api/dupes/decide", {"set": "nope", "keep": "all"}),
            lambda: jpost("/api/dupes/sets", {"sets": [{"id": "x", "kind": "exact", "members": []}]}),
            lambda: jpost("/api/dupes/decide", {"set": "abc00001", "keep": "all"}, Origin="http://evil.example"),
            lambda: urllib.request.urlopen(urllib.request.Request(base + "/api/dupes/thumb/..%2f..%2fetc", b"\xff\xd8", method="PUT")),
            lambda: get("/dupes/thumb/..%2f..%2fetc%2fpasswd")):
    try:
        bad()
        raise SystemExit("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 403, 404), e.code
assert b"Duplicate photos" in get("/dupes").read()
print("ok")
