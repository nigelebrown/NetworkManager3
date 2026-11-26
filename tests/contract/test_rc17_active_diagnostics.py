"""rc17 device-initiated ping/traceroute: fixed templates, validated targets, RBAC, unsupported kinds explicit."""
import pytest

from backend.infrastructure.device_diagnostics import active_command
from tests.conftest import make_user


def test_command_templates_accept_only_validated_values():
    assert active_command("switch", "ping", "10.1.1.1", vrf="mgmt", count=3) == "ping 10.1.1.1 repetitions 3 vrf mgmt"
    assert active_command("access_point", "traceroute", "10.1.1.1") == "traceroute 10.1.1.1"
    for bad in ("10.1.1.1; reload", "example.com", "224.0.0.1", "0.0.0.0", "10.1.1.1 vrf x"):
        with pytest.raises(ValueError):
            active_command("switch", "ping", bad)
    with pytest.raises(ValueError):
        active_command("switch", "ping", "10.1.1.1", vrf="evil")


def test_active_diagnostics_api_emulated_rbac_and_unsupported(client, admin):
    r = client.post("/api/v1/quick-devices", headers=admin, json={"site_name": "HQ", "family": "switch", "hostname": "diag",
                    "serial_number": "DIAG-1", "model": "CX 6200F 24G", "status": "up", "metrics": {}})
    did = r.json()["id"]
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    with session_scope() as db:
        db.get(InmDevice, did).source_type = "DEMO"
    ok = client.post(f"/api/v1/devices/{did}/active-diagnostics", headers=admin, json={"kind": "ping", "target": "10.0.0.1"})
    assert ok.status_code == 200 and ok.json()["environment"] == "EMULATED" and ok.json()["device_writes"] == 0
    assert client.post(f"/api/v1/devices/{did}/active-diagnostics", headers=admin,
                       json={"kind": "ping", "target": "10.0.0.1;reboot"}).status_code == 422
    unsup = client.post(f"/api/v1/devices/{did}/active-diagnostics", headers=admin, json={"kind": "packet_capture"})
    assert unsup.status_code == 409 and "privacy" in unsup.json()["detail"]
    viewer = make_user(client, admin, "diag.viewer", "viewer")
    assert client.post(f"/api/v1/devices/{did}/active-diagnostics", headers=viewer,
                       json={"kind": "ping", "target": "10.0.0.1"}).status_code == 403
