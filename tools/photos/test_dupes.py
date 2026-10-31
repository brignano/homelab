"""Run: python tools/photos/test_dupes.py  (no Photos needed)"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dupes import find_dupes

T0 = datetime(2026, 1, 1, 12, 0, 0)


def ph(u, secs=0, size=100, score=0.5, fav=False, dupes=(), burst=False, movie=False):
    return NS(uuid=u, original_filename=u + ".HEIC", date=T0 + timedelta(seconds=secs),
              original_filesize=size, score=NS(overall=score), favorite=fav,
              duplicates=list(dupes), burst=burst, screenshot=False, ismovie=movie)


def main():
    none = lambda p: None
    # exact copies: keep the largest
    a, b = ph("a", 0, 300), ph("b", 5000, 100)
    a.duplicates, b.duplicates = [b], [a]
    ex, keep = find_dupes([a, b], none, 0)
    assert list(ex) == ["b"] and keep["b"] == "a.HEIC", ex
    # similar: best score kept, far shot untouched, favourite wins
    s1, s2, s3, far = ph("s1", 0, score=.2), ph("s2", 3, score=.9), ph("s3", 6, score=.5), ph("far", 60)
    ex, keep = find_dupes([s1, s2, s3, far], none, 20)
    assert sorted(ex) == ["s1", "s3"] and keep["s1"] == "s2.HEIC"
    s1.favorite = True
    ex, _ = find_dupes([s1, s2, s3, far], lambda p: "fav" if p.favorite else None, 20)
    assert sorted(ex) == ["s2", "s3"], ex  # favourite is the keeper, never a candidate
    # protected extras are never candidates; videos and bursts are ignored
    ex, _ = find_dupes([s1, s2], lambda p: "x" if p.uuid == "s1" else None, 20)
    assert "s1" not in ex
    v1, v2, bu = ph("v1", 0, movie=True), ph("v2", 1, movie=True), ph("bu", 2, burst=True)
    ex, _ = find_dupes([v1, v2, bu], none, 20)
    assert not ex
    # window measured from the first shot, not chained
    chain = [ph("c%d" % i, i * 15) for i in range(5)]
    ex, _ = find_dupes(chain, none, 20)
    assert len(ex) == 2, ex  # groups {0,15} {30,45} {60}
    print("ok")


main()
