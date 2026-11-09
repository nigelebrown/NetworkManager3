"""Credential reuse and process-driven read-only Aruba onboarding contracts."""
from __future__ import annotations
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import make_user


def new_profile(client, admin, name="HQ shared CX"):
    return client.post("/api/v1/onboarding/credentials", headers=admin, json={
        "name": name, "device_family": "switch", "username": "readonly",
        "password": "real-equipment-password-demo", "description": "Reusable read-only credential",
    })


def test_credential_library_encrypts_and_never_discloses_secret(client, admin):
    r = new_profile(client, admin)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["device_family"] == "switch"
    assert data["has_password"] is True
    assert "password" not in data and "password_enc" not in data
    listing = client.get("/api/v1/onboarding/credentials", headers=admin).json()
    assert len(listing) == 1 and listing[0]["id"] == data["id"]
    assert "real-equipment-password-demo" not in str(listing)
    from backend.database.models_onboarding import OnboardingCredential
    from backend.database.session import session_scope
    with session_scope() as db:
        stored = db.get(OnboardingCredential, data["id"])
        assert stored.password_enc != "real-equipment-password-demo"
        assert "real-equipment-password-demo" not in stored.password_enc
    dupe = new_profile(client, admin, name="hq SHARED cx")
    assert dupe.status_code == 409


def test_credential_rotation_updates_one_shared_profile(client, admin):
    r = new_profile(client, admin)
    profile = r.json()
    q = client.post(f"/api/v1/onboarding/credentials/{profile['id']}/rotate",
                    headers=admin, json={"password": "revised-secret-for-all-linked-switches"})
    assert q.status_code == 200, q.text
    assert q.json()["last_test_status"] is None
    from backend.database.models_onboarding import OnboardingCredential
    from backend.database.session import session_scope
    from backend.security.crypto import decrypt_secret
    with session_scope() as db:
        stored = db.get(OnboardingCredential, profile["id"])
        assert decrypt_secret(stored.password_enc) == "revised-secret-for-all-linked-switches"


