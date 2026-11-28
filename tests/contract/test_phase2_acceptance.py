"""Phase 2 acceptance API tests: truthful evidence, role scope, and no hardware I/O."""
from __future__ import annotations

from unittest.mock import patch

from tests.conftest import make_user


def _registration(client, admin):
    response = client.post("/api/v1/quick-devices", headers=admin, json={
        "family": "switch", "site_name": "Phase 2 Hardware Pilot",
        "hostname": "LAB-CX6200", "serial_number": "PHASE2-CX-001",
        "model": "CX 6200F", "firmware": "10.14",
        "management_ip": "10.103.10.21", "status": "unknown", "metrics": {},
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _profile(client, admin):
    response = client.post("/api/v1/onboarding/credentials", headers=admin, json={
        "name": "Phase2 CX Shared",
        "device_family": "switch", "username": "readonly",
        "password": "NONLOGGABLE-PHASE2-TEST-SECRET",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_manual_device_reports_missing_evidence_without_claiming_hardware_certified(client, admin):
    did = _registration(client, admin)
    r = client.get(f"/api/v1/onboarding/devices/{did}/acceptance", headers=admin)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["recognized_pilot_model"] == "CX 6200"
    assert report["hardware_certified"] is False
    assert report["inventory_evidence"] == "MANUAL_ENTRY"
    assert report["stage"] == "MANUAL_REGISTERED"
    assert report["credential"]["bound"] is False
    assert report["device_writes"] == "LOCKED"
    assert report["baseline"]["completeness"] == "NOT_CAPTURED"
    assert all(x["status"] == "NOT_VERIFIED" for x in report["field_checks"])
    assert any("authorized read-only REST discovery" in x for x in report["remaining_actions"])
    assert "password" not in str(report).lower()
    overview = client.get("/api/v1/onboarding/acceptance", headers=admin)
    assert overview.status_code == 200, overview.text
    assert overview.json()["device_count"] == 1
    assert overview.json()["hardware_certified"] == 0
    assert overview.json()["stages"]["MANUAL_REGISTERED"] == 1


def test_real_rest_baseline_records_observed_and_unverified_separately(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow

    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.10.0/24")
    profile_id = _profile(client, admin)
    sample = {
        "hostname": "LAB-CX-01", "serial_number": "P2-REST-SERIAL",
        "model": "Aruba CX 6100", "firmware": "10.14",
        "status": "up", "metrics": {"ports_up": 2, "ports_total": 4},
    }
    with patch.object(flow, "_probe", return_value=sample) as probe:
        scanned = client.post("/api/v1/onboarding/discover", headers=admin, json={
            "credential_id": profile_id, "site_name": "Phase 2 Hardware Pilot",
            "scope": "10.103.10.21",
        })
    assert scanned.status_code == 201, scanned.text
    assert probe.call_count == 1
    scan_id = scanned.json()["id"]

    registered = client.post(f"/api/v1/onboarding/discoveries/{scan_id}/register",
                             headers=admin, json={"indices": [0]})
    assert registered.status_code == 201, registered.text
    did = registered.json()["registered"][0]["device_id"]
    with patch.object(flow, "_cx_baseline", return_value={
        "vlans": [{"id": 100, "name": "Data"}],
        "interfaces": [{"name": "1/1/1", "admin_state": "up", "oper_state": "up"}],
        "ssids": [],
    }) as baseline:
        captured = client.post(f"/api/v1/onboarding/devices/{did}/capture",
                               headers=admin, json={})
    assert captured.status_code == 201, captured.text
    assert baseline.call_count == 1
    report = client.get(f"/api/v1/onboarding/devices/{did}/acceptance",
                        headers=admin).json()
    assert report["recognized_pilot_model"] == "CX 6100"
    assert report["inventory_evidence"] == "REST_DISCOVERY_RECORDED"
    assert report["credential"]["profile_id"] == profile_id
    assert report["baseline"]["completeness"] == "PARTIAL"
    assert report["stage"] == "BASELINE_CAPTURED"
    observed = {x["field"] for x in report["field_checks"] if x["status"] == "OBSERVED"}
    missing = {x["field"] for x in report["field_checks"] if x["status"] == "NOT_VERIFIED"}
    assert {"vlans", "interfaces", "interface_admin_state"} <= observed
    assert {"interface_access_vlan", "lacp_poe_lldp"} <= missing
    assert report["hardware_certified"] is False
    assert report["device_writes"] == "LOCKED"
    assert "NONLOGGABLE-PHASE2-TEST-SECRET" not in str(report)
    assert "password_enc" not in str(report)

    draft = client.post(f"/api/v1/onboarding/devices/{did}/drafts", headers=admin,
                        json={"proposed": {"vlans": [100, 200]}})
    assert draft.status_code == 201, draft.text
    after = client.get(f"/api/v1/onboarding/devices/{did}/acceptance", headers=admin).json()
    assert after["stage"] == "DRAFTED"
    assert after["draft_count"] == 1
    assert after["hardware_certified"] is False


def test_acceptance_endpoints_enforce_admin_role_and_real_inventory(client, admin):
    did = _registration(client, admin)
    viewer = make_user(client, admin, "phase2.viewer", "viewer")
    assert client.get("/api/v1/onboarding/acceptance", headers=viewer).status_code == 403
    assert client.get(f"/api/v1/onboarding/devices/{did}/acceptance",
                      headers=viewer).status_code == 403
    assert client.get("/api/v1/onboarding/devices/not-a-device/acceptance",
                      headers=admin).status_code == 404
