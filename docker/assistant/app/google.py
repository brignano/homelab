"""Read-only Google Calendar and Gmail, for the digest's "Today" section.

See docs/design/tsd-personal-assistant.md (phase 1, the watch lane).

Scopes are the capability ceiling
---------------------------------
The token this module holds can do exactly two things, and Google enforces both:

  calendar.events.readonly   read events. Not calendars, not settings, no writes.
  gmail.metadata             read labels and headers. Google refuses to return a
                             message BODY to this scope, and refuses the `q`
                             search parameter too. That is why `list_unread`
                             filters by label IDs and by date in code.

That ceiling matters more than any check in this file: if a bug, or a hostile
email, ever got this process to ask for more, Google would say no. It is the
same idea as the MCP gateway's Viewer service account
(tsd-homelab-mcp-server.md) — read-only by capability, not by configuration.

Raw aiohttp, no google-api-python-client: three endpoints, a token refresh, and
an image that stays ~50 MB.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

SCOPES = (
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/gmail.metadata",
)

# A Desktop-app OAuth client accepts any loopback port. Nothing listens on it:
# the browser's redirect fails to load, and the code is read out of the address
# bar instead. That is what lets the sign-in run on a headless box.
REDIRECT_URI = "http://127.0.0.1:8765"

# Messages fetched per digest. Each costs one metadata request; the digest shows
# three, and anything past this is reported as "20+" rather than counted.
MAIL_FETCH = 20


class GoogleError(RuntimeError):
    """A Google call failed in a way worth saying plainly in the digest."""


# --- Data ---------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    title: str
    # Timed events carry aware datetimes; all-day events carry dates.
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date
    all_day: bool
    # False for events marked "free" (transparent) — they never conflict.
    busy: bool


@dataclass(frozen=True)
class Mail:
    sender: str
    subject: str
    received: dt.datetime
    # Gmail's thread id, for a link that opens the conversation. Empty when
    # the response had none or it was not plain hex (see mail_link).
    thread: str = ""


# --- Response parsing (pure; no I/O, unit-testable) ---------------------------

def _when(raw: dict) -> tuple[dt.datetime | dt.date, bool] | None:
    if raw.get("dateTime"):
        try:
            return dt.datetime.fromisoformat(raw["dateTime"].replace("Z", "+00:00")), False
        except ValueError:
            return None
    if raw.get("date"):
        try:
            return dt.date.fromisoformat(raw["date"]), True
        except ValueError:
            return None
    return None


def parse_events(payload: dict) -> list[Event]:
    """Events from a Calendar `events.list` response.

    Dropped: cancelled events, events you declined, and anything whose start or
    end does not parse. A declined meeting still sits on the calendar, and
    listing it as something you're doing today would be wrong.
    """
    out: list[Event] = []
    for item in payload.get("items", []):
        if item.get("status") == "cancelled":
            continue
        if any(a.get("self") and a.get("responseStatus") == "declined"
               for a in item.get("attendees", [])):
            continue
        start, end = _when(item.get("start", {})), _when(item.get("end", {}))
        if start is None or end is None:
            continue
        out.append(Event(
            title=(item.get("summary") or "(no title)").strip(),
            start=start[0],
            end=end[0],
            all_day=start[1],
            busy=item.get("transparency") != "transparent",
        ))
    return out


def parse_message(payload: dict) -> Mail | None:
    """One Gmail `messages.get?format=metadata` response."""
    try:
        received = dt.datetime.fromtimestamp(int(payload["internalDate"]) / 1000, tz=dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("payload", {}).get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    thread = str(payload.get("threadId") or payload.get("id") or "")
    return Mail(
        sender=(name or addr or "unknown sender").strip(),
        subject=(headers.get("subject") or "(no subject)").strip(),
        received=received,
        thread=thread if _HEX_ID.fullmatch(thread) else "",
    )


# Gmail ids are hex. Anything else is not put into a URL at all.
_HEX_ID = re.compile(r"[0-9a-f]{6,32}")


def mail_link(m: Mail) -> str | None:
    """Where a digest line about this message should point: the conversation
    in Gmail's web app, first signed-in account. None when there is no id."""
    return f"https://mail.google.com/mail/u/0/#all/{m.thread}" if m.thread else None


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
        # offline + consent: without both, Google may issue no refresh token,
        # and the digest would work for exactly one hour.
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return AuthRequest(url=f"{AUTH_URL}?{urlencode(params)}", verifier=verifier, state=state)


def code_from_redirect(pasted: str, expected_state: str) -> str:
    """Pull the authorization code out of the URL the browser was sent to."""
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
        self._access: tuple[str, float] | None = None  # (token, expires_at)
        # A digest reads calendars and mail concurrently. Without this, every
        # request that starts before the first refresh returns does its own
        # refresh — measured at three per digest against a mock.
        self._lock = asyncio.Lock()

    async def _token(self) -> str:
        async with self._lock:
            if self._access and self._access[1] > time.time() + 60:
                return self._access[0]
            return await self._refresh()

    async def _refresh(self) -> str:
        async with self._session.post(TOKEN_URL, data={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.json(content_type=None)
        if "access_token" not in body:
            # invalid_grant is the one that will actually happen: the token was
            # revoked, the password changed, or the OAuth app was left in
            # "Testing", where Google expires refresh tokens after 7 days.
            reason = body.get("error") or f"HTTP {resp.status}"
            raise GoogleError(f"Google sign-in no longer valid ({reason}) — rerun --google-auth")
        self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._access[0]

    async def _get(self, url: str, params: list[tuple[str, str]] | dict) -> dict:
        token = await self._token()
        async with self._session.get(
            url, params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                message = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
                raise GoogleError(f"HTTP {resp.status}: {message or 'request failed'}")
            return body

    async def events(self, calendar_id: str, start: dt.datetime, end: dt.datetime) -> list[Event]:
        payload = await self._get(
            f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events",
            {
                # Whole seconds: RFC 3339 allows fractions, and there is no
                # reason to find out whether every Google frontend agrees.
                "timeMin": start.replace(microsecond=0).isoformat(),
                "timeMax": end.replace(microsecond=0).isoformat(),
                "singleEvents": "true",   # expand recurring events into today's instance
                "orderBy": "startTime",
                "maxResults": "50",
            },
        )
        return parse_events(payload)

    async def list_unread(self, labels: tuple[str, ...]) -> tuple[list[str], int | None]:
        """Newest message IDs carrying every label, and Gmail's estimate of the total.

        Label IDs rather than a search query, because gmail.metadata does not
        allow `q` at all.
        """
        params = [("labelIds", label) for label in labels] + [("maxResults", str(MAIL_FETCH))]
        payload = await self._get(f"{GMAIL_API}/messages", params)
        ids = [m["id"] for m in payload.get("messages", []) if m.get("id")]
        estimate = payload.get("resultSizeEstimate")
        return ids, estimate if isinstance(estimate, int) else None

    async def message(self, message_id: str) -> Mail | None:
        payload = await self._get(
            f"{GMAIL_API}/messages/{quote(message_id, safe='')}",
            [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject")],
        )
        return parse_message(payload)
