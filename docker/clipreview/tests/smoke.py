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
(tmp / "assets/fonts").mkdir(parents=True)
(tmp / "assets/design-tokens.css").write_text(":root{--bg:#fff}")
(tmp / "assets/fonts/geist.woff2").write_bytes(b"wOF2")
os.environ["ASSETS_DIR"] = str(tmp / "assets")
# videos with no climb verdict: a pair of identical copies, a lone one, a Live Photo half
(tmp / "photos/2026/09").mkdir(parents=True)
(tmp / "photos/2026/09/IMG_20.MOV").write_bytes(b"A" * 3000)
(tmp / "photos/2026/09/IMG_20 (1).MOV").write_bytes(b"A" * 3000)
(tmp / "photos/2026/09/IMG_21.MOV").write_bytes(b"B" * 5000)
(tmp / "photos/2026/09/IMG_22.MOV").write_bytes(b"C" * 10)
(tmp / "photos/2026/09/IMG_22.HEIC").write_bytes(b"h")
# triage: suggestions per video, a Live Photo half named _HEVC that must stay hidden
(tmp / "photos/2026/08").mkdir(parents=True)
for n, size in (("IMG_40.MOV", 4000), ("IMG_41.MOV", 2000), ("IMG_43.MOV", 1500), ("IMG_44.MOV", 1000)):
    (tmp / "photos/2026/08" / n).write_bytes(n[5:6].encode() * size)
(tmp / "photos/2026/08/IMG_42_HEVC.MOV").write_bytes(b"live")
(tmp / "photos/2026/08/IMG_46_HEVC_abc1234.MOV").write_bytes(b"live2")
with open(tmp / "data/video-triage.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["file", "category", "significance", "suggest", "confidence", "reason", "seconds",
                "model", "in_tokens", "out_tokens", "error"])
    w.writerow(["2026/08/IMG_40.MOV", "pet", "1", "cold", "0.90", "cat <b>asleep</b>", "5", "m", "1", "1", ""])
    w.writerow(["2026/08/IMG_41.MOV", "people", "4", "keep", "0.90", "family", "5", "m", "1", "1", ""])
    w.writerow(["2026/08/IMG_43.MOV", "climbing", "3", "judge", "0.80", "wall", "5", "m", "1", "1", ""])
    w.writerow(["2026/08/IMG_44.MOV", "pet", "2", "cold", "0.40", "dark", "5", "m", "1", "1", ""])
    w.writerow(["2026/09/IMG_21.MOV", "other", "", "", "", "", "", "m", "", "", "boom"])
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
assert [c["name"][:5] for c in clips if c["kind"] == "climb"][:2] == ["IMG_3", "IMG_2"], clips  # none first, then low conf
assert [c["name"] for c in clips if c["kind"] == "video"] == ["IMG_20 (1).MOV", "IMG_20.MOV", "IMG_21.MOV", "IMG_40.MOV", "IMG_41.MOV", "IMG_43.MOV", "IMG_44.MOV"], clips  # dupes first; the Live Photo half is not a video
d20 = [c for c in clips if c["name"] == "IMG_20.MOV"][0]
assert [d["file"] for d in d20["dupes"]] == ["2026/09/IMG_20 (1).MOV"] and d20["month"] == "2026-09", d20
assert clips[-1]["label"] == "send" and clips[-1]["name"].startswith("IMG_1")  # hand label seen
assert not [c for c in clips if "missing" in c["name"]][0]["present"]
c3 = [c for c in clips if c["name"].startswith("IMG_3")][0]
assert c3["present"] and c3["month"] == "2026-10" and c3["file"] == "2026/10/IMG_3_ccccccc.MOV", c3

by = {c["name"][:5]: c for c in clips if c["kind"] == "climb"}
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

# --- "not climbing": a verdict clip that leaves the climbing flow -------------
nc = by["IMG_3"]
post({"id": nc["id"], "label": "notclimb"})
assert "IMG_3" not in open(tmp / "data/labels.csv").read()           # --labels would reject it
assert "notclimb" in open(tmp / "data/review-log.csv").read()
c3 = [c for c in json.load(get("/api/clips")) if c["name"].startswith("IMG_3")][0]
assert c3["kind"] == "video" and c3["notclimb"] and c3["label"] == "" and c3["claude"] == "none", c3
assert "IMG_3" not in get("/export.csv?include=claude").read().decode()
post({"id": c3["id"], "label": "cold"})                              # now takes the video labels
c3 = [c for c in json.load(get("/api/clips")) if c["name"].startswith("IMG_3")][0]
assert c3["label"] == "cold" and "IMG_3" in get("/export.csv?set=cold").read().decode()
try:
    post({"id": c3["id"], "label": "send"})
    raise SystemExit("expected an error")
