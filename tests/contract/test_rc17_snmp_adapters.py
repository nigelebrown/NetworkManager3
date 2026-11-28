"""rc17 read-only non-Aruba adapters through the real collection pipeline with an isolated emulated SNMP agent
(EMULATED; no network): IF-MIB ports and 64-bit counters, ENTITY serial, LLDP, Cisco CPU labelled unverified,
missing objects omitted, unreachable agent -> UNREACHABLE, adapter refuses writes."""
from datetime import datetime, timedelta, timezone

from backend.printers.collector import EmulatedPrinterAgent
from backend.infrastructure.adapters import snmp_switch as SS
from tests.conftest import login

IF = {"name": SS.IF_NAME, "type": SS.IF_TYPE, "admin": SS.IF_ADMIN, "oper": SS.IF_OPER, "hin": SS.IF_HC_IN,
      "hout": SS.IF_HC_OUT, "ein": SS.IF_IN_ERR, "eout": SS.IF_OUT_ERR, "speed": SS.IF_HIGH_SPEED}


def cisco_agent(rx=1000):
    v = {SS.SYS_DESCR: "Cisco IOS XE Software, Catalyst 9300", SS.SYS_NAME: "c9300-lab",
         SS.ENT_SERIAL + ".1": "FOC12345ABC", SS.ENT_MODEL + ".1": "C9300-48P",
         SS.LLDP_REM_SYSNAME + ".0.1.1": "ap-1", SS.CISCO_CPU_5MIN + ".1": 17}
    for i, (name, typ, oper) in enumerate((("Gi1/0/1", 6, 1), ("Gi1/0/2", 6, 2), ("Vlan10", 53, 1)), start=1):
        v |= {f"{IF['name']}.{i}": name, f"{IF['type']}.{i}": typ, f"{IF['admin']}.{i}": 1, f"{IF['oper']}.{i}": oper,
              f"{IF['hin']}.{i}": rx * i, f"{IF['hout']}.{i}": 2 * rx * i, f"{IF['speed']}.{i}": 1000}
    v[f"{IF['ein']}.1"] = 0                       # Gi1/0/2 reports no error counters: must stay absent, not zero
    v[f"{IF['eout']}.1"] = 0
    return EmulatedPrinterAgent(v)


def test_snmp_switch_collection_pipeline(client, admin, monkeypatch):
    monkeypatch.setenv("INM_SNMP_EMULATION", "true")
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.77.0.0/24")
    from backend.infrastructure.runtime import telemetry_store
    telemetry_store().migrate()
    agent = cisco_agent()
    SS.EMULATORS["10.77.0.5"] = agent
    did = client.post("/api/v1/quick-devices", headers=admin, json={
        "site_name": "HQ", "family": "switch", "hostname": "c9300-lab", "serial_number": "FOC12345ABC",
        "model": "Catalyst 9300", "management_ip": "10.77.0.5", "status": "unknown", "metrics": {}}).json()["id"]
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    with session_scope() as db:
        db.get(InmDevice, did).vendor = "Cisco"
    plan = client.post("/api/v1/collection/plans", headers=admin, json={
        "device_id": did, "metric_families": ["system", "interfaces"], "interval_seconds": 300})
    assert plan.status_code == 201, plan.text
    assert plan.json()["adapter_key"] == "cisco_ios_xe_snmpv3"
    from backend.infrastructure.collection_runner import run_due
    t0 = datetime.now(timezone.utc) - timedelta(minutes=12)
    with session_scope() as db:
        s1 = run_due(db, now=t0, store=telemetry_store(), worker="t", plan_ids=[plan.json()["id"]])
    assert s1["succeeded"] == 1, s1
    for i in (1, 2, 3):
        agent.values[f"{IF['hin']}.{i}"] += 75_000_000                 # ~1 Mbit/s over 10 minutes
    with session_scope() as db:
        s2 = run_due(db, now=t0 + timedelta(minutes=10), store=telemetry_store(), worker="t", plan_ids=[plan.json()["id"]])
    assert s2["succeeded"] == 1
    ports = {p["name"]: p for p in client.get(f"/api/v1/devices/{did}/ports", headers=admin).json()["ports"]}
    assert set(ports) == {"Gi1/0/1", "Gi1/0/2"}                         # SVI (ifType 53) excluded
    assert ports["Gi1/0/1"]["link"] == "up" and ports["Gi1/0/2"]["link"] == "down"
    assert ports["Gi1/0/2"]["rx_errors"] is None                        # missing counter is not zero
    assert ports["Gi1/0/1"]["rates"]["rx_bps"] > 900_000
    series = client.get(f"/api/v1/devices/{did}/metrics/series?metric=cpu_percent&hours=1", headers=admin).json()
    assert series["points"][-1]["v"] == 17.0
    assert all(op == "GET" or op == "GETNEXT" for op, _ in agent.requests)   # read-only protocol operations only
    agent.reachable = False
    with session_scope() as db:
        s3 = run_due(db, now=t0 + timedelta(minutes=20), store=telemetry_store(), worker="t", plan_ids=[plan.json()["id"]])
    assert s3["succeeded"] == 0 and (s3["retry"] + s3["dlq"]) >= 1


def test_snmp_collection_plan_binds_a_saved_snmp_profile(client, admin, monkeypatch):
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.77.0.0/24")
    did = client.post("/api/v1/quick-devices", headers=admin, json={
        "site_name": "HQ", "family": "switch", "hostname": "core-snmp", "serial_number": "SNMP-GEN-1",
        "model": "Generic L3", "management_ip": "10.77.0.9", "status": "unknown", "metrics": {}}).json()["id"]
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    with session_scope() as db:
        db.get(InmDevice, did).vendor = "Cisco"
    bad = client.post("/api/v1/collection/plans", headers=admin, json={
        "device_id": did, "metric_families": ["system"], "interval_seconds": 300, "snmp_profile_id": "nope"})
    assert bad.status_code == 422 and "profile not found" in bad.json()["detail"]
    prof = client.post("/api/printers/credential-profiles", headers=admin, json={
        "name": "Switch SNMPv3", "version": "3", "username": "inm-ro", "auth_passphrase": "switch-auth-pass",
        "privacy_passphrase": "switch-priv-pass"}).json()
    plan = client.post("/api/v1/collection/plans", headers=admin, json={
        "device_id": did, "metric_families": ["system"], "interval_seconds": 300, "snmp_profile_id": prof["id"]})
    assert plan.status_code == 201, plan.text
    from backend.database.models_phases import CollectionPlan
    with session_scope() as db:
        assert db.get(CollectionPlan, plan.json()["id"]).credential_profile_id == prof["id"]