def test_admin_discovery_two_devices_reuses_profile_then_captures_and_drafts(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.10.0/24")
    profile = new_profile(client, admin).json()
    def observed(p, ip):
        assert p.id == profile["id"]
        return {"hostname": f"CX-{str(ip).split('.')[-1]}",
                "serial_number": f"CX-IDENTITY-{str(ip).split('.')[-1]}",
                "model": "CX 6200F", "firmware": "10.14", "status": "up",
                "metrics": {"ports_total": 24, "ports_up": 18}}
    with patch.object(flow, "_probe", side_effect=observed) as read:
        discovery = client.post("/api/v1/onboarding/discover", headers=admin,
                                json={"credential_id": profile["id"],
                                      "scope": "10.103.10.0/30", "site_name": "Head Office"})
    assert discovery.status_code == 201, discovery.text
    record = discovery.json()
    assert len(record["results"]) == 2
    assert read.call_count == 2
    assert all(r["enrollable"] for r in record["results"])
    assert all("password" not in str(r).lower() for r in record["results"])
    check = client.get(f"/api/v1/onboarding/discoveries/{record['id']}", headers=admin)
    assert check.status_code == 200 and check.json()["results"] == record["results"]

    added = client.post(f"/api/v1/onboarding/discoveries/{record['id']}/register",
                        headers=admin, json={"indices": [0, 1]})
    assert added.status_code == 201, added.text
    devices = added.json()["registered"]
    assert len(devices) == 2
    from backend.database.models_onboarding import OnboardingDeviceCredential
    from backend.database.session import session_scope
    with session_scope() as db:
        bindings = [db.get(OnboardingDeviceCredential, d["device_id"]) for d in devices]
        assert all(b and b.credential_id == profile["id"] for b in bindings)

    with patch.object(flow, "_cx_baseline", return_value={
            "vlans": [{"id": 100, "name": "DATA"}],
            "interfaces": [{"name": "1/1/1", "admin_state": "up", "oper_state": "up"}],
            "ssids": [],
        }) as readcfg:
        for d in devices:
            got = client.post(f"/api/v1/onboarding/devices/{d['device_id']}/capture",
                              headers=admin, json={})
            assert got.status_code == 201, got.text
            assert got.json()["completeness"] == "PARTIAL"
    assert readcfg.call_count == 2

    did = devices[0]["device_id"]
    current = client.get(f"/api/v1/onboarding/devices/{did}/config", headers=admin)
    assert current.status_code == 200
    assert current.json()["baseline"]["config"]["vlans"][0]["id"] == 100
    assert current.json()["credential_id"] == profile["id"]

    bad = client.post(f"/api/v1/onboarding/devices/{did}/drafts", headers=admin, json={
        "proposed": {"vlans": [200], "ssids": [{"profile": "Office", "channel_width_mhz": 80}]}
    })
    assert bad.status_code == 422
    ok = client.post(f"/api/v1/onboarding/devices/{did}/drafts", headers=admin, json={
        "proposed": {"vlans": [100, 200], "interfaces": [
            {"name": "1/1/1", "admin_state": "up", "access_vlan": 200}]}
    })
    assert ok.status_code == 201, ok.text
    assert ok.json()["state"] == "DRAFT" and ok.json()["hardware_writes"] == "LOCKED"
    assert len(client.get(f"/api/v1/onboarding/devices/{did}/config", headers=admin).json()["drafts"]) == 1


def test_discovery_refuses_unauthorized_or_oversized_target_before_probe(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow
    p = new_profile(client, admin).json()
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.10.0/24")
    with patch.object(flow, "_probe", side_effect=AssertionError("device contacted unexpectedly")):
        for scope, status in (("128.0.0.0/9", 422), ("10.103.10.0/24", 422),
                              ("10.103.11.1", 403), ("10.103.10.0/29", 201)):
            if status == 201:
                continue
            r = client.post("/api/v1/onboarding/discover", headers=admin, json={
                "credential_id": p["id"], "scope": scope, "site_name": "HQ",
            })
            assert r.status_code == status, (scope, r.text)


def test_read_only_profile_test_failure_keeps_credentials_secret(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow
    from backend.aruba.errors import ArubaAuthError
    profile = new_profile(client, admin).json()
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.10.4/32")
    with patch.object(flow, "_probe", side_effect=ArubaAuthError("Password=MY-SUPERSECRET")):
        response = client.post(f"/api/v1/onboarding/credentials/{profile['id']}/test",
                               headers=admin, json={"management_ip": "10.103.10.4"})
    assert response.status_code == 502
    assert "MY-SUPERSECRET" not in response.text
    listing = client.get("/api/v1/onboarding/credentials", headers=admin).json()
    assert listing[0]["last_test_status"] == "FAILED"


def test_operator_cannot_list_or_use_shared_credential_library(client, admin):
    profile = new_profile(client, admin).json()
    operator = make_user(client, admin, "onboarding.operator", "operator")
    assert client.get("/api/v1/onboarding/credentials", headers=operator).status_code == 403
    assert client.post(f"/api/v1/onboarding/credentials/{profile['id']}/rotate",
                       headers=operator, json={"password": "not-authorized"}).status_code == 403
    assert client.post("/api/v1/onboarding/discover", headers=operator,
                       json={"credential_id": profile["id"], "scope": "10.103.10.1",
                             "site_name": "HQ"}).status_code == 403


def test_disabled_profile_is_not_available_to_discover(client, admin):
    profile = new_profile(client, admin).json()
    r = client.post(f"/api/v1/onboarding/credentials/{profile['id']}/disable",
                    headers=admin)
    assert r.status_code == 200 and r.json()["enabled"] is False
    denied = client.post("/api/v1/onboarding/discover", headers=admin, json={
        "credential_id": profile["id"], "scope": "10.103.10.1", "site_name": "HQ",
    })
    assert denied.status_code == 409


def test_resumable_authorized_24_progress_and_idempotent_batch(client, admin, monkeypatch):
    """A single /24 request scans 254 usable hosts in short persisted batches."""
    from backend.api import onboarding_workflow as flow
    from backend.aruba.errors import ArubaConnectionError
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.192.57.0/24")
    profile = new_profile(client, admin, name="AP /24 profile").json()
    # Create the requested job WITHOUT sending a single probe packet.
    with patch.object(flow, "_probe", side_effect=AssertionError("start must not probe")):
        started = client.post("/api/v1/onboarding/discoveries/start", headers=admin,
                              json={"credential_id": profile["id"],
                                    "scope": "10.192.57.0/24", "site_name": "Pilot Site"})
    assert started.status_code == 201, started.text
    job = started.json()
    assert job["status"] == "IN_PROGRESS"
    assert job["total_targets"] == 254
    assert job["completed_targets"] == 0
    assert job["device_writes"] == "LOCKED"
    job_id = job["id"]

    def probe(profile_obj, ip):
        if str(ip) == "10.192.57.55":
            return {"hostname": "AP-505-55", "model": "AP-505",
                    "serial_number": "AP-55-CERTIFIED", "firmware": "8.10.0.21"}
        raise ArubaConnectionError("host unreachable")

    with patch.object(flow, "_probe", side_effect=probe) as mocked:
        first = client.post(f"/api/v1/onboarding/discoveries/{job_id}/next",
                            headers=admin, json={"offset": 0, "batch_size": 8})
        assert first.status_code == 200, first.text
        assert first.json()["completed_targets"] == 8
        assert mocked.call_count == 8
        # A repeated request after a lost response must NOT rescan prior hosts.
        replay = client.post(f"/api/v1/onboarding/discoveries/{job_id}/next",
                             headers=admin, json={"offset": 0, "batch_size": 8})
        assert replay.status_code == 200 and replay.json()["completed_targets"] == 8
        assert mocked.call_count == 8
        # Closing the browser stops future requests; the database retains progress.
        saved = client.get(f"/api/v1/onboarding/discoveries/{job_id}", headers=admin)
        assert saved.status_code == 200
        assert saved.json()["completed_targets"] == 8
        premature = client.post(f"/api/v1/onboarding/discoveries/{job_id}/register",
                                 headers=admin, json={"indices": [0]})
        assert premature.status_code == 409
        too_far = client.post(f"/api/v1/onboarding/discoveries/{job_id}/next",
                              headers=admin, json={"offset": 25, "batch_size": 4})
        assert too_far.status_code == 409

        current = saved.json()
        while current["status"] == "IN_PROGRESS":
            current_resp = client.post(f"/api/v1/onboarding/discoveries/{job_id}/next",
                                       headers=admin,
                                       json={"offset": current["completed_targets"], "batch_size": 8})
            assert current_resp.status_code == 200, current_resp.text
            current = current_resp.json()
    assert current["status"] == "SCANNED"
    assert current["completed_targets"] == current["total_targets"] == 254
    assert current["found_count"] == 1
    assert current["failed_count"] == 253
    assert current["results"][0]["management_ip"] == "10.192.57.1"
    assert current["results"][-1]["management_ip"] == "10.192.57.254"
    found_index = next(i for i, row in enumerate(current["results"]) if row["enrollable"])
    assert current["results"][found_index]["management_ip"] == "10.192.57.55"
    add = client.post(f"/api/v1/onboarding/discoveries/{job_id}/register", headers=admin,
                      json={"indices": [found_index]})
    assert add.status_code == 201, add.text
    assert add.json()["registered"][0]["serial_number"] == "AP-55-CERTIFIED"


def test_resumable_discovery_rejects_unapproved_broader_or_public_scopes(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow
    p = new_profile(client, admin, name="Rejected /24 test").json()
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.192.57.55/32")
    with patch.object(flow, "_probe", side_effect=AssertionError("device must not be probed")):
        for scope, expected in (
            ("10.192.57.0/24", 403),
            ("10.192.56.0/23", 422),
            ("128.0.0.0/24", 422),
        ):
            response = client.post("/api/v1/onboarding/discoveries/start", headers=admin, json={
                "credential_id": p["id"], "scope": scope, "site_name": "Not Approved",
            })
            assert response.status_code == expected, (scope, response.text)
