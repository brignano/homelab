"""The digest's one line about unread leads and starred mail.

Deterministic: counts from Gmail searches, no model involved. Counts and
product names only. Never a sender, subject or body, in the line or in a log.
Read-only: it searches and counts, and never labels, archives or sends.
"""

from __future__ import annotations

import logging

log = logging.getLogger("concierge.maildigest")

# Lead notification sublabels, by the product name shown in the digest.
LEAD_LABELS = (("Driftwood", "Leads/Driftwood"), ("Sites", "Leads/Sites"), ("Guidebook", "Leads/Guidebook"))
STARRED_QUERY = "is:starred"


def lead_query(label: str) -> str:
    return f"label:{label} is:unread"


def mail_line(leads: dict[str, int], starred: int) -> str:
    """The line, or "" when every count is zero. Pure; tested."""
    parts = []
    for product, _label in LEAD_LABELS:
        n = leads.get(product, 0)
        if n > 0:
            parts.append(f"{n} new {product} lead{'' if n == 1 else 's'}")
    if starred > 0:
        parts.append(f"{starred} starred waiting")
    return "📬 " + ", ".join(parts) if parts else ""


async def digest_line(google) -> str:
    """Count with `google.count` and build the line. "" on zero counts, and
    on any failure (the line is a nicety and must not break the brief)."""
    try:
        leads = {product: await google.count(lead_query(label)) for product, label in LEAD_LABELS}
        starred = await google.count(STARRED_QUERY)
    except Exception as exc:  # noqa: BLE001 — class name only, nothing from the mail
        log.warning("mail line unavailable (%s)", exc.__class__.__name__)
        return ""
    return mail_line(leads, starred)
