"""rc17 batch 1: device-log retention, truthful feature registry, demo gating (covered in capability tests)."""
from datetime import datetime, timedelta, timezone

from tests.conftest import make_user


def _log(db, ident, age_days):
    from backend.database.models_phases import DeviceLogEvent
    at = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.add(DeviceLogEvent(id=ident, device_id=None, device_serial="S1", source_ip="192.0.2.1", vendor="Aruba",
                          severity="info", message=f"event {ident}", observed_at=at, ingested_at=at,
                          parser_version="t", transport_quality="UDP_ALLOWLISTED", raw_hash=ident * 4))


def test_device_log_retention_off_by_default_then_deletes_old_but_keeps_attribution_evidence(client, admin):
    from backend.database.models_phases import ChangeAttribution, DeviceLogEvent
    from backend.database.session import session_scope
    with session_scope() as db:
        for ident, age in (("a" * 16, 400), ("b" * 16, 400), ("c" * 16, 10)):
            _log(db, ident, age)
        db.flush()
        db.add(ChangeAttribution(device_id="d1", log_event_id="b" * 16, change_at=datetime.now(timezone.utc),
                                 level="UNKNOWN", change_type="DIRECT_DEVICE", evidence={}, candidates=[],
                                 created_by="t"))
    assert client.get("/api/v1/device-log-retention", headers=admin).json()["enabled"] is False
    assert client.post("/api/v1/device-log-retention/apply", headers=admin).json() == {"enabled": False, "deleted": 0}
    bad = client.put("/api/v1/device-log-retention", headers=admin, json={"enabled": True, "days": 365})
    assert bad.status_code == 422                                    # approval reference required
    assert client.put("/api/v1/device-log-retention", headers=admin,
                      json={"enabled": True, "days": 10}).status_code == 422   # below the 30-day minimum
    ok = client.put("/api/v1/device-log-retention", headers=admin,
                    json={"enabled": True, "days": 365, "approval_reference": "DPO-2026-17"})
    assert ok.status_code == 200 and ok.json()["preview"] == {"enabled": True, "cutoff": ok.json()["preview"]["cutoff"],
                                                              "would_delete": 1, "protected": 1}
    viewer = make_user(client, admin, "log.viewer", "viewer")
    assert client.post("/api/v1/device-log-retention/apply", headers=viewer).status_code == 403
    out = client.post("/api/v1/device-log-retention/apply", headers=admin).json()
    assert out["deleted"] == 1
    with session_scope() as db:
        left = {e.id for e in db.query(DeviceLogEvent)}
    assert left == {"b" * 16, "c" * 16}                               # evidence + recent kept
    rows = client.get("/api/audit", headers=admin).json()
    assert any(r["action"] == "device_log_retention_applied" for r in rows)


def test_feature_registry_reports_partial_states_and_live_predictive_state(client, admin):
    mods = {m["key"]: m for m in client.get("/api/v1/system/features", headers=admin).json()["modules"]}
    assert mods["predictive"]["status"] == "NO_RUN"
    assert mods["switch_port_access_control"]["status"] == "IMPLEMENTED_UNCERTIFIED"     # rc18: full AAA lifecycle
    assert "BGP: UNSUPPORTED" in mods["routing_dynamic"]["note"] and mods["optics"]["status"] == "LIVE_READ_ONLY"
    assert mods["client_roaming_rf_interference"]["status"] == "LIVE_INFERRED"
