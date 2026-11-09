"""Keebdex reader (T4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .match import WantedSet


@dataclass
class GroupBuy:
    name: str
    url: str
    ends: date | None
    days_left: int | None


@dataclass
class GroupBuyMatch:
    set: WantedSet
    gb: GroupBuy
    key: str
    closing: bool
