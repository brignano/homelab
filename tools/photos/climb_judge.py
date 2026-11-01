# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "opencv-python-headless>=4.9",
#   "mediapipe==0.10.14",
#   "anthropic>=0.70",
#   "numpy<2",
# ]
# ///
"""Did this climbing clip end in a send or a fall? Two ways of telling, compared.

Runs on CT 100 against the iCloud mirror (tools/photos/climb-judge.sh wraps it
in a container). Nothing in Photos records whether a clip is a send, so this
looks at the video itself, two ways:

  local   MediaPipe body tracking on the box. Follows the climber's hips; a
          send is a pause at the high point (matching the top hold) before
          any drop, a fall is a fast drop with no pause. Free, and nothing
          leaves the house. Clips run by hand from a phone are hard on it.
  claude  A dozen frames per clip, with timestamps, sent to the Claude API,
          which says send / fall / no climb. Costs money (about $0.02 a clip)
          and the frames leave the house.

With --labels, each method is scored against your own answers, so you can see
which one to trust before ~560 clips depend on it:

    tools/photos/climb-judge.sh --labels /out/labels.csv

labels.csv is two columns, a file name (or its start, e.g. IMG_5069) and
send / fall / none:

    file,label
    IMG_5069,send
    IMG_5102,fall

Read-only on the mirror. It writes one CSV of verdicts (--out) and changes
nothing in iCloud or Photos; acting on the verdicts is a later, separate step.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
# icloudpd saves the 1-3 s motion half of a Live Photo as IMG_1234_HEVC.MOV (or
# IMG_1234_HEVC_<id>.MOV with name-id7). They are never climbs, and in a month
# of the mirror they outnumber real videos about four to one.
LIVE_PHOTO_MARK = "_HEVC"
VERDICTS = ("send", "fall", "none", "unsure")


@dataclass
class Clip:
    path: Path
    label: str = ""
    seconds: float = 0.0
    local: str = ""
    local_why: str = ""
    active_seconds: float = 0.0
    claude: str = ""
    claude_conf: float = 0.0
    claude_why: str = ""
    errors: list[str] = field(default_factory=list)


# --- reading frames ---------------------------------------------------------

def open_video(path: Path) -> tuple[cv2.VideoCapture, float, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("cannot open (codec?)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    return cap, fps, frames / fps if fps else 0.0


def shrink(img: np.ndarray, longest: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = longest / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img


def frames_every(path: Path, step_s: float, longest: int):
    """(t, frame) every step_s seconds. Grabs without decoding the frames in
    between, which matters for 4K60 HEVC."""
    cap, fps, _ = open_video(path)
    every = max(1, round(fps * step_s))
    i = 0
    try:
        while True:
            if not cap.grab():
                break
            if i % every == 0:
                ok, img = cap.retrieve()
                if ok:
                    yield i / fps, shrink(img, longest)
            i += 1
    finally:
        cap.release()


def frames_at(path: Path, times: list[float], longest: int) -> list[tuple[float, np.ndarray]]:
    cap, _, _ = open_video(path)
    out = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, img = cap.read()
            if ok:
                out.append((t, shrink(img, longest)))
    finally:
        cap.release()
    return out


# --- local: body tracking ---------------------------------------------------

def judge_local(path: Path, pose) -> tuple[str, str, float]:
    """Returns (verdict, why, active_seconds). Heights are 1 - hip y, in
    frame heights, so 0 is the bottom edge and 1 the top."""
    ts, hs, hands_up = [], [], []
    for t, img in frames_every(path, 0.2, 640):
        res = pose.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        lm = res.pose_landmarks.landmark if res.pose_landmarks else None
        if lm is None or min(lm[23].visibility, lm[24].visibility) < 0.5:
            continue
        ts.append(t)
        hs.append(1 - (lm[23].y + lm[24].y) / 2)
        # Both wrists above the nose: reaching or holding something overhead.
        hands_up.append(lm[15].y < lm[0].y and lm[16].y < lm[0].y)
    if len(ts) < 10:
        return "none", "no climber tracked", 0.0
    t = np.array(ts)
    h = np.array(hs)
    # Median of 3 against single-frame tracking jumps.
    h = np.array([np.median(h[max(0, i - 1):i + 2]) for i in range(len(h))])

    moving = np.abs(np.diff(h)) > 0.01
    active = float(np.sum(np.diff(t)[moving])) if len(t) > 1 else 0.0
    if h.max() - h.min() < 0.2:
        return "none", "climber never left the ground (in frame)", active

    # The biggest drop within one second: falls and jump-offs both make one.
    best, at = 0.0, None
    for i in range(len(t)):
        j = np.searchsorted(t, t[i] + 1.0, side="right")
        if j > i + 1:
            d = h[i] - h[i + 1:j].min()
            if d > best:
                best, at = d, i
    if at is None or best < 0.25:
        # Climbed and came down slowly: a downclimb after the top, or lowered.
        return "send", "no fast drop (%.2f); came down under control" % best, active

    # A send pauses at the top before letting go: matched the last hold.
    top = h[at]
    pause = 0.0
    k = at
    while k > 0 and h[k - 1] >= top - 0.05 and t[at] - t[k - 1] <= 4.0:
        k -= 1
    pause = t[at] - t[k]
    high = top >= h.max() - 0.05
    up = any(hands_up[k:at + 1])
    if high and pause >= 1.2 and up:
        return "send", "held the high point %.1fs, hands up, then dropped" % pause, active
    return "fall", "dropped %.2f frame heights after %.1fs at that height%s" % (
        best, pause, "" if high else ", below its own high point"), active


# --- claude: sampled frames --------------------------------------------------

PROMPT = """These are {n} frames from one climbing video ({seconds:.0f} s long), \
in order, each labelled with its time in seconds. It is a phone video of a \
climber, usually bouldering or a short route.

