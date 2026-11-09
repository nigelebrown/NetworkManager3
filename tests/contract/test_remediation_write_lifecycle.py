"""Audit E2E-08/12/14/20 on the strict emulators (EMULATED — software evidence only, never hardware):

* capability registry == implemented writer operations; per-device capability report from certification records
* new typed operations (switch security/QoS/system, SSID create, 802.1X SSID) through certify -> plan -> approve ->
  apply -> readback, with secrets never persisted
* process crash mid-change -> durable encrypted journal -> RECOVERY_REQUIRED -> recovery with a NEW writer
  instance restores the exact pre-change configuration (CX checkpoint and Instant inverse operations)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.database.session import session_scope
from backend.infrastructure import change_executor as X
from tests.contract.test_production_read_write import approve, certify, env, live, plan  # noqa: F401


def test_registry_matches_writers_and_never_claims_unimplemented_or_uncertified():
    from backend.domain import adapters as AD
    from backend.infrastructure.writers.aruba_cx import ArubaCxWriter
    from backend.infrastructure.writers.aruba_instant import ArubaInstantWriter
    cx = next(s for s in AD.REGISTRY if s.key == ArubaCxWriter.key)
    iap = next(s for s in AD.REGISTRY if s.key == ArubaInstantWriter.key)
    assert cx.typed_operations == ArubaCxWriter.OPERATIONS and iap.typed_operations == ArubaInstantWriter.OPERATIONS
    for spec in (cx, iap):
        assert spec.operations["apply_change"] == "IMPLEMENTED" and spec.operations["backup_full_config"] == "IMPLEMENTED"
        assert spec.certification == "UNCERTIFIED"
    assert cx.operations["restore_full_config"] == "IMPLEMENTED" and iap.operations["restore_full_config"] == "GUIDED"
    for spec in AD.REGISTRY:
        if spec.vendor in ("Cisco", "Ubiquiti", "Generic"):
            assert not spec.implemented and "PLACEHOLDER" in spec.notes
            assert AD.operation_result(spec, "collect")["status"] == "UNSUPPORTED"
    # every typed op the writers declare is accepted by domain validation for its family
    from backend.domain import change_control as CC
    assert set(ArubaCxWriter.OPERATIONS) <= CC.SWITCH_OPERATIONS
    assert set(ArubaInstantWriter.OPERATIONS) <= CC.AP_OPERATIONS


def test_device_capability_report_follows_certification_records(env):
    c, adm, appr, sw, ap = env
    before = c.get(f"/api/v1/devices/{sw['id']}/capabilities", headers=adm).json()
    assert before["environment"] == "EMULATED" and before["hardware_certified"] is False
    assert {r["certification"] for r in before["operations"]} == {"UNCERTIFIED"}
    assert not any(r["executable_now"] for r in before["operations"])
    assert len(before["operations"]) == 16 and all(r["software"] == "IMPLEMENTED" for r in before["operations"])
    certify(c, adm, appr, sw, test_port="1/1/20", lag_ports=["1/1/21", "1/1/22"])
    after = c.get(f"/api/v1/devices/{sw['id']}/capabilities", headers=adm).json()
    st = {r["operation"]: r for r in after["operations"]}
    # switch.central.disable is only certifiable on a lab switch that is actually Central-managed
    assert st.pop("switch.central.disable")["certification"] == "UNCERTIFIED"
    assert all(r["certification"] == "CERTIFIED_WRITE" and r["executable_now"] for r in st.values())
    assert after["hardware_certified"] is False                      # emulated certification is never hardware
    apcap = c.get(f"/api/v1/devices/{ap['id']}/capabilities", headers=adm).json()
    restore = next(r for r in apcap["operations"] if r["operation"] == "config.restore")
    assert restore["software"] == "GUIDED" and restore["executable_now"] is False
    matrix = c.get("/api/v1/capability-matrix", headers=adm).json()
    assert any(g["environment"] == "EMULATED" and g["operations"].get("switch.system.ntp") == "CERTIFIED_WRITE"
               for g in matrix["certifications"])
    # a REAL device of the same model is still uncertified (different environment) and locked
    real = c.post("/api/v1/quick-devices", headers=adm, json={
        "site_name": "HQ", "family": "switch", "hostname": "real-1", "serial_number": "REAL-CX-1",
        "model": sw["model"], "firmware": sw["firmware"], "management_ip": "10.1.1.1", "status": "up",
        "metrics": {}}).json()["id"]
    rc = c.get(f"/api/v1/devices/{real}/capabilities", headers=adm).json()
    assert rc["environment"] == "HARDWARE" and {r["certification"] for r in rc["operations"]} == {"UNCERTIFIED"}
    assert rc["write_posture"]["hardware_writes"] == "LOCKED"


def test_new_switch_and_wireless_operations_end_to_end_with_secret_hygiene(env):
    c, adm, appr, sw, ap = env
    certify(c, adm, appr, sw, test_port="1/1/20")
    certify(c, adm, appr, ap)
    p = approve(c, appr, plan(c, adm, sw, [
        {"op": "switch.port.description", "port": "1/1/5", "value": "Printer room"},
        {"op": "switch.port.stp_bpdu_guard", "port": "1/1/5", "value": "enabled"},
        {"op": "switch.port.storm_control", "port": "1/1/5", "broadcast_kbps": 2000},
        {"op": "switch.port.qos_trust", "port": "1/1/5", "value": "dscp"},
        {"op": "switch.system.ntp", "value": "10.10.0.10"},
        {"op": "switch.system.dns", "value": ["10.10.0.53", "10.10.0.54"], "domain": "jcf.local"},
        {"op": "switch.system.syslog", "value": "10.10.0.20", "severity": "warning"}], "Harden access port"))
    r = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert r["outcome"] == "APPLIED_VERIFIED", r
    emu = X._EMULATORS[sw["id"]]
    port = emu.state["interfaces"]["1/1/5"]
    assert port["description"] == "Printer room" and port["qos_trust"] == "dscp"
    assert port["stp_config"]["bpdu_guard_enable"] is True and port["rate_limits"] == {"broadcast": 2000}
    assert emu.state["vrfs"]["mgmt"]["dns_name_servers"] == {"0": "10.10.0.53", "1": "10.10.0.54"}
    # policy: plans may not carry secrets; bad values refused before any device contact
    for bad in ({"op": "ap.ssid.create", "ssid": "Guest", "vlan": 30, "passphrase": "x" * 12},
                {"op": "ap.ssid.enterprise", "ssid": "Corp1X", "vlan": 20, "radius_name": "nps",
                 "radius_ip": "not-an-ip", "secret_ref": "x"},
                {"op": "switch.port.qos_trust", "port": "1/1/5", "value": "ip-precedence"}):
        dev = ap if bad["op"].startswith("ap.") else sw
        assert c.post("/api/v1/changes/plans", headers=adm, json={"device_id": dev["id"], "title": "bad",
                                                                   "operations": [bad]}).status_code == 422
    # secrets come from the encrypted credential library by reference
    psk = "Guest-Passphrase-9931"
    radius = "Radius-Shared-Secret-7720"
    refs = {}
    for name, secret in (("guest-psk", psk), ("nps-radius", radius)):
        refs[name] = c.post("/api/v1/onboarding/credentials", headers=adm, json={
            "name": name, "device_family": "access_point", "protocol": "aruba_instant_rest",
            "username": "secret", "password": secret}).json()["id"]
    p2 = approve(c, appr, plan(c, adm, ap, [
        {"op": "ap.ssid.create", "ssid": "Guest", "essid": "JCF-Guest", "vlan": 30, "security": "wpa2-psk-aes",
         "secret_ref": refs["guest-psk"]},
        {"op": "ap.ssid.enterprise", "ssid": "Corp1X", "essid": "JCF-Corp", "vlan": 20, "radius_name": "nps1",
         "radius_ip": "10.10.0.30", "security": "wpa2-enterprise", "secret_ref": refs["nps-radius"]}], "WLANs"))
    preview = c.get(f"/api/v1/changes/plans/{p2['id']}/render", headers=adm).text
    assert psk not in preview and radius not in preview and "********" in preview
    r2 = c.post(f"/api/v1/changes/plans/{p2['id']}/execute", headers=adm).json()
    assert r2["outcome"] == "APPLIED_VERIFIED", r2
    text = X._EMULATORS[ap["id"]].text
    assert "wlan ssid-profile Corp1X" in text and " opmode wpa2-aes" in text and " auth-server nps1" in text
    assert psk not in text and radius not in text               # device stores encrypted forms only
    # nothing persisted anywhere contains either secret
    from backend.database.models_inm import EventRecord
    from backend.database.models_phases import ChangeExecution, ChangePlan, DeviceCertification
    from backend.database.models_remediation import EventOutbox
    with session_scope() as db:
        blobs = [json.dumps(x.steps) for x in db.scalars(select(ChangeExecution))]
        blobs += [json.dumps(x.operations) for x in db.scalars(select(ChangePlan))]
        blobs += [json.dumps(x.evidence) for x in db.scalars(select(DeviceCertification))]
        blobs += [x.envelope for x in db.scalars(select(EventOutbox))]
        blobs += [str(x.summary) for x in db.scalars(select(EventRecord))]
    assert blobs and not any(psk in b or radius in b for b in blobs)
    audit = c.get("/api/v1/audit?limit=500", headers=adm)
    assert psk not in audit.text and radius not in audit.text


class ProcessDied(BaseException):
    """Simulates the API/worker process being killed (not an exception the executor can handle)."""


@pytest.mark.parametrize("family", ["switch", "access_point"])
def test_crash_mid_change_is_recovered_from_the_durable_journal(env, family):
    c, adm, appr, sw, ap = env
    dev = sw if family == "switch" else ap
    certify(c, adm, appr, dev, **({"test_port": "1/1/20"} if family == "switch" else {}))
    ops = ([{"op": "switch.vlan.define", "value": 777}, {"op": "switch.port.description", "port": "1/1/9",
                                                          "value": "never finished"}]
           if family == "switch" else
           [{"op": "ap.client_match", "value": "disabled"}, {"op": "ap.band_steering", "value": "force-5ghz"}])
    p = approve(c, appr, plan(c, adm, dev, ops, "Will crash"))
    emu = X._EMULATORS[dev["id"]]
    before = json.dumps(emu.state, sort_keys=True) if family == "switch" else emu.text
    writer = emu.writer()
    real_apply = writer.apply
    calls = {"n": 0}

    def dying_apply(op):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ProcessDied()          # first op is already on the device
        return real_apply(op)
    writer.apply = dying_apply
    from backend.database.models_phases import ChangePlan
    with pytest.raises(ProcessDied):
        with session_scope() as db:
            X.execute_plan(db, db.get(ChangePlan, p["id"]), actor="admin", writer=writer)
    changed = json.dumps(emu.state, sort_keys=True) if family == "switch" else emu.text
    assert changed != before                                       # the device really is half-changed
    j = c.get("/api/v1/recovery/journal", headers=adm).json()
    entry = next(x for x in j if x["plan_id"] == p["id"])
    assert entry["state"] == "OPEN" and entry["last_step"] == f"apply:{ops[1]['op']}"
    assert entry["strategy"] == ("DEVICE_CHECKPOINT" if family == "switch" else "INVERSE_OPERATIONS")
    with session_scope() as db:
        plan_row = db.get(ChangePlan, p["id"])
        assert plan_row.state == "EXECUTING"                       # committed before the first write
        from backend.database.models_remediation import ChangeRecoveryJournal
        row = db.get(ChangeRecoveryJournal, entry["id"])
        assert "777" not in row.payload_enc and "client" not in row.payload_enc   # encrypted at rest
        row.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    j = c.get("/api/v1/recovery/journal", headers=adm).json()
    entry = next(x for x in j if x["plan_id"] == p["id"])
    assert entry["state"] == "RECOVERY_REQUIRED"
    alerts = c.get("/api/v1/alerts", headers=adm).json()
    assert any(a["rule_id"] == "CHANGE_INTERRUPTED" and a["device_id"] == dev["id"] for a in alerts)
    # a different process: brand-new writer instance, nothing in memory from the dead execution
    out = c.post(f"/api/v1/recovery/journal/{entry['id']}/recover", headers=adm).json()
    assert out["recovered"] is True, out
    after = json.dumps(emu.state, sort_keys=True) if family == "switch" else emu.text
    from backend.infrastructure.change_executor import _norm
    assert _norm(after.encode(), family) == _norm(before.encode(), family)
    assert c.post(f"/api/v1/recovery/journal/{entry['id']}/recover", headers=adm).status_code == 409
    alerts = c.get("/api/v1/alerts", headers=adm).json()
    assert not any(a["rule_id"] == "CHANGE_INTERRUPTED" and a["device_id"] == dev["id"] for a in alerts)
    guide = c.get(f"/api/v1/devices/{dev['id']}/guided-restore", headers=adm).json()
    assert guide["automatic"] is (family == "switch")


def test_out_of_band_change_after_approval_stops_execution_until_renewed_review(env):
    """P6-06: someone edits the switch directly after the plan was approved. Execution must not write on top of an
    unreviewed configuration: no device write, plan back to PENDING_APPROVAL with a new revision and baseline."""
    c, adm, appr, sw, _ = env
    certify(c, adm, appr, sw, test_port="1/1/20")
    p = approve(c, appr, plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 640}], "VLAN 640"))
    emu = X._EMULATORS[sw["id"]]
    emu.state["vlans"]["777"] = {"id": 777, "name": "added-at-the-console"}       # out-of-band edit
    writes_before = emu.writes
    ex = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    oob = next(s for s in ex["steps"] if s["step"] == "out_of_band_check")
    assert oob["ok"] is False and oob["observed_sha256"] != oob["approved_sha256"]
    assert ex["outcome"] == "ABORTED" and emu.writes == writes_before and "640" not in emu.state["vlans"]
    assert any(s.get("category") == "STALE_APPROVAL" for s in ex["steps"])
    renewed = next(x for x in c.get("/api/v1/changes/plans", headers=adm).json() if x["id"] == p["id"])
    assert renewed["state"] == "PENDING_APPROVAL" and renewed["revision"] == p["revision"] + 1
    assert renewed["expected_config_hash"] == oob["observed_sha256"]
    # the old approval cannot be replayed; a fresh review against the current configuration can proceed
    assert c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()["decision"] == "DENY"
    approve(c, appr, renewed)
    ex2 = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex2["outcome"] == "APPLIED_VERIFIED", ex2
    assert next(s for s in ex2["steps"] if s["step"] == "out_of_band_check")["ok"] is True
    assert "640" in emu.state["vlans"] and "777" in emu.state["vlans"]           # console edit preserved


def test_snapshot_retrieval_failure_is_recorded_and_last_good_snapshot_stays_verifiable(env):
    """P5-05: the scheduled read of the running configuration fails. The failure is visible on the schedule and in
    the audit trail, no partial artifact is sealed, and the last good snapshot is still listed and hash-verified."""
    from backend.api.production_ops import run_due_captures
    c, adm, _, sw, _ = env
    assert c.post(f"/api/v1/vault/devices/{sw['id']}/capture-from-device", headers=adm).status_code in (200, 201)
    good = c.get(f"/api/v1/devices/{sw['id']}/config-versions", headers=adm).json()
    good = good["full_fidelity"]
    assert good
    c.post(f"/api/v1/vault/devices/{sw['id']}/schedule", headers=adm, json={"interval_hours": 1})
    X._EMULATORS[sw["id"]].fail_read = "fullconfigs"                  # the device answers HTTP 500 to the read
    with session_scope() as db:
        assert run_due_captures(db)["failed"] == 1
    sched = next(s for s in c.get("/api/v1/vault/schedules", headers=adm).json() if s["device_id"] == sw["id"])
    assert sched["last_result"].startswith("FAILED")
    after = c.get(f"/api/v1/devices/{sw['id']}/config-versions", headers=adm).json()
    after = after["full_fidelity"]
    assert len(after) == len(good)                                    # nothing partial was sealed
    v = c.post(f"/api/v1/vault/artifacts/{good[0]['id']}/verify", headers=adm).json()
    assert v["integrity"] == "PASS", v
    X._EMULATORS[sw["id"]].fail_read = None
