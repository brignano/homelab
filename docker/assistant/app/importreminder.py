"""The weekly nudge to download bank statements and import them into Actual Budget.

Actual Budget (budget.home.brignano.io) has no bank sync, by design, so the
transactions only arrive when someone downloads them and imports the file. This
is the "someone": once a week the bot posts one fixed message in #ask that pings
you, names the accounts, and gives the date range to download. No Claude call,
so it costs nothing and cannot hallucinate a date.

The range starts a few days before the previous reminder, on purpose: a little
overlap means a skipped week or a transaction that posted late is still caught.
"""

from __future__ import annotations

import datetime as dt

# Where the files come from. Edit here when an account is added or closed.
ACCOUNTS = ("Bank of America checking", "Bank of America savings", "Discover card")

OVERLAP_DAYS = 3
BUDGET_URL = "https://budget.home.brignano.io"


def range_start(now: dt.datetime) -> dt.date:
    """Seven days back, plus the overlap."""
    return now.date() - dt.timedelta(days=7 + OVERLAP_DAYS)


def fmt(d: dt.date) -> str:
    return f"{d:%a %b} {d.day}"


def text(now: dt.datetime, user_id: int | None) -> str:
    start = range_start(now)
    who = f"<@{user_id}> " if user_id else ""
    lines = "\n".join(f"- {a}" for a in ACCOUNTS)
    return (
        f"{who}**Time to import your bank statements.**\n"
        f"Download from **{fmt(start)}** to today ({fmt(now.date())}), as OFX/QFX if the bank "
        f"offers it (CSV otherwise), for:\n{lines}\n"
        f"Then import each file in {BUDGET_URL} (account → Import). "
        f"The overlap is on purpose; if you skipped last week, start earlier."
    )
