"""Keebdex reader (F5, F6; gate G-F4).

Keebdex is a server-rendered Nuxt site. Each page carries a __NUXT_DATA__ JSON payload in
"devalue" form: a flat array where objects refer to other entries by index. The parser reads
that payload, not the markup, because the markup's utility classes change often.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from .http import NoConnection, Timeout
from .match import Wanted, WantedSet, matches, normalise

USER_AGENT = "Radar/1.0 (+https://github.com/kkharrigan-ship-it/radar)"
TIMEOUT = 15
SITE = "https://keebdex.org"
CLOSING_DAYS = 3
LAYOUT_CHANGED = "Keebdex layout changed"
UNREACHABLE = "Keebdex unreachable"

_PAYLOAD = re.compile(r'<script\b[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NEEDED = {"name", "end_date", "profile_keyset_id"}


class LayoutChanged(Exception):
    def __init__(self) -> None:
        super().__init__(LAYOUT_CHANGED)


@dataclass
class GroupBuy:
    name: str                  # as listed, such as "GMK Testbed Nº3"
    url: str                   # the listing's link on keebdex.org
    ends: date | None
    days_left: int | None


@dataclass
class KeebdexResult:
    kind: Literal["ok", "error"]
    group_buys: list[GroupBuy] = field(default_factory=list)
    error: str = ""


@dataclass
class GroupBuyMatch:
    set: WantedSet
    gb: GroupBuy
    key: str
    closing: bool


def _ref(data: list, index):
    """Follow one devalue reference. Negative indices are special values (undefined, NaN...)."""
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(data):
        return None
    value = data[index]
    # Reactive wrappers: ["ShallowReactive", i], ["Reactive", i], ["Ref", i] ...
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0].endswith(("Reactive", "Ref")):
        return _ref(data, value[1])
    return value


def parse(html: str, today: date) -> list[GroupBuy]:
    m = _PAYLOAD.search(html)
    if not m:
        raise LayoutChanged()
    try:
        data = json.loads(m.group(1))
    except ValueError:
        raise LayoutChanged() from None
    if not isinstance(data, list):
        raise LayoutChanged()

    found: dict[str, GroupBuy] = {}
    seen_any = False
    for obj in data:
        if not (isinstance(obj, dict) and _NEEDED <= obj.keys()):
            continue
        name, end, keyset = (_ref(data, obj[k]) for k in ("name", "end_date", "profile_keyset_id"))
        if not (isinstance(name, str) and isinstance(end, str) and isinstance(keyset, str) and keyset):
            continue
        try:
            ends = date.fromisoformat(end[:10])
        except ValueError:
            continue
        seen_any = True
        if keyset in found:
            continue
        days_left = (ends - today).days
        if days_left < 0:
            found[keyset] = None  # type: ignore[assignment]  # passed: dropped, still de-duplicated
            continue
        found[keyset] = GroupBuy(_listed_name(data, obj, name), f"{SITE}/keyset/{keyset}", ends, days_left)
    if not seen_any:
        raise LayoutChanged()
    return [gb for gb in found.values() if gb is not None]


def _listed_name(data: list, obj: dict, name: str) -> str:
    """Keebdex shows a listing as "<profile> <name>", such as "GMK Testbed Nº3"."""
    profile = _ref(data, obj.get("profile"))
    prefix = _ref(data, profile.get("name")) if isinstance(profile, dict) else None
    name = name.strip()
    if isinstance(prefix, str) and prefix.strip() and not normalise(name).startswith(normalise(prefix) + " "):
        return f"{prefix.strip()} {name}"
    return name


def fetch(source, transport, today: date) -> KeebdexResult:
    try:
        resp = transport.request("GET", source.address, {"User-Agent": USER_AGENT}, None, TIMEOUT)
    except (Timeout, NoConnection):
        return KeebdexResult("error", error=UNREACHABLE)
    if resp.status != 200:
        return KeebdexResult("error", error=f"Keebdex error {resp.status}")
    try:
        return KeebdexResult("ok", parse(resp.body.decode("utf-8", errors="replace"), today))
    except LayoutChanged:
        return KeebdexResult("error", error=LAYOUT_CHANGED)


def match_group_buys(gbs: list[GroupBuy], wanted: Wanted) -> list[GroupBuyMatch]:
    out = []
    for gb in gbs:
        norm = normalise(gb.name)
        closing = gb.days_left is not None and gb.days_left <= CLOSING_DAYS
        for s in wanted.sets:
            if any(matches(t, norm) for t in s.terms):
                key = f"keebdex:{s.norm}:{'closing' if closing else 'live'}"
                out.append(GroupBuyMatch(s, gb, key, closing))
    return out
