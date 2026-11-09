"""Production read/write contract (EMULATED devices — protocol emulators, never hardware evidence).

Certification -> independent approval -> change plan -> approval -> execution with pre-change backup,
checkpoint, apply, readback, save, post-change backup; failure injection (device error, readback lie,
link loss) -> automatic rollback proven by full-config readback; restore and replacement restore;
hardware path refused while writes are locked; 80 MHz refused; per-device automatic optimization.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.infrastructure import change_executor as X
from tests.conftest import login, make_user

pytest_plugins = []


@pytest.fixture
def live(app_env, tmp_path):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    X._EMULATORS.clear()
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c
    X._EMULATORS.clear()


@pytest.fixture
def env(live):
    adm = login(live)
    assert live.post("/api/v1/demo-data/load", headers=adm).status_code == 200
    appr = make_user(live, adm, "cab.approver", "approver")
    devs = live.get("/api/v1/devices", headers=adm).json()
    sw = next(d for d in devs if d["source_type"] == "DEMO" and d["family"] == "switch")
    ap = next(d for d in devs if d["source_type"] == "DEMO" and d["family"] == "access_point")
    return live, adm, appr, sw, ap


def certify(c, adm, appr, dev, **test):
    r = c.post("/api/v1/certification/run", headers=adm, json={"device_id": dev["id"], "lab_device_acknowledged": True, **test})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["environment"] == "EMULATED"
    failed = [x for x in res["results"] if not x["passed"]]
    certs = [x for x in c.get("/api/v1/certification", headers=adm).json() if x["run_id"] == res["run_id"]]
    assert c.post(f"/api/v1/certification/{certs[0]['id']}/approve", headers=adm).status_code == 403  # tester
    for x in certs:
        if x["status"] == "TESTED_PENDING_APPROVAL":
            assert c.post(f"/api/v1/certification/{x['id']}/approve", headers=appr).json()["status"] == "CERTIFIED_WRITE"
    return res, failed


def plan(c, h, dev, ops, title="Change"):
    r = c.post("/api/v1/changes/plans", headers=h, json={"device_id": dev["id"], "title": title, "operations": ops})
    assert r.status_code == 201, r.text
    return r.json()


def approve(c, appr, p):
    r = c.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=appr, json={"expected_revision": p["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_switch_certify_apply_readback_and_rollback_paths(env):
    c, adm, appr, sw, _ = env
    res, failed = certify(c, adm, appr, sw, test_port="1/1/20", lag_ports=["1/1/21", "1/1/22"])
    assert not failed, failed
    ops = {r["operation"] for r in res["results"]}
    assert {"switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans", "switch.port.admin_state",
            "switch.port.poe", "switch.lldp", "switch.lag.members", "config.restore", "read.backup_full_config"} <= ops
    # dry-run rendering shows the exact REST calls before approval
    p = plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 300},
                          {"op": "switch.port.access_vlan", "port": "1/1/4", "value": 300}], "Guest VLAN on 1/1/4")
    rendered = c.get(f"/api/v1/changes/plans/{p['id']}/render", headers=adm).json()
    assert rendered["operations"][1]["requests"][0]["json"]["vlan_mode"] == "access"
    assert set(rendered["certification"].values()) == {"CERTIFIED_WRITE"}
    approve(c, appr, p)
    ex = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex["decision"] == "ALLOW" and ex["outcome"] == "APPLIED_VERIFIED" and ex["environment"] == "EMULATED"
    names = [s["step"] for s in ex["steps"]]
    assert names[:4] == ["connect", "management_authority", "pre_change_backup_verified", "checkpoint"]
    assert names.count("apply_readback") == 2 and names[-2:] == ["save", "post_change_backup"]
    assert all(s["ok"] for s in ex["steps"])
    again = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert again["decision"] == "DENY" and again["device_writes"] == 0          # no double execution
    tl = c.get(f"/api/v1/devices/{sw['id']}/change-timeline", headers=adm).json()
    assert tl["attributions"][0]["change_type"] == "APPLICATION" and tl["attributions"][0]["level"] == "VERIFIED"
    emu = X._EMULATORS[sw["id"]]
    assert "300" in emu.state["vlans"] and emu.startup is not None

    # device error mid-plan -> rollback proven by full readback
    emu.fail_path = "/system/interfaces/1/1/6"
    p2 = approve(c, appr, plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 310},
                                            {"op": "switch.port.access_vlan", "port": "1/1/6", "value": 310}]))
    ex2 = c.post(f"/api/v1/changes/plans/{p2['id']}/execute", headers=adm).json()
    assert ex2["outcome"] == "ROLLED_BACK" and "310" not in emu.state["vlans"]
    rb = next(s for s in ex2["steps"] if s["step"] == "rollback_readback")
    assert rb["ok"] and rb["matches_pre_change"]
    assert c.get("/api/v1/changes/plans", headers=adm).json()[0]["state"] == "FAILED_ROLLED_BACK"
    emu.fail_path = None

    # device accepts but readback disagrees -> rollback
    emu.lie = True
    p3 = approve(c, appr, plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 320}]))
    ex3 = c.post(f"/api/v1/changes/plans/{p3['id']}/execute", headers=adm).json()
    assert ex3["outcome"] == "ROLLED_BACK" and any(s.get("category") == "READBACK_MISMATCH" for s in ex3["steps"])
    emu.lie = False

    # link lost after the first write -> rollback
    emu.drop_after = emu.writes + 1
    p4 = approve(c, appr, plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 330},
                                            {"op": "switch.port.access_vlan", "port": "1/1/7", "value": 330}]))
    ex4 = c.post(f"/api/v1/changes/plans/{p4['id']}/execute", headers=adm).json()
    assert ex4["outcome"] == "ROLLED_BACK" and "330" not in emu.state["vlans"]
    emu.drop_after = None

    # restore the PRE_CHANGE artifact of the first change (removes VLAN 300) through approval
    pre = next(s["artifact"] for s in ex["steps"] if s["step"] == "pre_change_backup_verified")
    rp = c.post(f"/api/v1/vault/artifacts/{pre}/restore-plan", headers=adm, json={})
    assert rp.status_code == 201, rp.text
    plans = {x["id"]: x for x in c.get("/api/v1/changes/plans", headers=adm).json()}
    approve(c, appr, plans[rp.json()["plan_id"]])
    exr = c.post(f"/api/v1/changes/plans/{rp.json()['plan_id']}/execute", headers=adm).json()
    assert exr["outcome"] == "APPLIED_VERIFIED" and "300" not in emu.state["vlans"]


def test_uncertified_and_locked_hardware_paths_never_touch_devices(env):
    c, adm, appr, sw, ap = env
    # emulated switch, no certification yet -> DENY
    p = approve(c, appr, plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 340}]))
    ex = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex["decision"] == "DENY" and "adapter_certified_for_every_op" in ex["denial_reason"] and not ex["adapter_called"]
    # real device: global write lock on -> DENY before any connection
    real = c.post("/api/v1/quick-devices", headers=adm, json={"site_name": "Lab", "family": "switch", "hostname": "lab-cx",
                  "serial_number": "LAB-CX-1", "model": "CX 6200F 24G", "firmware": "10.14.1020",
                  "management_ip": "10.50.0.2", "status": "up", "metrics": {}}).json()["id"]
    p2 = approve(c, appr, plan(c, adm, {"id": real}, [{"op": "switch.vlan.define", "value": 350}]))
    ex2 = c.post(f"/api/v1/changes/plans/{p2['id']}/execute", headers=adm).json()
    assert ex2["decision"] == "DENY" and "global_write_lock_off" in ex2["denial_reason"] and ex2["device_writes"] == 0
    # certification of real hardware refused while writes are disabled
    r = c.post("/api/v1/certification/run", headers=adm, json={"device_id": real, "lab_device_acknowledged": True,
                                                              "test_port": "1/1/24"})
    assert r.status_code == 409 and "WRITES_DISABLED" in r.text
    # 80 MHz never reaches a device
    assert c.post("/api/v1/changes/plans", headers=adm, json={"device_id": ap["id"], "title": "wide",
                  "operations": [{"op": "ap.radio.width", "value": 80}]}).status_code == 422


def test_instant_certify_and_apply_rf_and_ssid(env):
    c, adm, appr, _, ap = env
    res, failed = certify(c, adm, appr, ap, test_ssid="Corp-WiFi", test_vlan=200)
    assert {f["operation"] for f in failed} == {"config.restore"}      # Instant full restore is guided, not automatic
    assert all(f.get("unsupported") for f in failed)
    st = {x["operation"]: x["status"] for x in c.get("/api/v1/certification", headers=adm).json()
          if x["run_id"] == res["run_id"]}
    assert st["config.restore"] == "UNSUPPORTED" and "FAILED" not in st.values()
    p = approve(c, appr, plan(c, adm, ap, [{"op": "ap.radio.width", "value": 40},
                                           {"op": "ap.client_match", "value": "enabled"},
                                           {"op": "ap.band_steering", "value": "prefer-5ghz"},
                                           {"op": "ap.airtime_fairness", "value": "fair-access"},
                                           {"op": "ap.radio.tx_power", "min_dbm": 12, "max_dbm": 18},
                                           {"op": "ap.radio.channel", "band": "5", "value": 149, "power": 15},
                                           {"op": "ap.ssid.vlan", "ssid": "Corp-WiFi", "value": 200}], "RF baseline"))
    ex = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex["outcome"] == "APPLIED_VERIFIED", ex
    emu = X._EMULATORS[ap["id"]]
    assert " no 80mhz-support" in emu.text and emu.radios[0]["channel"] == "149" and " vlan 200" in emu.text
    # SSH rejection mid-plan -> inverse rollback restores the ARM block
    emu.fail_on = "preferred-access"
    p2 = approve(c, appr, plan(c, adm, ap, [{"op": "ap.client_match", "value": "disabled"},
                                            {"op": "ap.airtime_fairness", "value": "preferred-access"}]))
    ex2 = c.post(f"/api/v1/changes/plans/{p2['id']}/execute", headers=adm).json()
    assert ex2["outcome"] == "ROLLED_BACK" and " client-match" in emu.text


def test_vault_capture_schedule_and_replacement_restore(env):
    c, adm, appr, sw, _ = env
    cap = c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm)
    assert cap.status_code == 201 and cap.json()["environment"] == "EMULATED"
    assert c.post(f"/api/v1/vault/devices/{sw['id']}/schedule", headers=adm, json={"interval_hours": 1}).status_code == 200
    from backend.api.production_ops import run_due_captures
    from backend.database.session import session_scope
    with session_scope() as db:
        assert run_due_captures(db) == {"captured": 1, "failed": 0}
    assert c.get("/api/v1/vault/schedules", headers=adm).json()[0]["last_result"] == "UNCHANGED"
    certify(c, adm, appr, sw, test_port="1/1/20")
    devs = c.get("/api/v1/devices", headers=adm).json()
    new = next(d for d in devs if d["source_type"] == "DEMO" and d["family"] == "switch" and d["id"] != sw["id"]
               and X.model_family(d["model"]) == X.model_family(sw["model"]))
    rp = c.post("/api/v1/replacements/plan", headers=adm, json={"old_device_id": sw["id"], "new_device_id": new["id"],
                                                               "artifact_id": cap.json()["artifact_id"],
                                                               "compatible_models": [sw["model"]]}).json()
    assert rp["state"] == "LAB_VALIDATION_REQUIRED", rp
    r = c.post(f"/api/v1/replacements/{rp['id']}/restore-plan", headers=adm)
    assert r.status_code == 201, r.text
    plans = {x["id"]: x for x in c.get("/api/v1/changes/plans", headers=adm).json()}
    approve(c, appr, plans[r.json()["plan_id"]])


def test_per_device_automatic_optimization_end_to_end(env, monkeypatch):
    c, adm, appr, _, first = env
    certify(c, adm, appr, first)
    ap = next(d for d in c.get("/api/v1/devices", headers=adm).json()
              if d["source_type"] == "DEMO" and d["family"] == "access_point" and d["model"] == first["model"]
              and c.get(f"/api/v1/devices/{d['id']}/optimization", headers=adm).json()["enrollment"] is None)
    other = next(d for d in c.get("/api/v1/devices", headers=adm).json()
                 if d["source_type"] == "DEMO" and d["family"] == "access_point" and d["id"] != ap["id"])
    exp = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    body = {"serial": ap["serial_number"], "mode": "DEVICE_OPT_IN", "allowed_operation_types": ["ap.radio.channel"],
            "value_constraints": {"allowed_widths_mhz": [20, 40]}, "cooldown_seconds": 3600, "max_actions_per_day": 2,
            "expires_at_utc": exp, "standing_approval": True}
    # platform feature off (default) -> DEVICE_OPT_IN enrollment refused
    monkeypatch.delenv("INM_AUTOMATIC_OPTIMIZATION", raising=False)
    assert c.post(f"/api/v1/devices/{ap['id']}/optimization/enroll", headers=adm, json=body).status_code == 422
    # 80 MHz in the constraints is refused even with the feature on
    monkeypatch.setenv("INM_AUTOMATIC_OPTIMIZATION", "true")
    bad = dict(body, value_constraints={"allowed_widths_mhz": [20, 40, 80]})
    assert c.post(f"/api/v1/devices/{ap['id']}/optimization/enroll", headers=adm, json=bad).status_code == 422
    e = c.post(f"/api/v1/devices/{ap['id']}/optimization/enroll", headers=adm, json=body).json()
    assert c.post(f"/api/v1/optimization/enrollments/{e['id']}/approve", headers=appr).json()["status"] == "ARMED"
    # make the AP congested now (fresh measured telemetry)
    from backend.domain.model import DataQuality, SourceType, TelemetryPoint
    from backend.infrastructure.runtime import telemetry_store
    now = datetime.now(timezone.utc)
    telemetry_store().write_points([TelemetryPoint(device_id=ap["id"], metric_name="util_5g_percent", observed_at=now,
                                                   value=92.0, unit="%", collection_method="DEMO_SYNTHETIC",
                                                   quality=DataQuality.OBSERVED, source_type=SourceType.DEMO,
                                                   source_event_id="opt-test-1")])
    from backend.database.session import session_scope
    from backend.database.models_phases import CollectionPlan
    with session_scope() as db:
        for p in db.query(CollectionPlan).all():
            p.last_success_at = now
    # feature flag off -> nothing happens even though the device is armed
    monkeypatch.delenv("INM_AUTOMATIC_OPTIMIZATION", raising=False)
    off = c.post("/api/v1/optimization/run-now", headers=adm).json()
    assert off["executed"] == 0 and off["denied"] == 1
    monkeypatch.setenv("INM_AUTOMATIC_OPTIMIZATION", "true")
    on = c.post("/api/v1/optimization/run-now", headers=adm).json()
    assert on["executed"] == 1, on
    auto = next(p for p in c.get("/api/v1/changes/plans", headers=adm).json() if p["source"] == "AUTO_OPTIMIZER")
    assert auto["state"] == "APPLIED_VERIFIED" and auto["device_id"] == ap["id"]
    # cooldown: immediately again -> denied, other AP never touched
    again = c.post("/api/v1/optimization/run-now", headers=adm).json()
    assert again["executed"] == 0
    assert other["id"] not in X._EMULATORS or X._EMULATORS[other["id"]].radios[0]["channel"] == "36"
    assert c.get(f"/api/v1/devices/{other["id"]}/optimization", headers=adm).json()["mode"] in ("RECOMMEND_ONLY", "SHADOW")
    # emergency stop pauses the device
    assert c.post("/api/v1/optimization/emergency-stop", headers=adm, json={"active": True, "reason": "drill"}).json()["enrollments_paused"] >= 1
    assert c.get(f"/api/v1/devices/{ap['id']}/optimization", headers=adm).json()["status"] == "PAUSED"


def test_template_inheritance_deploys_and_reconciles(env):
    """Global/site template -> capture installed config -> deploy plan with only the needed ops -> approve
    -> execute with readback -> recapture -> compliant (C04/C05/C08)."""
    c, adm, appr, _, _ = env
    devs = c.get("/api/v1/devices", headers=adm).json()
    sw = next(d for d in devs if d["source_type"] == "DEMO" and d["family"] == "switch"
              and c.get(f"/api/v1/config-templates/devices/{d['id']}/preview", headers=adm).json()["templates"])
    certify(c, adm, appr, sw, test_port="1/1/20")
    assert c.post(f"/api/v1/config-templates/devices/{sw['id']}/deploy-plan", headers=adm).status_code in (201, 409)
    cap = c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm)
    assert cap.status_code == 201
    dp = c.post(f"/api/v1/config-templates/devices/{sw['id']}/deploy-plan", headers=adm).json()
    assert dp["in_compliance"] is False
    kinds = [(o["op"], o.get("value")) for o in dp["operations"]]
    assert ("switch.vlan.define", 200) in kinds and ("switch.port.access_vlan", 200) in kinds
    plans = {x["id"]: x for x in c.get("/api/v1/changes/plans", headers=adm).json()}
    approve(c, appr, plans[dp["plan_id"]])
    ex = c.post(f"/api/v1/changes/plans/{dp['plan_id']}/execute", headers=adm).json()
    assert ex["outcome"] == "APPLIED_VERIFIED", ex
    c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm)
    again = c.post(f"/api/v1/config-templates/devices/{sw['id']}/deploy-plan", headers=adm).json()
    assert again["in_compliance"] is True
    comp = c.get(f"/api/v1/compliance/devices/{sw['id']}", headers=adm).json()
    assert comp["intended_vs_installed"]["status"] == "COMPLIANT"
