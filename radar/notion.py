"""Notion client (data sources API) and property helpers.

Errors are plain words only: never a URL, an ID or the token.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .http import NoConnection, Timeout, Transport

BASE = "https://api.notion.com/v1"
VERSION = "2026-03-11"
SPACING = 0.4
RETRY_WAIT = 2.0
MAX_RETRY_AFTER = 60.0
TIMEOUT = 20.0
TEXT_PART = 2000


class NotionUnavailable(Exception):
    """Notion could not be used, after the retry. Message is plain words only."""


@dataclass
class Page:
    id: str
    props: dict
    last_edited: datetime


class NotionStore(Protocol):
    def query(self, ds_id: str, filter: dict | None = None,
              sorts: list | None = None) -> list[Page]: ...
    def create(self, ds_id: str, props: dict) -> str: ...
    def update(self, page_id: str, props: dict) -> None: ...


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class NotionClient:
    def __init__(self, token: str, transport: Transport, clock):
        self._token = token
        self._transport = transport
        self._clock = clock
        self._last: datetime | None = None
        self.calls = 0

    # -- plumbing --

    def _space(self) -> None:
        if self._last is not None:
            gap = (self._clock.now() - self._last).total_seconds()
            if gap < SPACING:
                self._clock.sleep(SPACING - gap)

    def _send(self, method: str, path: str, payload: dict):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }
        body = json.dumps(payload).encode()
        self._space()
        self.calls += 1
        try:
            return self._transport.request(method, BASE + path, headers, body, TIMEOUT)
        finally:
            self._last = self._clock.now()

    def _call(self, method: str, path: str, payload: dict) -> dict:
        for attempt in (1, 2):
            try:
                resp = self._send(method, path, payload)
            except (Timeout, NoConnection):
                if attempt == 2:
                    raise NotionUnavailable("notion unreachable") from None
                self._clock.sleep(RETRY_WAIT)
                continue
            if resp.status == 200:
                try:
                    return json.loads(resp.body or b"{}")
                except ValueError:
                    raise NotionUnavailable("notion bad reply") from None
            retryable = resp.status == 429 or resp.status >= 500
            if not retryable or attempt == 2:
                raise NotionUnavailable(f"notion {resp.status}")
            self._clock.sleep(self._wait(resp))
        raise NotionUnavailable("notion unavailable")  # pragma: no cover

    @staticmethod
    def _wait(resp) -> float:
        if resp.status != 429:
            return RETRY_WAIT
        headers = {k.lower(): v for k, v in resp.headers.items()}
        try:
            return min(max(float(headers.get("retry-after", "")), 0.0), MAX_RETRY_AFTER)
        except ValueError:
            return RETRY_WAIT

    # -- the store --

    def query(self, ds_id: str, filter: dict | None = None, sorts: list | None = None) -> list[Page]:
        pages: list[Page] = []
        cursor = None
        while True:
            payload: dict = {"page_size": 100}
            if filter is not None:
                payload["filter"] = filter
            if sorts is not None:
                payload["sorts"] = sorts
            if cursor:
                payload["start_cursor"] = cursor
            data = self._call("POST", f"/data_sources/{ds_id}/query", payload)
            for r in data.get("results", []):
                pages.append(Page(r["id"], r.get("properties", {}), _parse_time(r["last_edited_time"])))
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return pages

    def create(self, ds_id: str, props: dict) -> str:
        data = self._call("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": ds_id},
            "properties": props,
        })
        return data["id"]

    def update(self, page_id: str, props: dict) -> None:
        self._call("PATCH", f"/pages/{page_id}", {"properties": props})


# --- property writers --------------------------------------------------------

def _rich(s: str) -> list[dict]:
    return [{"type": "text", "text": {"content": s[i:i + TEXT_PART]}} for i in range(0, len(s), TEXT_PART)]


def title(s: str) -> dict:
    return {"title": _rich(s)}


def text(s: str) -> dict:
    return {"rich_text": _rich(s or "")}


def select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def number(n: float | None) -> dict:
    return {"number": n}


def checkbox(b: bool) -> dict:
    return {"checkbox": bool(b)}


def date(dt: datetime | None) -> dict:
    if dt is None:
        return {"date": None}
    return {"date": {"start": dt.replace(microsecond=0).isoformat(timespec="seconds")}}


def url(s: str | None) -> dict:
    return {"url": s or None}


def relation(page_ids) -> dict:
    return {"relation": [{"id": i} for i in page_ids]}


# --- property readers --------------------------------------------------------

def _join(parts) -> str:
    out = []
    for p in parts or []:
        if "plain_text" in p:
            out.append(p["plain_text"])
        else:
            out.append((p.get("text") or {}).get("content", ""))
    return "".join(out)


def read_title(p: dict | None) -> str:
    return _join((p or {}).get("title"))


def read_text(p: dict | None) -> str:
    return _join((p or {}).get("rich_text"))


def read_select(p: dict | None) -> str | None:
    sel = (p or {}).get("select")
    return sel.get("name") if sel else None


def read_number(p: dict | None) -> float | None:
    return (p or {}).get("number")


def read_checkbox(p: dict | None) -> bool:
    return bool((p or {}).get("checkbox"))


def read_date(p: dict | None) -> datetime | None:
    d = (p or {}).get("date")
    if not d or not d.get("start"):
        return None
    return _parse_time(d["start"])


def read_url(p: dict | None) -> str:
    return (p or {}).get("url") or ""


def read_relation(p: dict | None) -> list[str]:
    return [r["id"] for r in (p or {}).get("relation") or []]
