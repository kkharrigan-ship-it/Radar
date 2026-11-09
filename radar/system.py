"""System rows (errors, cap) and the daily heartbeat. Gates G-F8 and G-F8b; step F13.

Each key is made at most once, through the Dedupe Key check. The heartbeat names sources,
which is fine: it goes to Notion and the phone, never to the log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from . import clock as clk
from . import config as c
from . import notion as n
from .log_rows import CreateFailed, Source, ensure_row_page, system_row
from .notion import NotionUnavailable, Page
from .push import is_system, send_row

FAILING_RUNS = 3
HEARTBEAT_HOUR = 8


@dataclass
class RunProblems:
    failing_sources: list[Source] = field(default_factory=list)   # Error Count >= 3 after this run
    alert_problem: bool = False
    judging_problem: bool = False


def make_system_rows(store, settings, problems: RunProblems, clock) -> list[Page]:
    today = clk.today(clock, settings.tz).isoformat()
    wanted: list[tuple[str, str]] = []
    for s in problems.failing_sources:
        wanted.append((f"error:source:{s.name}:{today}",
                       f"Radar: {s.name} has failed {s.error_count} runs in a row. Last error: {s.last_error}"))
    if problems.alert_problem:
        wanted.append((f"error:pushes:{today}",
                       "Radar: some alerts failed to send or may not have arrived. See Needs attention."))
    if problems.judging_problem:
        wanted.append((f"error:judging:{today}",
                       "Radar: a row couldn't be judged after 3 tries. See Needs attention."))
    made = []
    now = clk.now_local(clock, settings.tz)
    for key, words in wanted:
        _, props = system_row(key, words, now)
        try:
            _, page = ensure_row_page(store, settings.log_ds, props, key, now)
        except CreateFailed:
            continue
        if page is not None:
            made.append(page)
    return made


def make_heartbeat(store, settings, sources: list[Source], clock) -> Page | None:
    start = clk.local(clk.run_start(clock), settings.tz)
    if start.hour < HEARTBEAT_HOUR:
        return None
    today = start.date()
    key = f"heartbeat:{today.isoformat()}"
    if store.query(settings.log_ds, {"property": c.LOG_DEDUPE_KEY, "rich_text": {"equals": key}}):
        return None
    begin = clk.day_start(today - timedelta(days=1), settings.tz)
    end = clk.day_start(today, settings.tz)

    def window(prop: str) -> list[dict]:
        return [{"property": prop, "date": {"on_or_after": begin.isoformat()}},
                {"property": prop, "date": {"before": end.isoformat()}}]

    pushed = store.query(settings.log_ds, {"and": [
        {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_PUSHED}}, *window(c.LOG_PUSHED_AT)]})
    held = store.query(settings.log_ds, {"and": [
        {"property": c.LOG_STAGE, "select": {"equals": c.STAGE_HELD_CAP}}, *window(c.LOG_FOUND_AT)]})
    alerts = sum(1 for r in pushed if not is_system(r))
    erroring = [s.name for s in sources if s.enabled and s.error_count > 0]
    words = (f"Radar alive. Yesterday: {alerts} alerts, {len(held)} held. "
             f"Sources with errors: {', '.join(erroring) or 'none'}")
    now = clk.now_local(clock, settings.tz)
    _, props = system_row(key, words, now)
    props = {**props, c.LOG_DEDUPE_KEY: n.text(key)}
    try:
        page_id = store.create(settings.log_ds, props)
    except NotionUnavailable:
        return None
    return Page(page_id, props, now)


def send_system_rows(store, settings, transport, clock, rows: list[Page]) -> None:
    """F13: send this run's System rows. send_row applies the System rule: a failed send goes
    back to Push pending, with no attempt limit and no new error row."""
    for row in rows:
        send_row(store, settings, transport, clock, row)
