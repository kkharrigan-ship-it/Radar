"""Stand-ins for every outside service. Made-up data only."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from radar.http import Response


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += timedelta(seconds=seconds)

    def advance(self, **kw) -> None:
        self._now += timedelta(**kw)


@dataclass
class Call:
    method: str
    url: str
    headers: dict
    body: bytes | None
    timeout: float
    at: datetime | None = None


class ScriptedTransport:
    """Returns queued responses, or raises queued errors, in order, recording each call."""

    def __init__(self, *script, clock: FakeClock | None = None):
        self.script = list(script)
        self.calls: list[Call] = []
        self.clock = clock

    def push(self, *items) -> None:
        self.script.extend(items)

    def request(self, method, url, headers, body, timeout):
        self.calls.append(Call(method, url, dict(headers), body, timeout,
                               self.clock.now() if self.clock else None))
        if not self.script:
            raise AssertionError("ScriptedTransport ran out of responses")
        item = self.script.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item


def json_response(status: int, payload, headers: dict | None = None) -> Response:
    import json
    return Response(status, headers or {}, json.dumps(payload).encode())


# --- Notion ------------------------------------------------------------------

import itertools
from radar import notion as _n


def _prop_value(prop: dict | None):
    if not prop:
        return None
    if "title" in prop:
        return _n.read_title(prop)
    if "rich_text" in prop:
        return _n.read_text(prop)
    if "select" in prop:
        return _n.read_select(prop)
    if "number" in prop:
        return _n.read_number(prop)
    if "checkbox" in prop:
        return _n.read_checkbox(prop)
    if "date" in prop:
        return _n.read_date(prop)
    if "url" in prop:
        return _n.read_url(prop)
    if "relation" in prop:
        return _n.read_relation(prop)
    return None


def _as_time(v):
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class FakeNotion:
    """An in-memory NotionStore. Evaluates only the filter shapes the script uses."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.rows: dict[str, dict] = {}       # page id -> {"ds", "props", "last_edited"}
        self.log: list[tuple] = []            # (kind, ds or page id, props)
        self.calls = 0
        self._ids = itertools.count(1)
        self._failures: list[list] = []       # [kind, remaining, predicate]

    # -- set-up helpers --

    def add(self, ds: str, props: dict, page_id: str | None = None, last_edited: datetime | None = None) -> str:
        page_id = page_id or f"00000000-0000-4000-8000-{next(self._ids) + 500:012d}"
        self.rows[page_id] = {"ds": ds, "props": dict(props),
                              "last_edited": last_edited or self.clock.now()}
        return page_id

    def fail_next(self, kind: str, n: int = 1, when=None) -> None:
        """Make the next n calls of a kind ("query", "create", "update") raise NotionUnavailable.
        `when(target, props)` narrows which calls count."""
        self._failures.append([kind, n, when])

    def rows_in(self, ds: str) -> list[_n.Page]:
        return [self._page(pid) for pid, r in self.rows.items() if r["ds"] == ds]

    def value(self, page_id: str, prop: str):
        return _prop_value(self.rows[page_id]["props"].get(prop))

    def find(self, ds: str, prop: str, value) -> list[str]:
        return [pid for pid, r in self.rows.items()
                if r["ds"] == ds and _prop_value(r["props"].get(prop)) == value]

    # -- NotionStore --

    def _maybe_fail(self, kind, target, props):
        for f in self._failures:
            if f[0] == kind and f[1] > 0 and (f[2] is None or f[2](target, props)):
                f[1] -= 1
                raise _n.NotionUnavailable("notion unavailable")

    def _page(self, pid) -> _n.Page:
        r = self.rows[pid]
        return _n.Page(pid, dict(r["props"]), r["last_edited"])

    def query(self, ds_id, filter=None, sorts=None):
        self.calls += 1
        self.log.append(("query", ds_id, filter))
        self._maybe_fail("query", ds_id, filter)
        pids = [pid for pid, r in self.rows.items()
                if r["ds"] == ds_id and (filter is None or self._match(r, filter))]
        for s in reversed(sorts or []):
            key = s["property"]
            present = [p for p in pids if _prop_value(self.rows[p]["props"].get(key)) is not None]
            absent = [p for p in pids if p not in present]
            present.sort(key=lambda p: _prop_value(self.rows[p]["props"][key]),
                         reverse=s.get("direction") == "descending")
            pids = present + absent
        return [self._page(p) for p in pids]

    def create(self, ds_id, props):
        self.calls += 1
        self.log.append(("create", ds_id, props))
        self._maybe_fail("create", ds_id, props)
        return self.add(ds_id, props)

    def update(self, page_id, props):
        self.calls += 1
        self.log.append(("update", page_id, props))
        self._maybe_fail("update", page_id, props)
        row = self.rows[page_id]
        row["props"].update(props)
        row["last_edited"] = self.clock.now()

    # -- filters --

    def _match(self, row, f) -> bool:
        if "and" in f:
            return all(self._match(row, g) for g in f["and"])
        if "or" in f:
            return any(self._match(row, g) for g in f["or"])
        if f.get("timestamp") == "last_edited_time":
            cond = f["last_edited_time"]
            return self._date_cond(row["last_edited"], cond)
        value = _prop_value(row["props"].get(f["property"]))
        for kind in ("select", "rich_text", "title", "checkbox"):
            if kind in f:
                cond = f[kind]
                if set(cond) != {"equals"}:
                    raise AssertionError(f"FakeNotion: unsupported {kind} filter {cond}")
                want = cond["equals"]
                if kind == "checkbox":
                    return bool(value) == want
                return (value or "") == want if kind != "select" else value == want
        if "date" in f:
            return self._date_cond(value, f["date"])
        raise AssertionError(f"FakeNotion: unsupported filter {f}")

    @staticmethod
    def _date_cond(value, cond) -> bool:
        if value is None:
            return False
        value = _as_time(value)
        (op, arg), = cond.items()
        arg = _as_time(arg)
        if op == "on_or_after":
            return value >= arg
        if op == "before":
            return value < arg
        raise AssertionError(f"FakeNotion: unsupported date filter {op}")


