"""Single source of "now" for time-window logic (rc18, fixes the intermittent predictive test).

Production always uses the real UTC wall clock. Tests (and replay tools) freeze it with ``frozen(dt)``, so code that
issues a prediction window and code that scores it agree on the same instant. Every value returned is timezone-aware
UTC; aware inputs in other zones are converted, naive inputs (SQLite) are treated as UTC.
"""
from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone

_local = threading.local()
_global: list[datetime | None] = [None]


def utc(t: datetime | str) -> datetime:
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def now() -> datetime:
    fixed = getattr(_local, "fixed", None) or _global[0]
    return fixed if fixed is not None else datetime.now(timezone.utc)


@contextlib.contextmanager
def frozen(at: datetime, *, all_threads: bool = True):
    """Freeze ``now()``. all_threads=True also covers request handlers running in the server's thread pool."""
    at = utc(at)
    prev_local, prev_global = getattr(_local, "fixed", None), _global[0]
    _local.fixed = at
    if all_threads:
        _global[0] = at
    try:
        yield at
    finally:
        _local.fixed = prev_local
        if all_threads:
            _global[0] = prev_global
