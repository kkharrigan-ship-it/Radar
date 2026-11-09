"""Radar Log row builders, the Dedupe Key check, and Radar Sources updates (F7, F8)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from . import config as c
from . import notion as n
from .notion import NotionUnavailable, Page

ALERT_MAX = 120
POST_TEXT_MAX = 1500


class CreateFailed(Exception):
    def __init__(self) -> None:
        super().__init__("Couldn't save a row")


@dataclass
class Source:
    page_id: str
    name: str
    kind: str
    address: str
    enabled: bool = True
    cursor: str = ""
    error_count: int = 0
    last_error: str = ""

    @classmethod
    def from_page(cls, page: Page) -> "Source":
        p = page.props
        return cls(
            page_id=page.id,
            name=n.read_title(p.get(c.SRC_NAME)),
            kind=n.read_select(p.get(c.SRC_KIND)) or "",
            address=n.read_text(p.get(c.SRC_ADDRESS)).strip() or n.read_url(p.get(c.SRC_ADDRESS)),
            enabled=n.read_checkbox(p.get(c.SRC_ENABLED)),
            cursor=n.read_text(p.get(c.SRC_CURSOR)).strip(),
            error_count=int(n.read_number(p.get(c.SRC_ERROR_COUNT)) or 0),
            last_error=n.read_text(p.get(c.SRC_LAST_ERROR)),
        )


def ensure_row(store, log_ds: str, row: dict, key: str) -> Literal["created", "exists"]:
    """Create the row only if no row has this Dedupe Key. Raises CreateFailed."""
    return ensure_row_page(store, log_ds, row, key)[0]


def ensure_row_page(store, log_ds: str, row: dict, key: str, now: datetime | None = None
                    ) -> tuple[Literal["created", "exists"], Page | None]:
    """As ensure_row, also giving the new row as a Page (None when it already existed)."""
    try:
        found = store.query(log_ds, {"property": c.LOG_DEDUPE_KEY, "rich_text": {"equals": key}})
        if found:
            return "exists", None
        props = {**row, c.LOG_DEDUPE_KEY: n.text(key)}
        page_id = store.create(log_ds, props)
    except NotionUnavailable:
        raise CreateFailed() from None
    return "created", Page(page_id, props, now or datetime.now().astimezone())


def system_row(key: str, words: str, now: datetime) -> tuple[str, dict]:
    """A System row: Source Radar, Match Type System, Push pending, Confidence empty."""
    props = _common(c.SOURCE_RADAR, c.SOURCE_RADAR, "", "", now, key)
    props.update({
        c.LOG_ALERT: n.title(cut(words, ALERT_MAX)),
        c.LOG_STAGE: n.select(c.STAGE_PUSH_PENDING),
        c.LOG_MATCH_TYPE: n.select(c.MATCH_SYSTEM),
        c.LOG_WHY: n.text(words),
    })
    return key, props


def cut(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[: limit - 1] + "…"


def alert_title(match_type: str, item: str, source_name: str) -> str:
    return cut(f"{match_type}: {item} · {source_name}", ALERT_MAX)


def _common(source: str, source_name: str, link: str, post_text: str, now: datetime, key: str) -> dict:
    return {
        c.LOG_SOURCE: n.select(source),
        c.LOG_SOURCE_NAME: n.text(source_name),
        c.LOG_DEDUPE_KEY: n.text(key),
        c.LOG_LINK: n.url(link),
        c.LOG_POST_TEXT: n.text(post_text[:POST_TEXT_MAX]),
        c.LOG_FOUND_AT: n.date(now),
        c.LOG_PUSH_ATTEMPTS: n.number(0),
        c.LOG_JUDGE_ATTEMPTS: n.number(0),
    }


def wanted_row(msg, m, now: datetime) -> tuple[str, dict]:
    key = f"discord:{msg.id}:{m.set.norm}"
    item = f"{', '.join(k.name for k in m.kits)} of {m.set.name}"
    props = _common(c.SOURCE_DISCORD, msg.source_name, msg.link, msg.text, now, key)
    props.update({
        c.LOG_ALERT: n.title(alert_title(c.MATCH_WANTED, item, msg.source_name)),
        c.LOG_STAGE: n.select(c.STAGE_PUSH_PENDING),
        c.LOG_MATCH_TYPE: n.select(c.MATCH_WANTED),
        c.LOG_ITEM: n.text(item),
        c.LOG_WHY: n.text(f"{item} mentioned in {msg.source_name}"),
        c.LOG_SET: n.relation([m.set.page_id]),
        c.LOG_KIT: n.relation([k.page_id for k in m.kits]),
        c.LOG_CONFIDENCE: n.select(c.CONFIDENCE_HIGH),
    })
    return key, props


def candidate_row(msg, now: datetime) -> tuple[str, dict]:
    key = f"discord:{msg.id}:candidate"
    props = _common(c.SOURCE_DISCORD, msg.source_name, msg.link, msg.text, now, key)
    props.update({
        c.LOG_ALERT: n.title(cut(f"{c.STAGE_TO_JUDGE}: {msg.text[:60]} · {msg.source_name}", ALERT_MAX)),
        c.LOG_STAGE: n.select(c.STAGE_TO_JUDGE),
    })
    return key, props


def _closing_words(days: int | None) -> str:
    if days is None or days > 1:
        return f"closes in {days} days"
    return "closes in 1 day" if days == 1 else "closes today"


def group_buy_row(source: Source, m, now: datetime) -> tuple[str, dict]:
    gb = m.gb
    why = (f"Group buy for {m.set.name} {_closing_words(gb.days_left)}" if m.closing
           else f"Group buy for {m.set.name} is live on Keebdex")
    props = _common(c.SOURCE_KEEBDEX, source.name, gb.url, gb.name, now, m.key)
    props.update({
        c.LOG_ALERT: n.title(alert_title(c.MATCH_GROUP_BUY, gb.name, source.name)),
        c.LOG_STAGE: n.select(c.STAGE_PUSH_PENDING),
        c.LOG_MATCH_TYPE: n.select(c.MATCH_GROUP_BUY),
        c.LOG_ITEM: n.text(gb.name),
        c.LOG_WHY: n.text(why),
        c.LOG_SET: n.relation([m.set.page_id]),
        c.LOG_CONFIDENCE: n.select(c.CONFIDENCE_HIGH),
    })
    return m.key, props


def source_ok(store, source: Source, now: datetime, cursor: str | None = None) -> None:
    props = {c.SRC_LAST_OK: n.date(now), c.SRC_ERROR_COUNT: n.number(0)}
    if cursor is not None:
        props[c.SRC_CURSOR] = n.text(cursor)
    source.error_count = 0
    store.update(source.page_id, props)
    if cursor is not None:
        source.cursor = cursor


def source_error(store, source: Source, words: str) -> None:
    source.error_count += 1
    source.last_error = words
    store.update(source.page_id, {
        c.SRC_LAST_ERROR: n.text(words),
        c.SRC_ERROR_COUNT: n.number(source.error_count),
    })
