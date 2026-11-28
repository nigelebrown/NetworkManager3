"""rc18 printer settings beyond location: contact, device name, asset id, printer name, default input tray and
default media path (duplex). Preview -> identity check -> apply -> readback -> rollback; per-operation hardware
test (lab serials only); energy saving reported UNSUPPORTED; non-existent index refused. In-memory SNMP agent."""
from types import SimpleNamespace

import pytest

from backend.database.models_printer import PrinterDevice
from backend.database.models_printer_srs import PrinterMonitoring
from backend.database.session import session_scope
from backend.printers import configuration as PC


@pytest.fixture
def agent(client, admin, monkeypatch):
    mib = {PC.SERIAL: "LAB-PRN-1", PC.LOCATION: "Store room", "1.3.6.1.2.1.1.4.0": "helpdesk@example.org",
           "1.3.6.1.2.1.1.5.0": "prn-old", "1.3.6.1.2.1.47.1.1.1.1.15.1": "", "1.3.6.1.2.1.43.5.1.1.16.1": "HQ printer",
           "1.3.6.1.2.1.43.5.1.1.6.1": 1, "1.3.6.1.2.1.43.8.2.1.12.1.1": "A4", "1.3.6.1.2.1.43.8.2.1.12.1.2": "Letter",
           "1.3.6.1.2.1.43.5.1.1.9.1": 1, "1.3.6.1.2.1.43.13.4.1.9.1.1": 4, "1.3.6.1.2.1.43.13.4.1.9.1.2": 2}
    writes = []
    with session_scope() as db:
        p = PrinterDevice(manufacturer="Kyocera", model="ECOSYS M2640idw", serial_number="LAB-PRN-1", hostname="lab-prn",
                          management_ip="10.1.2.9")
        db.add(p); db.flush()
        pid = p.id
        db.add(PrinterMonitoring(printer_id=pid, credential_profile_id="prof"))

    class Reader:
        async def get(self, oid):
            v = mib.get(oid)
            return str(v) if isinstance(v, int) else v

        async def close(self):
            pass

    async def set_value(reader, op, value):
        oid = PC.SETTINGS[op]["oid"]
        writes.append((op, value))
        mib[oid] = int(value) if PC.SETTINGS[op]["type"] == "index" else value
        return True
    monkeypatch.setattr(PC.S, "make_reader", lambda *a: Reader())
    monkeypatch.setattr(PC, "_set_value", set_value)
    monkeypatch.setattr(PC, "get_settings", lambda: SimpleNamespace(device_writes_permitted=True))
    monkeypatch.setenv("INM_CERTIFICATION_LAB_SERIALS", "LAB-PRN-1")
    return client, admin, pid, mib, writes


@pytest.mark.parametrize("op,value,oid", [
    ("system.contact", "ict-printers@example.org", "1.3.6.1.2.1.1.4.0"),
    ("system.name", "prn-hq-01", "1.3.6.1.2.1.1.5.0"),
    ("entity.asset_id", "JCF-AST-00123", "1.3.6.1.2.1.47.1.1.1.1.15.1"),
    ("printer.name", "HQ Ops Printer", "1.3.6.1.2.1.43.5.1.1.16.1"),
    ("printer.default_input", 2, "1.3.6.1.2.1.43.5.1.1.6.1"),
    ("printer.default_media_path", 2, "1.3.6.1.2.1.43.5.1.1.9.1"),
])
def test_setting_needs_hardware_test_then_previews_applies_and_rolls_back(agent, op, value, oid):
    c, h, pid, mib, writes = agent
    before = mib[oid]
    r = c.post(f"/api/printers/{pid}/configuration/preview", headers=h, json={"operation": op, "value": value})
    assert r.status_code == 409                                     # not hardware-verified yet for this operation
    t = c.post(f"/api/printers/{pid}/configuration/hardware-test", headers=h, json={"operation": op}).json()
    assert t["status"] == "PASSED" and t["result"]["restored"] is True and mib[oid] == before
    view = c.get(f"/api/printers/{pid}/configuration", headers=h).json()
    assert op in view["available"] and view["settings"][op]["status"] == "VERIFIED"
    assert view["settings"]["printer.energy_saving"]["status"] == "UNSUPPORTED"
    plan = c.post(f"/api/printers/{pid}/configuration/preview", headers=h, json={"operation": op, "value": value}).json()
    assert plan["operation"] == op and plan["before"] == str(before) and plan["after"] == str(value)
    done = c.post(f"/api/printers/{pid}/configuration/{plan['id']}/apply", headers=h).json()
    assert done["state"] == "APPLIED_VERIFIED" and str(mib[oid]) == str(value)
    rb = c.post(f"/api/printers/{pid}/configuration/{plan['id']}/rollback", headers=h).json()
    assert rb["state"] == "ROLLED_BACK" and str(mib[oid]) == str(before)


def test_refusals(agent, monkeypatch):
    c, h, pid, mib, writes = agent
    c.post(f"/api/printers/{pid}/configuration/hardware-test", headers=h, json={"operation": "printer.default_input"})
    r = c.post(f"/api/printers/{pid}/configuration/preview", headers=h, json={"operation": "printer.default_input", "value": 7})
    assert r.status_code == 422 and "does not exist" in r.text and not [w for w in writes if w[1] == "7"]
    r = c.post(f"/api/printers/{pid}/configuration/preview", headers=h, json={"operation": "printer.energy_saving", "value": "on"})
    assert r.status_code == 422 and "No standard writable MIB" in r.text
    c.post(f"/api/printers/{pid}/configuration/hardware-test", headers=h, json={"operation": "system.name"})
    assert c.post(f"/api/printers/{pid}/configuration/preview", headers=h,
                  json={"operation": "system.name", "value": "bad name!"}).status_code == 422
    monkeypatch.setenv("INM_CERTIFICATION_LAB_SERIALS", "OTHER")
    assert c.post(f"/api/printers/{pid}/configuration/hardware-test", headers=h,
                  json={"operation": "system.contact"}).status_code == 403