# --- Discord -----------------------------------------------------------------

import json as _json
import urllib.parse as _up


def discord_msg(id, type=0, content="", embeds=None, **extra) -> dict:
    return {"id": str(id), "type": type, "content": content, "embeds": embeds or [],
            "author": {"id": "9000000000000000999", "username": "someone"}, **extra}


class FakeDiscord:
    """A Transport that serves Discord channels like the real API: honours `after` and
    `limit`, and returns newest first. Queue errors with `fail_next`."""

    def __init__(self, channels: dict | None = None, guilds: dict | None = None):
        self.channels = {k: list(v) for k, v in (channels or {}).items()}
        self.guilds = guilds or {}
        self.calls: list[str] = []
        self._errors: list = []

    def fail_next(self, *items) -> None:
        self._errors.extend(items)

    def request(self, method, url, headers, body, timeout):
        self.calls.append(url)
        self.last_headers = dict(headers)
        if self._errors:
            item = self._errors.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        parts = _up.urlsplit(url)
        path = parts.path.split("/")
        channel = path[4]
        if len(path) == 5:
            return Response(200, {}, _json.dumps({"id": channel, "guild_id": self.guilds.get(channel)}).encode())
        q = dict(_up.parse_qsl(parts.query))
        msgs = sorted(self.channels.get(channel, []), key=lambda m: int(m["id"]))
        if "after" in q:
            msgs = [m for m in msgs if int(m["id"]) > int(q["after"])]
            msgs = msgs[: int(q.get("limit", 50))]
        else:
            msgs = msgs[-int(q.get("limit", 50)):]
        return Response(200, {}, _json.dumps(list(reversed(msgs))).encode())


# --- Keebdex -----------------------------------------------------------------

class FakeKeebdex:
    """A Transport serving saved Keebdex pages by URL. Queue errors with `fail_next`."""

    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = dict(pages or {})
        self.calls: list[tuple[str, dict, float]] = []
        self._errors: list = []

    def fail_next(self, *items) -> None:
        self._errors.extend(items)

    def request(self, method, url, headers, body, timeout):
        self.calls.append((url, dict(headers), timeout))
        if self._errors:
            item = self._errors.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if url not in self.pages:
            return Response(404, {}, b"not found")
        return Response(200, {"Content-Type": "text/html"}, self.pages[url].encode())


# --- Pushover and settings ---------------------------------------------------

from urllib.parse import parse_qs as _parse_qs
from zoneinfo import ZoneInfo as _ZoneInfo

from radar.config import Settings as _Settings


def make_settings(tz: str = "America/Chicago") -> _Settings:
    return _Settings(
        notion_token="fake-notion-token", discord_token="fake-discord-token",
        pushover_app="fake-app-token", pushover_user="fake-user-key",
        log_ds="ds-log", sources_ds="ds-sources", sets_ds="ds-sets",
        kits_ds="ds-kits", collection_ds="ds-collection", tz=_ZoneInfo(tz),
    )


class FakePushover:
    """A Transport for Pushover. `responder(form)` returns a Response or raises; default accepts.
    Each call's form is recorded as a dict of single values."""

    OK = b'{"status":1,"request":"00000000-0000-4000-8000-000000000077"}'

    def __init__(self, responder=None):
        self.responder = responder
        self.calls: list[dict] = []
        self.urls: list[str] = []

    def request(self, method, url, headers, body, timeout):
        form = {k: v[0] for k, v in _parse_qs((body or b"").decode()).items()}
        self.calls.append(form)
        self.urls.append(url)
        if self.responder:
            result = self.responder(form)
            if result is not None:
                if isinstance(result, BaseException):
                    raise result
                return result
        return Response(200, {}, self.OK)

    def messages(self) -> list[str]:
        return [c.get("message", "") for c in self.calls]
