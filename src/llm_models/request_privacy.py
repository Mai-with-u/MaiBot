"""Per-request control of diagnostic snapshots for sensitive inputs."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_request_snapshots_suppressed = ContextVar("request_snapshots_suppressed", default=False)


def request_snapshots_suppressed() -> bool:
    return _request_snapshots_suppressed.get()


@contextmanager
def suppress_request_snapshots() -> Iterator[None]:
    token = _request_snapshots_suppressed.set(True)
    try:
        yield
    finally:
        _request_snapshots_suppressed.reset(token)
