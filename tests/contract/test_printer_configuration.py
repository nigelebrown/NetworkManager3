"""Printer configuration contracts use an in-memory agent; never real hardware."""
from types import SimpleNamespace

import pytest

from backend.database.session import session_scope
from backend.database.models import AppSetting
from backend.database.models_printer import PrinterDevice
from backend.database.models_printer_srs import PrinterMonitoring
from backend.printers import configuration as PC
from tests.conftest import make_user


@pytest.fixture
def printer_config(client, admin, monkeypatch):
    device = {"serial": "TEST-HP-1", "location": "Original room", "writes": [], "ack": True, "fail_after_set": False}
    with session_scope() as db:
        p = PrinterDevice(manufacturer="HP", model="M281fdw", serial_number=device["serial"],
                          hostname="TEST-PRINTER", management_ip="10.1.2.3")
        db.add(p)
        db.flush()
        pid = p.id
        db.add(PrinterMonitoring(printer_id=pid, credential_profile_id="test-profile"))
        db.add(AppSetting(key="printer-test:test", value={
            "printer_id": pid, "serial": p.serial_number, "firmware": p.firmware,
            "operation": "system.location", "status": "PASSED", "result": {"restored": True},
        }))
    class Reader:
        async def get(self, oid):
            if device["fail_after_set"] and device["writes"]:
                raise TimeoutError("emulated read timeout")
            return device["serial"] if oid == PC.SERIAL else device["location"]
        async def close(self):
            pass
    async def set_location(reader, value):
        device["writes"].append(value)
        device["location"] = value
        return device["ack"]
    monkeypatch.setattr(PC.S, "make_reader", lambda *args: Reader())
    monkeypatch.setattr(PC, "_set_location", set_location)
    monkeypatch.setattr(PC, "get_settings", lambda: SimpleNamespace(device_writes_permitted=True))
    return client, admin, pid, device


def preview(c, h, pid, value="New room"):
    r = c.post(f"/api/printers/{pid}/configuration/preview", headers=h, json={"location": value})
    assert r.status_code == 201, r.text
    return r.json()


def apply(c, h, pid, change):
    return c.post(f"/api/printers/{pid}/configuration/{change['id']}/apply", headers=h)


def test_printer_location_preview_apply_readback_and_rollback(printer_config):
    c, h, pid, agent = printer_config
    current = c.get(f"/api/printers/{pid}/configuration", headers=h).json()
    assert current["hardware_verified"] and current["current_location"] == "Original room"
    change = preview(c, h, pid)
    assert change["before"] == "Original room" and change["after"] == "New room" and not agent["writes"]
    assert apply(c, h, pid, change).json()["state"] == "APPLIED_VERIFIED"
    assert agent["location"] == "New room"
    # Network retry or double-click never sends the same SET again.
    assert apply(c, h, pid, change).json()["state"] == "APPLIED_VERIFIED"
    assert agent["writes"] == ["New room"]
    r = c.post(f"/api/printers/{pid}/configuration/{change['id']}/rollback", headers=h).json()
    assert r["state"] == "ROLLED_BACK" and r["result"]["restored"]
    assert agent["location"] == "Original room"
    with session_scope() as db:
        stored = db.get(AppSetting, PC.PREFIX + change["id"]).value
        assert "Original room" not in str(stored) and "New room" not in str(stored)
        assert stored["state"] == "ROLLED_BACK"


def test_printer_location_rejects_stale_preview_without_writes(printer_config):
    c, h, pid, agent = printer_config
    change = preview(c, h, pid)
    agent["location"] = "External administrator edit"
    assert apply(c, h, pid, change).status_code == 409
    assert agent["writes"] == []


def test_printer_serial_mismatch_never_sends_set(printer_config):
    c, h, pid, agent = printer_config
    change = preview(c, h, pid)
    agent["serial"] = "DIFFERENT-DEVICE"
    assert apply(c, h, pid, change).status_code == 409
    assert agent["writes"] == []


@pytest.mark.parametrize("value", ["line\nbreak", "bad\x00value", "x" * 256, "\u2603"])
def test_printer_location_validation(printer_config, value):
    c, h, pid, agent = printer_config
    assert c.post(f"/api/printers/{pid}/configuration/preview", headers=h,
                  json={"location": value}).status_code == 422
    assert not agent["writes"]


def test_printer_configuration_requires_hardware_evidence(printer_config):
    c, h, pid, agent = printer_config
    with session_scope() as db:
        db.delete(db.get(AppSetting, "printer-test:test"))
    assert c.post(f"/api/printers/{pid}/configuration/preview", headers=h,
                  json={"location": "New"}).status_code == 409
    assert not agent["writes"]


def test_printer_configuration_lock_enforced(printer_config, monkeypatch):
    c, h, pid, agent = printer_config
    change = preview(c, h, pid)
    monkeypatch.setattr(PC, "get_settings", lambda: SimpleNamespace(device_writes_permitted=False))
    assert apply(c, h, pid, change).status_code == 403
    assert not agent["writes"]


def test_printer_uncertain_set_is_reconciled_and_compensated(printer_config):
    c, h, pid, agent = printer_config
    change = preview(c, h, pid)
    agent["ack"] = False
    result = apply(c, h, pid, change).json()
    assert result["state"] == "FAILED_RESTORED" and result["result"]["restored"]
    assert agent["writes"] == ["New room", "Original room"]
    assert agent["location"] == "Original room"


def test_printer_unreadable_after_set_keeps_durable_recovery_record(printer_config):
    c, h, pid, agent = printer_config
    change = preview(c, h, pid)
    agent["fail_after_set"] = True
    result = apply(c, h, pid, change).json()
    assert result["state"] == "RECOVERY_REQUIRED"
    agent["fail_after_set"] = False
    result = c.post(f"/api/printers/{pid}/configuration/{change['id']}/rollback", headers=h).json()
    assert result["state"] == "ROLLED_BACK"
    assert agent["location"] == "Original room"


def test_printer_config_admin_only(printer_config):
    c, h, pid, agent = printer_config
    viewer = make_user(c, h, "printer.viewer", "viewer")
    assert c.get(f"/api/printers/{pid}/configuration", headers=viewer).status_code == 403
    assert c.post(f"/api/printers/{pid}/configuration/preview", headers=viewer,
                  json={"location": "New"}).status_code == 403
    assert not agent["writes"]


def test_printer_hardware_evidence_invalidated_by_firmware_change(printer_config):
    c, h, pid, agent = printer_config
    with session_scope() as db:
        db.get(PrinterDevice, pid).firmware = "new-firmware"
    assert c.post(f"/api/printers/{pid}/configuration/preview", headers=h,
                  json={"location": "New"}).status_code == 409
    assert not agent["writes"]
