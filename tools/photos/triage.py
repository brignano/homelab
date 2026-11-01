# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "opencv-python-headless>=4.9",
#   "anthropic>=0.70",
#   "numpy<2",
# ]
# ///
"""Sort every video in the iCloud mirror: climbing or not, keep hot or go cold.

Runs on CT 100 (tools/photos/triage.sh wraps it in a container). Four frames
per clip plus its length go to a cheap model (default claude-haiku-5-5), which
answers with a category, how much the clip matters, and a suggestion:

  climbing  -> judge  (the Opus send/fall pass in climb_judge.py decides the rest)
  anything  -> keep   (stay in iCloud: people, trips, events, anything you'd miss)
  anything  -> cold   (the mirror is the copy that stays: pets doing nothing,
                       screen recordings, accidental or near-identical clips)

It only suggests. The result is /out/video-triage.csv; the review page shows
each suggestion and lets you accept a batch with one click. Nothing is deleted
or moved here, and the mirror is read-only.

Resumable: clips already in the CSV are skipped, so a stopped run carries on.
Clips that already have a climb-judge verdict (--skip-verdicts) are skipped too.
Token usage is logged per clip, so the real cost is measured, not estimated.

    tools/photos/triage.sh --limit 50          # a trial
    tools/photos/triage.sh --month 2026/09
"""

from __future__ import annotations

import argparse
import base64
import csv
import glob
import json
import os
import sys
import time
from pathlib import Path

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".heic", ".jpg", ".jpeg", ".png", ".dng"}
LIVE_PHOTO_MARK = "_HEVC"
CATEGORIES = ["climbing", "pet", "people", "travel", "event", "screen", "other"]
SUGGEST = ["judge", "keep", "cold"]
FIELDS = ["file", "category", "significance", "suggest", "confidence", "reason",
          "seconds", "model", "in_tokens", "out_tokens", "error"]
# USD per 1M tokens (input, output); only used to print a running cost.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (0.60, 3.00)}

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "significance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "suggest": {"type": "string", "enum": SUGGEST},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["category", "significance", "suggest", "confidence", "reason"],
    "additionalProperties": False,
}

PROMPT = """These are {n} frames, in order, from one phone video that is {seconds:.0f} seconds long.
It sits in a personal iCloud library, and the owner is deciding which videos stay in iCloud (hot)
and which move to a cold hard-drive copy.

category: climbing (bouldering, rock or gym climbing, a person on a wall or rock, or a view of
holds and pads while someone climbs), pet, people, travel, event, screen (screen recording or a
screenshot-like clip), other.
significance: 1 = nothing worth keeping hot (accidental, pocket recording, a blur, near-empty
scene), 3 = ordinary, 5 = a moment the owner would miss (family, friends, a trip highlight, a
milestone).
suggest: "judge" for climbing clips, which get a separate send/fall pass. Otherwise "keep" if
significance is 3 or more, "cold" if it is 1 or 2.
confidence: 0 to 1 for your suggestion. Be honest; low confidence sends the clip to a human.
reason: one short sentence."""


def sample_times(seconds: float, n: int = 4) -> list[float]:
    """n frames spread across the clip, away from the very first and last frame."""
    if seconds <= 0:
        return [0.0]
    return [round(seconds * (i + 0.5) / n, 1) for i in range(n)]


def parse_reply(text: str) -> dict:
    """Validate the model's JSON; anything off the schema raises ValueError."""
    d = json.loads(text)
    if d["category"] not in CATEGORIES or d["suggest"] not in SUGGEST:
        raise ValueError("unexpected category or suggestion: %r" % d)
    sig = int(d["significance"])
    conf = max(0.0, min(1.0, float(d["confidence"])))
    suggest = d["suggest"]
    if d["category"] == "climbing":
        suggest = "judge"  # one rule, whatever the model said
    elif suggest == "judge":
        suggest = "keep" if sig >= 3 else "cold"
    return {"category": d["category"], "significance": sig, "suggest": suggest,
            "confidence": round(conf, 2), "reason": " ".join(str(d["reason"]).split())[:300]}


