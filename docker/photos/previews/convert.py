"""Make a JPEG preview of every HEIC under /src in /dst, same year/month layout.

Incremental: a preview that already exists is skipped. The originals are never
touched (/src is mounted read-only). A preview is written to a temporary name
and renamed, so a stop halfway never leaves a truncated JPEG for Jellyfin.

Exit 0 when every HEIC has a preview or was stopped by the deadline; 1 if any
file failed to convert.
"""
import os
import sys
import time
from multiprocessing import Pool

from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()

SRC = "/src"
DST = "/dst"
MAX_EDGE = int(os.environ.get("PREVIEW_MAX_EDGE", "2048"))
QUALITY = int(os.environ.get("PREVIEW_QUALITY", "85"))
WORKERS = max(1, int(os.environ.get("PREVIEW_WORKERS", "1")))
DEADLINE = time.time() + 60 * int(os.environ.get("PREVIEW_MAX_MINUTES", "240"))

ORIENTATION = 0x0112
EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 0x9003


def preview_path(src):
    rel = os.path.relpath(src, SRC)
    return os.path.join(DST, os.path.splitext(rel)[0] + ".jpg")


def capture_time(exif, fallback):
    # "2026:09:14 18:03:22" -> epoch, local time. The original's mtime (set by
    # icloudpd to the creation date) when the photo carries no EXIF date.
    raw = exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) or exif.get(0x0132)
    if raw:
        try:
            return time.mktime(time.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S"))
        except ValueError:
            pass
    return fallback


def convert(src):
    if time.time() > DEADLINE:
        return "deadline", src
    dst = preview_path(src)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return "skipped", src
    tmp = dst + ".part"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with Image.open(src) as im:
            exif = im.getexif()
            when = capture_time(exif, os.path.getmtime(src))
            # libheif has already applied the rotation, so say so in the EXIF
            # rather than let a viewer rotate the pixels a second time.
            if ORIENTATION in exif:
                exif[ORIENTATION] = 1
            im = im.convert("RGB")
            im.thumbnail((MAX_EDGE, MAX_EDGE))
            im.save(tmp, format="JPEG", quality=QUALITY, exif=exif)
        os.utime(tmp, (when, when))
        os.replace(tmp, dst)
        return "converted", src
    except Exception as e:  # one bad file must not stop the run
        if os.path.exists(tmp):
            os.remove(tmp)
        return "failed", f"{src}: {e}"


def main():
    files = sorted(
        os.path.join(d, f)
        for d, _, names in os.walk(SRC)
        for f in names
        if f.lower().endswith(".heic")
    )
    counts = {"converted": 0, "skipped": 0, "failed": 0, "deadline": 0}
    with Pool(WORKERS) as pool:
        for status, detail in pool.imap_unordered(convert, files, chunksize=8):
            counts[status] += 1
            if status == "failed":
                print("FAILED", detail, flush=True)
            elif status == "converted" and counts["converted"] % 200 == 0:
                print(f"converted {counts['converted']} so far", flush=True)
    print(
        f"previews: {len(files)} HEIC, {counts['converted']} converted, "
        f"{counts['skipped']} already there, {counts['failed']} failed, "
        f"{counts['deadline']} left for tomorrow",
        flush=True,
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
