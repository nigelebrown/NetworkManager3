"""Bring-forward read-only device intake: REAL labels, RBAC and hardware guardrails."""
from unittest.mock import patch

from tests.conftest import login, make_user


def ap_payload():
    return {
        "family": "access_point",
        "site_name": "HQ wireless",
        "hostname": "HQ-AP-535-1",
        "serial_number": "CN-REAL-535-01",
        "model": "AP-535",
        "management_ip": "10.101.7.15",
        "status": "up",
        "metrics": {"clients": 12, "util_5g_percent": 31.5, "cpu_percent": 23},
    }


def switch_payload():
    return {
        "family": "switch", "site_name": "HQ data",
        "hostname": "HQ-CX6200-1", "serial_number": "CN-REAL-CX-01",
        "model": "CX 6200F", "management_ip": "10.103.7.21", "status": "up",
        "metrics": {"ports_total": 24, "ports_up": 18, "poe_power_w": 81},
    }


def migrate_telemetry():
    from backend.infrastructure.runtime import telemetry_store
    telemetry_store().migrate()


def test_manual_real_ap_and_cx_inventory_and_metrics(client, admin):
    migrate_telemetry()
    for body in (ap_payload(), switch_payload()):
        r = client.post("/api/v1/quick-devices", headers=admin, json=body)
        assert r.status_code == 201, r.text
        assert r.json()["source_type"] == "REAL" and r.json()["evidence"] == "MANUAL"
        did = r.json()["id"]
        device = client.get(f"/api/v1/devices/{did}", headers=admin).json()
        assert device["adapter"] == "manual_snapshot"
        metrics = client.get(f"/api/v1/devices/{did}/metrics", headers=admin).json()["points"]
        assert set(body["metrics"]).issubset({x["metric_name"] for x in metrics})
        assert all(x["collection_method"] == "MANUAL_ENTRY" for x in metrics)
    all_devices = client.get("/api/v1/devices", headers=admin).json()
    assert len([d for d in all_devices if d["source_type"] == "REAL"]) == 2
    assert any(d["source_type"] == "DEMO" for d in all_devices) is False  # fixture inventory not synced by poll


def test_duplicate_real_identity_is_upserted_but_cross_family_rejected(client, admin):
    migrate_telemetry()
    first = client.post("/api/v1/quick-devices", headers=admin, json=ap_payload())
    second = client.post("/api/v1/quick-devices", headers=admin, json=ap_payload())
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    different = {**switch_payload(), "serial_number": ap_payload()["serial_number"]}
    bad = client.post("/api/v1/quick-devices", headers=admin, json=different)
    assert bad.status_code == 409


def test_negative_readings_and_unapproved_metric_rejected(client, admin):
    migrate_telemetry()
    for vals in ({"cpu_percent": 101}, {"clients": -1}, {"ports_up": 1}, {"util_5g_percent": -5}):
        p = {**ap_payload(), "metrics": vals}
        assert client.post("/api/v1/quick-devices", headers=admin, json=p).status_code == 422


def test_site_scoped_operator_cannot_read_manual_global_inventory(client, admin):
    migrate_telemetry()
    r = client.post("/api/v1/quick-devices", headers=admin, json=ap_payload())
    user = make_user(client, admin, "intake.tech", "operator", scope="granted", grants=[])
    assert client.get(f"/api/v1/devices/{r.json()['id']}", headers=user).status_code == 404
    assert not any(d["source_type"] == "REAL" for d in client.get("/api/v1/devices", headers=user).json())
    assert client.post("/api/v1/quick-devices", headers=user, json=ap_payload()).status_code == 403


def test_direct_probe_requires_allowlisted_private_target(client, admin, monkeypatch):
    probe = {"family": "switch", "site_name": "HQ", "hostname": "sw1",
             "serial_number": "CX-1", "model": "CX 6100", "management_ip": "10.103.7.21",
             "username": "readonly", "password": "not-stored"}
    monkeypatch.delenv("INM_READONLY_DEVICE_CIDRS", raising=False)
    assert client.post("/api/v1/quick-devices/probe", headers=admin, json=probe).status_code == 403
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.101.0.0/16")
    assert client.post("/api/v1/quick-devices/probe", headers=admin, json=probe).status_code == 403
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.7.0/24")
    assert client.post("/api/v1/quick-devices/probe", headers=admin,
                       json={**probe, "management_ip": "127.0.0.1"}).status_code == 422


