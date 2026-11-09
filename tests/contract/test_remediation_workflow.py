"""E2E-06 / E2E-20: one persistent workflow from site selection to a monitored, configured device.

1. REAL-mode switches (24- and 48-port) behind strict CX protocol emulators: site -> credentials -> discovery ->
   identify (existing device recognised) -> register -> import + vault backup -> group + port-role templates ->
   preview -> configure now -> independent approval -> execute is REFUSED because hardware writes are locked and
   the models are uncertified (no hardware evidence is ever fabricated) -> monitoring ONLINE.
2. EMULATED (demo) devices: the same workflow runs to APPLIED_VERIFIED with readback, then monitoring and DONE.
3. Multi-device deployment batch with stop-on-failure.
"""
import httpx
import pytest

from backend.infrastructure import change_executor as X
from backend.infrastructure.writers.emulator import CxEmulator
from tests.conftest import login, make_user

TEMPLATE_GLOBAL = {
    "vlans": [10, 20, 30],
    "port_roles": {"user": {"access_vlan": 10, "bpdu_guard": "enabled", "qos_trust": "dscp"},
                   "ap": {"trunk_vlans": [10, 20, 30], "native_vlan": 1, "poe": "enabled", "description": "AP uplink"}},
    "port_assignments": [{"ports": "1/1/1-1/1/44", "role": "user"}, {"ports": "1/1/45-1/1/48", "role": "ap"}],
    "system": {"ntp": ["10.10.0.10"], "syslog": ["10.10.0.20"]},
}
TEMPLATE_GROUP = {"port_roles": {"user": {"access_vlan": 20, "bpdu_guard": "enabled", "qos_trust": "dscp"}}}


def _templates(c, adm, family, group_id):
    g = c.post("/api/v1/config-templates", headers=adm, json={"name": f"Fleet {family}", "family": family,
                                                              "scope": "global", "config": TEMPLATE_GLOBAL})
    assert g.status_code == 201, g.text
    t = c.post("/api/v1/config-templates", headers=adm, json={"name": "Clinics group", "family": family,
                                                              "scope": "group", "group_id": group_id,
                                                              "priority": 50, "config": TEMPLATE_GROUP})
    assert t.status_code == 201, t.text


@pytest.fixture
def real_lab(app_env, tmp_path, monkeypatch):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.80.0.0/29")
    from backend.config import get_settings
    get_settings.cache_clear()
    emus = {"10.80.0.2": CxEmulator(model="6100 24G", firmware="PL.10.14.1000", ports=24),
            "10.80.0.3": CxEmulator(model="6200F 48G", firmware="ML.10.13.1020", ports=48)}
    emus["10.80.0.2"].state["system"]["serial_number"] = "LAB-CX-24"
    emus["10.80.0.3"].state["system"]["serial_number"] = "LAB-CX-48"
    real = httpx.Client

    def client(*a, **kw):
        host = str(kw.get("base_url", "")).split("//")[-1]
        if host not in emus:
            raise httpx.ConnectError("no route to host")
        kw["transport"] = emus[host].transport()
        return real(*a, **kw)
    monkeypatch.setattr("backend.infrastructure.writers.aruba_cx.httpx.Client", client)
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c, emus


