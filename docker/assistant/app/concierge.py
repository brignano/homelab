"""The bot's side of the act lane: forward #ask to the concierge, render the reply.

See docs/design/tsd-personal-assistant.md, phase 3, and docker/concierge/.

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

import logging
import re
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger(__name__)

# How long a ✅/❌ prompt stays live. Unanswered counts as ❌ (Decision 2): a
# prompt nobody looked at must never turn into a yes.
APPROVAL_TIMEOUT_S = 15 * 60

# Every tool the bot knows how to carry out after approval. A proposal naming
# anything else is shown as refused, never executed.
APPROVABLE = frozenset({"remember"})

MAX_FACT_CHARS = 300
MEMORY_SCAN = 200


class ConciergeError(RuntimeError):
    """The concierge could not be reached or refused the request."""


@dataclass(frozen=True)
class Proposal:
    tool: str
    args: dict


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


def parse_reply(body: dict) -> Reply:
    proposals = []
    for p in body.get("proposals") or []:
        if isinstance(p, dict) and isinstance(p.get("tool"), str) and isinstance(p.get("args"), dict):
            proposals.append(Proposal(p["tool"], p["args"]))
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


def fact_of(proposal: Proposal) -> str | None:
    """The text a `remember` approval would store, or None if malformed."""
    if proposal.tool != "remember":
        return None
    raw = proposal.args.get("fact")
    if not isinstance(raw, str):
        return None
    fact = " ".join(raw.split())[:MAX_FACT_CHARS]
    return fact or None


def render_proposal(proposal: Proposal) -> str:
    """The approval prompt, built from the arguments alone.

    Inside a code block, so the fact cannot ping anyone, render a link, or
    format itself into something that looks like a different message. Backticks
    are swapped out so it cannot close the block early.
    """
    if proposal.tool not in APPROVABLE or fact_of(proposal) is None:
        return f"⚠️ The assistant proposed `{proposal.tool[:40].replace('`', 'ˋ')}`, which I can't carry out. Ignored."
    fact = fact_of(proposal).replace("`", "ˋ")
    return f"**Remember this?** Saved to #memory only if you approve.\n```\nremember(fact={fact!r})\n```"


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
        self._token = token
        # Longer than the concierge's own CONCIERGE_TIMEOUT_S, so its timeout,
        # which says what happened, wins the race.
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def ask(self, prompt: str, history: list[dict], memories: list[str]) -> Reply:
        try:
            async with self._session.post(
                self._url,
                json={"prompt": prompt, "history": history, "memories": memories},
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
