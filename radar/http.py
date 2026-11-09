"""One request() over urllib, behind a Transport interface tests replace.

Error messages are plain words and never hold the URL.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass
class Response:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


class Timeout(Exception):
    """The request was sent but no reply came in time. It may have arrived."""

    def __init__(self) -> None:
        super().__init__("timeout")


class NoConnection(Exception):
    """Could not connect, so nothing was sent."""

    def __init__(self) -> None:
        super().__init__("no connection")


class Transport(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> Response: ...


class UrllibTransport:
    """The real transport. Returns 4xx and 5xx as a Response, never raises on them."""

    def request(self, method: str, url: str, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> Response:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return Response(resp.status, dict(resp.headers.items()), resp.read())
        except urllib.error.HTTPError as err:
            try:
                data = err.read()
            except Exception:
                data = b""
            return Response(err.code, dict(err.headers.items()) if err.headers else {}, data)
        except urllib.error.URLError as err:
            if isinstance(err.reason, (socket.timeout, TimeoutError)):
                raise Timeout() from None
            raise NoConnection() from None
        except (socket.timeout, TimeoutError):
            raise Timeout() from None
        except (ConnectionError, OSError):
            raise NoConnection() from None
