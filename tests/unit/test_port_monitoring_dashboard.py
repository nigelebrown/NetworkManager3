from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.api.port_groups import _status
from backend.infrastructure.device_state import canonical_inventory, state_of


def test_port_status_uses_measurements_and_expected_power():
    member = {"minimum_watts": 6, "error_threshold": 5, "reset_threshold": 2}
    assert _status(member, {})[0] == "No data"
    assert _status(member, {"port_link_up": 0})[0] == "Critical"
    metrics = {"port_link_up": 1, "port_poe_w": 4, "port_errors_delta": 0, "port_link_resets_delta": 0}
    assert _status(member, metrics) == ("Warning", ["PoE below minimum"])
    metrics["port_poe_w"] = 8
    assert _status(member, metrics)[0] == "Healthy"
    metrics["port_link_resets_delta"] = 2
    assert _status(member, metrics)[0] == "Warning"


def test_inventory_prefers_controller_entry_and_uses_controller_poll_state():
    now = datetime.now(timezone.utc)
    fields = {"source_type": "REAL", "family": "access_point", "status": "up"}
    manual = SimpleNamespace(**fields, vendor="Aruba", serial_number="test", legacy_ap_id=None)
    projected = SimpleNamespace(**fields, vendor="aruba", serial_number="TEST", legacy_ap_id=1)
    controller = SimpleNamespace(last_poll_at=now, last_poll_ok=True)
    assert canonical_inventory([manual, projected]) == [projected]
    assert state_of(projected, None, now=now, controller=controller)["state"] == "ONLINE"
    assert state_of(projected, None, now=now, controller=controller)["last_persisted_at"] == now.isoformat()
    controller.last_poll_at = now - timedelta(hours=1)
    assert state_of(projected, None, now=now, controller=controller)["state"] == "UNKNOWN"
    controller.last_poll_ok = False
    assert state_of(projected, None, now=now, controller=controller)["last_failure"] == "CONTROLLER_POLL_FAILED"
