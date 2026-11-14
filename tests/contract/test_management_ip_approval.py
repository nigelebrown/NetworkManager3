"""Admin-only, exact /32 management IP approval contract.

Tests run against SQLite and never access Docker, host firewalls, or hardware.
"""
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import make_user


def _setup(client, admin):
    device = client.post("/api/v1/quick-devices", headers=admin, json={
        "family": "access_point", "site_name": "HQ", "hostname": "ACP-Admin",
        "serial_number": "ACP-AP-505-TEST", "model": "AP-505",
        "management_ip": "10.190.97.211", "status": "unknown", "metrics": {},
    })
    assert device.status_code == 201, device.text
    did = device.json()["id"]
    profile = client.post("/api/v1/onboarding/credentials", headers=admin, json={
        "name": "AP-Standard", "device_family": "access_point",
        "username": "admin", "password": "lab-test-credential",
    })
    assert profile.status_code == 201, profile.text
    pid = profile.json()["id"]
    binding = client.post("/api/v1/onboarding/credentials/assign", headers=admin,
                          json={"credential_id": pid, "device_ids": [did]})
    assert binding.status_code == 200, binding.text
    return did, pid


def _request(did, pid, ip="10.190.97.211", confirmation=None):
    return {"device_id": did, "profile_id": pid, "management_ip": ip,
            "confirmation": confirmation if confirmation is not None else "APPROVE IP " + ip}


def _locked_live(monkeypatch):
    from backend.api import management_approvals
    monkeypatch.setattr(management_approvals, "get_settings", lambda: SimpleNamespace(
        mode="live", inm_profile="lab-readonly", device_write_lock=True,
        allow_config_changes=False, device_writes_permitted=False))


def test_ip_approval_requires_locked_live_and_an_administrator(client, admin, monkeypatch):
    did, pid = _setup(client, admin)
    payload = _request(did, pid)
    # No admin can bypass the deployment safety profile.
    response = client.post("/api/v1/management-access/approve-ip", headers=admin, json=payload)
    assert response.status_code == 423, response.text
    _locked_live(monkeypatch)
    operator = make_user(client, admin, "approval.operator", "operator")
    assert client.post("/api/v1/management-access/approve-ip", headers=operator, json=payload).status_code == 403
    assert client.post("/api/v1/management-access/approve-ip", headers=admin,
                       json=_request(did, pid, confirmation="YES")).status_code == 422
    assert client.post("/api/v1/management-access/approve-ip", headers=admin,
                       json=_request(did, pid, ip="8.8.8.8")).status_code == 422
    assert client.post("/api/v1/management-access/approve-ip", headers=admin,
                       json=_request(did, pid, ip="10.190.97.212")).status_code == 404


def test_ip_approval_queues_and_audits_without_hardware_access(client, admin, monkeypatch):
    from backend.api import management_approvals
    from backend.database.models import AppSetting, AuditLog
    from backend.database.session import session_scope
    did, pid = _setup(client, admin)
    _locked_live(monkeypatch)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: set())
    with patch("backend.api.onboarding_workflow._probe", side_effect=AssertionError("No hardware reads")), \
         patch("backend.api.quick_devices._probe_ap", side_effect=AssertionError("No device writes")):
        got = client.post("/api/v1/management-access/approve-ip",
                          headers=admin, json=_request(did, pid))
    assert got.status_code == 200, got.text
    assert got.json()["state"] == "PENDING"
    assert got.json()["prefix"] == 32
    with session_scope() as db:
        row = db.get(AppSetting, "inm.approved_device_ips")
        assert row.value["ips"] == ["10.190.97.211"]
        events = list(db.scalars(select(AuditLog).where(
            AuditLog.action == "management_ip_approval_requested")))
        assert events and events[-1].username == "admin"
    # Retrying is idempotent and must not introduce wider network ranges.
    assert client.post("/api/v1/management-access/approve-ip", headers=admin,
                       json=_request(did, pid)).status_code == 200
    with session_scope() as db:
        assert db.get(AppSetting, "inm.approved_device_ips").value["ips"] == ["10.190.97.211"]


def test_host_applied_state_is_distinct_from_requested_and_read_only(client, admin, monkeypatch):
    from backend.api import management_approvals
    from backend.api.quick_devices import _permit_probe
    import ipaddress
    from fastapi import HTTPException
    did, pid = _setup(client, admin)
    _locked_live(monkeypatch)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: set())
    client.post("/api/v1/management-access/approve-ip", headers=admin, json=_request(did, pid))
    endpoint = f"/api/v1/management-access/approval-status/{did}?profile_id={pid}&management_ip=10.190.97.211"
    assert client.get(endpoint, headers=admin).json()["state"] == "PENDING"
    monkeypatch.delenv("INM_READONLY_DEVICE_CIDRS", raising=False)
    monkeypatch.delenv("INM_APPROVED_DEVICE_IPS", raising=False)
    try:
        _permit_probe(ipaddress.IPv4Address("10.190.97.211"))
        assert False, "Pending approval must not authorize device contact"
    except HTTPException as exc:
        assert exc.status_code == 403
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {"10.190.97.211"})
    assert client.get(endpoint, headers=admin).json()["state"] == "APPLIED"
    assert _permit_probe(ipaddress.IPv4Address("10.190.97.211")) is None
    try:
        _permit_probe(ipaddress.IPv4Address("10.190.97.201"))
        assert False, "Other IP must remain blocked"
    except HTTPException as exc:
        assert exc.status_code == 403



