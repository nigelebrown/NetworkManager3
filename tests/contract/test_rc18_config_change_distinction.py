"""rc18 (register CXAPI-07): two snapshots with the same normalised configuration but different metadata or
representation are NOT a configuration change; a real (including order-only, semantic) change is.

Uses the emulated CX switch and Instant VC through the same capture and scheduled-capture code paths."""
from backend.database.models_phases import ConfigArtifact
from backend.database.session import session_scope
from backend.infrastructure import change_executor as X
from tests.contract.test_production_read_write import env, live  # noqa: F401


def _artifacts(device_id):
    with session_scope() as db:
        return db.query(ConfigArtifact).filter(ConfigArtifact.device_id == device_id).count()


def _scheduled(c, adm, device_id):
    from backend.api.production_ops import run_due_captures
    assert c.post(f"/api/v1/vault/devices/{device_id}/schedule", headers=adm, json={"interval_hours": 24}).status_code == 200
    with session_scope() as db:
        run_due_captures(db)
    return {s["device_id"]: s for s in c.get("/api/v1/vault/schedules", headers=adm).json()}[device_id]["last_result"]


def test_metadata_only_snapshots_are_identical_and_real_changes_are_detected(env):
    c, adm, _, sw, ap = env
    # CX: two captures -> different manifests (capture time, trigger) but the same configuration
    a1 = c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm).json()
    a2 = c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm).json()
    assert a1["artifact_id"] != a2["artifact_id"] and a1["sha256"] == a2["sha256"]
    with session_scope() as db:
        m1, m2 = db.get(ConfigArtifact, a1["artifact_id"]).manifest, db.get(ConfigArtifact, a2["artifact_id"]).manifest
        assert m1 != m2                                            # metadata differs ...
    diff = c.get(f"/api/v1/vault/artifacts/{a1['artifact_id']}/diff/{a2['artifact_id']}", headers=adm).json()
    assert not (diff.get("added") or diff.get("removed") or diff.get("changes")), diff   # ... content does not
    n = _artifacts(sw["id"])
    assert _scheduled(c, adm, sw["id"]) == "UNCHANGED" and _artifacts(sw["id"]) == n
    # a real CX change is a new version with drift detected
    X._EMULATORS[sw["id"]].state["vlans"]["777"] = {"id": 777, "name": "OUT-OF-BAND", "type": "static", "voice": False}
    assert _scheduled(c, adm, sw["id"]) == "CAPTURED_DRIFT_DETECTED" and _artifacts(sw["id"]) == n + 1

    # Instant: representation-only differences (CRLF, trailing spaces, blank lines) are not a change
    c.post(f"/api/v1/vault/devices/{ap['id']}/capture-from-device", headers=adm)
    n = _artifacts(ap["id"])
    iemu = X._EMULATORS[ap["id"]]
    original = iemu.text
    iemu.text = "\r\n".join(line + "  " for line in original.split("\n")) + "\r\n\r\n"
    assert _scheduled(c, adm, ap["id"]) == "UNCHANGED (format only)" and _artifacts(ap["id"]) == n
    # ...but re-ordering lines inside a block is semantic (e.g. access-rule order) and is a change
    lines = original.split("\n")
    i = lines.index(" min-tx-power 9")
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    iemu.text = "\n".join(lines)
    assert _scheduled(c, adm, ap["id"]) == "CAPTURED_DRIFT_DETECTED" and _artifacts(ap["id"]) == n + 1


def test_drift_key_is_order_preserving_and_canonical():
    from backend.api.production_ops import drift_key
    assert drift_key(b'{"b": 1, "a": [1, 2]}', "switch") == drift_key(b'{\n "a": [1, 2],\n "b": 1\n}', "switch")
    assert drift_key(b'{"a": [1, 2]}', "switch") != drift_key(b'{"a": [2, 1]}', "switch")       # list order is data
    assert drift_key(b"x 1\r\ny 2  \r\n\r\n", "access_point") == drift_key(b"x 1\ny 2\n", "access_point")
    assert drift_key(b"x 1\ny 2\n", "access_point") != drift_key(b"y 2\nx 1\n", "access_point")
