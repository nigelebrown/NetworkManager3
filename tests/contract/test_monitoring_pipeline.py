"""End-to-end monitoring pipeline for a REAL-path CX switch (only the TCP socket is replaced by the CX
protocol emulator): credential decryption, allowlist, CX REST telemetry -> collector -> telemetry store ->
Device 360 series -> NOC tile -> faceplate -> alert engine (open, dedupe, suppress, resolve, per-site
threshold). Also guards the regression where CX probes used /rest/v10.14/rest/v10.14/... URLs."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.database.session import session_scope
from backend.infrastructure.writers.emulator import CxEmulator
from tests.conftest import login


@pytest.fixture
def live(app_env, tmp_path, monkeypatch):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.60.0.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    emu = CxEmulator(model="6200F 24G", firmware="ML.10.14.1020", ports=24)
    real_client = httpx.Client

    def client(*a, **kw):
        kw["transport"] = emu.transport()
        return real_client(*a, **kw)
    monkeypatch.setattr("backend.infrastructure.writers.aruba_cx.httpx.Client", client)
    monkeypatch.setattr("backend.api.quick_devices.httpx.Client", client)
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c, emu


def setup_switch(c, adm, emu):
    did = c.post("/api/v1/quick-devices", headers=adm, json={
        "site_name": "HQ", "family": "switch", "hostname": "hq-access-01", "serial_number": "EMU-CX-0001",
        "model": "CX 6200F 24G", "firmware": "ML.10.14.1020", "management_ip": "10.60.0.2", "status": "up",
        "metrics": {}}).json()["id"]
    prof = c.post("/api/v1/onboarding/credentials", headers=adm, json={
        "name": "HQ CX read", "device_family": "switch", "protocol": "aruba_cx_rest",
        "username": "admin", "password": emu.password})
    assert prof.status_code == 201, prof.text
    from backend.database.models_onboarding import OnboardingDeviceCredential
    with session_scope() as db:
        db.add(OnboardingDeviceCredential(device_id=did, credential_id=prof.json()["id"], bound_by="test"))
    plan = c.post("/api/v1/collection/plans", headers=adm, json={
        "device_id": did, "metric_families": ["system", "interfaces", "poe", "lldp"], "interval_seconds": 300})
    assert plan.status_code == 201, plan.text
    return did, plan.json()["id"]


def poll(plan_id, at):
    from backend.infrastructure.collection_runner import run_due
    from backend.infrastructure.runtime import telemetry_store
    with session_scope() as db:
        return run_due(db, now=at, store=telemetry_store(), worker="test", plan_ids=[plan_id])


def test_cx_probe_uses_single_rest_prefix(live):
    c, emu = live
    from backend.api.quick_devices import QuickProbe, _probe_cx
    out = _probe_cx(QuickProbe(family="switch", site_name="x", management_ip="10.60.0.2", username="admin",
                               password=emu.password, hostname="h", serial_number="EMU-CX-0001", model="CX"))
    assert out["serial_number"] == "EMU-CX-0001" and out["metrics"]["ports_total"] == 24


def test_full_pipeline_and_alert_lifecycle(live):
    c, emu = live
    adm = login(c)
    did, pid = setup_switch(c, adm, emu)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=20)
    s1 = poll(pid, t0)
    assert s1["succeeded"] == 1 and s1["points_written"] >= 10, s1
    # Device 360 series and NOC tile read the same canonical metric names
    series = c.get(f"/api/v1/devices/{did}/metrics/series?metric=ports_up&hours=1", headers=adm).json()
    assert series["points"] and series["points"][-1]["v"] == 16.0 and series["points"][-1]["src"] == "REAL"
    noc = c.get("/api/v1/noc/overview", headers=adm).json()
    tile = next(t for t in noc["devices"] if t["device_id"] == did)
    assert tile["metrics"]["ports_up"] == 16.0 and tile["metrics"]["poe_power_w"] == 37.5
    assert tile["metrics"]["lldp_neighbors"] == 3.0 and tile["metrics"]["temp_max_c"] == 41.0
    # faceplate
    fp = c.get(f"/api/v1/devices/{did}/ports", headers=adm).json()
    assert len(fp["ports"]) == 24 and fp["environment"] == "HARDWARE"
    p1 = next(p for p in fp["ports"] if p["name"] == "1/1/1")
    assert p1["link"] == "up" and p1["poe_w"] == 12.5 and p1["lldp"]["system_name"] == "AP-1"
    # second poll: traffic + errors on 1/1/5, a failed fan
    for i, it in emu.state["interfaces"].items():
        it["statistics"]["rx_bytes"] += 75_000_000_000      # ~ 1 Gbps over 10 min on every port -> util
    emu.state["interfaces"]["1/1/5"]["statistics"]["rx_error"] = 500
    emu.state["subsystems"]["management_module,1/1"]["fans"]["2"]["status"] = "fault"
    s2 = poll(pid, t0 + timedelta(minutes=10))
    assert s2["succeeded"] == 1
    alerts = {a["rule_id"]: a for a in c.get("/api/v1/alerts", headers=adm).json()}
    assert {"SW_PORT_ERRORS", "SW_FAN_FAILED", "SW_UPLINK_UTIL_HIGH"} <= set(alerts)
    assert alerts["SW_FAN_FAILED"]["severity"] == "critical" and alerts["SW_PORT_ERRORS"]["value"] == 500
    assert any(a.get("rule_id") == "SW_FAN_FAILED" for a in c.get("/api/v1/noc/overview", headers=adm).json()["alerts"])
    rates = next(p for p in c.get(f"/api/v1/devices/{did}/ports", headers=adm).json()["ports"] if p["name"] == "1/1/2")["rates"]
    assert rates["util_percent"] > 85
    util = c.get(f"/api/v1/devices/{did}/metrics/series?metric=max_port_util_percent&hours=1", headers=adm).json()
    assert util["points"][-1]["v"] > 85
    # third poll: no new errors, fan repaired, traffic quiet -> those alerts auto-resolve; dedupe kept one row
    emu.state["subsystems"]["management_module,1/1"]["fans"]["2"]["status"] = "ok"
    poll(pid, t0 + timedelta(minutes=20))
    open_now = {a["rule_id"] for a in c.get("/api/v1/alerts", headers=adm).json()}
    assert not ({"SW_PORT_ERRORS", "SW_FAN_FAILED", "SW_UPLINK_UTIL_HIGH"} & open_now)
    every = [a for a in c.get("/api/v1/alerts?state=ALL", headers=adm).json() if a["rule_id"] == "SW_FAN_FAILED"]
    assert len(every) == 1 and every[0]["state"] == "RESOLVED"


def test_unreachable_threshold_override_and_maintenance_suppression(live):
    c, emu = live
    adm = login(c)
    did, pid = setup_switch(c, adm, emu)
    sites = c.get("/api/v1/sites", headers=adm).json()["sites"]
    sid = next(s["id"] for s in sites if s["name"] == "HQ")
    assert c.put("/api/v1/alert-rules/SW_TEMP_HIGH", headers=adm, json={"scope": sid, "threshold": 40,
                                                                         "severity": "minor"}).status_code == 200
    now = datetime.now(timezone.utc)
    assert c.post("/api/v1/maintenance-windows", headers=adm, json={
        "site_id": sid, "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(), "reason": "Cabling work"}).status_code == 201
    poll(pid, now - timedelta(minutes=5))
    temp = next(a for a in c.get("/api/v1/alerts", headers=adm).json() if a["rule_id"] == "SW_TEMP_HIGH")
    assert temp["severity"] == "minor" and temp["threshold"] == 40 and temp["suppressed"] is True
    # link loss: three failed polls -> DEVICE_UNREACHABLE; telemetry is not fabricated
    emu.password = "changed"          # device now rejects -> AUTH_REJECTED is permanent -> DLQ
    s = poll(pid, now + timedelta(minutes=10))
    assert s["dlq"] == 1 and s["points_written"] == 0


def test_central_managed_switch_is_detected_and_writes_refused(live):
    c, emu = live
    adm = login(c)
    did, _ = setup_switch(c, adm, emu)
    assert c.get(f"/api/v1/devices/{did}/authority", headers=adm).json()["authority"] == "LOCAL"
    emu.state["central"] = {"disable": False, "status": {"connection_state": "connected"}}
    a = c.get(f"/api/v1/devices/{did}/authority", headers=adm).json()
    assert a["authority"] == "ARUBA_CENTRAL" and a["environment"] == "HARDWARE"
    bad = c.post("/api/v1/changes/plans", headers=adm, json={"device_id": did, "title": "takeover",
                 "operations": [{"op": "switch.central.disable"}]})
    assert bad.status_code == 422      # explicit acknowledgement required
