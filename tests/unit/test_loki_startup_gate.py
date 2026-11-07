"""Deployment gate: transient Loki warm-up must not cause a false failure."""
from unittest.mock import patch

from backend import cli


class FakeLoki:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def ready(self):
        self.calls += 1
        return next(self.results)


def test_loki_warmup_is_retried_until_ready():
    loki = FakeLoki([(False, "Ingester not ready"), (False, "starting"), (True, "ready")])
    with patch("backend.infrastructure.runtime.log_store", return_value=loki):
        with patch.object(cli.time, "sleep", return_value=None) as sleeping:
            assert cli._await_loki_startup(max_wait_s=10) is True
    assert loki.calls == 3
    assert sleeping.call_count == 2


def test_persistent_loki_failure_never_reported_as_ready():
    loki = FakeLoki([(False, "Loki unavailable")])
    with patch("backend.infrastructure.runtime.log_store", return_value=loki):
        assert cli._await_loki_startup(max_wait_s=0) is False
    assert loki.calls == 1


def test_loki_not_configured_is_not_faked_as_ready():
    with patch("backend.infrastructure.runtime.log_store", return_value=None):
        assert cli._await_loki_startup(max_wait_s=0) is False
