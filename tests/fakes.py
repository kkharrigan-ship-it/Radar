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
