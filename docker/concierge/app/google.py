"""Google Calendar and Gmail, for the concierge's tools.

See docs/design/tsd-personal-assistant.md, Decision 4.

Scopes
------
  calendar.events   read events, and create or change them. No calendar
                    settings, sharing or ACLs.
  gmail.readonly    read and search mail, bodies included.
  gmail.compose     create drafts. Google also lets this scope SEND a draft,
                    so "never sends" is held by this file, not by the scope:
                    the only write calls here are `events.insert`,
                    `events.patch` and `drafts.create`, and a test fails if a
                    send, delete or modify URL appears. `gmail.send` and
                    `gmail.modify` are never requested.

Phase 4 widened this from the read-only pair; rerun `--google-auth` after
upgrading. Every write here runs only from actions.execute(), after a person
approved the exact arguments in Discord (Decision 2).

This is the act lane's own sign-in, separate from the digest's in
docker/assistant/ (which holds `gmail.metadata` and can never see a body). The
OAuth client can be the same one; the refresh token is not.

Raw aiohttp, no google-api-python-client — the same choice the assistant made.
The sign-in flow below mirrors docker/assistant/app/google.py; the two images
are built separately, and a shared package for ~60 lines was not worth the
coupling between the lanes.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import html
import re
import secrets
import time
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)

REDIRECT_URI = "http://127.0.0.1:8765"

# A body is cut here before it reaches the model. Long newsletters are the usual
# case, and the first few thousand characters are where the answer is.
BODY_CHARS = 8000


class GoogleError(RuntimeError):
    """A Google call failed in a way worth handing back to the agent plainly."""


# --- Response parsing (pure; unit-tested) --------------------------------------

def _when(raw: dict) -> str:
    return raw.get("dateTime") or raw.get("date") or "?"


def parse_events(payload: dict) -> list[dict]:
    """Events as small dicts. Cancelled and declined events are dropped."""
    out = []
    for item in payload.get("items", []):
        if item.get("status") == "cancelled":
            continue
        if any(a.get("self") and a.get("responseStatus") == "declined"
               for a in item.get("attendees", [])):
            continue
        out.append({
            "id": item.get("id", ""),
            "title": (item.get("summary") or "(no title)").strip(),
            "start": _when(item.get("start", {})),
            "end": _when(item.get("end", {})),
            "location": item.get("location") or "",
            "busy": item.get("transparency") != "transparent",
        })
    return out


def other_attendees(item: dict) -> int:
    """How many people besides the user are on an event, organizer included."""
    others = sum(1 for a in item.get("attendees", []) if not a.get("self") and not a.get("resource"))
    organizer = item.get("organizer") or {}
    if organizer and not organizer.get("self") and not any(
            a.get("organizer") for a in item.get("attendees", []) if not a.get("self")):
        others += 1
    return others


def headers_of(payload: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "")
            for h in payload.get("payload", {}).get("headers", [])}


def summarize_message(payload: dict) -> dict:
    h = headers_of(payload)
    name, addr = parseaddr(h.get("from", ""))
    return {
        "id": payload.get("id", ""),
        "from": f"{name} <{addr}>" if name and addr else (addr or name or "unknown"),
        "subject": h.get("subject") or "(no subject)",
        "date": h.get("date", ""),
        "snippet": html.unescape(payload.get("snippet", "")),
    }


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def _html_to_text(raw: str) -> str:
    text = _BLOCK.sub("", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = html.unescape(_TAG.sub("", text))
    return _BLANKS.sub("\n\n", _SPACE.sub(" ", text)).strip()


def extract_text(payload: dict, limit: int = BODY_CHARS) -> str:
    """The readable body of a `format=full` message.

    text/plain wins; HTML is reduced to text only when there is no plain part.
    Attachments are never decoded — only parts with inline body data are read.
    """
    plain: list[str] = []
    htmls: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and not part.get("filename"):
            if mime == "text/plain":
                plain.append(_b64(data))
            elif mime == "text/html":
                htmls.append(_b64(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload.get("payload", {}))
    text = "\n".join(plain).strip() or _html_to_text("\n".join(htmls))
    if len(text) > limit:
        text = text[:limit].rstrip() + f"\n[… cut at {limit} characters]"
    return text


# --- One-time sign-in (PKCE, loopback redirect) --------------------------------

@dataclass(frozen=True)
class AuthRequest:
    url: str
    verifier: str
    state: str


def auth_request(client_id: str) -> AuthRequest:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return AuthRequest(url=f"{AUTH_URL}?{urlencode(params)}", verifier=verifier, state=state)


def code_from_redirect(pasted: str, expected_state: str) -> str:
    query = parse_qs(urlparse(pasted.strip()).query)
    if "error" in query:
        raise GoogleError(f"Google refused the sign-in: {query['error'][0]}")
    if query.get("state", [""])[0] != expected_state:
        raise GoogleError("state mismatch — paste the URL from this sign-in, not an older one")
    code = query.get("code", [""])[0]
    if not code:
        raise GoogleError("no ?code= in that URL — paste the whole address bar")
    return code


async def exchange_code(session: aiohttp.ClientSession, client_id: str, client_secret: str,
                        code: str, verifier: str) -> dict:
    async with session.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise GoogleError(f"token exchange failed: {body.get('error_description') or body.get('error')}")
        return body


# --- Client ---------------------------------------------------------------------

class Google:
    def __init__(self, session: aiohttp.ClientSession, client_id: str,
                 client_secret: str, refresh_token: str) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    async def _token(self) -> str:
        async with self._lock:
            if self._access and self._access[1] > time.time() + 60:
                return self._access[0]
            try:
                async with self._session.post(TOKEN_URL, data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                }, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise GoogleError(f"Google sign-in did not answer ({exc.__class__.__name__})") from exc
            if not isinstance(body, dict) or "access_token" not in body:
                body = body if isinstance(body, dict) else {}
                reason = body.get("error") or f"HTTP {resp.status}"
                raise GoogleError(f"Google sign-in no longer valid ({reason}) — rerun --google-auth")
            self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
            return self._access[0]

    async def _get(self, url: str, params: list[tuple[str, str]] | dict) -> dict:
        token = await self._token()
        try:
            async with self._session.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                body = await resp.json(content_type=None)
                status = resp.status
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise GoogleError(f"Google did not answer ({exc.__class__.__name__})") from exc
        if status != 200:
            message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            raise GoogleError(f"HTTP {status}: {message or 'request failed'}")
        return body

    async def _send(self, method: str, url: str, body: dict) -> dict:
        """A write. Called only from actions.execute(), after approval."""
        token = await self._token()
        try:
            async with self._session.request(
                method, url, json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                status = resp.status
                out = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            # The write may have landed before the connection dropped.
            raise GoogleError(f"no clear answer from Google ({exc.__class__.__name__}) — "
                              "check before asking again, it may have gone through") from exc
        if status not in (200, 201):
            message = (out.get("error") or {}).get("message") if isinstance(out, dict) else None
            raise GoogleError(f"HTTP {status}: {message or 'request failed'}")
        return out

    async def check(self) -> None:
        """Refresh the access token. Proves the sign-in still works; reads nothing."""
        self._access = None
        await self._token()

    async def events(self, calendar_id: str, start: dt.datetime, end: dt.datetime,
                     query: str = "") -> list[dict]:
        params = {
            "timeMin": start.replace(microsecond=0).isoformat(),
            "timeMax": end.replace(microsecond=0).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "100",
        }
        if query:
            params["q"] = query
        payload = await self._get(f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events", params)
        return parse_events(payload)

    async def search(self, query: str, limit: int) -> list[dict]:
        payload = await self._get(f"{GMAIL_API}/messages", {"q": query, "maxResults": str(limit)})
        ids = [m["id"] for m in payload.get("messages", []) if m.get("id")]
        metas = await asyncio.gather(*(self._get(
            f"{GMAIL_API}/messages/{quote(i, safe='')}",
            [("format", "metadata"), ("metadataHeaders", "From"),
             ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")],
        ) for i in ids))
        return [summarize_message(m) for m in metas]

    async def read(self, message_id: str) -> dict:
        payload = await self._get(f"{GMAIL_API}/messages/{quote(message_id, safe='')}", {"format": "full"})
        return {**summarize_message(payload), "body": extract_text(payload)}

    async def event(self, calendar_id: str, event_id: str) -> dict:
        """One event, as parse_events shapes it, plus who else is on it. Read."""
        item = await self._get(
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}", {})
        parsed = parse_events({"items": [item]})
        if not parsed:
            raise GoogleError("that event is cancelled or declined")
        return {**parsed[0], "others": other_attendees(item)}

    async def reply_context(self, message_id: str) -> dict:
        """What a reply to this message needs: thread, headers, sender. Read."""
        payload = await self._get(
            f"{GMAIL_API}/messages/{quote(message_id, safe='')}",
            [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Reply-To"),
             ("metadataHeaders", "Subject"), ("metadataHeaders", "Message-ID"),
             ("metadataHeaders", "References")],
        )
        h = headers_of(payload)
        return {
            "thread_id": payload.get("threadId", ""),
            "from": h.get("from", ""),
            "reply_to": h.get("reply-to") or h.get("from", ""),
            "subject": h.get("subject", ""),
            "message_id": h.get("message-id", ""),
            "references": h.get("references", ""),
        }

    # --- writes: only actions.execute() calls these -----------------------------

    async def insert_event(self, calendar_id: str, body: dict) -> dict:
        # sendUpdates=none: no attendees are ever set, and nobody is emailed.
        return await self._send(
            "POST", f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events?sendUpdates=none", body)

    async def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        return await self._send(
            "PATCH",
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events/"
            f"{quote(event_id, safe='')}?sendUpdates=none", body)

    async def create_draft(self, raw: str, thread_id: str = "") -> dict:
        message: dict = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return await self._send("POST", f"{GMAIL_API}/drafts", {"message": message})
