"""Radarr and Sonarr: look up titles, report downloads, and add one title.

See docker/concierge/README.md → "Movies and shows".

What this file may do
---------------------
Reads: title lookup, the download queue, quality profiles. One write per app:
add a movie (Radarr) or a series (Sonarr), monitored, and search for it now.
Nothing here deletes, edits, unmonitors or changes a setting, and a test pins
the write count, so a new write is added on purpose.

The writes are reached only from actions.execute(), after a person approved
the exact title, id, quality profile and folder in Discord. The quality profile
and folder are fixed by config (MEDIA_QUALITY_PROFILE, RADARR_ROOT,
SONARR_ROOT), not chosen by the model.

Everything a lookup returns came from TMDB or TVDB, and a queue entry's title
from whatever an indexer called it. Overviews are cut short and release names
are left out entirely: the agent gets what it needs to pick a title and no more
text written by strangers than that.
"""

from __future__ import annotations

import re
from typing import Any

import aiohttp

TIMEOUT_S = 20
MAX_RESULTS = 8
MAX_OVERVIEW = 200
MAX_QUEUE = 20
MAX_REASON = 160
_NAMEISH = re.compile(r"\w\.\w")


class MediaError(RuntimeError):
    """Radarr or Sonarr could not be reached, or refused."""


def _year(raw: object) -> str:
    return str(raw) if isinstance(raw, int) and raw > 0 else ""


def _overview(raw: object) -> str:
    text = " ".join(str(raw or "").split())
    return text if len(text) <= MAX_OVERVIEW else text[:MAX_OVERVIEW].rstrip() + "…"


def lookup_rows(items: object, id_key: str) -> list[dict]:
    """A lookup reply cut to what picking a title needs. Pure; tested."""
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get(id_key), int) or item[id_key] <= 0:
            continue
        rows.append({
            "title": " ".join(str(item.get("title") or "").split())[:200],
            "year": _year(item.get("year")),
            id_key: str(item[id_key]),
            # Radarr and Sonarr give a lookup result their own id when the title
            # is already in the library.
            "in_library": bool(item.get("id")),
            "overview": _overview(item.get("overview")),
        })
        if len(rows) >= MAX_RESULTS:
            break
    return rows


def _reasons(r: dict) -> list[str]:
    """Why Radarr/Sonarr flagged an entry, in their own words.

    The messages are theirs ("The download is stalled with no connections"),
    but some quote back a path, a release or folder name, or a file inside the
    torrent. Any message that names anything is dropped rather than cleaned:
    those names are text a stranger wrote. "Names anything" means a path
    separator, one of the entry's own titles, or a word with a dot inside it
    (Show.S01E02, x.mkv) — Radarr/Sonarr's own sentences have none.
    """
    names = {str(r.get("title") or "").strip().lower()}
    found = [r.get("errorMessage")]
    for sm in r.get("statusMessages") or []:
        if isinstance(sm, dict):
            names.add(str(sm.get("title") or "").strip().lower())
            if isinstance(sm.get("messages"), list):
                found += sm["messages"]
    names.discard("")
    out: list[str] = []
    for m in found:
        text = " ".join(m.split()) if isinstance(m, str) else ""
        low = text.lower()
        if (not text or text in out or "/" in text or "\\" in text
                or _NAMEISH.search(text) or any(n in low for n in names)):
            continue
        out.append(text[:MAX_REASON])
        if len(out) >= 3:
            break
    return out


def queue_rows(records: object, app: str) -> list[dict]:
    """The download queue, without release names. Pure; tested."""
    rows = []
    for r in records if isinstance(records, list) else []:
        if not isinstance(r, dict):
            continue
        owner = r.get("movie") if app == "radarr" else r.get("series")
        title = owner.get("title") if isinstance(owner, dict) else None
        row: dict[str, Any] = {"app": app, "title": " ".join(str(title or "unknown").split())[:200]}
        ep = r.get("episode")
        if app == "sonarr" and isinstance(ep, dict):
            s, e = ep.get("seasonNumber"), ep.get("episodeNumber")
            if isinstance(s, int) and isinstance(e, int):
                row["episode"] = f"S{s:02d}E{e:02d}"
        size, left = r.get("size"), r.get("sizeleft")
        if isinstance(size, (int, float)) and isinstance(left, (int, float)) and size > 0:
            row["progress"] = f"{max(0, min(100, round(100 * (1 - left / size))))}%"
        for key in ("status", "timeleft", "trackedDownloadStatus", "trackedDownloadState"):
            if isinstance(r.get(key), str):
                row[key] = r[key][:40]
        if reasons := _reasons(r):
            row["reasons"] = reasons
        rows.append(row)
        if len(rows) >= MAX_QUEUE:
            break
    return rows


def queue_page(data: object, app: str) -> dict:
    """One page of the queue and how many entries there are in all. Pure; tested.

    The total is what stops the agent guessing whether a list of exactly
    MAX_QUEUE was cut short.
    """
    data = data if isinstance(data, dict) else {}
    items = queue_rows(data.get("records"), app)
    total = data.get("totalRecords")
    ok = isinstance(total, int) and not isinstance(total, bool) and total >= len(items)
    return {"total": total if ok else len(items), "items": items}


