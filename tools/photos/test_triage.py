"""Run: python tools/photos/test_triage.py  (no cv2, API or Photos needed)"""
import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage

assert triage.sample_times(8) == [1.0, 3.0, 5.0, 7.0] and triage.sample_times(0) == [0.0]


def reply(**kw):
    d = {"category": "pet", "significance": 2, "suggest": "cold", "confidence": 0.9, "reason": "cat\nasleep"}
    d.update(kw)
    return json.dumps(d)


assert triage.parse_reply(reply())["reason"] == "cat asleep"
assert triage.parse_reply(reply(category="climbing", suggest="keep"))["suggest"] == "judge"
assert triage.parse_reply(reply(suggest="judge", significance=4))["suggest"] == "keep"   # judge only for climbing
assert triage.parse_reply(reply(suggest="judge", significance=1))["suggest"] == "cold"
assert triage.parse_reply(reply(confidence=7))["confidence"] == 1.0
for bad in (reply(category="dog"), reply(suggest="delete"), "{}", "not json"):
    try:
        triage.parse_reply(bad)
        raise SystemExit("expected an error: " + bad)
    except (ValueError, KeyError):
        pass

root = Path(tempfile.mkdtemp())
(root / "2026/09").mkdir(parents=True)
for n in ("IMG_1.MOV", "IMG_2.mp4", "IMG_3_HEVC.MOV", "IMG_4_HEVC_abc1234.MOV", "IMG_5.MOV", "IMG_5.HEIC", "IMG_6.HEIC", "a.xmp"):
    (root / "2026/09" / n).write_bytes(b"x")
assert [p.name for p in triage.find_videos(root)] == ["IMG_1.MOV", "IMG_2.mp4"]
assert [p.name for p in triage.find_videos(root, "2026/10")] == []

out = root / "t.csv"
assert triage.read_done(out) == set()
triage.append_row(out, {"file": "2026/09/IMG_1.MOV", "category": "pet"})
triage.append_row(out, {"file": "2026/09/IMG_2.mp4", "error": "boom"})
rows = list(csv.DictReader(open(out)))
assert [r["file"] for r in rows] == ["2026/09/IMG_1.MOV", "2026/09/IMG_2.mp4"] and list(rows[0]) == triage.FIELDS
assert triage.read_done(out) == {"2026/09/IMG_1.MOV"}   # errors are retried
print("triage ok")
