"""The bot's side of the act lane: forward #ask to the concierge, render the reply.

See docs/design/tsd-personal-assistant.md, phases 3–4, and docker/concierge/.

The bot is the only front door (Decision 1). It does three things here and
nothing else: gather the conversation and the approved memories out of Discord,
POST them to the concierge over the internal `assistant_concierge` network, and
render what comes back. No agent runs in this process and no Anthropic key is
held here.

Approval prompts are rendered by THIS code, from the proposal's arguments
(Decision 2). The model's prose never reaches the approval message, so an email
that steered the agent can change what is proposed, but not how it is shown.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field

import aiohttp

from . import followups

log = logging.getLogger(__name__)

# How long a ✅/❌ prompt stays live. Unanswered counts as ❌ (Decision 2): a
# prompt nobody looked at must never turn into a yes.
APPROVAL_TIMEOUT_S = 15 * 60

# Every tool the bot knows how to render for approval. `remember` and
# `follow_up` are carried out here (a post to #memory or #follow-ups); the
# rest go back to the concierge's
# /v1/execute with the id it issued. A proposal naming anything else is shown
# as refused, never executed.
APPROVABLE = frozenset({"remember", "follow_up", "create_event", "move_event", "draft_email", "run_scene",
                        "add_movie", "add_show"})

# How each executable proposal is shown: a heading, then the fields in order.
# Every field the concierge acts on is listed, except draft_email's
# reply_to_message_id, which `in_reply_to` (built by the concierge from that
# message) stands in for.
_LAYOUT: dict[str, tuple[str, tuple[str, ...]]] = {
    "create_event": ("**Create this event?** Nobody is invited.",
                     ("title", "start", "end", "location", "description", "calendar")),
    "move_event": ("**Move this event?** Only the time changes; nobody is notified.",
                   ("title", "was", "start", "end", "calendar", "event_id")),
    "draft_email": ("**Save this Gmail draft?** It is not sent; you send it from Gmail.",
                    ("to", "subject", "in_reply_to", "body")),
    "run_scene": ("**Run this Home Assistant scene?**", ("entity_id",)),
    "add_movie": ("**Download this movie?** Radarr adds it and searches now.",
                  ("title", "year", "tmdb_id", "quality", "folder")),
    "add_show": ("**Download this show?** Sonarr adds every season and searches now.",
                 ("title", "year", "tvdb_id", "quality", "folder")),
}
_REQUIRED = {
    "create_event": ("title", "start", "end", "calendar"),
    "move_event": ("event_id", "start", "end", "calendar"),
    "draft_email": ("to", "body"),
    "run_scene": ("entity_id",),
    "add_movie": ("title", "tmdb_id", "quality", "folder"),
    "add_show": ("title", "tvdb_id", "quality", "folder"),
}

MAX_FACT_CHARS = 300
MEMORY_SCAN = 200
EXECUTE_TIMEOUT_S = 90


class ConciergeError(RuntimeError):
    """The concierge could not be reached or refused the request."""


@dataclass(frozen=True)
class Proposal:
    tool: str
    args: dict
    id: str = ""


@dataclass
class Reply:
    ok: bool
    answer: str
    proposals: list[Proposal] = field(default_factory=list)
    tool_calls: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    model: str = ""
    stop: str = ""
    # The brief's "mail was really read" (concierge tools.google_reads_ok).
    reads_ok: bool = False


def parse_reply(body: dict) -> Reply:
    proposals = []
    for p in body.get("proposals") or []:
        if isinstance(p, dict) and isinstance(p.get("tool"), str) and isinstance(p.get("args"), dict):
            pid = p.get("id") if isinstance(p.get("id"), str) else ""
            proposals.append(Proposal(p["tool"], p["args"], pid))
    cost = body.get("cost_usd")
    return Reply(
        ok=bool(body.get("ok")),
        answer=str(body.get("answer") or "").strip() or "(no answer)",
        proposals=proposals,
        tool_calls=int(body.get("tool_calls") or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        seconds=float(body.get("seconds") or 0),
        model=str(body.get("model") or ""),
        stop=str(body.get("stop") or ""),
        reads_ok=body.get("reads_ok") is True,
    )


def footer(reply: Reply) -> str:
    parts = [reply.model or "claude"]
    if reply.tool_calls:
        parts.append(f"{reply.tool_calls} lookup{'s' if reply.tool_calls != 1 else ''}")
    if reply.cost_usd is not None:
        parts.append(f"${reply.cost_usd:.3f}")
    parts.append(f"{reply.seconds:.0f}s")
    if not reply.ok and reply.stop:
        parts.append(reply.stop)
    return "-# " + " · ".join(parts)


_MASKED = re.compile(r"\[([^\]\n]*)\]\(\s*<?(https?://[^\s)>]+)>?\s*\)")
_BARE = re.compile(r"(?<![<(])(https?://[^\s<>)\]]+)")


def defang(answer: str) -> str:
    """Make every link in an answer visible and inert until clicked.

    The answer can be steered by an email the agent read. Two ways that text
    could move data out of the house without a ✅:
      * a URL with mailbox contents in its query string, which Discord's
        crawler fetches to build a preview, with no click needed. Wrapping
        it in <…> suppresses the preview (the messages also pass
        suppress_embeds);
      * a masked link, [harmless text](https://evil/?d=…), which hides where
        a click goes. It is rewritten as `text (<url>)`, so the destination
        is on screen before anyone clicks.
    """
    answer = _MASKED.sub(lambda m: f"{m.group(1)} (<{m.group(2)}>)", answer)
    return _BARE.sub(r"<\1>", answer)


@dataclass(frozen=True)
class Brief:
    """The morning brief as a section of the digest, not a message of its own.

    `lines` go under the digest's Today section. `owns_mail` says the brief
    really read mail, so the digest drops its own unread-count line rather
    than print a second, narrower answer to the same question. `note` joins
    the digest's footer.
    """
    lines: tuple[str, ...]
    owns_mail: bool = False
    note: str = ""


BRIEF_HEADING = "### Needs you"

# A `#` heading or `-#` subtext at the start of a line, also behind list or
# quote markers, where Discord renders them too (`- # x`, `> -# x`, `1. ## x`).
# Escaped with a backslash so it shows as typed.
_UNHEAD = re.compile(r"^(\s*(?:(?:[-*+]|\d+[.)]|>{1,3})\s+)*)(?=-?#)")


def brief_section(reply: Reply) -> Brief:
    """The concierge's brief, made safe to sit inside the digest.

    The answer can quote mail a stranger wrote, so it gets the same treatment
    as #ask: links defanged here, mentions and previews suppressed where the
    digest is sent. It sits under the lab's verdict, so a line the model wrote
    must not pass for one of the bot's headings or footers (`#`, `-#`). A run
    that did not finish says so above its text. Proposals are ignored: the
    brief has none to make, and nothing here would render one. Length is the
    digest's business (digest.render cuts the brief, never the facts).
    """
    lines = [BRIEF_HEADING]
    if not reply.ok:
        lines.append(f"⚠️ _Incomplete ({reply.stop or 'no result'}): may be partial._")
    for ln in defang(reply.answer).split("\n"):
        lines.append(_UNHEAD.sub(r"\1\\", ln))
    return Brief(tuple(lines), owns_mail=reply.ok and reply.reads_ok,
                 note=footer(reply).removeprefix("-# "))


def brief_failed(reason: str) -> Brief:
    """A brief that did not run: one line in its place, the digest's mail line kept."""
    return Brief((f"⚠️ _Morning brief unavailable: {reason}_",))


def fact_of(proposal: Proposal) -> str | None:
    """The text a `remember` approval would store, or None if malformed."""
    if proposal.tool != "remember":
        return None
    raw = proposal.args.get("fact")
    if not isinstance(raw, str):
        return None
    fact = " ".join(raw.split())[:MAX_FACT_CHARS]
    return fact or None


def approvable(proposal: Proposal) -> bool:
    """Can a ✅ on this proposal do anything? Unknown or malformed: no."""
    if proposal.tool == "remember":
        return fact_of(proposal) is not None
    if proposal.tool == "follow_up":
        return followups.valid_args(proposal.args)
    if proposal.tool not in APPROVABLE or not proposal.id.isalnum():
        return False
    for key in _REQUIRED[proposal.tool]:
        value = proposal.args.get(key)
        if not value or not isinstance(value, (str, list)):
            return False
    if not all(isinstance(v, (str, list)) for v in proposal.args.values()):
        return False
    # What cannot be shown in full is not approvable: a cut prompt hides part
    # of what would run.
    return len(_show(proposal)) <= MAX_PROMPT_CHARS


MAX_PROMPT_CHARS = 1900  # under Discord's 2000, with room to spare


def _inert(value: object) -> str:
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text.replace("`", "ˋ")


def _show(proposal: Proposal) -> str:
    heading, order = _LAYOUT[proposal.tool]
    lines = []
    for key in order:
        value = proposal.args.get(key)
        if not value:
            continue
        text = _inert(value)
        if "\n" in text:
            lines.append(f"{key}:")
            lines.extend("  " + ln for ln in text.split("\n"))
        else:
            lines.append(f"{key}: {text}")
    return f"{heading}\n```\n" + "\n".join(lines) + "\n```"


def render_proposal(proposal: Proposal, tz: dt.tzinfo = dt.timezone.utc) -> str:
    """The approval prompt, built from the arguments alone.

    Inside a code block, so no field can ping anyone, render a link, or
    format itself into something that looks like a different message. Backticks
    are swapped out so nothing can close the block early.
    """
    if not approvable(proposal):
        return f"⚠️ The assistant proposed `{proposal.tool[:40].replace('`', 'ˋ')}`, which I can't carry out. Ignored."
    if proposal.tool == "remember":
        fact = fact_of(proposal).replace("`", "ˋ")
        return f"**Remember this?** Saved to #memory only if you approve.\n```\nremember(fact={fact!r})\n```"
    if proposal.tool == "follow_up":
        body = _inert(followups.prompt_text(proposal.args, tz))
        return ("**Set this follow-up?** Kept in #follow-ups; delete it there to cancel."
                f"\n```\n{body}\n```")
    return _show(proposal)


def usable_memories(messages: list[tuple[bool, str]]) -> list[str]:
    """#memory, as the agent should see it: the bot's and the owner's posts, oldest first.

    `messages` is (trusted_author, content), newest first, the order Discord
    returns them. Trusted means the bot itself or an allowlisted user.
    """
    out = []
    for trusted, content in messages:
        text = " ".join((content or "").split())
        if trusted and text and not text.startswith("//"):
            out.append(text)
    out.reverse()
    return out


class Concierge:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str,
                 timeout_s: int = 360) -> None:
        self._session = session
        self._url = base_url.rstrip("/") + "/v1/ask"
        self._brief_url = base_url.rstrip("/") + "/v1/brief"
        self._review_url = base_url.rstrip("/") + "/v1/review"
        self._execute_url = base_url.rstrip("/") + "/v1/execute"
        self._token = token
        # Longer than the concierge's own CONCIERGE_TIMEOUT_S, so its timeout,
        # which says what happened, wins the race.
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def ask(self, prompt: str, history: list[dict], memories: list[str]) -> Reply:
        return await self._run(self._url, {"prompt": prompt, "history": history, "memories": memories})

    async def brief(self, memories: list[str]) -> Reply:
        """The scheduled morning brief. The request is fixed in the concierge;
        only the approved memories are sent. It comes back with no proposals."""
        return await self._run(self._brief_url, {"memories": memories})

    async def review(self, memories: list[str], conversations: list[dict], activity: list[str],
                     upcoming: list[str], home: list[str]) -> Reply:
        """The evening review. Data only: the request is fixed in the concierge,
        and the only proposals that come back are memories and follow-ups."""
        return await self._run(self._review_url, {
            "memories": memories, "conversations": conversations, "activity": activity,
            "upcoming": upcoming, "home": home})

    async def _run(self, url: str, payload: dict) -> Reply:
        try:
            async with self._session.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            ) as resp:
                body = await resp.json(content_type=None)
                status = resp.status
        except aiohttp.ClientConnectorError as exc:
            raise ConciergeError("the concierge isn't running (docker/concierge)") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ConciergeError(f"no answer from the concierge ({exc.__class__.__name__})") from exc
        if not isinstance(body, dict):
            raise ConciergeError(f"HTTP {status}: unreadable reply")
        if status == 429:
            raise ConciergeError("busy with another request — try again in a minute")
        if status == 401:
            raise ConciergeError("refused: CONCIERGE_TOKEN differs between the two .env files")
        if status != 200:
            raise ConciergeError(f"HTTP {status}: {body.get('error') or 'request failed'}")
        return parse_reply(body)

    async def execute(self, proposal: Proposal) -> str:
        """Carry out one approved proposal. Returns the concierge's one-line result."""
        try:
            async with self._session.post(
                self._execute_url,
                json={"id": proposal.id, "tool": proposal.tool, "args": proposal.args},
                headers={"Authorization": f"Bearer {self._token}"},
                # Above the concierge's worst case (three 20s Google calls),
                # so a slow write is not reported as failed after it landed.
                timeout=aiohttp.ClientTimeout(total=EXECUTE_TIMEOUT_S),
            ) as resp:
                status = resp.status
                body = await resp.json(content_type=None)
        except aiohttp.ClientConnectorError as exc:
            raise ConciergeError("the concierge isn't running, so nothing was done") from exc
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ConciergeError(f"outcome unknown ({exc.__class__.__name__}) — "
                                 "check before asking again, it may have gone through") from exc
        if not isinstance(body, dict):
            raise ConciergeError(f"HTTP {status}: unreadable reply")
        if status == 410:
            raise ConciergeError("expired (the concierge restarted or it timed out)")
        if status != 200 or not body.get("ok"):
            raise ConciergeError(str(body.get("error") or f"HTTP {status}"))
        return str(body.get("summary") or "done")