def test_saved_credential_group_can_resume_onboarding_without_duplicate_inventory(client, admin):
    did, pid = _setup(client, admin)
    url = f"/api/v1/onboarding/credentials/{pid}/devices"
    got = client.get(url, headers=admin)
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["profile_id"] == pid
    assert data["profile_name"] == "AP-Standard"
    assert data["device_writes"] == "LOCKED"
    assert data["devices"] == [{
        "id": did, "name": "ACP-Admin", "address": "10.190.97.211"
    }]
    assert "password" not in got.text
    # Retrieving assignments must not create more device records or rebind credentials.
    assert client.get(url, headers=admin).json()["devices"] == data["devices"]
    operator = make_user(client, admin, "group.operator", "operator")
    assert client.get(url, headers=operator).status_code == 403
    assert client.get("/api/v1/onboarding/credentials/00000000-0000-0000-0000-000000000000/devices",
                      headers=admin).status_code == 404



def test_locked_registered_ap505_rest_only_enablement_does_not_unlock_general_writes(client, admin, monkeypatch):
    """End-to-end API policy; no actual network I/O or hardware writes in this test."""
    from backend.api import onboarding_workflow as workflow
    from backend.api import management_approvals
    from backend.aruba import ssh
    did, pid = _setup(client, admin)
    policy = SimpleNamespace(mode="live", inm_profile="lab-readonly", device_write_lock=True,
                             allow_config_changes=False, device_writes_permitted=False,
                             aruba_verify_tls=True)
    monkeypatch.setattr(workflow, "get_settings", lambda: policy)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {"10.190.97.211"})
    state = {"rest_enabled": False, "count": 0}
    monkeypatch.setattr(workflow, "_rest_status", lambda profile, ip: (
        ("ENABLED", "verified") if state["rest_enabled"] else ("UNREACHABLE", "no REST listener")))
    monkeypatch.setattr(ssh, "scan_host_key", lambda host: {
        "fingerprint": "SHA256:TEST_VERIFIED_DEVICE_KEY", "key_type": "ssh-rsa",
        "legacy_algorithms": True,
    })

    def fake_rest_only_enable(ip, username, password, host_key, **kwargs):
        assert ip == "10.190.97.211" and username == "admin"
        assert password == "lab-test-credential"
        assert host_key == "SHA256:TEST_VERIFIED_DEVICE_KEY"
        assert kwargs == {"approved_ap_rest_only": True}
        state["rest_enabled"] = True
        state["count"] += 1
        return [{"command": name, "ok": True} for name in ssh.ENABLE_REST_COMMANDS]

    monkeypatch.setattr(ssh, "enable_rest_api", fake_rest_only_enable)
    endpoint = f"/api/v1/onboarding/credentials/{pid}/rest-api/"
    check = client.post(endpoint + "check", headers=admin, json={
        "management_ip": "10.190.97.211", "device_id": did,
    })
    assert check.status_code == 200, check.text
    assert check.json()["write_unlock_required"] is False
    assert check.json()["ap_rest_only_authorized"] is True
    assert check.json()["ssh"]["legacy_algorithms"] is True

    payload = {"management_ip": "10.190.97.211", "device_id": did,
               "host_key_sha256": "SHA256:TEST_VERIFIED_DEVICE_KEY",
               "confirm_text": "ENABLE REST API 10.190.97.211", "vrf": "default"}
    denied = client.post(endpoint + "enable", headers=admin, json={
        **payload, "device_id": None,
    })
    assert denied.status_code == 423 and state["count"] == 0
    denied = client.post(endpoint + "enable", headers=admin, json={
        **payload, "confirm_text": "yes",
    })
    assert denied.status_code == 422 and state["count"] == 0

    verified = client.post(endpoint + "enable", headers=admin, json=payload)
    assert verified.status_code == 200, verified.text
    assert verified.json()["rest_enabled"] is True
    assert state["count"] == 1
    assert policy.device_write_lock is True
    assert policy.allow_config_changes is False


def test_rest_only_write_stays_denied_if_model_not_ap505_or_ap535(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as workflow
    from backend.api import management_approvals
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    did, pid = _setup(client, admin)
    with session_scope() as db:
        db.get(InmDevice, did).model = "AP-515"
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {"10.190.97.211"})
    monkeypatch.setattr(workflow, "get_settings", lambda: SimpleNamespace(
        mode="live", inm_profile="lab-readonly", device_write_lock=True,
        allow_config_changes=False, device_writes_permitted=False))
    endpoint = f"/api/v1/onboarding/credentials/{pid}/rest-api/enable"
    reply = client.post(endpoint, headers=admin, json={
        "management_ip": "10.190.97.211", "device_id": did,
        "host_key_sha256": "SHA256:TEST_VERIFIED_DEVICE_KEY",
        "confirm_text": "ENABLE REST API 10.190.97.211", "vrf": "default",
    })
    assert reply.status_code == 423, reply.text
