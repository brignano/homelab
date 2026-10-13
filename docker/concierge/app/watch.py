"""Movie night: a few picks to watch, by genre, and where each one is.

See docker/concierge/README.md → "Movie night".

The hard part of movie night is choosing, so `movie_night` answers "comedy or
thriller, under two hours" with three titles instead of a catalogue to scroll.
The pool it picks from is the backlog:

  1. Jellyfin: what is already in the library and nobody here has watched.
  2. The watchlist: a TMDB list the user keeps (TMDB_LIST_ID), added to from
     TMDB's site or app whenever someone mentions something good.
  3. TMDB discover: well-rated titles in those genres, streaming on the user's
     own services (STREAMING_SERVICES) when they are set, so the pool is never
     empty even before the watchlist has anything in it.

Every pick says where it is: "in Jellyfin" (with a link when JELLYFIN_PUBLIC_URL
is set), the user's services that stream it, other services, and where it can
be rented or bought. Provider data is JustWatch's, through TMDB. Nothing here
downloads, adds or changes anything: Jellyfin and TMDB are only read, and a test
pins that every request is a GET.

Overviews, titles and list names were written by strangers (TMDB editors,
whoever made the list). They are cut short, and the system prompt says tool
results are information, never instructions.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Iterable

import aiohttp

TIMEOUT_S = 15
CACHE_S = 600
MAX_OVERVIEW = 200
MAX_PICKS = 5
MAX_DETAILS = 12          # TMDB detail lookups per request, to check runtime and providers
MAX_LIST_PAGES = 5
TMDB_API = "https://api.themoviedb.org/3"
KINDS = ("movie", "show", "any")

# The genres a request can name, and TMDB's ids for them. TMDB's genre ids
# have not changed in a decade; hard-coding them saves two calls per request.
# Shows have no horror, thriller or romance genre on TMDB; the nearest is used.
GENRES = ("action", "adventure", "animation", "comedy", "crime", "documentary", "drama",
          "family", "fantasy", "history", "horror", "music", "mystery", "romance",
          "science fiction", "thriller", "war", "western")
MOVIE_GENRE_IDS = {
    "action": 28, "adventure": 12, "animation": 16, "comedy": 35, "crime": 80,
    "documentary": 99, "drama": 18, "family": 10751, "fantasy": 14, "history": 36,
    "horror": 27, "music": 10402, "mystery": 9648, "romance": 10749,
    "science fiction": 878, "thriller": 53, "war": 10752, "western": 37,
}
TV_GENRE_IDS = {
    "action": 10759, "adventure": 10759, "animation": 16, "comedy": 35, "crime": 80,
    "documentary": 99, "drama": 18, "family": 10751, "fantasy": 10765,
    "mystery": 9648, "science fiction": 10765, "war": 10768, "western": 37,
    "thriller": 9648, "horror": 9648, "romance": 18,
}
# TMDB genre ids back to ours. The ids movies and shows share mean the same
# thing in both; the show-only ones are combined genres.
_ID_TO_GENRES: dict[int, set[str]] = {gid: {name} for name, gid in MOVIE_GENRE_IDS.items()}
_ID_TO_GENRES.update({10759: {"action", "adventure"}, 10765: {"science fiction", "fantasy"},
                      10762: {"family"}, 10768: {"war"}})
# What a show counts as when asked for a genre TMDB has no show genre for:
# the same stand-ins TV_GENRE_IDS searches with.
_SHOW_NEAREST = {"thriller": {"mystery"}, "horror": {"mystery"}, "romance": {"drama"}}

# Jellyfin genre names (from TMDB, or whatever a file's metadata says) to ours.
_ALIASES = {
    "sci-fi": "science fiction", "scifi": "science fiction", "sci fi": "science fiction",
    "science-fiction": "science fiction", "suspense": "thriller", "romantic comedy": "romance",
    "animated": "animation", "anime": "animation", "kids": "family", "war & politics": "war",
    "musical": "music", "historical": "history", "biography": "history",
}


class WatchError(RuntimeError):
    """TMDB or Jellyfin could not be reached, or refused."""


def _clean(raw: object, limit: int = 200) -> str:
    return " ".join(str(raw or "").split())[:limit]


def _overview(raw: object) -> str:
    text = " ".join(str(raw or "").split())
    return text if len(text) <= MAX_OVERVIEW else text[:MAX_OVERVIEW].rstrip() + "…"


def norm_title(raw: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").lower())


def norm_provider(raw: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(raw or "").lower().replace("+", "plus"))


def canon_genres(names: Iterable[object]) -> set[str]:
    """Free-form genre names (Jellyfin's, or the agent's) to ours. Pure; tested.

    "Sci-Fi & Fantasy" and "Action & Adventure" count as both halves."""
    out: set[str] = set()
    for raw in names:
        text = str(raw or "").strip().lower()
        if not text:
            continue
        parts = [text] + [p.strip() for p in re.split(r"\s*(?:&|/|,| and )\s*", text) if p.strip()]
        for part in parts:
            part = _ALIASES.get(part, part)
            if part in GENRES:
                out.add(part)
    return out


def genre_args(raw: object) -> set[str]:
    """The request's genres, or a ValueError naming the ones we don't know."""
    if raw in (None, "", []):
        return set()
    items = raw if isinstance(raw, list) else [raw]
    known = canon_genres(items)
    unknown = [str(g) for g in items if not canon_genres([g])]
    if unknown:
        raise ValueError(f"unknown genre {', '.join(unknown)}; use any of: {', '.join(GENRES)}")
    return known


def _year(raw: object) -> str:
    if isinstance(raw, int) and raw > 0:
        return str(raw)
    text = str(raw or "")
    return text[:4] if re.fullmatch(r"\d{4}(-\d\d-\d\d)?", text) else ""


def _num(raw: object) -> float | None:
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


# --- candidates ----------------------------------------------------------------
#
# Every source becomes the same shape, so pick() can rank them together:
#   kind        movie | show
#   title, year, tmdb_id ("" if unknown), genres (set of ours), rating (0-10 or None)
#   votes, runtime (minutes or None), overview
#   jellyfin_id ("" if not in the library), played (bool)
#   sources     subset of {jellyfin, watchlist, discover}
#   on_yours    True when discover found it on the user's services

def jellyfin_rows(items: object) -> list[dict]:
    """Jellyfin /Items cut to candidates. Pure; tested."""
    rows = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("Type") not in ("Movie", "Series") or not it.get("Id"):
            continue
        ticks = it.get("RunTimeTicks")
        runtime = round(ticks / 600_000_000) if isinstance(ticks, int) and ticks > 0 else None
        ids = it.get("ProviderIds") if isinstance(it.get("ProviderIds"), dict) else {}
        user = it.get("UserData") if isinstance(it.get("UserData"), dict) else {}
        rows.append({
            "kind": "movie" if it["Type"] == "Movie" else "show",
            "title": _clean(it.get("Name")),
            "year": _year(it.get("ProductionYear")),
            "tmdb_id": str(ids.get("Tmdb") or "").strip(),
            "genres": canon_genres(it.get("Genres") or []),
            "rating": _num(it.get("CommunityRating")),
            "votes": None,
            "runtime": runtime,
            "overview": _overview(it.get("Overview")),
            "jellyfin_id": str(it["Id"]),
            "played": bool(user.get("Played")),
            "sources": {"jellyfin"},
            "on_yours": False,
        })
    return rows


def tmdb_rows(results: object, default_kind: str | None, source: str, on_yours: bool = False) -> list[dict]:
    """TMDB list or discover results cut to candidates. Pure; tested.

    A list mixes movies and shows and says which (`media_type`); discover is
    one kind per call, passed as default_kind. People and anything else are
    skipped."""
    rows = []
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("id"), int):
            continue
        media_type = r.get("media_type") or {"movie": "movie", "show": "tv"}.get(default_kind or "")
        if media_type not in ("movie", "tv"):
            continue
        genres: set[str] = set()
        for gid in r.get("genre_ids") or []:
            genres |= _ID_TO_GENRES.get(gid, set()) if isinstance(gid, int) else set()
        rows.append({
            "kind": "movie" if media_type == "movie" else "show",
            "title": _clean(r.get("title") or r.get("name")),
            "year": _year(r.get("release_date") or r.get("first_air_date")),
            "tmdb_id": str(r["id"]),
            "genres": genres,
            "rating": _num(r.get("vote_average")),
            "votes": r.get("vote_count") if isinstance(r.get("vote_count"), int) else None,
            "runtime": None,
            "overview": _overview(r.get("overview")),
            "jellyfin_id": "",
            "played": False,
            "sources": {source},
            "on_yours": on_yours,
        })
    return rows


def merge(groups: Iterable[list[dict]]) -> list[dict]:
    """One candidate per title. The library's copy wins (it knows runtime and
    whether it was played); the others add their sources. Pure; tested."""
    out: list[dict] = []
    by_key: dict[tuple, dict] = {}
    for group in groups:
        for c in group:
            keys = [(c["kind"], "tmdb", c["tmdb_id"])] if c["tmdb_id"] else []
            keys.append((c["kind"], "title", norm_title(c["title"]), c["year"]))
            seen = next((by_key[k] for k in keys if k in by_key), None)
            if seen is None:
                seen = dict(c, genres=set(c["genres"]), sources=set(c["sources"]))
                out.append(seen)
            else:
                seen["sources"] |= c["sources"]
                seen["on_yours"] = seen["on_yours"] or c["on_yours"]
                seen["genres"] = seen["genres"] or set(c["genres"])
                for key in ("tmdb_id", "rating", "votes", "runtime", "overview", "year"):
                    if not seen.get(key) and c.get(key):
                        seen[key] = c[key]
            for k in keys:
                by_key.setdefault(k, seen)
    return out


def score(c: dict, genres: set[str]) -> float:
    """Higher is a better pick. Pure; tested.

    Rating first. Matching more of the asked-for genres, already being in the
    library (press play, nothing to sign in to), being on the user's own
    services and being on their watchlist each add to it."""
    s = c["rating"] if c["rating"] is not None else 6.0
    if genres:
        s += 0.75 * (matched(c, genres) - 1)
    if "jellyfin" in c["sources"]:
        s += 1.5
    elif c["on_yours"]:
        s += 0.75
    if "watchlist" in c["sources"]:
        s += 1.0
    return s


def matched(c: dict, genres: set[str]) -> int:
    """How many of the asked-for genres a candidate has. Pure; tested."""
    n = 0
    for g in genres:
        if g in c["genres"] or (c["kind"] == "show" and _SHOW_NEAREST.get(g, set()) & c["genres"]):
            n += 1
    return n


def eligible(c: dict, kind: str, genres: set[str], max_minutes: int | None,
             exclude: set[str], played_tmdb: set[str]) -> bool:
    """Pure; tested. An unknown runtime passes here; it is checked before the pick is returned."""
    if kind != "any" and c["kind"] != kind:
        return False
    if c["played"] or (c["tmdb_id"] and c["tmdb_id"] in played_tmdb):
        return False
    if norm_title(c["title"]) in exclude or not c["title"]:
        return False
    if genres and not matched(c, genres):
        return False
    if max_minutes and c["runtime"] and c["runtime"] > max_minutes:
        return False
    return True


def rank(candidates: list[dict], genres: set[str], rng: random.Random) -> list[dict]:
    """Best first, with a little shuffle so asking again gives different picks.

    Each candidate's score gets up to one point of noise: a 7.9 can beat an
    8.2 on one night but a 6.0 never beats an 8.5. Pure given rng; tested."""
    return sorted(candidates, key=lambda c: score(c, genres) + rng.random(), reverse=True)


# --- where to watch ----------------------------------------------------------

def where(providers: object, region: str, mine: set[int]) -> dict:
    """TMDB's watch/providers for one title, as the four lists a person needs. Pure; tested.

    `mine` is the user's services' provider ids. Streaming on one of theirs
    comes first and is what the agent should lead with."""
    results = providers.get("results") if isinstance(providers, dict) else None
    here = results.get(region) if isinstance(results, dict) else None
    if not isinstance(here, dict):
        return {}

    def names(key: str, only: bool | None = None) -> list[str]:
        out = []
        for p in here.get(key) or []:
            if not isinstance(p, dict) or not isinstance(p.get("provider_id"), int):
                continue
            if only is not None and (p["provider_id"] in mine) != only:
                continue
            name = _clean(p.get("provider_name"), 60)
            if name and name not in out:
                out.append(name)
        return out[:6]

    out: dict[str, Any] = {}
    if mine:
        out["streaming_on_yours"] = list(dict.fromkeys(names("flatrate", True) + names("free", True)
                                                       + names("ads", True)))
        out["streaming_elsewhere"] = names("flatrate", False)
    else:
        out["streaming"] = names("flatrate")
    out["free_with_ads"] = [n for n in dict.fromkeys(names("free") + names("ads"))
                            if n not in out.get("streaming_on_yours", [])]
    out["rent_or_buy"] = list(dict.fromkeys(names("rent") + names("buy")))[:6]
    out = {k: v for k, v in out.items() if v}
    link = here.get("link")
    if isinstance(link, str) and link.startswith("https://www.themoviedb.org/"):
        out["link"] = link
    return out


def resolve_services(wanted: Iterable[str], available: object) -> tuple[dict[str, list[int]], list[str]]:
    """STREAMING_SERVICES names to TMDB provider ids, and the names that matched
    nothing. Pure; tested.

    "Netflix" also takes "Netflix Standard with Ads", and "Disney+" equals
    "Disney Plus": an exact match on the normalised name first, else every
    provider whose normalised name starts with it."""
    rows = [p for p in (available.get("results") if isinstance(available, dict) else []) or []
            if isinstance(p, dict) and isinstance(p.get("provider_id"), int)]
    found: dict[str, list[int]] = {}
    missing: list[str] = []
    for name in wanted:
        n = norm_provider(name)
        if not n:
            continue
        exact = [p["provider_id"] for p in rows if norm_provider(p.get("provider_name")) == n]
        ids = exact or [p["provider_id"] for p in rows if norm_provider(p.get("provider_name")).startswith(n)]
        if ids:
            found[name] = sorted(set(ids))
        else:
            missing.append(name)
    return found, missing


def present(c: dict, where_info: dict | None, jellyfin_link: str) -> dict:
    """One pick as the agent sees it. Pure; tested."""
    row: dict[str, Any] = {
        "title": c["title"], "year": c["year"], "kind": c["kind"],
        "rating": round(c["rating"], 1) if c["rating"] is not None else None,
        "runtime_minutes": c["runtime"],
        "genres": sorted(c["genres"]),
        "overview": c["overview"],
        "on_watchlist": "watchlist" in c["sources"],
    }
    if c["jellyfin_id"]:
        row["where"] = {"jellyfin": True}
        if jellyfin_link:
            row["where"]["link"] = f"{jellyfin_link}/web/#/details?id={c['jellyfin_id']}"
    else:
        row["where"] = where_info or {"unknown": "TMDB lists nowhere to stream, rent or buy it here"}
    if c["kind"] == "show" and row["runtime_minutes"]:
        row["runtime_minutes_per_episode"] = row.pop("runtime_minutes")
    return {k: v for k, v in row.items() if v not in (None, "", [])}


# --- the clients -----------------------------------------------------------------

class _Cache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self._data.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < CACHE_S else None

    def put(self, key: str, value: Any) -> Any:
        self._data[key] = (time.monotonic(), value)
        return value


async def _get_json(session: aiohttp.ClientSession | None, who: str, url: str,
                    headers: dict, params: dict | None = None) -> Any:
    """The only request this file makes. GET, always: see the module docstring."""
    if session is None:
        raise WatchError(f"{who}: no HTTP session")
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as resp:
            if resp.status == 401:
                raise WatchError(f"{who}: key rejected (401)")
            if resp.status == 404:
                raise WatchError(f"{who}: not found (404)")
            if resp.status >= 400:
                raise WatchError(f"{who}: HTTP {resp.status}")
            try:
                return await resp.json(content_type=None)
            except ValueError as exc:
                raise WatchError(f"{who}: reply was not JSON") from exc
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise WatchError(f"{who}: could not reach it ({exc.__class__.__name__})") from exc


class Tmdb:
    """TMDB's v3 API with a read access token. Reads only."""

    def __init__(self, session: aiohttp.ClientSession | None, token: str, region: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.region = region
        self._cache = _Cache()

    async def get(self, path: str, **params) -> Any:
        key = path + repr(sorted(params.items()))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        return self._cache.put(key, await _get_json(self._session, "tmdb", TMDB_API + path,
                                                    self._headers, params or None))

    async def providers(self) -> dict:
        """Every streaming service TMDB knows in the region, movies and TV together."""
        movie = await self.get("/watch/providers/movie", watch_region=self.region)
        tv = await self.get("/watch/providers/tv", watch_region=self.region)
        rows = [p for d in (movie, tv) if isinstance(d, dict) for p in d.get("results") or []]
        return {"results": rows}

    async def list_items(self, list_id: str) -> list[dict]:
        items: list = []
        for page in range(1, MAX_LIST_PAGES + 1):
            data = await self.get(f"/list/{list_id}", page=page)
            if not isinstance(data, dict):
                break
            items += data.get("items") or []
            if page >= (data.get("total_pages") or 1):
                break
        return tmdb_rows(items, None, "watchlist")

    async def discover(self, kind: str, genres: set[str], max_minutes: int | None,
                       provider_ids: list[int], page: int) -> list[dict]:
        table = MOVIE_GENRE_IDS if kind == "movie" else TV_GENRE_IDS
        params: dict[str, Any] = {
            "sort_by": "popularity.desc",
            "vote_average.gte": 6.8,
            "vote_count.gte": 500 if kind == "movie" else 150,
            "include_adult": "false",
            "page": page,
        }
        if genres:
            params["with_genres"] = "|".join(str(g) for g in sorted({table[g] for g in genres if g in table}))
        if max_minutes:
            params["with_runtime.lte"] = max_minutes
        if provider_ids:
            params["watch_region"] = self.region
            params["with_watch_providers"] = "|".join(str(i) for i in provider_ids)
            params["with_watch_monetization_types"] = "flatrate|free|ads"
        data = await self.get(f"/discover/{'movie' if kind == 'movie' else 'tv'}", **params)
        results = data.get("results") if isinstance(data, dict) else None
        return tmdb_rows(results, kind, "discover", on_yours=bool(provider_ids))

    async def details(self, kind: str, tmdb_id: str) -> dict:
        """Runtime and watch providers in one call."""
        data = await self.get(f"/{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}",
                              append_to_response="watch/providers")
        if not isinstance(data, dict):
            return {}
        runtime = data.get("runtime")
        if kind == "show":
            runs = [r for r in data.get("episode_run_time") or [] if isinstance(r, int) and r > 0]
            last = data.get("last_episode_to_air")
            if not runs and isinstance(last, dict) and isinstance(last.get("runtime"), int):
                runs = [last["runtime"]]
            runtime = round(sum(runs) / len(runs)) if runs else None
        return {"runtime": runtime if isinstance(runtime, int) and runtime > 0 else None,
                "providers": data.get("watch/providers")}


class Jellyfin:
    """The library, as one Jellyfin user sees it. Reads only."""

    def __init__(self, session: aiohttp.ClientSession | None, base_url: str, api_key: str,
                 user: str, public_url: str = "") -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f'MediaBrowser Token="{api_key}"', "Accept": "application/json"}
        self.user = user
        self.public_url = public_url.rstrip("/")
        self._user_id: str | None = None
        self._cache = _Cache()

    async def _get(self, path: str, **params) -> Any:
        return await _get_json(self._session, "jellyfin", self._base + path, self._headers, params or None)

    async def user_id(self) -> str:
        """JELLYFIN_USER's id: whose "watched" counts. Blank = the only user."""
        if self._user_id:
            return self._user_id
        users = await self._get("/Users")
        users = [u for u in users if isinstance(u, dict) and u.get("Id")] if isinstance(users, list) else []
        if self.user:
            match = [u for u in users if str(u.get("Name", "")).lower() == self.user.lower()]
        else:
            match = users if len(users) == 1 else []
        if not match:
            names = ", ".join(str(u.get("Name")) for u in users)
            raise WatchError(f"jellyfin: set JELLYFIN_USER to whose watch history counts (users: {names})"
                             if not self.user else f"jellyfin: no user named {self.user!r} (users: {names})")
        self._user_id = str(match[0]["Id"])
        return self._user_id

    async def library(self) -> list[dict]:
        hit = self._cache.get("library")
        if hit is not None:
            return hit
        data = await self._get("/Items", userId=await self.user_id(), Recursive="true",
                               IncludeItemTypes="Movie,Series", Limit=5000,
                               Fields="Genres,ProviderIds,Overview",
                               EnableImages="false")
        items = data.get("Items") if isinstance(data, dict) else None
        return self._cache.put("library", jellyfin_rows(items))