def test_real_mode_workflow_runs_end_to_end_and_hardware_writes_stay_locked(real_lab):
    c, emus = real_lab
    adm = login(c)
    appr = make_user(c, adm, "wf.approver", "approver")
    # an existing device is recognised, not duplicated
    c.post("/api/v1/quick-devices", headers=adm, json={"site_name": "Clinic A", "family": "switch",
           "hostname": "old-name", "serial_number": "LAB-CX-24", "model": "CX 6100", "management_ip": "10.80.0.2",
           "status": "up", "metrics": {}})
    wf = c.post("/api/v1/workflows", headers=adm, json={"title": "Clinic A switches", "device_family": "switch",
                                                        "site_name": "Clinic A"}).json()
    wid = wf["id"]
    assert wf["step"] == "CREDENTIAL" and [s["status"] for s in wf["steps"][:2]] == ["done", "current"]
    cred = c.post("/api/v1/onboarding/credentials", headers=adm, json={
        "name": "clinic-cx", "device_family": "switch", "protocol": "aruba_cx_rest", "username": "admin",
        "password": "emulator-password"}).json()["id"]
    assert c.post(f"/api/v1/workflows/{wid}/credential", headers=adm, json={"credential_id": cred}).json()["step"] == "DISCOVER"
    v = c.post(f"/api/v1/workflows/{wid}/discover", headers=adm, json={"scope": "10.80.0.0/29"}).json()
    found = [r for r in v["discovery"]["results"] if r["status"] == "FOUND"]
    assert sorted(r["serial_number"] for r in found) == ["LAB-CX-24", "LAB-CX-48"]
    assert next(r for r in found if r["serial_number"] == "LAB-CX-24")["existing_device_id"]
    idx = [i for i, r in enumerate(v["discovery"]["results"]) if r["status"] == "FOUND"]
    v = c.post(f"/api/v1/workflows/{wid}/register", headers=adm, json={"indices": idx}).json()
    assert v["step"] == "BASELINE" and len(v["devices"]) == 2
    devs = c.get("/api/v1/devices", headers=adm).json()
    assert sum(1 for d in devs if d["serial_number"] == "LAB-CX-24") == 1          # no duplicate asset
    v = c.post(f"/api/v1/workflows/{wid}/baseline", headers=adm).json()
    assert v["step"] == "TEMPLATES" and all(x["ok"] for x in v["outcome"]["baseline"].values()), v["outcome"]
    v = c.post(f"/api/v1/workflows/{wid}/templates", headers=adm, json={"new_group_name": "Clinic switches"}).json()
    gid = v["template_scope"]["group_id"]
    _templates(c, adm, "switch", gid)
    pv = c.get(f"/api/v1/workflows/{wid}/preview", headers=adm).json()["devices"]
    by = {d["hostname"]: d for d in pv}
    assert len(pv) == 2
    for d in pv:
        n = 24 if "LAB-CX-24" in str(d) or len(d["port_roles"]) <= 24 else 48
        roles = d["port_roles"]
        ops = d["operations"]
        # the group's "user" role (VLAN 20) overrides the global one (VLAN 10); ranges cut to the model's ports
        assert all(o["value"] == 20 for o in ops if o["op"] == "switch.port.access_vlan")
        assert set(roles.values()) <= {"user", "ap"} and max(int(p.split("/")[-1]) for p in roles) in (24, 48)
        assert {"switch.vlan.define", "switch.system.ntp", "switch.system.syslog"} <= {o["op"] for o in ops}
        assert set(d["certification"].values()) == {"UNCERTIFIED"}
    big = next(d for d in pv if max(int(p.split("/")[-1]) for p in d["port_roles"]) == 48)
    assert big["port_roles"]["1/1/46"] == "ap" and any(o["op"] == "switch.port.trunk_vlans" and o["port"] == "1/1/46"
                                                      for o in big["operations"])
    v = c.post(f"/api/v1/workflows/{wid}/decide", headers=adm, json={"mode": "NOW"}).json()
    assert v["step"] == "APPROVAL" and v["plan_progress"]["pending_approval"] == 2
    assert c.get(f"/api/v1/workflows/{wid}", headers=appr).status_code == 200      # approver can open it to approve
    for p in v["plans"]:
        rev = c.get("/api/v1/changes/plans", headers=adm).json()
        plan = next(x for x in rev if x["id"] == p["plan_id"])
        assert c.post(f"/api/v1/changes/plans/{plan['id']}/approve", headers=adm,
                      json={"expected_revision": plan["revision"]}).status_code == 403          # requester cannot
        assert c.post(f"/api/v1/changes/plans/{plan['id']}/approve", headers=appr,
                      json={"expected_revision": plan["revision"]}).status_code == 200
    v = c.post(f"/api/v1/workflows/{wid}/execute", headers=adm).json()
    res = v["outcome"]["execution"]
    assert len(res) == 2 and all(r["decision"] == "DENY" and r["environment"] == "HARDWARE" for r in res)
    assert all("global_write_lock_off" in r["denial_reason"] and "adapter_certified" in r["denial_reason"] for r in res)
    assert all(e.writes == 0 for e in emus.values())                      # nothing was written to the "hardware"
    v = c.post(f"/api/v1/workflows/{wid}/monitor", headers=adm).json()
    assert set(v["outcome"]["monitoring"]["states"].values()) == {"ONLINE"}
    assert [h["step"] for h in v["history"]][:4] == ["SITE", "CREDENTIAL", "DISCOVER", "REGISTER"]
    assert all(e.violations == [] for e in emus.values())
    # resumable: a fresh GET returns the same state
    again = c.get(f"/api/v1/workflows/{wid}", headers=adm).json()
    assert again["step"] == v["step"] and len(again["history"]) == len(v["history"])


@pytest.fixture
def demo(app_env, tmp_path):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    X._EMULATORS.clear()
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        adm = login(c)
        assert c.post("/api/v1/demo-data/load", headers=adm).status_code == 200
        yield c, adm, make_user(c, adm, "wf.cab", "approver")
    X._EMULATORS.clear()


def _certify(c, adm, appr, dev):
    r = c.post("/api/v1/certification/run", headers=adm, json={"device_id": dev, "lab_device_acknowledged": True,
                                                                  "test_port": "1/1/10"}).json()  # on every demo model
    for x in c.get("/api/v1/certification", headers=adm).json():
        if x["run_id"] == r["run_id"] and x["status"] == "TESTED_PENDING_APPROVAL":
            c.post(f"/api/v1/certification/{x['id']}/approve", headers=appr)


