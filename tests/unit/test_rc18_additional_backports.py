"""Regression coverage for the additional RC18 backports.

These are software-only tests; they do not establish hardware acceptance.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend.domain.predictive import _utc
from backend.infrastructure import outbox
from backend.infrastructure.logs.loki import LokiLogStore


def test_prediction_timestamps_use_utc():
    assert _utc("2026-11-25T08:00:00-05:00") == datetime(2026, 11, 25, 13, tzinfo=timezone.utc)
    assert _utc("2026-11-25T08:00:00-05:00").hour == 13
    assert _utc(datetime(2026, 11, 25, 13)).tzinfo is timezone.utc


def test_bad_outbox_envelope_is_retried_and_dead_lettered():
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    flushed = []
    db = SimpleNamespace(flush=lambda: flushed.append(True))
    row = SimpleNamespace(envelope="invalid JSON", attempts=0, state="PENDING",
                          event_id="test-invalid-event", last_error=None, next_attempt_at=None)
    assert outbox.deliver_row(db, row, now=now) is False
    assert row.attempts == 1
    assert row.state == "PENDING"
    assert row.next_attempt_at > now
    assert row.last_error
    row.attempts = outbox.MAX_ATTEMPTS - 1
    assert outbox.deliver_row(db, row, now=now) is False
    assert row.state == "DLQ"
    assert len(flushed) == 2


def test_loki_queries_preserve_nanosecond_order_and_direction():
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    ns = int(now.timestamp()) * 10**9
    requested = []

    def handle(request):
        requested.append(request.url.params["direction"])
        return httpx.Response(200, json={"data": {"result": [
            {"stream": {"app": "inm"}, "values": [[str(ns + 2), "second"]]},
            {"stream": {"app": "inm"}, "values": [[str(ns + 1), "first"]]},
        ]}})

    store = LokiLogStore("http://loki.test", transport=httpx.MockTransport(handle))
    try:
        for direction, expected in [("forward", ["first", "second"]),
                                    ("backward", ["second", "first"])]:
            rows = store.query('{app="inm"}', now, until=now + timedelta(seconds=1),
                               direction=direction)
            assert [row["line"] for row in rows] == expected
            assert {row["ns"] for row in rows} == {str(ns + 1), str(ns + 2)}
        with pytest.raises(ValueError):
            store.query('{app="inm"}', now, direction="invalid")
        assert requested == ["forward", "backward"]
    finally:
        store._http.close()
