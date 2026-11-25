from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from backend.firmware import upgrade
from backend.api import quick_devices

class Reader:
    def __init__(self, version="8.10.0.30", serial="AP001", missing=False):
        self.version, self.serial, self.missing = version, serial, missing
    def show(self, cmd, iap_ip=None):
        if cmd == "show aps":
            return {"show aps": [] if self.missing else [
                {"Name": "LAB", "IP Address": "192.0.2.10", "Type": "AP-505", "Serial #": "AP001"}]}
        if cmd == "show summary":
            assert iap_ip == "192.0.2.10"
            return {"Command output": f"Serial Number: {self.serial}\nIP Address: {iap_ip}"}
        assert cmd == "show version" and iap_ip == "192.0.2.10"
        return {"Command output": f"ArubaOS Version {self.version}"}

@pytest.mark.parametrize("reader,state", [
    (Reader(), "VERIFIED_RUNNING_TARGET"),
    (Reader(version="8.10.0.21"), "UNVERIFIED"),
    (Reader(serial="OTHER"), "UNVERIFIED"),
    (Reader(missing=True), "UNVERIFIED"),
])
def test_verified_running_requires_each_planned_serial_and_target(reader,state,monkeypatch):
    @contextmanager
    def client(*args):
        yield reader
    monkeypatch.setattr(upgrade,"client_for_site",client)
    monkeypatch.setattr(quick_devices,"_permit_probe",lambda ip: None)
    monkeypatch.setattr(upgrade,"audit",lambda *args,**kwargs: None)
    site = SimpleNamespace(name="Lab", vc_host="192.0.2.10", simulated=False,enabled=True)
    change = SimpleNamespace(id=1,kind="firmware_stage",status="applied",
        plan={"target":"8.10.0.30","expected_members":[{"serial":"AP001","ip":"192.0.2.10"}]})
    result = upgrade.verify_members(None,change,site,"test")
    assert result["state"] == state
    assert change.plan["member_verification"] == result
