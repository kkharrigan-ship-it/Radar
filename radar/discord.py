"""Discord reader (F3, F4; gates G-F2, G-F3, G-F3b).

Only normal messages (type 0) and replies (type 19) are read. Every other type, such as a
follow notice (type 12), is skipped but still moves the Cursor on.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from .http import NoConnection, Timeout

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/kkharrigan-ship-it/radar, 1.0)"
TIMEOUT = 10.0
PAGE = 100
MAX_PAGES = 5
MAX_RETRY_AFTER = 10.0
READ_TYPES = {0, 19}          # DEFAULT, REPLY

NO_TEXT = "Messages have no text: check the bot's Message Content setting"
RATE_LIMIT = "Discord rate limit"
FORBIDDEN = "Bot can't read this channel"
UNREACHABLE = "Discord unreachable"


@dataclass
class Message:
    id: str                   # snowflake, compared as int
    type: int
    text: str                 # built from content and embeds
    link: str                 # empty if it cannot be built
    source_name: str


@dataclass
class ReadResult:
    kind: Literal["ok", "first_run", "no_text", "error"]
    messages: list[Message] = field(default_factory=list)
    newest_id: str | None = None
    error: str = ""


class _Failed(Exception):
    def __init__(self, words: str):
        super().__init__(words)
        self.words = words


def message_text(raw: dict) -> str:
    parts = [raw.get("content") or ""]
    for embed in raw.get("embeds") or []:
        parts.append(embed.get("title") or "")
        parts.append(embed.get("description") or "")
        parts.extend(f.get("value") or "" for f in embed.get("fields") or [])
    return "\n".join(p for p in parts if p.strip())


def _get(transport, clock, token: str, url: str):
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    for attempt in (1, 2):
        try:
            resp = transport.request("GET", url, headers, None, TIMEOUT)
        except (Timeout, NoConnection):
            raise _Failed(UNREACHABLE) from None
        if resp.status == 200:
            try:
                return json.loads(resp.body or b"null")
            except ValueError:
                raise _Failed("Discord error 200") from None
        if resp.status == 429:
            try:
                wait = float(json.loads(resp.body or b"{}").get("retry_after"))
            except (ValueError, TypeError, AttributeError):
                wait = MAX_RETRY_AFTER
            if attempt == 1 and 0 <= wait < MAX_RETRY_AFTER:
                clock.sleep(wait)
                continue
            raise _Failed(RATE_LIMIT)
        if resp.status in (401, 403):
            raise _Failed(FORBIDDEN)
        raise _Failed(f"Discord error {resp.status}")
    raise _Failed(RATE_LIMIT)  # pragma: no cover


def _sorted(raws) -> list[dict]:
    if not isinstance(raws, list):
        raise _Failed("Discord error 200")
    return sorted(raws, key=lambda m: int(m["id"]))


def _read(source, token, transport, clock) -> ReadResult:
    base = f"{API}/channels/{source.address}/messages"
    if not source.cursor:
        raws = _sorted(_get(transport, clock, token, f"{base}?limit=1"))
        return ReadResult("first_run", newest_id=raws[-1]["id"] if raws else None)

    raws: list[dict] = []
    after = source.cursor
    for _ in range(MAX_PAGES):
        page = _sorted(_get(transport, clock, token, f"{base}?after={after}&limit={PAGE}"))
        raws.extend(page)
        if len(page) < PAGE:
            break
        after = page[-1]["id"]
    raws = _sorted(raws)
    newest = raws[-1]["id"] if raws else None

    kept = [r for r in raws if r.get("type") in READ_TYPES]
    texts = [message_text(r) for r in kept]
    if kept and not any(texts):
        return ReadResult("no_text", newest_id=newest, error=NO_TEXT)

    guild = _Lazy(lambda: _channel_guild(transport, clock, token, source.address))
    messages = []
    for raw, text in zip(kept, texts):
        if not text:
            continue
        messages.append(Message(
            id=raw["id"],
            type=raw["type"],
            text=text,
            link=_link(raw, source.address, guild),
            source_name=_source_name(raw, source.name),
        ))
    return ReadResult("ok", messages=messages, newest_id=newest)


class _Lazy:
    def __init__(self, fn):
        self._fn = fn
        self._done = False
        self._value = None

    def get(self):
        if not self._done:
            self._value = self._fn()
            self._done = True
        return self._value


def _channel_guild(transport, clock, token, channel) -> str | None:
    try:
        data = _get(transport, clock, token, f"{API}/channels/{channel}")
    except _Failed:
        return None
    return (data or {}).get("guild_id") if isinstance(data, dict) else None


def _link(raw: dict, channel: str, guild: _Lazy) -> str:
    ref = raw.get("message_reference") or {}
    if ref.get("guild_id") and ref.get("channel_id") and ref.get("message_id"):
        return f"https://discord.com/channels/{ref['guild_id']}/{ref['channel_id']}/{ref['message_id']}"
    g = guild.get()
    return f"https://discord.com/channels/{g}/{channel}/{raw['id']}" if g else ""


def _source_name(raw: dict, fallback: str) -> str:
    if raw.get("webhook_id"):
        name = ((raw.get("author") or {}).get("username") or "").strip()
        if name:
            return name
    return fallback


def read_source(source, token: str, transport, clock) -> ReadResult:
    try:
        return _read(source, token, transport, clock)
    except _Failed as f:
        return ReadResult("error", error=f.words)
    except (KeyError, ValueError, TypeError):
        return ReadResult("error", error="Discord error 200")
