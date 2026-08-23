#!/usr/bin/env python3
"""Offline smoke tests for the assistant.

Run:  python3 tests/smoke.py        (from docker/assistant/)

Deliberately plain Python with no pytest. Everything here is pure and offline —
no Discord, no Ollama, no Prometheus — so it runs anywhere in under a second and
CI needs nothing but `pip install -r requirements.txt`.

What it is for: catching the class of breakage that has actually bitten this
repo. Every check below corresponds to a real bug or a real invariant, not to a
coverage target:

  * `num_thread` leaking into a request payload would silently undo the
    Modelfile pin that took generation from ~0.5 to ~16 tok/s.
  * The chat system prompt announcing "I cannot see live data" while holding
    live readings is the bug PR #42 existed to fix.
  * A malformed guild.yml would create a mis-named Discord channel that cannot
    be undone without losing its history.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_failures: list[str] = []
_passed = 0


def check(name: str):
    """Decorator: run the function immediately, record pass/fail, keep going."""
    def wrap(fn):
        global _passed
        try:
            fn()
        except Exception:
            _failures.append(f"{name}\n{traceback.format_exc()}")
            print(f"FAIL  {name}")
        else:
            _passed += 1
            print(f"ok    {name}")
        return fn
    return wrap


BASE_ENV = {
    "DISCORD_TOKEN": "x",
    "DISCORD_GUILD_ID": "1",
    "DISCORD_DIGEST_CHANNEL_ID": "2",
    "DISCORD_ALLOWED_USER_IDS": "3",
    "TZ": "UTC",
}


def with_env(**extra):
    """A clean environment holding only the required vars plus `extra`."""
    keep = {k: v for k, v in os.environ.items() if not k.startswith(
        ("DISCORD_", "CHAT_", "OLLAMA_", "ASK_", "SUMMARIZE_", "DIGEST_", "LLM_", "MAX_", "TZ")
    )}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update(BASE_ENV)
    os.environ.update({k: str(v) for k, v in extra.items()})


# --- config -------------------------------------------------------------------

@check("config: defaults")
def _():
    from app.config import Config
    with_env()
    c = Config.from_env()
    assert c.ollama_keep_alive == "30m", c.ollama_keep_alive
    assert c.chat_temperature == 0.6, c.chat_temperature
    assert c.chat_predict == 350
    assert c.num_ctx == 4096
    assert c.chat_channel_id is None, "chat mode must be off unless configured"
    assert c.chat_live_metrics is True


@check("config: overrides are read")
def _():
    from app.config import Config
    with_env(OLLAMA_KEEP_ALIVE="-1", CHAT_TEMPERATURE="0.85", CHAT_NUM_PREDICT="500",
             DISCORD_CHAT_CHANNEL_ID="99", CHAT_LIVE_METRICS="false")
    c = Config.from_env()
    assert c.ollama_keep_alive == "-1"
    assert c.chat_temperature == 0.85
    assert c.chat_predict == 500
    assert c.chat_channel_id == 99
    assert c.chat_live_metrics is False


@check("config: bad values fail loudly, not silently")
def _():
    from app.config import Config
    for var, bad in (("CHAT_TEMPERATURE", "hot"), ("CHAT_NUM_PREDICT", "lots"), ("TZ", "Mars/Olympus")):
        with_env(**{var: bad})
        try:
            Config.from_env()
        except SystemExit as exc:
            assert "config error" in str(exc), (var, exc)
        else:
            raise AssertionError(f"{var}={bad!r} was accepted")


@check("config: missing allowlist is refused")
def _():
    from app.config import Config
    with_env()
    del os.environ["DISCORD_ALLOWED_USER_IDS"]
    try:
        Config.from_env()
    except SystemExit as exc:
        assert "ALLOWED_USER_IDS" in str(exc), exc
    else:
        raise AssertionError("an empty allowlist would let the whole guild queue jobs")


# --- chat prompt --------------------------------------------------------------

@check("chat: readings present -> stated as fact, blindness not claimed")
def _():
    from app.chat import build_system
    s = build_system("services 15/15 up; CPU 2%")
    assert "services 15/15 up" in s
    assert "cannot see live system data" not in s, "claims blindness while holding readings"
    assert "Never invent" in s
    assert "Do NOT mention the readings" in s, "missing the do-not-volunteer guard"


@check("chat: no readings -> says so, invents nothing")
def _():
    from app.chat import build_system
    s = build_system(None)
    assert "cannot see live system data" in s
    assert "Never invent readings" in s
    assert "LIVE HOMELAB READINGS" not in s


@check("chat: tone line survives in both variants")
def _():
    from app.chat import build_system
    for facts in ("CPU 2%", None):
        s = build_system(facts)
        assert "knowledgeable colleague" in s, f"tone line missing (facts={facts!r})"
        assert "no cheerleading" in s


@check("chat: context follows Discord's primitives")
def _():
    from app.chat import CHAIN, SINGLE, THREAD, context_mode
    assert context_mode(in_thread=False, is_reply=False) == SINGLE
    assert context_mode(in_thread=False, is_reply=True) == CHAIN
    assert context_mode(in_thread=True, is_reply=False) == THREAD
    # Inside a thread the whole thread is the conversation; replying adds nothing.
    assert context_mode(in_thread=True, is_reply=True) == THREAD


@check("chat: history trims oldest-first and always keeps the question")
def _():
    from app.chat import Turn, build_messages
    turns = [Turn("user", f"m{i}" * 10) for i in range(20)]
    msgs = build_messages(turns, system="S", max_turns=5, max_chars=10_000)
    assert msgs[0]["role"] == "system"
    assert len(msgs) == 6, len(msgs)
    assert msgs[-1]["content"] == turns[-1].content, "newest turn must be last"
    assert msgs[1]["content"] == turns[-5].content, "should keep the newest 5"

    # The newest turn is kept even when it alone blows the character budget.
    huge = [Turn("user", "a" * 50), Turn("user", "b" * 5000)]
    msgs = build_messages(huge, system="S", max_turns=12, max_chars=100)
    assert len(msgs) == 2 and msgs[1]["content"] == "b" * 5000


@check("chat: notes and footers stay out of the conversation")
def _():
    from app.chat import clean_bot_text, is_ignorable
    assert is_ignorable("// just a note for me")
    assert is_ignorable("   ")
    assert not is_ignorable("what is the load?")
    assert clean_bot_text("the answer\n-# llama3.2:3b · 12s") == "the answer"
    assert clean_bot_text("line one\nline two") == "line one\nline two"


# --- facts --------------------------------------------------------------------

@check("facts: Python computes the verdict, not the model")
def _():
    from app.facts import Facts
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    assert Facts(collected_at=now, targets_up=5, targets_total=5).all_clear
    hot = Facts(collected_at=now, targets_up=5, targets_total=5, cpu_pct=99.0)
    assert not hot.all_clear and "CPU averaged 99%" in hot.concerns[0]
    down = Facts(collected_at=now, targets_up=4, targets_total=5, targets_down=["loki"])
    assert "loki" in down.concerns[0]
    # A collection failure must never render as "all good".
    assert not Facts(collected_at=now, problems=["prometheus unreachable"]).all_clear


@check("facts: compact line carries readings and no interpretation")
def _():
    from app.facts import Facts
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    line = Facts(collected_at=now, targets_up=15, targets_total=15,
                 cpu_pct=2.0, mem_pct=30.0, disk_pct=12.0).compact()
    assert "15/15" in line and "2%" in line
    assert "\n" not in line, "must be one line — it rides on every turn"
    assert len(line) < 400, f"too long for a per-turn injection: {len(line)}"


# --- text ---------------------------------------------------------------------

@check("text: oversized input and output are bounded")
def _():
    from app.text import chunk, clamp_input
    kept, trimmed = clamp_input("x" * 100, 10)
    assert len(kept) <= 10 and trimmed is True
    kept, trimmed = clamp_input("short", 100)
    assert kept == "short" and trimmed is False
    parts = chunk("y" * 5000)
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts)
    assert "".join(parts).replace("\n", "") == "y" * 5000


# --- ollama -------------------------------------------------------------------

class _Resp:
    status = 200

    def __init__(self, body):
        self._b = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._b

    async def text(self):
        return ""


class _Session:
    """Records the payload instead of talking to Ollama."""

    def __init__(self, body):
        self._b = body
        self.sent = None

    def post(self, url, json=None, timeout=None):
        self.sent = json
        return _Resp(self._b)


@check("ollama: num_thread is NEVER sent (it would undo the Modelfile pin)")
def _():
    from app.ollama import Ollama
    for body, call in (
        ({"message": {"content": "hi"}}, lambda o: o.chat([{"role": "user", "content": "q"}], num_predict=10)),
        ({"response": "hi"}, lambda o: o.generate(prompt="p", system="s", num_predict=10)),
    ):
        sess = _Session(body)
        o = Ollama(sess, base_url="http://x", model="m", num_ctx=4096, timeout_s=5)
        asyncio.run(call(o))
        assert "num_thread" not in sess.sent["options"], sess.sent["options"]


@check("ollama: keep_alive and temperature reach the request")
def _():
    from app.ollama import Ollama
    sess = _Session({"message": {"content": "hi"}})
    o = Ollama(sess, base_url="http://x", model="m", num_ctx=4096, timeout_s=5, keep_alive="45m")
    asyncio.run(o.chat([{"role": "user", "content": "q"}], num_predict=10, temperature=0.85))
    assert sess.sent["keep_alive"] == "45m"
    assert sess.sent["options"]["temperature"] == 0.85
    assert sess.sent["options"]["num_ctx"] == 4096


@check("ollama: a length-capped reply is reported, not passed off as complete")
def _():
    from app.ollama import Ollama
    for reason, expected in (("length", True), ("stop", False)):
        sess = _Session({"message": {"content": "hi"}, "done_reason": reason})
        o = Ollama(sess, base_url="http://x", model="m", num_ctx=4096, timeout_s=5)
        comp = asyncio.run(o.chat([{"role": "user", "content": "q"}], num_predict=10))
        assert comp.truncated is expected, (reason, comp.truncated)


@check("ollama: an empty response is an error, not an empty message")
def _():
    from app.ollama import Ollama, OllamaError
    sess = _Session({"message": {"content": "   "}})
    o = Ollama(sess, base_url="http://x", model="m", num_ctx=4096, timeout_s=5)
    try:
        asyncio.run(o.chat([{"role": "user", "content": "q"}], num_predict=10))
    except OllamaError:
        pass
    else:
        raise AssertionError("empty generation should raise")


# --- provisioning -------------------------------------------------------------

@check("provision: the committed guild.yml is valid")
def _():
    from app.provision import load_desired
    desired = load_desired(str(HERE.parent / "guild.yml"))
    names = [c["name"] for cat in desired["categories"] for c in cat["channels"]]
    assert "chat" in names and "digest" in names and "alerts" in names, names
    assert desired["bot"]["nickname"] == "Otto"


@check("provision: matching server -> no actions; missing channel -> create")
def _():
    from app.provision import load_desired, plan
    desired = load_desired(str(HERE.parent / "guild.yml"))

    existing = {}
    for cat in desired["categories"]:
        existing[cat["name"]] = [
            {"name": c["name"], "topic": c.get("topic") or "", "bot_only": bool(c.get("bot_only"))}
            for c in cat["channels"]
        ]
    assert plan(desired, existing, current_nickname="Otto") == [], "a matching server needs no changes"

    # Drop #chat and it should be recreated — and nothing should be deleted.
    trimmed = {k: [c for c in v if c["name"] != "chat"] for k, v in existing.items()}
    actions = plan(desired, trimmed, current_nickname="Otto")
    kinds = {a.kind for a in actions}
    assert kinds == {"create_channel"}, kinds
    assert actions[0].channel == "chat"


@check("provision: unknown channels are reported, never deleted")
def _():
    from app.provision import load_desired, plan
    desired = load_desired(str(HERE.parent / "guild.yml"))
    existing = {
        cat["name"]: [
            {"name": c["name"], "topic": c.get("topic") or "", "bot_only": bool(c.get("bot_only"))}
            for c in cat["channels"]
        ]
        for cat in desired["categories"]
    }
    existing["HOMELAB"].append({"name": "old-notes", "topic": "", "bot_only": False})
    actions = plan(desired, existing, current_nickname="Otto")
    assert [a.kind for a in actions] == ["extra_channel"]
    assert "left alone" in actions[0].detail


@check("provision: a malformed guild.yml is rejected")
def _():
    import tempfile
    from app.provision import load_desired
    bad = [
        "categories:\n  - name: X\n    channels:\n      - name: Has Space\n",
        "categories:\n  - name: X\n    channels:\n      - name: UPPER\n",
        "categories:\n  - channels: []\n",
        "bot:\n  nickname: ''\ncategories: []\n",
    ]
    for src in bad:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
            fh.write(src)
            path = fh.name
        try:
            load_desired(path)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"accepted a bad guild.yml:\n{src}")
        finally:
            os.unlink(path)


# --- every module imports -----------------------------------------------------

@check("all modules import cleanly")
def _():
    import importlib
    for mod in ("bot", "chat", "config", "digest", "facts", "jobqueue",
                "logs", "ollama", "provision", "text"):
        importlib.import_module(f"app.{mod}")


# --- report -------------------------------------------------------------------

print()
if _failures:
    for f in _failures:
        print("=" * 70)
        print(f)
    print(f"{len(_failures)} failed, {_passed} passed")
    sys.exit(1)
print(f"{_passed} passed")
