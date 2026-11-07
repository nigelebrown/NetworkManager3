import logging

import httpx
import pytest

from backend.aruba.client import ArubaClient, ArubaTarget, validate_command
from backend.aruba.errors import ArubaAuthError, ArubaConnectionError, ArubaUnsupportedError
from backend.aruba.monitoring import collect_snapshot

from .conftest import aruba_handler


def make(handler, password="s3cret-pw"):
    return ArubaClient(ArubaTarget("10.0.0.1", 4343, "admin", password),
                       transport=httpx.MockTransport(handler))


def test_snapshot_collection():
    with make(aruba_handler()) as c:
        snap = collect_snapshot(c)
    assert len(snap.aps) == 2 and len(snap.clients) == 2
    assert snap.firmware == "8.10.0.21-8.10.0.21"
    assert [w for w in snap.warnings if "telemetry" not in w] == []   # mock has no RF commands


def test_login_failure_raises_auth_error():
    with make(aruba_handler(fail_login=True)) as c:
        with pytest.raises(ArubaAuthError):
            c.login()


def test_expired_session_is_retried_once():
    h = aruba_handler(expire_first_sid=True)
    with make(h) as c:
        c.show("show aps")
    assert h.calls["login"] == 2


@pytest.mark.parametrize("cmd", [
    "configure terminal", "reload", "write memory", "show aps; reload", "show aps | include x",
    "show aps\nreload", "wlan ssid-profile x", "copy flash tftp",
])
def test_non_readonly_or_unsafe_commands_are_blocked(cmd):
    with pytest.raises(ArubaUnsupportedError):
        validate_command(cmd)


def test_connection_error_is_wrapped():
    def boom(request):
        raise httpx.ConnectError("refused")
    with make(boom) as c:
        with pytest.raises(ArubaConnectionError):
            c.login()


def test_secrets_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    with make(aruba_handler(), password="UNIQUE-PASSWORD-123") as c:
        collect_snapshot(c)
    assert "UNIQUE-PASSWORD-123" not in caplog.text
    assert "sid-1" not in caplog.text
