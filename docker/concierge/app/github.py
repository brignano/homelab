"""What needs attention on the user's GitHub repositories. Read-only.

For "anything broken on brignano.io?": open pull requests, the latest run of
each workflow on the default branch when it failed, and open Dependabot
alerts. The same three things the weekly site check reads, asked for whenever
it's convenient instead of once a week.

Only GET, only api.github.com, only the fixed paths below, and only the
repositories in GITHUB_REPOS. The model picks at most one of those by name; it
cannot send a path, a query or an owner of its own.

GITHUB_REPOS=* means every repository the token can see, listed from
/user/repos and refreshed hourly, so a new repository is covered without a
config change. Archived repositories and forks are left out: neither runs CI
or gets alerts worth a morning line. The token's own repository access is
then the only list, which is the point of choosing "All repositories" there.

Repositories with nothing to report are collapsed into one `quiet` list, so
twenty healthy repositories cost a line, not twenty objects, in a run that
pays per token.

The token is a fine-grained personal access token, read-only, on these
repositories: Metadata, Pull requests, Actions and Dependabot alerts, all
"Read". Nothing here could write with a broader token either (there is no
POST), but a read-only token means a leak costs reading, not pushing. It
expires, deliberately; the day it does is a 401, which comes back as one
plain sentence saying so rather than as an empty "nothing open".

PR titles, branch names and workflow names are written by whoever opened the
PR, which on a public repository is anyone. They are untrusted text, as
everything a tool returns is (app/tools.py).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re

import aiohttp

API = "https://api.github.com"
TIMEOUT_S = 15
MAX_ITEMS = 10
# owner/name, as GitHub allows them. Checked in config.py, again here.
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
SEVERITIES = ("critical", "high", "medium", "low")
ALL = "*"
DISCOVER_TTL_S = 3600
MAX_REPOS = 60
# GitHub's secondary rate limit dislikes bursts; four requests per repository.
CONCURRENCY = 8

EXPIRED = ("GitHub refused the token (401): it has expired or been revoked. Make a new "
           "fine-grained token (read-only: Metadata, Pull requests, Actions, Dependabot "
           "alerts) and set GITHUB_TOKEN in docker/concierge/.env, then recreate the container.")


class GitHubError(RuntimeError):
    """GitHub could not be read at all."""


class Unreadable(Exception):
    """One endpoint could not be read; the rest of the report still stands."""


def _age_days(stamp: object, now: dt.datetime) -> int | None:
    try:
        when = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (now - when).days)


def _line(text: object, limit: int = 120) -> str:
    return " ".join(str(text or "").split())[:limit]


def summarize(repo: str, results: dict[str, object], now: dt.datetime) -> dict:
    """One repository's report from each endpoint's reply (or the exception it
    raised). Pure; tested.

    An endpoint that could not be read is named in `could_not_read`, never left
    out: no Dependabot permission must not read as "no alerts".
    """
    out: dict = {"repo": repo}
    unread = []

    def get(key: str):
        value = results.get(key)
        if isinstance(value, Exception) or value is None:
            unread.append(key if not isinstance(value, Unreadable) else f"{key} ({value})")
            return None
        return value

    pulls = get("pulls")
    if isinstance(pulls, list):
        prs = []
        for p in pulls:
            if not isinstance(p, dict):
                continue
            user = p.get("user") or {}
            prs.append({
                "number": p.get("number"),
                "title": _line(p.get("title")),
                "author": _line(user.get("login"), 40),
                "bot": user.get("type") == "Bot",
                "draft": bool(p.get("draft")),
                "age_days": _age_days(p.get("created_at"), now),
                "url": p.get("html_url"),
            })
        out["open_prs"] = len(prs)
        if prs:
            out["prs"] = prs[:MAX_ITEMS]
    elif pulls is not None:
        unread.append("pulls")

    runs = get("runs")
    if isinstance(runs, dict):
        latest: dict[str, dict] = {}
        for r in runs.get("workflow_runs") or []:
            if not isinstance(r, dict) or r.get("status") != "completed":
                continue
            name = _line(r.get("name"), 60)
            # Newest first from the API, so the first completed run per
            # workflow is the one that says whether it is red right now.
            latest.setdefault(name, r)
        failing = [{"workflow": name, "conclusion": r.get("conclusion"),
                    "age_days": _age_days(r.get("updated_at"), now), "url": r.get("html_url")}
                   for name, r in latest.items()
                   if r.get("conclusion") in ("failure", "timed_out", "startup_failure")]
        out["workflows_checked"] = len(latest)
        if failing:
            out["failing_workflows"] = failing[:MAX_ITEMS]
    elif runs is not None:
        unread.append("runs")

    alerts = get("alerts")
    if isinstance(alerts, list):
        counts = {s: 0 for s in SEVERITIES}
        worst = []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            adv = a.get("security_advisory") or {}
            sev = str(adv.get("severity") or "").lower()
            if sev in counts:
                counts[sev] += 1
            pkg = ((a.get("dependency") or {}).get("package") or {}).get("name")
            worst.append((SEVERITIES.index(sev) if sev in SEVERITIES else 9,
                          {"package": _line(pkg, 60), "severity": sev or "unknown",
                           "summary": _line(adv.get("summary")), "url": a.get("html_url")}))
        out["dependabot_alerts"] = {s: n for s, n in counts.items() if n}
        if worst:
            out["worst_alerts"] = [w for _, w in sorted(worst, key=lambda p: p[0])][:5]
    elif alerts is not None:
        unread.append("alerts")

    if unread:
        out["could_not_read"] = unread
    return out


def quiet(report: dict) -> bool:
    """Nothing in it needs anyone: no PR, no red workflow, no alert, nothing unread."""
    return not (report.get("open_prs") or report.get("failing_workflows")
                or report.get("dependabot_alerts") or report.get("could_not_read"))


def for_brief(status: dict) -> dict:
    """The morning brief's view of status(): only what it may bullet. Pure; tested.

    The brief bullets a red workflow, a critical or high alert, or something
    unreadable, and nothing else, so open PRs (every Renovate bump across
    every repository), medium and low counts and URLs are dropped here rather
    than paid for in tokens on every turn of a scheduled run. A repository
    left with nothing joins `quiet`.
    """
    repos, calm = [], list(status.get("quiet") or [])
    for r in status.get("repos") or []:
        slim: dict = {"repo": r.get("repo")}
        if r.get("failing_workflows"):
            slim["failing_workflows"] = [w.get("workflow") for w in r["failing_workflows"]]
        alerts = {s: n for s, n in (r.get("dependabot_alerts") or {}).items() if s in ("critical", "high")}
        if alerts:
            slim["dependabot_alerts"] = alerts
            slim["worst_alerts"] = [{"package": a.get("package"), "severity": a.get("severity")}
                                    for a in r.get("worst_alerts") or []
                                    if a.get("severity") in ("critical", "high")][:3]
        if r.get("could_not_read"):
            slim["could_not_read"] = r["could_not_read"]
        if len(slim) > 1:
            repos.append(slim)
        else:
            calm.append(r.get("repo"))
    out: dict = {"repos": repos}
    if calm:
        out["quiet"] = calm
    return out


def discovered(payload: object) -> tuple[str, ...]:
    """/user/repos as owner/name, without archived repositories and forks. Pure; tested."""
    if not isinstance(payload, list):
        raise GitHubError("GitHub's repository list was not a list")
    out = []
    for r in payload:
        if not isinstance(r, dict) or r.get("archived") or r.get("fork") or r.get("disabled"):
            continue
        name = str(r.get("full_name") or "")
        if REPO_RE.match(name):
            out.append(name)
    return tuple(sorted(out))[:MAX_REPOS]


class GitHub:
    def __init__(self, session: aiohttp.ClientSession | None, token: str, repos: tuple[str, ...]) -> None:
        if repos != (ALL,):
            for r in repos:
                if not REPO_RE.match(r):
                    raise ValueError(f"not an owner/name repository: {r!r}")
        self._session = session
        self._token = token
        self.repos = repos
        self.all = repos == (ALL,)
        self._found: tuple[str, ...] = ()
        self._found_at = 0.0
        self._sem = asyncio.Semaphore(CONCURRENCY)

    async def targets(self) -> tuple[str, ...]:
        """The repositories this reads: the configured list, or for `*` what the
        token can see right now (cached for an hour)."""
        if not self.all:
            return self.repos
        loop = asyncio.get_running_loop()
        if not self._found or loop.time() - self._found_at > DISCOVER_TTL_S:
            try:
                payload = await self._get("/user/repos", {"per_page": "100", "sort": "pushed"})
            except Unreadable as exc:
                raise GitHubError(f"could not list the token's repositories ({exc})")
            self._found = discovered(payload)
            self._found_at = loop.time()
        return self._found

    async def _get(self, path: str, params: dict | None = None) -> object:
        if self._session is None:
            raise GitHubError("no HTTP session")
        headers = {"Authorization": f"Bearer {self._token}",
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "homelab-concierge"}
        async with self._sem, self._session.get(API + path, params=params, headers=headers,
                                                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as resp:
            if resp.status == 401:
                raise GitHubError(EXPIRED)
            if resp.status in (403, 404):
                # Missing permission, a repository the token does not cover,
                # or Dependabot alerts turned off. Named, not swallowed.
                raise Unreadable(f"HTTP {resp.status}")
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _repo(self, repo: str, now: dt.datetime) -> dict:
        try:
            meta = await self._get(f"/repos/{repo}")
        except GitHubError:
            raise
        except Exception as exc:  # noqa: BLE001 — one repository, not the whole report
            return {"repo": repo, "could_not_read": [f"repository ({exc or exc.__class__.__name__})"]}
        if not isinstance(meta, dict):
            return {"repo": repo, "could_not_read": ["repository"]}
        branch = str(meta.get("default_branch") or "main")
        keys = ("pulls", "runs", "alerts")
        replies = await asyncio.gather(
            self._get(f"/repos/{repo}/pulls", {"state": "open", "per_page": "30"}),
            self._get(f"/repos/{repo}/actions/runs", {"branch": branch, "per_page": "50"}),
            self._get(f"/repos/{repo}/dependabot/alerts", {"state": "open", "per_page": "100"}),
            return_exceptions=True)
        for r in replies:
            if isinstance(r, GitHubError):
                raise r
        out = summarize(repo, dict(zip(keys, replies)), now)
        out["default_branch"] = branch
        return out

    async def status(self, repo: str | None = None) -> dict:
        """Every repository this reads, or the one named. Raises GitHubError
        for an expired token or when nothing at all could be reached."""
        known = await self.targets()
        if repo is not None and repo not in known:
            raise GitHubError(f"not a repository this assistant reads; it reads: {', '.join(known)}")
        if not known:
            raise GitHubError("the token can see no repositories (check its repository access)")
        now = dt.datetime.now(dt.timezone.utc)
        targets = [repo] if repo else list(known)
        replies = await asyncio.gather(*(self._repo(r, now) for r in targets), return_exceptions=True)
        for r in replies:
            if isinstance(r, GitHubError):
                raise r
        if all(isinstance(r, Exception) for r in replies):
            raise GitHubError(f"could not reach GitHub ({replies[0].__class__.__name__})")
        reports = [r if not isinstance(r, Exception) else {"repo": t, "could_not_read": ["all"]}
                   for t, r in zip(targets, replies)]
        out: dict = {"repos": [r for r in reports if not quiet(r)]}
        calm = [r["repo"] for r in reports if quiet(r)]
        if calm:
            out["quiet"] = calm
        return out
