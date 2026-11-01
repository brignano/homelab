"""Duplicate and same-moment detection shared by estimate.py and
duplicates-to-album.py. Pure logic over osxphotos PhotoInfo objects, so it can
be tested with stand-ins (tools/photos/test_dupes.py).
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Callable, Iterable

RAW_EXT = {".nef", ".nrw", ".cr2", ".cr3", ".arw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw", ".raw"}


def is_raw(p) -> bool:
    return bool(getattr(p, "israw", False)) or \
        PurePath(p.original_filename or "").suffix.lower() in RAW_EXT


def is_raw_pair(a, b) -> bool:
    """A RAW and its JPEG: the same shot saved twice, not a duplicate."""
    fa, fb = PurePath(a.original_filename or ""), PurePath(b.original_filename or "")
    return fa.stem.lower() == fb.stem.lower() and fa.suffix.lower() != fb.suffix.lower() \
        and (is_raw(a) or is_raw(b))


def find_dupes(photos: Iterable, protected: Callable, similar_seconds: float = 20.0,
               same_look: Callable | None = None):
    """Return (extras, keep_of, keeper_of).

    extras:  {uuid: (photo, ["duplicates" | "similar", ...])} for every photo
             that is an extra copy or an all-but-the-best shot and is not protected.
    keep_of: {uuid: filename of the photo kept instead}
    keeper_of: {uuid: the kept photo itself}, to group extras into sets

    Exact duplicates keep the largest copy. Similar shots: photos within
    `similar_seconds` of a group's FIRST shot join it (chaining would turn a
    camera fired all afternoon into one giant group), and the best is a
    favourite if there is one, else Photos' highest overall score.
    Bursts, screenshots and videos are left to their own rules. RAW files are
    never proposed for removal, and a RAW and its same-name JPEG are not
    duplicates of each other.

    same_look(best, p), if given, must also be true for a same-moment shot to
    count: time alone cannot tell a retake from a different pose seconds later.
    """
    photos = list(photos)
    inner = protected

    def protected(p):  # noqa: F811  RAW files are never proposed for removal
        return inner(p) or ("raw file" if is_raw(p) else None)

    extras: dict[str, tuple] = {}
    keep_of: dict[str, str] = {}
    keeper_of: dict[str, object] = {}

    def add(p, reason, keeper):
        extras.setdefault(p.uuid, (p, []))[1].append(reason)
        keep_of.setdefault(p.uuid, keeper.original_filename)
        keeper_of.setdefault(p.uuid, keeper)

    seen: set[frozenset] = set()
    for p in photos:
        if protected(p):
            continue
        dupes = p.duplicates
        if not dupes:
            continue
        group = frozenset([p.uuid] + [d.uuid for d in dupes])
        if group in seen:
            continue
        seen.add(group)
        members = sorted([p] + list(dupes), key=lambda x: x.original_filesize or 0, reverse=True)
        for extra in members[1:]:
            if not protected(extra):
                add(extra, "duplicates", members[0])

    if similar_seconds > 0:
        stills = sorted((p for p in photos
                         if not p.ismovie and not p.burst and not p.screenshot),
                        key=lambda p: p.date.timestamp())
        groups, cur = [], []
        for p in stills:
            if cur and p.date.timestamp() - cur[0].date.timestamp() > similar_seconds:
                groups.append(cur)
                cur = []
            cur.append(p)
        if cur:
            groups.append(cur)

        def rank(p):
            sc = p.score.overall if p.score and p.score.overall is not None else -1e9
            return (bool(p.favorite), sc, p.original_filesize or 0)

        for g in groups:
            if len(g) < 2:
                continue
            best = max(g, key=rank)
            for p in g:
                if p is not best and not protected(p) and not is_raw_pair(best, p) and (same_look is None or same_look(best, p)):
                    add(p, "similar", best)
    return extras, keep_of, keeper_of


# --- picture fingerprint (no image library needed) ---------------------------

def bmp_gray_rows(data: bytes):
    """Grey values of an uncompressed 24/32-bit BMP (what `sips -s format bmp`
    writes), top row first."""
    off = int.from_bytes(data[10:14], "little")
    w = int.from_bytes(data[18:22], "little")
    h = int.from_bytes(data[22:26], "little", signed=True)
    bpp = int.from_bytes(data[28:30], "little")
    if bpp not in (24, 32):
        raise ValueError("unsupported BMP depth %d" % bpp)
    n = bpp // 8
    stride = (w * n + 3) // 4 * 4
    rows = []
    for y in range(abs(h)):
        r = data[off + y * stride: off + y * stride + w * n]
        rows.append([(r[x * n] + r[x * n + 1] + r[x * n + 2]) // 3 for x in range(w)])
    if h > 0:  # stored bottom-up
        rows.reverse()
    return rows


def dhash_from_bmp(data: bytes) -> int:
    """64-bit difference hash of a BMP already scaled to 9 wide x 8 high: one
    bit per neighbouring pair, brighter-than-right."""
    bits = 0
    for row in bmp_gray_rows(data)[:8]:
        for x in range(8):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
