"""Identity and build-specific certification checks; no physical device traffic."""
from types import SimpleNamespace

import pytest

from backend.infrastructure.change_executor import firmware_build, verify_hardware_identity
from backend.infrastructure.writers import Credentials, WriterError
from backend.infrastructure.writers.aruba_instant import ArubaInstantWriter


@pytest.mark.parametrize("observed,category", [
    ({"model": "CX 6200F", "firmware": "ML.10.14.0002", "serial_number": "ABC"}, "IDENTITY_MISMATCH"),
    ({"model": "CX 6200F", "firmware": "ML.10.14.0001", "serial_number": "OTHER"}, "IDENTITY_MISMATCH"),
    ({"model": "CX 6100", "firmware": "ML.10.14.0001", "serial_number": "ABC"}, "IDENTITY_MISMATCH"),
    ({"model": "CX 6200F", "firmware": "10.14", "serial_number": "ABC"}, "IDENTITY_UNVERIFIED"),
    ({"model": "CX 6200F", "firmware": "ML.10.14.0001"}, "IDENTITY_UNVERIFIED"),
])
def test_hardware_identity_refuses_mismatch(observed, category):
    device = SimpleNamespace(model="CX 6200F 48G", firmware="ML.10.14.0001", serial_number="ABC")
    with pytest.raises(WriterError) as exc:
        verify_hardware_identity(device, SimpleNamespace(identity=lambda: observed))
    assert exc.value.category == category


def test_hardware_identity_accepts_vendor_prefix_and_numeric_equivalence():
    device = SimpleNamespace(model="CX 6200F", firmware="10.14.0001", serial_number="ABC")
    observed = {"model": "CX 6200F", "firmware": "ML.10.14.0001", "serial_number": "ABC"}
    assert verify_hardware_identity(device, SimpleNamespace(identity=lambda: observed)) == observed
    assert firmware_build("8.10.0.21-8.10.0.21") == "8.10.0.21"
    assert firmware_build("10.14") is None


def test_instant_identity_uses_targeted_member():
    calls = []
    class Client:
        def show(self, command, *, iap_ip=None):
            calls.append((command, iap_ip))
            if command == "show aps":
                return {"show aps": [
                    {"Name": "controller", "IP Address": "192.0.2.1*", "Type": "AP-535", "Serial #": "MASTER"},
                    {"Name": "member", "IP Address": "192.0.2.2", "Type": "AP-505", "Serial #": "MEMBER"},
                ]}
            assert command == "show version" and iap_ip == "192.0.2.2"
            return {"Command output": "ArubaOS Version 8.10.0.21"}

    w = ArubaInstantWriter(Credentials("192.0.2.1", "test", "test", extra={"ap_ip": "192.0.2.2"}),
                           client=Client())
    result = w.identity()
    assert result["serial_number"] == "MEMBER" and result["firmware"] == "8.10.0.21"
    assert calls == [("show aps", None), ("show version", "192.0.2.2")]