except urllib.error.HTTPError as e:
    assert e.code == 400
# Claude's "none" alone is never a removal candidate; a confident "fall" is
# --- other videos: keep in iCloud / cold storage ------------------------------
vid = [c for c in clips if c["name"] == "IMG_21.MOV"][0]
post({"id": vid["id"], "label": "cold"})
post({"id": d20["id"], "label": "keep"})
rows = list(csv.DictReader(open(tmp / "data/video-review.csv")))
assert [(r["file"], r["decision"]) for r in rows[1:]] == [("2026/09/IMG_21.MOV", "cold"), ("2026/09/IMG_20.MOV", "keep")], rows
assert get("/export.txt").read().decode() == "IMG_2_bbbbbbb.MOV\n"  # climb export untouched by video picks
assert get("/export.csv?set=cold").read().decode().count("IMG_21.MOV") == 2  # name + mirror_path
assert "IMG_20" not in get("/export.csv?set=cold").read().decode()
after = {c["name"]: c["label"] for c in json.load(get("/api/clips"))}
assert after["IMG_21.MOV"] == "cold" and after["IMG_20.MOV"] == "keep"
for bad in (lambda: post({"id": vid["id"], "label": "send"}),       # climb labels do not apply
            lambda: post({"id": by["IMG_1"]["id"], "label": "cold"})):
    try:
        bad()
        raise SystemExit("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
assert get("/assets/design-tokens.css").read() == b":root{--bg:#fff}"
assert get("/assets/fonts/geist.woff2").read() == b"wOF2"
for bad in ("/assets/../server.py", "/assets/fonts/..%2f..%2fdesign-tokens.css", "/assets/fonts/x.txt"):
    try:
        get(bad)
        raise SystemExit("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code == 404, (bad, e.code)

# --- triage ---------------------------------------------------------------------
tri = {c["name"]: c["tri"] for c in json.load(get("/api/clips")) if c["kind"] == "video"}
assert tri["IMG_40.MOV"]["suggest"] == "cold" and tri["IMG_43.MOV"]["category"] == "climbing", tri
assert tri["IMG_21.MOV"] is None and tri["IMG_20.MOV"] is None, tri          # error rows are ignored
assert not [n for n in tri if "HEVC" in n]                                    # Live Photo halves are not videos
assert get("/export.txt?set=judge").read().decode() == "IMG_43.MOV\n"
def jpost(path, body, **h):
    h.setdefault("Content-Type", "application/json")
    return urllib.request.urlopen(urllib.request.Request(base + path, json.dumps(body).encode(), h))
def accept(**b):
    b.setdefault("min_conf", 0.7)
    return json.load(jpost("/api/triage/accept", b))["count"]
assert accept(suggest="cold") == 1 and accept(suggest="keep") == 1             # IMG_44 is under 0.7; IMG_43 is not keep/cold
assert accept(suggest="cold", min_conf=0.3) == 2 and accept(suggest="cold", month="2026-09") == 0
assert not [c for c in json.load(get("/api/clips")) if c["name"] == "IMG_40.MOV"][0]["label"]   # dry run writes nothing
assert accept(suggest="cold", dry=False) == 1 and accept(suggest="keep", dry=False) == 1
dec = {c["name"]: c["label"] for c in json.load(get("/api/clips"))}
assert dec["IMG_40.MOV"] == "cold" and dec["IMG_41.MOV"] == "keep" and dec["IMG_44.MOV"] == "" and dec["IMG_43.MOV"] == "", dec
assert accept(suggest="cold", dry=False) == 0                                  # decided ones are never overwritten
for bad in ({"suggest": "judge"}, {"suggest": "delete"}, {"suggest": "cold", "min_conf": "x"}):
    try:
        accept(**bad)
        raise SystemExit("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
try:
    jpost("/api/triage/accept", {"suggest": "cold", "min_conf": 0.1, "dry": False},
                                                          Origin="http://evil.example")
    raise SystemExit("expected an error")
except urllib.error.HTTPError as e:
    assert e.code == 403
assert "IMG_44" not in get("/export.csv?set=cold").read().decode()

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
conf = {x["id"]: (x["confidence"], x["auto_ok"]) for x in v}
assert conf == {"abc00002": ("high", True), "abc00001": ("low", False)}, conf  # tied scores: look at it
assert "too close" in [x for x in v if x["id"] == "abc00001"][0]["why"]
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
rep = json.load(get("/api/dupes"))["report"]
# abc00002 agreed with its proposal; abc00001 ended as "keep all"
assert rep["tiers"]["high"] == {"decided": 1, "agreed": 1, "picked_other": 0, "kept_all": 1 - 1}, rep
assert rep["tiers"]["low"]["kept_all"] == 1 and rep["total"]["decided"] == 2, rep
# keep several, keep none, clear
jpost("/api/dupes/decide", {"set": "abc00001", "keep": UA + "," + UB})   # keep two, remove UC
acc = json.load(get("/api/dupes/accepted.json"))
assert UC not in acc["uuids"] and any(x["uuid"] == UC for x in acc["skipped"]), acc  # similar + not in mirror: held back
assert UA not in acc["uuids"] and UB not in acc["uuids"], acc
jpost("/api/dupes/decide", {"set": "abc00001", "keep": "none"})         # remove all three; only mirror-confirmed ones go
acc = json.load(get("/api/dupes/accepted.json"))
assert UA in acc["uuids"] and UB not in acc["uuids"] and UC not in acc["uuids"], acc
jpost("/api/dupes/decide", {"set": "abc00002", "keep": "none"})         # exact, but nothing kept: needs the mirror too
acc = json.load(get("/api/dupes/accepted.json"))
assert UD not in acc["uuids"] and UE not in acc["uuids"], acc
jpost("/api/dupes/decide", {"set": "abc00002", "keep": ""})
assert [x for x in json.load(get("/api/dupes"))["sets"] if x["id"] == "abc00002"][0]["decision"] == ""
jpost("/api/dupes/decide", {"set": "abc00002", "keep": UD})
jpost("/api/dupes/decide", {"set": "abc00001", "keep": "all"})
for bad in (lambda: jpost("/api/dupes/decide", {"set": "abc00001", "keep": "not-a-member"}),
            lambda: jpost("/api/dupes/decide", {"set": "abc00001", "keep": UA + "," + UB + "," + UC}),  # all kept = use "all"
            lambda: jpost("/api/dupes/decide", {"set": "abc00001", "keep": UA + "," + UA}),
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
# --- branding ------------------------------------------------------------------
for path, ctype in (("/icons/favicon.svg", "image/svg+xml"), ("/favicon.ico", "image/x-icon"),
                    ("/apple-touch-icon.png", "image/png"), ("/site.webmanifest", "application/manifest+json")):
    r = get(path)
    assert r.status == 200 and r.headers["Content-Type"].startswith(ctype) and r.read(), path
assert json.load(get("/site.webmanifest"))["short_name"] == "Review"
for bad in ("/icons/../server.py", "/icons/nope.png", "/icons/%2e%2e/dupes.py"):
    try:
        get(bad)
        raise SystemExit("expected 404 for " + bad)
    except urllib.error.HTTPError as e:
        assert e.code == 404, (bad, e.code)
for path, title in (("/", "Videos"), ("/dupes", "Duplicate photos")):
    page = get(path).read().decode()
    assert "&gt;&gt;&gt;&gt;&gt;&gt;&gt;" not in page and ">>>>>>>" not in page and "<<<<<<<" not in page, path  # merge-conflict leftovers
    assert page.count("<header>") == page.count("</header>") == 1, path
    assert "id=\"mfy\"" in page and "const MF=" in page and "__MF" not in page and "id=\"month\"" not in page, path  # shared date filter
    assert "<title>%s \u00b7 Review</title>" % title in page, path
    assert 'rel="manifest"' in page and 'rel="apple-touch-icon"' in page and 'name="theme-color"' in page, path
    assert 'class="logo"' in page and "viewport-fit=cover" in page, path
print("ok")
