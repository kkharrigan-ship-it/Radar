"""Discord reader (T2)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Message:
    id: str                   # snowflake, compared as int
    type: int
    text: str                 # built from content and embeds
    link: str                 # empty if it cannot be built
    source_name: str