Decide how the attempt ended:
- "send": the climber completed the climb - reached and controlled the top \
hold(s) or topped out - and only then came down (jumped, downclimbed or was lowered).
- "fall": the climber came off before completing it.
- "none": no climbing attempt in the frames (setup, chatting, walking).
- "unsure": the frames don't show enough to tell.

If the clip has several attempts, judge the last one. Say which frames decided it."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "confidence", "reason"],
    "additionalProperties": False,
}


def judge_claude(path: Path, seconds: float, client, model: str, frames: int) -> tuple[str, float, str]:
    # Evenly spaced, plus extra density over the last third, where most
    # attempts end.
    n_even = max(4, frames - 4)
    times = [seconds * (i + 0.5) / n_even for i in range(n_even)]
    times += [seconds * (0.7 + 0.075 * i) for i in range(4)]
    times = sorted({round(min(x, max(seconds - 0.1, 0)), 1) for x in times})
    content: list[dict] = []
    for t, img in frames_at(path, times, 768):
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            continue
        content.append({"type": "text", "text": "t=%.1fs" % t})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpg.tobytes()).decode()}})
    if not content:
        raise RuntimeError("no frames decoded")
    content.append({"type": "text", "text": PROMPT.format(n=len(content) // 2, seconds=seconds)})

    resp = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        return "unsure", 0.0, "declined by the API"
    text = next(b.text for b in resp.content if b.type == "text")
    out = json.loads(text)
    return out["verdict"], float(out["confidence"]), out["reason"]


# --- running and scoring -----------------------------------------------------

def is_live_photo(path: Path) -> bool:
    stem = path.stem.upper()
    return stem.endswith(LIVE_PHOTO_MARK) or LIVE_PHOTO_MARK + "_" in stem


def find_clips(root: Path, labels: dict[str, str]) -> list[Clip]:
    files = [p for p in root.rglob("*")
             if p.suffix.lower() in VIDEO_EXT and p.is_file() and not is_live_photo(p)]
    if not labels:
        return [Clip(p) for p in sorted(files)]
    clips = []
    for name, label in labels.items():
        stem = Path(name).stem
        hits = [p for p in files if p.stem == stem or p.stem.startswith(stem + "_")]
        if not hits:
            print("not in the mirror (yet?): %s" % name, file=sys.stderr)
            continue
        clips += [Clip(p, label) for p in hits]
    return clips


def read_labels(path: str) -> dict[str, str]:
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            lab = (row.get("label") or "").strip().lower()
            if row.get("file") and lab in VERDICTS:
                out[row["file"].strip()] = lab
            elif row.get("file"):
                print("ignored (label must be send/fall/none): %s" % row, file=sys.stderr)
    return out


def score(clips: list[Clip], attr: str) -> str:
    rated = [c for c in clips if c.label and getattr(c, attr)]
    if not rated:
        return "%-7s no labelled clips judged" % attr
    right = sum(getattr(c, attr) == c.label for c in rated)
    # The costly mistake: calling a send a fall, since falls are what get
    # moved off iCloud.
    lost = sum(c.label == "send" and getattr(c, attr) == "fall" for c in rated)
    unsure = sum(getattr(c, attr) == "unsure" for c in rated)
    return "%-7s %d/%d right (%.0f%%), %d sends called falls, %d unsure" % (
        attr, right, len(rated), 100 * right / len(rated), lost, unsure)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="/photos", help="the iCloud mirror (default /photos in the container)")
    ap.add_argument("--list", help="text file of mirror paths or names (the review page's /export.txt?set=judge); "
                    "only those clips are judged")
    ap.add_argument("--labels", help="CSV of file,label to score against; only those clips are judged")
    ap.add_argument("--out", default="/out/climb-verdicts.csv", help="where to write the verdicts")
    ap.add_argument("--methods", default="local,claude", help="local, claude, or both (default)")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--frames", type=int, default=12, help="frames per clip sent to Claude (default 12)")
    ap.add_argument("--limit", type=int, default=0, help="judge at most N clips")
    args = ap.parse_args()

    methods = {m.strip() for m in args.methods.split(",") if m.strip()}
    labels = read_labels(args.labels) if args.labels else {}
    clips = find_clips(Path(args.root), labels)
    if args.list:
        wanted = {Path(l.strip()).name for l in open(args.list) if l.strip()}
        clips = [c for c in (clips if labels else find_clips(Path(args.root), {})) if c.path.name in wanted]
    if args.limit:
        clips = clips[: args.limit]
    if not clips:
        print("no clips to judge", file=sys.stderr)
        return 1

    pose = client = None
    if "local" in methods:
        import mediapipe as mp
        pose = mp.solutions.pose.Pose(model_complexity=1, min_detection_confidence=0.5)
    if "claude" in methods:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("claude: ANTHROPIC_API_KEY is not set; run with --methods local, or export it", file=sys.stderr)
            return 2
        import anthropic
        client = anthropic.Anthropic()

    for n, c in enumerate(clips, 1):
        try:
            cap, _, c.seconds = open_video(c.path)
            cap.release()
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            c.errors.append("open: %s" % e)
            continue
        if pose is not None:
            try:
                c.local, c.local_why, c.active_seconds = judge_local(c.path, pose)
            except Exception as e:  # noqa: BLE001
                c.errors.append("local: %s" % e)
        if client is not None:
            try:
                c.claude, c.claude_conf, c.claude_why = judge_claude(c.path, c.seconds, client, args.model, args.frames)
            except Exception as e:  # noqa: BLE001
                c.errors.append("claude: %s" % e)
        print("[%d/%d] %s  label=%s local=%s claude=%s%s" % (
            n, len(clips), c.path.name, c.label or "-", c.local or "-", c.claude or "-",
            "  ERR " + "; ".join(c.errors) if c.errors else ""), flush=True)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "seconds", "label", "local", "local_why", "active_seconds",
                    "claude", "claude_confidence", "claude_why", "errors"])
        for c in clips:
            w.writerow([c.path.relative_to(args.root) if c.path.is_relative_to(args.root) else c.path,
                        "%.1f" % c.seconds, c.label, c.local, c.local_why, "%.1f" % c.active_seconds,
                        c.claude, "%.2f" % c.claude_conf if c.claude else "", c.claude_why,
                        "; ".join(c.errors)])
    print("\nverdicts written to %s" % args.out)
    if labels:
        print()
        for m in ("local", "claude"):
            if m in methods:
                print(score(clips, m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