def triage_clip(path: Path, client, model: str) -> tuple[dict, int, int, float]:
    import cv2
    from climb_judge import frames_at, open_video
    cap, _, seconds = open_video(path)
    cap.release()
    content: list[dict] = []
    for t, img in frames_at(path, sample_times(seconds), 512):
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(jpg.tobytes()).decode()}})
    if not content:
        raise RuntimeError("no frames decoded")
    content.append({"type": "text", "text": PROMPT.format(n=len(content), seconds=seconds)})
    resp = client.messages.create(
        model=model, max_tokens=600,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}])
    u = resp.usage
    if resp.stop_reason == "refusal":
        raise RuntimeError("declined by the API")
    text = next(b.text for b in resp.content if b.type == "text")
    return parse_reply(text), u.input_tokens, u.output_tokens, seconds


def find_videos(root: Path, month: str = "") -> list[Path]:
    out = []
    base = root / month if month else root
    for dirpath, _dirs, names in os.walk(base):
        stems = {os.path.splitext(n)[0] for n in names if os.path.splitext(n)[1].lower() in IMAGE_EXT}
        for n in sorted(names):
            stem, ext = os.path.splitext(n)
            if ext.lower() not in VIDEO_EXT or stem in stems:
                continue  # not a video, or the motion half of a Live Photo
            if stem.upper().endswith(LIVE_PHOTO_MARK) or LIVE_PHOTO_MARK + "_" in stem.upper():
                continue
            out.append(Path(dirpath) / n)
    return sorted(out)


def read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {r["file"] for r in csv.DictReader(f) if r.get("file") and not r.get("error")}


def read_verdict_names(pattern: str) -> set[str]:
    names = set()
    for p in glob.glob(pattern):
        with open(p, newline="") as f:
            names |= {Path(r["file"]).name for r in csv.DictReader(f) if r.get("file")}
    return names


def append_row(path: Path, row: dict):
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="/photos", help="the iCloud mirror (default /photos in the container)")
    ap.add_argument("--out", default="/out/video-triage.csv")
    ap.add_argument("--skip-verdicts", default="/out/climb-verdicts*.csv",
                    help="glob of climb-judge CSVs; clips listed there are skipped")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--month", default="", help="only this folder under the mirror, e.g. 2026/09")
    ap.add_argument("--limit", type=int, default=0, help="triage at most N new clips")
    args = ap.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("ANTHROPIC_API_KEY is not set; export it before running", file=sys.stderr)
        return 2
    import anthropic
    client = anthropic.Anthropic()

    root, out = Path(args.root), Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = read_done(out)
    judged = read_verdict_names(args.skip_verdicts)
    todo = [p for p in find_videos(root, args.month)
            if str(p.relative_to(root)) not in done and p.name not in judged]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to triage")
        return 0
    pin, pout = PRICES.get(args.model, (0.0, 0.0))
    tin = tout = fails = 0
    t0 = time.time()
    for n, p in enumerate(todo, 1):
        rel = str(p.relative_to(root))
        row = {"file": rel, "model": args.model}
        try:
            res, i, o, secs = triage_clip(p, client, args.model)
            row.update(res, seconds="%.1f" % secs, in_tokens=i, out_tokens=o)
            tin, tout = tin + i, tout + o
            print("[%d/%d] %s  %s %s conf=%.2f  $%.4f so far" % (
                n, len(todo), p.name, res["category"], res["suggest"], res["confidence"],
                (tin * pin + tout * pout) / 1e6), flush=True)
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            fails += 1
            row["error"] = str(e)[:200]
            print("[%d/%d] %s  ERR %s" % (n, len(todo), p.name, row["error"]), flush=True)
        append_row(out, row)
    ok = len(todo) - fails
    cost = (tin * pin + tout * pout) / 1e6
    print("\n%d clips, %d failed, %d s. tokens in %d / out %d. cost $%.3f (%.4f a clip)" % (
        len(todo), fails, time.time() - t0, tin, tout, cost, cost / ok if ok else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
