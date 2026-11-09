"""End-to-end API contract for phases 5-11 + operations centre, run in LIVE mode (not demo).

Covers GAP-T02/T03/T04/T05/T06/T07/T08/T09/T11/T12/T14/T17-T24 at API level, module integration
(collection -> telemetry -> NOC/experience -> recommendations -> learning; vault -> compliance ->
change gate -> optimization approval; replacement -> enrollment revocation) and demo load/remove.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.database.session import session_scope
from backend.infrastructure import collection_runner as CR
from tests.conftest import login, make_user

GOOD_CX = """hostname hq-core-01
clock timezone america/bogota
ssh server vrf mgmt
ntp server 10.0.0.1 iburst
logging 10.0.0.2 udp 1514 vrf mgmt
tacacs-server host 10.0.0.3 key ciphertext AAA==
aaa accounting all-mgmt default start-stop group tacacs
banner motd !auth only!
spanning-tree
vlan 100
vlan 200
interface 1/1/1
    vlan access 100
    spanning-tree bpdu-guard
"""
BAD_CX = "hostname switch\ntelnet server\nsnmp-server community public\nvlan 100\ninterface 1/1/1\n    vlan access 1\n"


@pytest.fixture
def live(app_env, tmp_path):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def adm(live):
    return login(live)


def dev(c, h, serial, family="switch", model=None, ip=None, fw=None):
    r = c.post("/api/v1/quick-devices", headers=h, json={
        "site_name": "HQ", "family": family, "hostname": serial.lower(), "serial_number": serial,
        "model": model or ("CX 6200F 24G" if family == "switch" else "AP-535"),
        "firmware": fw or ("10.14.1020" if family == "switch" else "8.10.0.9"),
        "management_ip": ip, "status": "up", "metrics": {}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_live_mode_shows_no_demo_and_demo_toggle_round_trip(live, adm):
    real = dev(live, adm, "LIVE-SW-1")
    assert live.get("/api/v1/demo-data", headers=adm).json()["loaded"] is False
    noc = live.get("/api/v1/noc/overview", headers=adm).json()
    assert [d["source_type"] for d in noc["devices"]] == ["REAL"]
    loaded = live.post("/api/v1/demo-data/load", headers=adm)
    assert loaded.status_code == 200, loaded.text
    created = loaded.json()["created"]
    assert created["devices"] >= 10 and created["telemetry_points"] > 1000
    noc = live.get("/api/v1/noc/overview", headers=adm).json()
    assert {d["source_type"] for d in noc["devices"]} == {"REAL", "DEMO"}
    assert any(d["health"]["score"] is not None for d in noc["devices"] if d["source_type"] == "DEMO")
    fleet = live.get("/api/v1/compliance/fleet", headers=adm).json()
    assert fleet["fleet_score"] is not None and fleet["top_failures"]
    assert live.get("/api/v1/executive/trends", headers=adm).json()["hourly"]
    assert live.get("/api/v1/reports/executive", headers=adm).status_code == 200
    # legacy SIMS pages show demo sites in live mode only while loaded
    assert any(s["simulated"] for s in live.get("/api/sites", headers=adm).json())
    removed = live.post("/api/v1/demo-data/remove", headers=adm)
    assert removed.status_code == 200, removed.text
    noc = live.get("/api/v1/noc/overview", headers=adm).json()
    assert [d["device_id"] for d in noc["devices"]] == [real]
    assert not any(s["simulated"] for s in live.get("/api/sites", headers=adm).json())
    assert live.get("/api/v1/demo-data", headers=adm).json()["demo_devices"] == 0
    # load again works after removal (idempotent cycle)
    assert live.post("/api/v1/demo-data/load", headers=adm).status_code == 200
    assert live.post("/api/v1/demo-data/remove", headers=adm).status_code == 200


def test_viewer_cannot_toggle_demo(live, adm):
    v = make_user(live, adm, "noc.viewer", "viewer")
    assert live.post("/api/v1/demo-data/load", headers=v).status_code == 403
    assert live.get("/api/v1/noc/overview", headers=v).status_code == 200


def test_collection_plan_success_failure_dedupe_and_experience(live, adm, monkeypatch):
    did = dev(live, adm, "LIVE-AP-1", "access_point", ip="10.20.0.5")
    prev = live.post("/api/v1/collection/plans/preview", headers=adm, json={
        "device_id": did, "metric_families": ["system", "radios", "clients"], "interval_seconds": 300})
    assert prev.status_code == 200 and prev.json()["warnings"]  # no credential bound
    assert live.post("/api/v1/collection/plans", headers=adm, json={
        "device_id": did, "metric_families": ["interfaces"], "interval_seconds": 300}).status_code == 422
    plan = live.post("/api/v1/collection/plans", headers=adm, json={
        "device_id": did, "metric_families": ["system", "radios", "clients"], "interval_seconds": 300}).json()
    # GAP-T02: unreachable -> RETRY, no zero readings
    def fail(device, plan, db):
        raise CR.CollectionFailure("UNREACHABLE")
    monkeypatch.setattr(CR, "aruba_read_collector", fail)
    r = live.post(f"/api/v1/collection/plans/{plan['id']}/run-now", headers=adm).json()
    assert r["summary"]["retry"] == 1 and r["summary"]["points_written"] == 0
    assert r["plan"]["last_failure_category"] == "UNREACHABLE" and r["plan"]["freshness"] == "NEVER"
    exp = live.get(f"/api/v1/devices/{did}/experience", headers=adm).json()
    assert exp["sample_size"] == 0 and exp["measured"] == {}
    # An explicit operator "poll now" is never silently swallowed (E2E remediation): it runs again.
    r2 = live.post(f"/api/v1/collection/plans/{plan['id']}/run-now", headers=adm).json()
    assert r2["summary"]["retry"] == 1 and r2["summary"]["skipped_duplicate"] == 0
    # Scheduled runs from several workers in the same window ARE deduplicated.
    from datetime import datetime, timezone
    t = datetime(2026, 11, 9, 12, 0, 10, tzinfo=timezone.utc)
    with session_scope() as db:
        CR.run_due(db, now=t, plan_ids=[plan["id"]], worker="w1")
    with session_scope() as db:
        again = CR.run_due(db, now=t.replace(second=11), plan_ids=[plan["id"]], worker="w2")
    assert again["skipped_duplicate"] == 3 and again.get("retry", 0) == 0
    # success path in a fresh window
    def ok(device, plan, db):
        return {"metrics": {"cpu_percent": 33.0, "clients": 12.0, "util_5g_percent": 91.0}, "firmware": "8.10.0.9"}
    monkeypatch.setattr(CR, "aruba_read_collector", ok)
    with session_scope() as db:
        from backend.database.models_phases import CollectionRun
        db.query(CollectionRun).delete()
    r3 = live.post(f"/api/v1/collection/plans/{plan['id']}/run-now", headers=adm).json()
    assert r3["summary"]["succeeded"] == 1 and r3["summary"]["points_written"] == 3
    assert r3["plan"]["freshness"] == "FRESH"
    exp = live.get(f"/api/v1/devices/{did}/experience?window=1h", headers=adm).json()
    assert exp["measured"]["client_count" if "client_count" in exp["measured"] else "util_5g_percent"]
    noc = live.get("/api/v1/noc/overview", headers=adm).json()
    tile = next(t for t in noc["devices"] if t["device_id"] == did)
    assert tile["freshness"] == "FRESH" and tile["health"]["band"] in ("FAIR", "POOR")
    assert live.get("/api/v1/collection/jobs", headers=adm).json()
    paused = live.post(f"/api/v1/collection/plans/{plan['id']}/pause", headers=adm).json()
    assert paused["state"] == "PAUSED"
    assert live.post(f"/api/v1/collection/plans/{plan['id']}/run-now", headers=adm).status_code == 409


def test_syslog_attribution_levels_and_corrections(live, adm):
    did = dev(live, adm, "LIVE-SW-ATTR", ip="10.20.0.9")
    line = "<190>sw hpe-config[1]: Event|6801|LOG_INFO|AMM|-|User alice configuration change committed"
    # GAP-T03: no AAA -> CORRELATED is impossible, UNKNOWN only
    r = live.post("/api/v1/syslog/ingest", headers=adm, json={"source_ip": "10.20.0.9", "lines": [line]})
    assert r.status_code == 201 and r.json()["config_changes_attributed"] == 1
    tl = live.get(f"/api/v1/devices/{did}/change-timeline", headers=adm).json()
    assert tl["attributions"][0]["level"] == "UNKNOWN" and tl["attributions"][0]["actor"] is None
    assert "aaa" in tl["coverage"]["missing"]
    # duplicate line is not re-stored
    assert live.post("/api/v1/syslog/ingest", headers=adm, json={"source_ip": "10.20.0.9", "lines": [line]}).json()["duplicates"] == 1
    # GAP-T04: matching AAA session -> VERIFIED
    t = datetime.now(timezone.utc)
    assert live.post("/api/v1/aaa/sessions", headers=adm, json={
        "device_serial": "LIVE-SW-ATTR", "username": "alice", "start_at": (t - timedelta(minutes=5)).isoformat(),
        "end_at": (t + timedelta(minutes=5)).isoformat(), "source": "TACACS+"}).status_code == 201
    line2 = line.replace("committed", "saved")
    live.post("/api/v1/syslog/ingest", headers=adm, json={"source_ip": "10.20.0.9", "lines": [line2]})
    tl = live.get(f"/api/v1/devices/{did}/change-timeline", headers=adm).json()
    assert tl["attributions"][0]["level"] == "VERIFIED" and tl["attributions"][0]["actor"] == "alice"
    # unregistered UDP source refused; manual VERIFIED impossible; supersede keeps original
    assert live.post("/api/v1/syslog/ingest", headers=adm, json={"source_ip": "10.99.9.9", "lines": ["x"]}).status_code == 403
    aid = tl["attributions"][0]["id"]
    assert live.post(f"/api/v1/attributions/{aid}/correct", headers=adm, json={
        "level": "VERIFIED", "actor": "alice", "note": "trust me"}).status_code == 422
    assert live.post(f"/api/v1/attributions/{aid}/correct", headers=adm, json={
        "level": "CORRELATED", "actor": "mallory", "note": "not a candidate"}).status_code == 422
    c = live.post(f"/api/v1/attributions/{aid}/correct", headers=adm, json={
        "level": "CORRELATED", "actor": "alice", "note": "Under review by security."})
    assert c.status_code == 201 and c.json()["supersedes_id"] == aid
    assert len(live.get(f"/api/v1/devices/{did}/change-timeline", headers=adm).json()["attributions"]) == 3
    assert live.get("/api/v1/drift", headers=adm).json()


def test_vault_compliance_change_gate_and_integration(live, adm, tmp_path):
    did = dev(live, adm, "LIVE-SW-V")
    good = live.post(f"/api/v1/vault/devices/{did}/artifacts", headers=adm, json={"content": GOOD_CX})
    assert good.status_code == 201 and "content" not in good.json() and good.json()["fidelity"] == "FULL_FIDELITY"
    comp = live.get(f"/api/v1/compliance/devices/{did}", headers=adm).json()
    assert comp["score"] >= 90 and comp["grade"] == "A"
    bad = live.post(f"/api/v1/vault/devices/{did}/artifacts", headers=adm, json={"content": BAD_CX}).json()
    comp = live.get(f"/api/v1/compliance/devices/{did}", headers=adm).json()
    fails = {r["id"] for r in comp["results"] if r["status"] == "FAIL"}
    assert {"CX-SEC-02", "CX-SEC-03", "CX-L2-03", "CX-OPS-04"} <= fails and comp["critical_failures"]
    d = live.get(f"/api/v1/vault/artifacts/{good.json()['id']}/diff/{bad['id']}", headers=adm).json()
    assert any("telnet" in x for x in d["diff"]) and not any("AAA==" in x for x in d["diff"])  # secrets redacted
    # template intended vs installed
    sites = live.get("/api/v1/sites", headers=adm).json()["sites"]
    sid = next(x["id"] for x in sites if x["name"] == "HQ" and x["source_type"] == "REAL")
    assert live.post("/api/v1/config-templates", headers=adm, json={
        "name": "HQ VLANs", "family": "switch", "scope": "site", "site_id": sid, "priority": 100, "enabled": True,
        "config": {"vlans": [100, 200, 999]}}).status_code == 201
    comp = live.get(f"/api/v1/compliance/devices/{did}", headers=adm).json()
    assert comp["intended_vs_installed"]["status"] == "DRIFT"
    assert any(x["field"] == "vlan:999" for x in comp["intended_vs_installed"]["differences"])
    # GAP-T07 tamper -> integrity fail
    from backend.database.models_phases import ConfigArtifact
    with session_scope() as db:
        a = db.get(ConfigArtifact, bad["id"])
        path = tmp_path / "vault" / a.object_path
    path.chmod(0o600)
    path.write_bytes(path.read_bytes()[:-3] + b"xyz")
    assert live.post(f"/api/v1/vault/artifacts/{bad['id']}/verify", headers=adm).json()["integrity"] == "FAIL"
    # GAP-T08 restore precheck: wrong model rejected, same model passes
    other = dev(live, adm, "LIVE-SW-6300", model="CX 6300M")
    pre = live.post(f"/api/v1/vault/artifacts/{good.json()['id']}/restore-validation", headers=adm,
                    json={"device_id": other}).json()
    assert pre["result"] == "FAIL" and pre["device_writes"] == "LOCKED"
    same = dev(live, adm, "LIVE-SW-SPARE")
    ok = live.post(f"/api/v1/vault/artifacts/{good.json()['id']}/restore-validation", headers=adm,
                   json={"device_id": same}).json()
    assert ok["result"] == "PASS" and ok["restore_state"] == "PRECHECK_PASSED"
    vers = live.get(f"/api/v1/devices/{did}/config-versions", headers=adm).json()
    assert len(vers["full_fidelity"]) == 2


def test_change_plan_two_person_concurrency_and_gate(live, adm):
    did = dev(live, adm, "LIVE-SW-CP")
    eng = make_user(live, adm, "net.eng", "operator")
    appr = make_user(live, adm, "chg.mgr", "approver")
    appr2 = make_user(live, adm, "chg.mgr2", "approver")
    # GAP-T06: 80 MHz on an AP refused before any adapter
    ap = dev(live, adm, "LIVE-AP-CP", "access_point")
    assert live.post("/api/v1/changes/plans", headers=eng, json={
        "device_id": ap, "title": "Wide", "operations": [{"op": "ap.radio.width", "value": 80}]}).status_code == 422
    p = live.post("/api/v1/changes/plans", headers=eng, json={
        "device_id": did, "title": "Voice VLAN on 1/1/4",
        "operations": [{"op": "switch.port.access_vlan", "port": "1/1/4", "value": 200}]}).json()
    assert p["state"] == "PENDING_APPROVAL" and p["capability_report"][0]["capability"] == "UNCERTIFIED"
    assert live.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=eng,
                     json={"expected_revision": 1}).status_code == 403  # operator role
    a = live.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=appr, json={"expected_revision": 1})
    assert a.status_code == 200 and a.json()["approver"] == "chg.mgr"
    assert live.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=appr2,
                     json={"expected_revision": 1}).status_code == 409  # cannot double approve
    ex = live.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex["decision"] == "DENY" and ex["adapter_called"] is False and ex["device_writes"] == 0
    assert "adapter_certified_for_every_op" in ex["denial_reason"]
    # self approval by requester even with approver role
    selfp = live.post("/api/v1/changes/plans", headers=adm, json={
        "device_id": did, "title": "Self", "operations": [{"op": "switch.vlan.define", "value": 300}]}).json()
    assert live.post(f"/api/v1/changes/plans/{selfp['id']}/approve", headers=adm,
                     json={"expected_revision": 1}).status_code == 403
    assert live.get("/api/v1/changes/decisions", headers=appr).json()[0]["decision"] == "DENY"


def test_optimization_is_recommend_or_shadow_only(live, adm):
    """GAP-T17..T24 with the platform feature OFF (default): DEVICE_OPT_IN refused, shadow only."""
    ap = dev(live, adm, "LIVE-AP-OPT", "access_point")
    other = dev(live, adm, "LIVE-AP-OTHER", "access_point")
    appr = make_user(live, adm, "opt.appr", "approver")
    st = live.get(f"/api/v1/devices/{ap}/optimization", headers=adm).json()
    assert st["mode"] == "RECOMMEND_ONLY" and st["badge"] == "AUTO OFF" and st["inherited"] is False
    exp = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    body = {"serial": "LIVE-AP-OPT", "mode": "DEVICE_OPT_IN", "allowed_operation_types": ["ap.radio.channel"],
            "cooldown_seconds": 3600, "max_actions_per_day": 1, "expires_at_utc": exp}
    assert live.post(f"/api/v1/devices/{ap}/optimization/enroll", headers=adm, json=body).status_code == 422
    body["mode"] = "SHADOW"
    assert live.post(f"/api/v1/devices/{ap}/optimization/enroll", headers=adm,
                     json=dict(body, serial="SPOOF")).status_code == 422
    e = live.post(f"/api/v1/devices/{ap}/optimization/enroll", headers=adm, json=body).json()
    assert live.post(f"/api/v1/optimization/enrollments/{e['id']}/approve", headers=adm).status_code == 403  # self
    assert live.post(f"/api/v1/optimization/enrollments/{e['id']}/approve", headers=appr).status_code == 409  # no backup
    live.post(f"/api/v1/vault/devices/{ap}/artifacts", headers=adm, json={"content": "name ap\narm\n"})
    assert live.post(f"/api/v1/optimization/enrollments/{e['id']}/approve", headers=appr).json()["status"] == "ARMED"
    ev = live.post(f"/api/v1/devices/{ap}/optimization/evaluate", headers=adm,
                   json={"op": {"op": "ap.radio.channel", "band": "5", "value": 36}}).json()
    assert ev["decision"] == "DENY" and "feature is off" in ev["denial_reason"] and ev["adapter_called"] is False
    assert live.get(f"/api/v1/devices/{other}/optimization", headers=adm).json()["mode"] == "RECOMMEND_ONLY"
    # emergency stop pauses; disable revokes immediately
    assert live.post("/api/v1/optimization/emergency-stop", headers=adm,
                     json={"active": True, "reason": "drill"}).json()["enrollments_paused"] == 1
    assert live.post(f"/api/v1/devices/{ap}/optimization/disable", headers=adm).json()["badge"] == "AUTO OFF"
    sim = live.post("/api/v1/ai/evaluation/shadow", headers=adm, json={"cooldown_seconds": 1800, "degrade_after": 2,
        "proposals": [{"at": (datetime.now(timezone.utc) + timedelta(minutes=10 * i)).isoformat(),
                       "op": "ap.radio.channel"} for i in range(6)]}).json()
    assert sim["device_writes"] == 0 and sim["stopped"]


def test_replacement_and_successor_starts_off(live, adm):
    old = dev(live, adm, "LIVE-SW-OLD")
    new = dev(live, adm, "LIVE-SW-NEW")
    wrong = dev(live, adm, "LIVE-SW-WRONG", model="CX 6300M")
    live.post(f"/api/v1/vault/devices/{old}/artifacts", headers=adm, json={"content": GOOD_CX})
    bad = live.post("/api/v1/replacements/plan", headers=adm, json={"old_device_id": old, "new_device_id": wrong}).json()
    assert bad["state"] == "BLOCKED"
    ok = live.post("/api/v1/replacements/plan", headers=adm, json={"old_device_id": old, "new_device_id": new,
                   "identity_map": {"mgmt_ip": {"old": "10.0.0.1", "new": "10.0.0.2"}}}).json()
    assert ok["state"] == "LAB_VALIDATION_REQUIRED" and ok["identity_map"]["serial"]["new"] == "LIVE-SW-NEW"
    assert live.get(f"/api/v1/devices/{new}/optimization", headers=adm).json()["mode"] == "RECOMMEND_ONLY"
    assert len(live.get("/api/v1/replacements", headers=adm).json()) == 2


def test_learning_feedback_outcome_and_training_refusal(live, adm):
    live.post("/api/v1/demo-data/load", headers=adm)
    hist = live.get("/api/v1/ai/learning-history", headers=adm).json()
    rec = next(i for i in hist["items"] if i["feedback"] is None)
    assert rec["kind"] == "RULE_BASED"
    assert live.post("/api/v1/ai/feedback", headers=adm, json={
        "recommendation_id": rec["id"], "decision": "REJECTED", "reason": "Event day"}).status_code == 201
    assert live.post("/api/v1/ai/feedback", headers=adm, json={
        "recommendation_id": rec["id"], "decision": "ACCEPTED", "reason": "again"}).status_code == 409
    t = datetime.now(timezone.utc)
    o = live.post("/api/v1/ai/outcomes", headers=adm, json={
        "recommendation_id": rec["id"], "metric": "util_5g_percent", "higher_is_better": False,
        "before_start": (t - timedelta(hours=20)).isoformat(), "before_end": (t - timedelta(hours=12)).isoformat(),
        "after_start": (t - timedelta(hours=12)).isoformat(), "after_end": t.isoformat(), "action_applied": False})
    assert o.status_code == 201 and o.json()["success"] is None   # no action -> no success claim
    tr = live.post("/api/v1/ai/models/train", headers=adm).json()
    assert tr["status"] == "NOT_TRAINED"
    assert live.get("/api/v1/ai/models", headers=adm).json() == []


def test_adapters_reports_topology_and_printers(live, adm):
    did = dev(live, adm, "LIVE-CISCO", model="Catalyst 9300")
    with session_scope() as db:
        from backend.database.models_inm import InmDevice
        db.get(InmDevice, did).vendor = "Cisco"
    a = live.get(f"/api/v1/devices/{did}/adapter", headers=adm).json()
    assert a["adapter"]["key"] == "cisco_ios_xe_snmpv3"
    assert {o["status"] for o in a["operations"]} <= {"UNSUPPORTED", "DENIED_BY_CENTRAL_POLICY"}
    ex = live.post("/api/v1/reports/exports", headers=adm, json={
        "kind": "audit", "start": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "end": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}).json()
    assert ex["row_count"] > 0 and ex["meta"]["identities"] == "MASKED"
    assert live.post("/api/v1/reports/exports/verify", headers=adm, json=ex).json()["valid"] is True
    assert live.post("/api/v1/reports/exports/verify", headers=adm, json=dict(ex, body=ex["body"] + "x")).json()["valid"] is False
    topo = live.get("/api/v1/topology/roles", headers=adm).json()
    assert len(topo["roles"]) == 7 and "password" not in str(topo).lower()
    assert live.post("/api/v1/printers/counter-check", headers=adm,
                     json={"previous": 4294967290, "current": 4}).json()["kind"] == "ROLLOVER"
    assert live.get("/api/v1/printers/mib-packs", headers=adm).json()["write_oids_permitted"] is False
