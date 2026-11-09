"""Push queue, the daily cap, the Sending mark, the Pushover call, and Sending clean-up.

Steps F10, F11 and F12; gates G-F7 and G-F7b. Nothing is pushed without its Sending mark saved first.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum

from . import clock as clk
from . import config as c
from . import notion as n
from .http import NoConnection, Timeout
from .log_rows import CreateFailed, ensure_row_page, system_row
from .notion import NotionUnavailable, Page

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
TIMEOUT = 10.0
DAILY_CAP = 20
MAX_ATTEMPTS = 3
STALE_SENDING = timedelta(minutes=10)
CAP_WORDS = "Radar hit today's 20-push cap. Held alerts are in Notion."


class Outcome(Enum):
    ACCEPTED = "accepted"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class PushReport:
    pushed: int = 0
    held: int = 0
    failed: int = 0
    unknown: int = 0
    alert_problem: bool = False          # a non-System row became Push failed or Push unknown
    system_rows: list[Page] = field(default_factory=list)   # the cap row, if made this run


def send_pushover(transport, app: str, user: str, title: str, message: str, url: str) -> Outcome:
    form = {"token": app, "user": user, "title": title[:250], "message": message[:1024]}
    if url:
        form["url"] = url[:512]
        form["url_title"] = "Open"
    body = urllib.parse.urlencode(form).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        resp = transport.request("POST", PUSHOVER_URL, headers, body, TIMEOUT)
    except Timeout:
        return Outcome.TIMEOUT
    except NoConnection:
        return Outcome.ERROR
    if resp.status != 200:
        return Outcome.ERROR
    try:
        ok = json.loads(resp.body).get("status") == 1
    except (ValueError, AttributeError):
        ok = False
    return Outcome.ACCEPTED if ok else Outcome.ERROR


def is_system(row: Page) -> bool:
    return n.read_select(row.props.get(c.LOG_MATCH_TYPE)) == c.MATCH_SYSTEM


def _content(row: Page) -> tuple[str, str, str]:
    p = row.props
    match_type = n.read_select(p.get(c.LOG_MATCH_TYPE)) or "Alert"
    message = n.read_text(p.get(c.LOG_WHY)) or n.read_title(p.get(c.LOG_ALERT))
    return f"Radar: {match_type}", message, n.read_url(p.get(c.LOG_LINK))


def send_row(store, settings, transport, clock, row: Page) -> Outcome | None:
    """Mark, call, record. Returns None when the Sending mark could not be saved (no push)."""
    try:
        store.update(row.id, {c.LOG_STAGE: n.select(c.STAGE_SENDING)})
    except NotionUnavailable:
        return None
    title, message, url = _content(row)
    outcome = send_pushover(transport, settings.pushover_app, settings.pushover_user, title, message, url)
    now = clk.now_local(clock, settings.tz)
    if outcome is Outcome.ACCEPTED:
        props = {c.LOG_STAGE: n.select(c.STAGE_PUSHED), c.LOG_PUSHED_AT: n.date(now)}
    elif is_system(row):
        props = {c.LOG_STAGE: n.select(c.STAGE_PUSH_PENDING)}
    elif outcome is Outcome.TIMEOUT:
        props = {c.LOG_STAGE: n.select(c.STAGE_PUSH_UNKNOWN), c.LOG_PUSHED_AT: n.date(now)}
    else:
        attempts = int(n.read_number(row.props.get(c.LOG_PUSH_ATTEMPTS)) or 0) + 1
        stage = c.STAGE_PUSH_FAILED if attempts >= MAX_ATTEMPTS else c.STAGE_PUSH_PENDING
        props = {c.LOG_STAGE: n.select(stage), c.LOG_PUSH_ATTEMPTS: n.number(attempts)}
    try:
        store.update(row.id, props)
    except NotionUnavailable:
        pass  # the row stays Sending; F12 picks it up
    else:
        row.props.update(props)
    return outcome


def _stage(row: Page) -> str | None:
    return n.read_select(row.props.get(c.LOG_STAGE))


def pushes_today(store, settings, clock) -> int:
    start = clk.day_start(clk.today(clock, settings.tz), settings.tz)
    rows = store.query(settings.log_ds, {"and": [
        {"or": [{"property": c.LOG_STAGE, "select": {"equals": c.STAGE_PUSHED}},
                {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_PUSH_UNKNOWN}}]},
        {"property": c.LOG_PUSHED_AT, "date": {"on_or_after": start.isoformat()}},
    ]})
    return sum(1 for r in rows if not is_system(r))


def run_queue(store, settings, transport, clock) -> PushReport:
    report = PushReport()
    queue = store.query(settings.log_ds,
                        {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_PUSH_PENDING}},
                        [{"property": c.LOG_FOUND_AT, "direction": "ascending"}])
    if not queue:
        return report
    count = pushes_today(store, settings, clock)
    cap_checked = False
    for row in queue:
        system = is_system(row)
        if system or count < DAILY_CAP:
            outcome = send_row(store, settings, transport, clock, row)
            if outcome is None:
                continue
            stage = _stage(row)
            if outcome is Outcome.ACCEPTED:
                report.pushed += 1
            if not system:
                if outcome in (Outcome.ACCEPTED, Outcome.TIMEOUT):
                    count += 1
                if stage == c.STAGE_PUSH_UNKNOWN:
                    report.unknown += 1
                    report.alert_problem = True
                elif stage == c.STAGE_PUSH_FAILED:
                    report.failed += 1
                    report.alert_problem = True
            continue
        try:
            store.update(row.id, {c.LOG_STAGE: n.select(c.STAGE_HELD_CAP)})
            report.held += 1
        except NotionUnavailable:
            continue
        if not cap_checked:
            cap_checked = True
            today = clk.today(clock, settings.tz)
            now = clk.now_local(clock, settings.tz)
            key, props = system_row(f"cap:{today.isoformat()}", CAP_WORDS, now)
            try:
                status, page = ensure_row_page(store, settings.log_ds, props, key, now)
            except CreateFailed:
                continue
            if page is not None:
                report.system_rows.append(page)
    return report


def clean_sending(store, settings, clock) -> int:
    """F12: rows left in Sending over 10 minutes become Push unknown (System rows go back to
    Push pending). Returns how many non-System rows became Push unknown."""
    cutoff = clock.now() - STALE_SENDING
    rows = store.query(settings.log_ds, {"and": [
        {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_SENDING}},
        {"timestamp": "last_edited_time", "last_edited_time": {"before": cutoff.isoformat()}},
    ]})
    unknown = 0
    for row in rows:
        try:
            if is_system(row):
                store.update(row.id, {c.LOG_STAGE: n.select(c.STAGE_PUSH_PENDING)})
            else:
                store.update(row.id, {c.LOG_STAGE: n.select(c.STAGE_PUSH_UNKNOWN),
                                      c.LOG_PUSHED_AT: n.date(clk.now_local(clock, settings.tz))})
                unknown += 1
        except NotionUnavailable:
            continue
    return unknown
