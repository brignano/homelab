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
    w.writerow(["IMG_3_ccccccc.MOV", "9", "", "", "", "0", "none", "0.50", "empty", ""])
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
c3 = [c for c in clips if c["name"].startswith("IMG_3")][0]
assert c3["present"] and c3["month"] == "2026-10" and c3["file"] == "2026/10/IMG_3_ccccccc.MOV", c3

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
print("ok")
