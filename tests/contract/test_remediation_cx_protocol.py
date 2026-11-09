"""E2E-04/05/09/16: AOS-CX authentication, CSRF, session expiry, API-version negotiation and credential secrecy,
exercised on EVERY CX code path (probe, onboarding baseline, writer, telemetry) against the strict emulator."""
import logging

import httpx
import pytest

from backend.infrastructure.writers import Credentials, WriterError
from backend.infrastructure.writers.aruba_cx import ArubaCxWriter, CxSession, redact
from backend.infrastructure.writers.emulator import CxEmulator

SECRET = "S3cret-Pass-Value!"


def patched(monkeypatch, emu):
    real = httpx.Client
    seen = []

    def client(*a, **kw):
        inner = emu.transport()

        def handler(req):
            seen.append(req)
            return inner.handle_request(req)
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)
    monkeypatch.setattr("backend.infrastructure.writers.aruba_cx.httpx.Client", client)
    return seen


def test_credentials_only_in_form_body_and_csrf_on_every_later_request(monkeypatch):
    emu = CxEmulator(firmware="FL.10.14.1000", ports=24)
    emu.password = SECRET
    seen = patched(monkeypatch, emu)
    from backend.api.onboarding_workflow import _cx_baseline
    from backend.api.quick_devices import QuickProbe, _probe_cx
    out = _probe_cx(QuickProbe(family="switch", site_name="x", management_ip="10.60.0.2", username="admin",
                               password=SECRET, hostname="h", serial_number="EMU-CX-0001", model="CX"))
    assert out["metrics"]["ports_total"] == 24 and "v10.14" in out["notes"]
    base = _cx_baseline("10.60.0.2", "admin", SECRET)
    assert {"id": 1, "name": "DEFAULT_VLAN_1"} in base["vlans"] and len(base["interfaces"]) == 24
    w = ArubaCxWriter(Credentials("10.60.0.2", "admin", SECRET))
    w.open(); w.telemetry(); w.close()
    logins = [r for r in seen if r.url.path.endswith("/login")]
    assert len(logins) == 3
    for r in seen:
        assert SECRET not in str(r.url) and "password" not in r.url.params and "username" not in r.url.params
    for r in logins:
        assert r.headers["content-type"].startswith("application/x-www-form-urlencoded")
        assert r.headers["x-use-csrf-token"] == "true" and SECRET in r.content.decode().replace("%21", "!")
    for r in seen:
        if not r.url.path.endswith("/login"):
            assert r.headers.get("x-csrf-token"), r.url.path      # every authenticated call carries the token
    assert emu.violations == [] and emu.sessions == {}            # all sessions logged out


def test_strict_emulator_rejects_the_old_query_string_login_and_missing_csrf():
    emu = CxEmulator(firmware="FL.10.14.1000")
    c = httpx.Client(base_url="https://sw", transport=emu.transport())
    assert c.post("/rest/v10.14/login", params={"username": "admin", "password": emu.password}).status_code == 400
    r = c.post("/rest/v10.14/login", data={"username": "admin", "password": emu.password},
               headers={"x-use-csrf-token": "true"})
    assert r.status_code == 200 and r.headers["x-csrf-token"]
    assert c.get("/rest/v10.14/system").status_code == 403                        # cookie but no CSRF token
    assert c.get("/rest/v10.14/system", headers={"x-csrf-token": r.headers["x-csrf-token"]}).status_code == 200
    assert "credentials-in-url" in emu.violations and "csrf-missing:/system" in emu.violations


def test_session_expiry_relogs_in_once_and_wrong_credentials_fail_cleanly(monkeypatch):
    emu = CxEmulator(firmware="FL.10.14.1000", ports=8)
    w = emu.writer()
    w.open()
    assert w.identity()["csrf"] is True
    emu.expire_sessions()                              # idle timeout / switch reboot
    assert w.readback({"op": "switch.port.admin_state", "port": "1/1/2", "value": "up"})[0] is True
    assert emu.logins == 2
    emu.password = "changed"
    emu.expire_sessions()
    with pytest.raises(WriterError) as e:              # re-login refused -> clean AUTH_REJECTED, no retry loop
        w.identity()
    assert e.value.category == "AUTH_REJECTED" and emu.logins == 2
    bad = CxEmulator(firmware="FL.10.14.1000")
    with pytest.raises(WriterError) as e:
        bad.writer(password="wrong").open()
    assert e.value.category == "AUTH_REJECTED"