class Watch:
    """Whichever of Jellyfin and TMDB is configured; at least one is."""

    def __init__(self, tmdb: Tmdb | None, jellyfin: Jellyfin | None, list_id: str,
                 services: tuple[str, ...]) -> None:
        self.tmdb = tmdb
        self.jellyfin = jellyfin
        self.list_id = list_id
        self.services = services
        self._service_ids: list[int] | None = None
        self.missing_services: list[str] = []

    async def service_ids(self) -> list[int]:
        if self._service_ids is None:
            if self.tmdb is None or not self.services:
                self._service_ids = []
            else:
                found, self.missing_services = resolve_services(self.services, await self.tmdb.providers())
                self._service_ids = sorted({i for ids in found.values() for i in ids})
        return self._service_ids

    async def pick(self, *, kind: str, genres: set[str], max_minutes: int | None, count: int,
                   exclude: set[str], rng: random.Random | None = None) -> dict:
        rng = rng or random.Random()
        notes: list[str] = []
        groups: list[list[dict]] = []
        library: list[dict] = []
        if self.jellyfin is not None:
            try:
                library = await self.jellyfin.library()
                groups.append(library)
            except WatchError as exc:
                notes.append(f"could not read Jellyfin: {exc}")
        played_tmdb = {c["tmdb_id"] for c in library if c["played"] and c["tmdb_id"]}

        mine: list[int] = []
        if self.tmdb is not None:
            try:
                mine = await self.service_ids()
            except WatchError as exc:
                notes.append(f"could not read TMDB's list of services: {exc}")
            if self.missing_services:
                notes.append("TMDB knows no service named " + ", ".join(self.missing_services)
                             + " (STREAMING_SERVICES)")
            if self.list_id:
                try:
                    groups.append(await self.tmdb.list_items(self.list_id))
                except WatchError as exc:
                    notes.append(f"could not read the watchlist (TMDB_LIST_ID): {exc}")
            try:
                for k in ("movie", "show") if kind == "any" else (kind,):
                    for page in (1, 2):
                        groups.append(await self.tmdb.discover(k, genres, max_minutes, mine, page))
            except WatchError as exc:
                notes.append(f"could not search TMDB: {exc}")

        pool = [c for c in merge(groups) if eligible(c, kind, genres, max_minutes, exclude, played_tmdb)]
        picks: list[dict] = []
        lookups = 0
        for c in rank(pool, genres, rng):
            if len(picks) >= count:
                break
            info = None
            if self.tmdb is not None and c["tmdb_id"] and (not c["jellyfin_id"] or c["runtime"] is None):
                if lookups >= MAX_DETAILS:
                    continue
                lookups += 1
                try:
                    d = await self.tmdb.details(c["kind"], c["tmdb_id"])
                except WatchError as exc:
                    notes.append(f"TMDB details for {c['title']}: {exc}")
                    d = {}
                c["runtime"] = c["runtime"] or d.get("runtime")
                if not c["jellyfin_id"]:
                    info = where(d.get("providers"), self.tmdb.region, set(mine))
            if max_minutes and c["runtime"] and c["runtime"] > max_minutes:
                continue
            picks.append(present(c, info, self.jellyfin.public_url if self.jellyfin else ""))

        out: dict[str, Any] = {"picks": picks, "pool_size": len(pool)}
        if self.tmdb is not None:
            out["provider_data"] = "JustWatch, via TMDB"
        if not mine and self.tmdb is not None:
            notes.append("STREAMING_SERVICES is not set, so streaming is not split into the user's own services")
        if notes:
            out["notes"] = notes
        return out