def test_emulated_workflow_reaches_applied_verified_monitored_done(demo):
    c, adm, appr = demo
    sws = [d for d in c.get("/api/v1/devices", headers=adm).json() if d["source_type"] == "DEMO" and d["family"] == "switch"]
    sw = sws[0]["id"]
    _certify(c, adm, appr, sw)
    wid = c.post("/api/v1/workflows", headers=adm, json={"title": "Demo core", "device_family": "switch",
                                                         "site_name": "Training"}).json()["id"]
    c.post(f"/api/v1/workflows/{wid}/attach", headers=adm, json={"device_ids": [sw]})
    v = c.post(f"/api/v1/workflows/{wid}/baseline", headers=adm).json()
    assert v["step"] == "TEMPLATES"
    v = c.post(f"/api/v1/workflows/{wid}/templates", headers=adm, json={"new_group_name": "Demo group"}).json()
    _templates(c, adm, "switch", v["template_scope"]["group_id"])
    pv = c.get(f"/api/v1/workflows/{wid}/preview", headers=adm).json()["devices"][0]
    assert pv["operations"] and set(pv["certification"].values()) == {"CERTIFIED_WRITE"}
    v = c.post(f"/api/v1/workflows/{wid}/decide", headers=adm, json={"mode": "NOW"}).json()
    pid = v["plans"][0]["plan_id"]
    plan = next(x for x in c.get("/api/v1/changes/plans", headers=adm).json() if x["id"] == pid)
    c.post(f"/api/v1/changes/plans/{pid}/approve", headers=appr, json={"expected_revision": plan["revision"]})
    v = c.post(f"/api/v1/workflows/{wid}/execute", headers=adm).json()
    assert v["outcome"]["execution"][0]["outcome"] == "APPLIED_VERIFIED" and v["step"] == "MONITOR"
    emu = X._EMULATORS[sw]
    assert emu.state["interfaces"]["1/1/3"]["vlan_tag"].endswith("/20")              # group override applied
    assert emu.state["interfaces"]["1/1/3"]["stp_config"]["bpdu_guard_enable"] is True
    ex = c.get(f"/api/v1/changes/plans/{pid}/executions", headers=adm).json()[0]
    assert all(s["ok"] for s in ex["steps"] if s["step"] == "apply_readback")
    # re-preview after apply: the device now matches intent
    assert c.get(f"/api/v1/workflows/{wid}/preview", headers=adm).json()["devices"][0]["operations"] == []
    v = c.post(f"/api/v1/workflows/{wid}/monitor", headers=adm).json()
    assert v["step"] == "DONE" and v["state"] == "DONE"


def test_multi_device_deployment_with_progress_and_stop_on_failure(demo):
    c, adm, appr = demo
    sws = [d["id"] for d in c.get("/api/v1/devices", headers=adm).json()
           if d["source_type"] == "DEMO" and d["family"] == "switch"][:3]
    assert len(sws) >= 2
    g = c.post("/api/v1/device-groups", headers=adm, json={"name": "Batch", "family": "switch"}).json()["id"]
    c.post(f"/api/v1/device-groups/{g}/members", headers=adm, json={"device_ids": sws})
    _templates(c, adm, "switch", g)
    for d in sws:
        _certify(c, adm, appr, d)
        assert c.post(f"/api/v1/vault/devices/{d}/capture-from-device", headers=adm).status_code == 201
    b = c.post("/api/v1/deployments", headers=adm, json={"group_id": g}).json()
    assert len([x for x in b["devices"] if x.get("plan_id")]) == len(sws)
    batch = b["batch_id"]
    st = c.get(f"/api/v1/deployments/{batch}", headers=adm).json()
    assert st["progress"]["pending_approval"] == len(sws)
    for p in st["plans"]:
        plan = next(x for x in c.get("/api/v1/changes/plans", headers=adm).json() if x["id"] == p["plan_id"])
        c.post(f"/api/v1/changes/plans/{plan['id']}/approve", headers=appr, json={"expected_revision": plan["revision"]})
    # the second device will reject a write -> automatic rollback there, and the batch stops
    order = [p["device_id"] for p in st["plans"]]
    X._EMULATORS[order[1]].fail_path = "/system/ntp_associations"
    r = c.post(f"/api/v1/deployments/{batch}/execute", headers=adm, json={"stop_on_failure": True}).json()
    outcomes = [e["outcome"] for e in r["executed"]]
    assert outcomes[0] == "APPLIED_VERIFIED" and outcomes[1] == "ROLLED_BACK" and len(outcomes) == 2
    assert r["stopped_at"] == st["plans"][1]["plan_id"]
    prog = c.get(f"/api/v1/deployments/{batch}", headers=adm).json()["progress"]
    assert prog["applied_verified"] == 1 and prog["failed_rolled_back"] == 1 and prog["approved"] == len(sws) - 2