class _Arr:
    """One Radarr or Sonarr instance, API v3."""

    def __init__(self, session: aiohttp.ClientSession | None, name: str, base_url: str, key: str) -> None:
        self._session = session
        self.name = name
        self._base = base_url.rstrip("/") + "/api/v3"
        self._key = key

    async def _call(self, method: str, path: str, *, params: dict | None = None,
                    body: dict | None = None) -> Any:
        if self._session is None:
            raise MediaError(f"{self.name}: no HTTP session")
        try:
            async with self._session.request(
                method, self._base + path, params=params, json=body,
                headers={"X-Api-Key": self._key},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                if resp.status == 401:
                    raise MediaError(f"{self.name}: API key rejected (401) — copy it again from "
                                     f"{self.name.capitalize()} → Settings → General")
                if resp.status >= 400:
                    detail = ""
                    try:
                        data = await resp.json(content_type=None)
                        if isinstance(data, list) and data and isinstance(data[0], dict):
                            detail = str(data[0].get("errorMessage") or "")
                        elif isinstance(data, dict):
                            detail = str(data.get("message") or "")
                    except (ValueError, aiohttp.ContentTypeError):
                        pass
                    raise MediaError(f"{self.name}: HTTP {resp.status}" + (f" — {detail[:200]}" if detail else ""))
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise MediaError(f"{self.name}: could not reach it ({exc.__class__.__name__})") from exc

    async def _get(self, path: str, **params) -> Any:
        return await self._call("GET", path, params=params or None)

    async def status(self) -> str:
        data = await self._get("/system/status")
        return str(data.get("version", "?")) if isinstance(data, dict) else "?"

    async def profile_id(self, name: str) -> int:
        profiles = await self._get("/qualityprofile")
        for p in profiles if isinstance(profiles, list) else []:
            if isinstance(p, dict) and p.get("name") == name and isinstance(p.get("id"), int):
                return p["id"]
        names = ", ".join(str(p.get("name")) for p in profiles if isinstance(p, dict)) if isinstance(profiles, list) else ""
        raise MediaError(f"{self.name}: no quality profile named {name!r} (have: {names})")

    async def lookup_one(self, prefix: str, ext_id: str) -> dict:
        found = await self._get(self._lookup_path, term=f"{prefix}:{ext_id}")
        if not isinstance(found, list) or not found or not isinstance(found[0], dict):
            raise MediaError(f"{self.name}: nothing found for {prefix}:{ext_id}")
        return found[0]

    _lookup_path = ""


class Radarr(_Arr):
    _lookup_path = "/movie/lookup"

    async def search(self, query: str) -> list[dict]:
        return lookup_rows(await self._get(self._lookup_path, term=query), "tmdbId")

    async def queue(self) -> dict:
        return queue_page(await self._get("/queue", pageSize=MAX_QUEUE, includeMovie="true"), "radarr")

    async def add(self, tmdb_id: str, profile: str, root: str) -> str:
        movie = await self.lookup_one("tmdb", tmdb_id)
        if movie.get("id"):
            raise MediaError(f"radarr: {movie.get('title')} is already in the library")
        movie.update({
            "qualityProfileId": await self.profile_id(profile),
            "rootFolderPath": root,
            "monitored": True,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": True},
        })
        made = await self._call("POST", "/movie", body=movie)
        return str(made.get("title") if isinstance(made, dict) else movie.get("title"))


class Sonarr(_Arr):
    _lookup_path = "/series/lookup"

    async def search(self, query: str) -> list[dict]:
        return lookup_rows(await self._get(self._lookup_path, term=query), "tvdbId")

    async def queue(self) -> dict:
        return queue_page(await self._get("/queue", pageSize=MAX_QUEUE, includeSeries="true",
                                          includeEpisode="true"), "sonarr")

    async def add(self, tvdb_id: str, profile: str, root: str) -> str:
        series = await self.lookup_one("tvdb", tvdb_id)
        if series.get("id"):
            raise MediaError(f"sonarr: {series.get('title')} is already in the library")
        series.update({
            "qualityProfileId": await self.profile_id(profile),
            "rootFolderPath": root,
            "monitored": True,
            "seasonFolder": True,
            "addOptions": {"monitor": "all", "searchForMissingEpisodes": True},
        })
        made = await self._call("POST", "/series", body=series)
        return str(made.get("title") if isinstance(made, dict) else series.get("title"))


class Media:
    """Whichever of the two is configured. Either may be None."""

    def __init__(self, radarr: Radarr | None, sonarr: Sonarr | None, profile: str,
                 radarr_root: str, sonarr_root: str) -> None:
        self.radarr = radarr
        self.sonarr = sonarr
        self.profile = profile
        self.radarr_root = radarr_root
        self.sonarr_root = sonarr_root
