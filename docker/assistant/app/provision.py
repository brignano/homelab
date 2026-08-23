"""Converge the Discord server to the layout declared in guild.yml.

Design rules:

* **Idempotent.** Running it twice is a no-op. It diffs desired state against
  what's actually on the server and emits only the differences.
* **Additive, never destructive.** It creates and it fixes drift. It does not
  delete channels — deleting one destroys its history, and #digest *is* the
  history. Channels present on the server but absent from guild.yml are
  reported so you can remove them by hand if you meant to.
* **Plan first.** `--provision` prints what it would do and changes nothing.
  Only `--provision --apply` touches the server.

The planning half (`plan()`) is pure: it takes two plain data structures and
returns a list of actions. All the Discord API work lives in `apply()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

log = logging.getLogger(__name__)

ActionKind = Literal[
    "create_category", "create_channel", "update_topic", "update_permissions",
    "extra_channel", "set_nickname",
]


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    category: str
    channel: str | None = None
    detail: str = ""
    # Desired channel spec, carried so apply() doesn't have to re-look it up.
    spec: dict[str, Any] = field(default_factory=dict, compare=False)

    def describe(self) -> str:
        where = f"{self.category}/#{self.channel}" if self.channel else self.category
        symbol = {
            "create_category": "+ category",
            "create_channel": "+ channel ",
            "update_topic": "~ topic   ",
            "update_permissions": "~ perms   ",
            "extra_channel": "? extra   ",
            "set_nickname": "~ nickname",
        }[self.kind]
        return f"  {symbol}  {where}" + (f"  — {self.detail}" if self.detail else "")


# --- Desired state -----------------------------------------------------------

def load_desired(path: str) -> dict[str, Any]:
    """Read and validate guild.yml. Fails loudly — a typo here would otherwise
    silently create a channel with the wrong name, which you then can't undo
    without losing whatever landed in it."""
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "categories" not in raw:
        raise SystemExit(f"{path}: expected a top-level 'categories:' list")

    bot = raw.get("bot") or {}
    if not isinstance(bot, dict):
        raise SystemExit(f"{path}: 'bot' must be a mapping")
    nickname = bot.get("nickname")
    if nickname is not None:
        if not isinstance(nickname, str) or not nickname.strip():
            raise SystemExit(f"{path}: bot.nickname must be a non-empty string")
        # Discord rejects nicknames over 32 characters outright.
        if len(nickname) > 32:
            raise SystemExit(f"{path}: bot.nickname is {len(nickname)} chars; Discord's limit is 32")

    categories = raw["categories"]
    if not isinstance(categories, list) or not categories:
        raise SystemExit(f"{path}: 'categories' must be a non-empty list")

    seen_channels: set[str] = set()
    for cat in categories:
        if not isinstance(cat, dict) or not cat.get("name"):
            raise SystemExit(f"{path}: every category needs a 'name'")
        channels = cat.get("channels") or []
        if not isinstance(channels, list):
            raise SystemExit(f"{path}: category {cat['name']!r} has a malformed 'channels' list")
        for ch in channels:
            if not isinstance(ch, dict) or not ch.get("name"):
                raise SystemExit(f"{path}: every channel in {cat['name']!r} needs a 'name'")
            name = ch["name"]
            if name != name.lower() or " " in name:
                raise SystemExit(
                    f"{path}: channel {name!r} must be lowercase with no spaces "
                    "(Discord silently rewrites others, which breaks the diff)"
                )
            if name in seen_channels:
                raise SystemExit(f"{path}: channel #{name} is declared twice")
            seen_channels.add(name)
    return raw


# --- Planning (pure) ---------------------------------------------------------

def plan(
    desired: dict[str, Any],
    existing: dict[str, list[dict[str, Any]]],
    current_nickname: str | None = None,
) -> list[Action]:
    """Diff desired state against the server.

    `existing` maps category name -> list of {name, topic, bot_only}. Channels
    outside any declared category appear under the "" key.
    """
    actions: list[Action] = []
    declared: set[str] = set()

    wanted_nick = (desired.get("bot") or {}).get("nickname")
    if wanted_nick and wanted_nick != current_nickname:
        actions.append(Action(
            "set_nickname", "bot",
            detail=f"{current_nickname or '(none)'} -> {wanted_nick}",
            spec={"nickname": wanted_nick},
        ))

    for cat in desired["categories"]:
        cat_name = cat["name"]
        current = existing.get(cat_name)
        if current is None:
            actions.append(Action("create_category", cat_name))
            current = []

        by_name = {c["name"]: c for c in current}
        for ch in cat.get("channels") or []:
            name = ch["name"]
            declared.add(name)
            want_topic = ch.get("topic") or ""
            want_bot_only = bool(ch.get("bot_only"))

            found = by_name.get(name)
            if found is None:
                actions.append(Action(
                    "create_channel", cat_name, name,
                    detail="locked to bot posts" if want_bot_only else "open",
                    spec=ch,
                ))
                continue

            if (found.get("topic") or "") != want_topic:
                actions.append(Action(
                    "update_topic", cat_name, name,
                    detail="topic differs from guild.yml", spec=ch,
                ))
            if bool(found.get("bot_only")) != want_bot_only:
                actions.append(Action(
                    "update_permissions", cat_name, name,
                    detail=f"should be {'bot-only' if want_bot_only else 'open'}", spec=ch,
                ))

    # Report anything on the server we don't manage — never delete it.
    for cat_name, channels in existing.items():
        for ch in channels:
            if ch["name"] not in declared:
                actions.append(Action(
                    "extra_channel", cat_name or "(uncategorised)", ch["name"],
                    detail="on the server but not in guild.yml — left alone",
                ))
    return actions


def render_plan(actions: list[Action]) -> str:
    changes = [a for a in actions if a.kind != "extra_channel"]
    extras = [a for a in actions if a.kind == "extra_channel"]

    lines: list[str] = []
    if not changes:
        lines.append("Server already matches guild.yml — nothing to do.")
    else:
        lines.append(f"{len(changes)} change(s) to apply:")
        lines += [a.describe() for a in changes]
    if extras:
        lines.append("")
        lines.append(f"{len(extras)} channel(s) not managed by guild.yml (never touched):")
        lines += [a.describe() for a in extras]
    return "\n".join(lines)


# --- Applying (Discord API) --------------------------------------------------

@dataclass
class Snapshot:
    """One REST read of the server, in the shapes the rest of this module wants."""
    state: dict[str, list[dict[str, Any]]]   # category name -> channel dicts, for plan()
    channels: dict[str, Any]                 # channel name -> discord object, for apply()
    categories: dict[str, Any]               # category name -> discord object, for apply()
    nickname: str | None = None              # the bot's current nickname in this guild


async def read_server(guild, me=None) -> Snapshot:
    """Snapshot the server's categories, text channels and the bot's nickname."""
    import discord

    fetched = await guild.fetch_channels()
    everyone = guild.default_role

    categories = {c.name: c for c in fetched if isinstance(c, discord.CategoryChannel)}
    by_id = {c.id: c.name for c in categories.values()}

    state: dict[str, list[dict[str, Any]]] = {}
    channels: dict[str, Any] = {}
    for ch in fetched:
        if not isinstance(ch, discord.TextChannel):
            continue
        state.setdefault(by_id.get(ch.category_id, ""), []).append({
            "name": ch.name,
            "topic": ch.topic or "",
            # bot_only is modelled exactly as we set it: @everyone cannot send.
            "bot_only": ch.overwrites_for(everyone).send_messages is False,
        })
        channels[ch.name] = ch

    # An existing but empty category must not be recreated.
    for name in categories:
        state.setdefault(name, [])
    return Snapshot(state=state, channels=channels, categories=categories,
                    nickname=getattr(me, "nick", None))


def _overwrites(guild, me, bot_only: bool) -> dict:
    """Permission overwrites for a channel.

    NOTE the second entry. Denying @everyone `send_messages` also denies the
    bot, since it's a member like any other — without an explicit allow for
    ourselves, locking #digest would stop the digest from posting into it.

    Only `send_messages` is granted, and that is deliberate. Discord rejects an
    overwrite that grants a permission the acting bot does not itself hold
    (403, error 50013), so every permission named here has to be in the invite.
    Keeping this to the single permission the bot actually uses means the invite
    stays minimal — the bot only ever posts, it never edits or deletes messages.
    """
    import discord

    if not bot_only:
        return {}
    return {
        guild.default_role: discord.PermissionOverwrite(send_messages=False),
        me: discord.PermissionOverwrite(send_messages=True),
    }


async def apply(guild, me, actions: list[Action], snapshot: Snapshot) -> dict[str, int]:
    """Execute the plan against the snapshot the plan was built from.

    Returns channel name -> id for every channel it created or touched, which is
    how you get the DISCORD_DIGEST_CHANNEL_ID that doesn't exist until now.
    """
    created_ids: dict[str, int] = {}
    objects = dict(snapshot.channels)
    categories = dict(snapshot.categories)

    for action in actions:
        if action.kind == "extra_channel":
            continue

        if action.kind == "set_nickname":
            nick = action.spec["nickname"]
            log.info("setting bot nickname to %s", nick)
            try:
                await me.edit(nick=nick)
            except Exception as exc:  # noqa: BLE001 — a cosmetic step must not abort provisioning
                log.warning("could not set nickname (needs Change Nickname permission): %s", exc)
            continue

        if action.kind == "create_category":
            log.info("creating category %s", action.category)
            categories[action.category] = await guild.create_category(action.category)
            continue

        category = categories.get(action.category)

        if action.kind == "create_channel":
            log.info("creating #%s", action.channel)
            channel = await guild.create_text_channel(
                action.channel,
                category=category,
                topic=action.spec.get("topic") or None,
                overwrites=_overwrites(guild, me, bool(action.spec.get("bot_only"))),
            )
            objects[action.channel] = channel
            created_ids[action.channel] = channel.id
            continue

        channel = objects.get(action.channel)
        if channel is None:
            log.warning("skipping %s — channel vanished between plan and apply", action.channel)
            continue

        if action.kind == "update_topic":
            log.info("updating topic on #%s", action.channel)
            await channel.edit(topic=action.spec.get("topic") or None)
        elif action.kind == "update_permissions":
            bot_only = bool(action.spec.get("bot_only"))
            log.info("setting #%s to %s", action.channel, "bot-only" if bot_only else "open")
            if bot_only:
                for target, ow in _overwrites(guild, me, True).items():
                    await channel.set_permissions(target, overwrite=ow)
            else:
                await channel.set_permissions(guild.default_role, overwrite=None)
        created_ids[action.channel] = channel.id

    return created_ids
