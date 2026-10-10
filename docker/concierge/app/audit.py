"""One line per event on stdout, for Loki.

Alloy ships every container's stdout to Loki, so "what did it do last Tuesday"
is a LogQL query rather than a feature:

    {container="concierge"} |= "event=tool_call"

Values are JSON-encoded so a quote or newline inside an email subject cannot
split a line or forge a field. Long values are cut: the audit trail records
WHAT was asked for, not the mailbox it came back with.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("concierge.audit")

MAX_VALUE = 300


def _fmt(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_VALUE:
        text = text[:MAX_VALUE] + "…"
    return json.dumps(text, ensure_ascii=False)


def line(event: str, **fields: object) -> str:
    return " ".join([f"event={event}"] + [f"{k}={_fmt(v)}" for k, v in fields.items()])


def emit(event: str, **fields: object) -> None:
    log.info(line(event, **fields))
