"""Time: the real clock, the run clock, and local days in RADAR_TZ."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class RunClock:
    """Wraps a clock and remembers the run's start, read once, so one run never straddles two days."""

    def __init__(self, clock):
        self._clock = clock
        self.start = clock.now()

    def now(self) -> datetime:
        return self._clock.now()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)


def run_start(clock) -> datetime:
    return getattr(clock, "start", None) or clock.now()


def local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz).replace(microsecond=0)


def now_local(clock, tz: ZoneInfo) -> datetime:
    return local(clock.now(), tz)


def today(clock, tz: ZoneInfo) -> date:
    return run_start(clock).astimezone(tz).date()


def day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def next_day_start(day: date, tz: ZoneInfo) -> datetime:
    return day_start(day + timedelta(days=1), tz)