@pytest.mark.parametrize("firmware,expected", [("FL.10.15.0005", "v10.14"), ("PL.10.14.1000", "v10.14"),
                                               ("ML.10.13.1020", "v10.13"), ("FL.10.10.1000", "v10.10"),
                                               ("FL.10.09.0010", "v10.09")])
def test_api_version_negotiated_per_firmware(firmware, expected):
    emu = CxEmulator(firmware=firmware, ports=4)
    w = emu.writer()
    w.open()
    assert w.protocol()["api_version"] == expected and w.protocol()["csrf"] is True
    w.apply({"op": "switch.vlan.define", "value": 77})
    assert w.readback({"op": "switch.vlan.define", "value": 77})[0]
    w.apply({"op": "switch.port.access_vlan", "port": "1/1/2", "value": 77})
    ok, got = w.readback({"op": "switch.port.access_vlan", "port": "1/1/2", "value": 77})
    assert ok and got["vlan_tag"] == f"/rest/{expected}/system/vlans/77"
    w.close()
    assert emu.violations == []


def test_unsupported_firmware_is_refused_with_a_clear_category():
    emu = CxEmulator(firmware="FL.10.08.0001")
    with pytest.raises(WriterError) as e:
        emu.writer().open()
    assert e.value.category == "UNSUPPORTED_API" and "v10.09" in str(e.value)


def test_credentials_never_reach_logs_errors_or_audit(client, admin, monkeypatch, caplog):
    emu = CxEmulator(firmware="FL.10.14.1000")
    patched(monkeypatch, emu)
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.60.0.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    from backend.security.log_redaction import install
    caplog.set_level(logging.DEBUG)
    install()
    r = client.post("/api/v1/quick-devices/probe", headers=admin, json={
        "family": "switch", "site_name": "HQ", "management_ip": "10.60.0.2", "username": "admin",
        "password": SECRET, "hostname": "x", "serial_number": "EMU-CX-0001", "model": "CX"})
    assert r.status_code == 502 and SECRET not in r.text
    ok = client.post("/api/v1/quick-devices/probe", headers=admin, json={
        "family": "switch", "site_name": "HQ", "management_ip": "10.60.0.2", "username": "admin",
        "password": emu.password, "hostname": "x", "serial_number": "EMU-CX-0001", "model": "CX"})
    assert ok.status_code == 201 and emu.password not in ok.text
    for rec in caplog.records:
        assert SECRET not in rec.getMessage() and emu.password not in rec.getMessage()
    audit = client.get("/api/v1/audit?limit=200", headers=admin)
    body = audit.text if audit.status_code == 200 else ""
    assert SECRET not in body and emu.password not in body
    assert redact("x?password=abc&sid=def") == "x?password=***&sid=***"


def test_probe_works_with_writes_enabled(client, admin, monkeypatch):
    """E2E-13: read-only intake keeps working when an operator enables authorized writes."""
    emu = CxEmulator(firmware="FL.10.14.1000", ports=8)
    patched(monkeypatch, emu)
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.60.0.0/24")
    monkeypatch.setenv("SIMS_DEVICE_WRITE_LOCK", "false")
    monkeypatch.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    from backend.config import get_settings
    get_settings.cache_clear()
    r = client.post("/api/v1/quick-devices/probe", headers=admin, json={
        "family": "switch", "site_name": "HQ", "management_ip": "10.60.0.2", "username": "admin",
        "password": emu.password, "hostname": "x", "serial_number": "EMU-CX-0001", "model": "CX"})
    assert r.status_code == 201, r.text
    assert r.json()["device_writes"] == "ENABLED" and r.json()["operation"] == "READ_ONLY"
    m = client.post("/api/v1/quick-devices", headers=admin, json={
        "site_name": "HQ", "family": "switch", "hostname": "m1", "serial_number": "MAN-1", "model": "CX 6100",
        "management_ip": "10.60.0.9", "status": "up", "metrics": {}})
    assert m.status_code == 201
    b = client.post("/api/v1/quick-devices/bulk", headers=admin, json={"devices": [{
        "site_name": "HQ", "family": "switch", "hostname": "b1", "serial_number": "BULK-1", "model": "CX 6100",
        "management_ip": "10.60.0.10"}]})
    assert b.status_code in (200, 201), b.text