def test_readonly_probe_stores_real_evidence_with_no_configuration_write(client, admin, monkeypatch):
    migrate_telemetry()
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.7.0/24")
    probe = {"family": "switch", "site_name": "HQ", "hostname": "sw1",
             "serial_number": "CX-1", "model": "CX 6100", "management_ip": "10.103.7.21",
             "username": "readonly", "password": "not-stored"}
    from backend.api import quick_devices
    with patch.object(quick_devices, "_probe_cx", return_value={
            "hostname": "sw1", "serial_number": "CX-1", "model": "CX 6100",
            "firmware": "10.14", "status": "up",
            "metrics": {"ports_total": 24, "ports_up": 19}, "notes": "mock API"}) as reader:
        r = client.post("/api/v1/quick-devices/probe", headers=admin, json=probe)
    assert r.status_code == 201, r.text
    reader.assert_called_once()
    assert r.json()["evidence"] == "PROBED" and r.json()["device_writes"] == "LOCKED"
    observed = client.get(f"/api/v1/devices/{r.json()['id']}/metrics", headers=admin).json()["points"]
    assert all(p["collection_method"] == "ARUBA_REST_READ" for p in observed)


def test_demo_simple_login_rejected_outside_localhost_locked_demo(app_env, monkeypatch):
    from backend.config import get_settings
    monkeypatch.setenv("SIMS_DEMO_SIMPLE_LOGIN", "true")
    monkeypatch.setenv("SIMS_ADMIN_PASSWORD", "demo123!")
    monkeypatch.setenv("INM_WEB_BIND", "127.0.0.1")
    get_settings.cache_clear()
    assert get_settings().validate_profile() == []
    monkeypatch.setenv("INM_WEB_BIND", "0.0.0.0")
    get_settings.cache_clear()
    assert get_settings().validate_profile()


def test_failed_aruba_instant_auth_returns_safe_502_and_persists_audit(client, admin, monkeypatch):
    from backend.api import quick_devices
    from backend.aruba.errors import ArubaAuthError
    from backend.database.session import session_scope
    from backend.database.models import AuditLog
    from sqlalchemy import select

    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.101.7.15/32")
    probe = {
        "family": "access_point", "site_name": "HQ", "hostname": "AP535",
        "serial_number": "AP-CERT-TEST", "model": "AP-535",
        "management_ip": "10.101.7.15", "username": "readonly",
        "password": "top-secret-device-password",
    }
    with patch.object(quick_devices, "_probe_ap", side_effect=ArubaAuthError("upstream text with secret")):
        r = client.post("/api/v1/quick-devices/probe", headers=admin, json=probe)
    assert r.status_code == 502, r.text
    assert "top-secret" not in r.text
    with session_scope() as db:
        failures = list(db.scalars(select(AuditLog).where(AuditLog.action == "device_probe_failed")))
        assert len(failures) >= 1
        assert failures[-1].success is False
        assert "top-secret" not in (failures[-1].detail or "")


def test_failed_cx_rest_connection_returns_502_without_leaking_credentials(client, admin, monkeypatch):
    from backend.api import quick_devices
    import httpx
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.7.21/32")
    probe = {
        "family": "switch", "site_name": "HQ", "hostname": "sw1",
        "serial_number": "CX-ERR", "model": "CX 6100",
        "management_ip": "10.103.7.21", "username": "readonly",
        "password": "secret-not-in-response",
    }
    with patch.object(quick_devices, "_probe_cx", side_effect=httpx.ConnectError("device unavailable")):
        response = client.post("/api/v1/quick-devices/probe", headers=admin, json=probe)
    assert response.status_code == 502
    assert "secret-not-in-response" not in response.text
