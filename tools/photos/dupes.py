"""Duplicate and same-moment detection shared by estimate.py and
duplicates-to-album.py. Pure logic over osxphotos PhotoInfo objects, so it can
be tested with stand-ins (tools/photos/test_dupes.py).
"""
from __future__ import annotations

from typing import Callable, Iterable


def find_dupes(photos: Iterable, protected: Callable, similar_seconds: float = 20.0):
    """Return (extras, keep_of, keeper_of).

    extras:  {uuid: (photo, ["duplicates" | "similar", ...])} for every photo
             that is an extra copy or an all-but-the-best shot and is not protected.
    keep_of: {uuid: filename of the photo kept instead}
    keeper_of: {uuid: the kept photo itself}, to group extras into sets

    Exact duplicates keep the largest copy. Similar shots: photos within
    `similar_seconds` of a group's FIRST shot join it (chaining would turn a
    camera fired all afternoon into one giant group), and the best is a
    favourite if there is one, else Photos' highest overall score.
    Bursts, screenshots and videos are left to their own rules.
    """
    photos = list(photos)
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
                if p is not best and not protected(p):
                    add(p, "similar", best)
    return extras, keep_of, keeper_of
