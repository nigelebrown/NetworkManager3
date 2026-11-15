"""Printer Intelligence SRS — end-to-end contract tests against emulated SNMP printers.

Real migrations, real REST API, real service/poller code; only the network is replaced by an in-memory
read-only agent (source = EMULATED). No hardware result is claimed by these tests.
"""
import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_user
from backend.printers import collector as C
from backend.printers import mib
from backend.printers import service as S

KYO_TOTAL = "1.3.6.1.4.1.1347.43.10.1.1.12.1.1"
KYO_BW = "1.3.6.1.4.1.1347.42.3.1.2.1.1.1.1"
KYO_COL = "1.3.6.1.4.1.1347.42.3.1.2.1.1.1.3"
LIFE = mib.PRT_MARKER_LIFE + ".1.1"


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def env(client, admin, monkeypatch):
    monkeypatch.setenv("INM_PRINTER_EMULATION", "true")
    S.EMULATORS.clear()
    clock = Clock(datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(S, "now_utc", clock)
    yield client, admin, clock
    S.EMULATORS.clear()


def agent(ip, **kw):
    a = C.EmulatedPrinterAgent(C.emulated_printer(**kw))
    S.EMULATORS[ip] = a
    return a


def cred_profile(client, admin, version="3"):
    body = {"name": f"prn-v{version}", "version": version}
    body |= ({"username": "inm-ro", "auth_passphrase": "auth-secret-1", "privacy_passphrase": "priv-secret-1"}
             if version == "3" else {"community": "ro-community"})
    r = client.post("/api/printers/credential-profiles", headers=admin, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def onboard(client, admin, ip, serial, model="ECOSYS MA4000cix", manufacturer="Kyocera", **kw):
    agent(ip, serial=serial, model=model, manufacturer=manufacturer, **kw)
    pid = client.post("/api/printers", headers=admin, json={"manufacturer": manufacturer, "model": model,
                                                            "serial_number": serial, "management_ip": ip}).json()["id"]
    r = client.put(f"/api/printers/{pid}/monitoring", headers=admin, json={"enabled": True, "interval_seconds": 900})
    assert r.status_code == 200, r.text
    return pid


def run_due():
    from backend.database.session import session_scope
    return asyncio.run(S.run_due(session_scope, "test-worker"))


# ---- discovery → approval → monitoring ------------------------------------------------------------
def test_discovery_job_progress_results_and_per_address_errors(env):
    client, admin, clock = env
    cid = cred_profile(client, admin)
    agent("10.103.50.10", serial="KY-001", impressions=5000, supplies=[{"descr": "TK-5345K Black", "level": 70}])
    S.EMULATORS["10.103.50.11"] = C.EmulatedPrinterAgent({mib.SYS_DESCR: "Linux switch", mib.SYS_OBJECT_ID: "1.3.6.1.4.1.8072.3"})
    S.EMULATORS["10.103.50.12"] = C.EmulatedPrinterAgent({}, reachable=False)
    r = client.post("/api/printers/discovery", headers=admin, json={
        "credential_profile_id": cid, "management_ips": ["10.103.50.10", "10.103.50.11", "10.103.50.12"]})
    assert r.status_code == 202, r.text
    job = client.get(f"/api/printers/jobs/{r.json()['id']}", headers=admin).json()
    assert job["status"] == "SUCCEEDED" and job["progress"] == 3
    found = job["result"]["printers"]
    assert [p["management_ip"] for p in found] == ["10.103.50.10"]
    assert found[0]["serial_number"] == "KY-001" and found[0]["model"] == "ECOSYS MA4000cix"
    assert found[0]["confidence"] == "HIGH" and found[0]["source"] == "EMULATED"
    errs = {e["address"]: e["error"] for e in job["errors"]}
    assert "not a printer" in errs["10.103.50.11"] and "no SNMP response" in errs["10.103.50.12"]
    assert client.get("/api/printers", headers=admin).json() == []        # discovery never auto-onboards
    ok = client.post(f"/api/printers/discovery/{job['id']}/approve", headers=admin, json={"items": [
        {"management_ip": "10.103.50.10", "manufacturer": "Kyocera", "model": "ECOSYS MA4000cix", "serial_number": "KY-001"}]})
    assert ok.status_code == 200 and len(ok.json()["created"]) == 1
    bad = client.post(f"/api/printers/discovery/{job['id']}/approve", headers=admin, json={"items": [
        {"management_ip": "10.103.50.11", "manufacturer": "X", "model": "Y", "serial_number": "Z"}]})
    assert bad.status_code == 422
    listed = client.get("/api/printers?monitored=true", headers=admin).json()
    assert listed[0]["monitoring"]["enabled"] is True and listed[0]["monitoring"]["freshness"] == "NEVER_POLLED"


def test_discovery_cancellation_and_snmp_policy(env):
    client, admin, _ = env
    v2 = cred_profile(client, admin, "2c")
    v1 = cred_profile(client, admin, "1")
    agent("10.103.51.10", serial="A")
    assert client.post("/api/printers/discovery", headers=admin,
                       json={"credential_profile_id": v1, "management_ips": ["10.103.51.10"]}).status_code == 403
    r = client.post("/api/printers/discovery", headers=admin, json={"credential_profile_id": v2, "management_ips": ["10.103.51.10"]})
    assert r.status_code == 202 and r.json()["warnings"]
    from backend.database.session import session_scope
    with session_scope() as db:
        j = S.create_job(db, "DISCOVERY", {"hosts": ["10.103.51.10"], "credential_profile_id": v2}, "admin")
        jid = j.id
    assert client.post(f"/api/printers/jobs/{jid}/cancel", headers=admin).json()["status"] == "CANCELLED"
    assert client.post(f"/api/printers/jobs/{jid}/cancel", headers=admin).status_code == 409
    client.put("/api/printers/settings", headers=admin, json={"allow_v2c": False})
    assert client.post("/api/printers/discovery", headers=admin,
                       json={"credential_profile_id": v2, "management_ips": ["10.103.51.10"]}).status_code == 403


# ---- polling, provenance, capabilities, intervals -------------------------------------------------
def test_poll_persists_provenance_capabilities_and_validated_intervals(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.52.10", "KY-100", impressions=10000, bw=7000, colour=3000,
                  supplies=[{"descr": "TK-5345K Black Toner", "level": 60}, {"type": 4, "class": 4, "descr": "Waste toner box", "level": -3}])
    r = client.post(f"/api/printers/{pid}/poll", headers=admin)
    assert r.status_code == 202
    assert client.get(f"/api/printers/jobs/{r.json()['id']}", headers=admin).json()["status"] == "SUCCEEDED"
    caps = {c["capability"]: c["state"] for c in client.get(f"/api/printers/{pid}/capabilities", headers=admin).json()["capabilities"]}
    assert caps["impressions"] == "SUPPORTED"
    assert caps["mono_counter"] == "UNVERIFIED" and caps["colour_counter"] == "UNVERIFIED"   # vendor OIDs
    assert caps["sheets"] == "UNSUPPORTED" and caps["waste_toner"] == "SUPPORTED"
    samples = client.get(f"/api/printers/{pid}/metrics?metric=marker_life_count", headers=admin).json()["samples"]
    assert samples[0]["raw_value"] == "10000" and samples[0]["source"] == "EMULATED" and samples[0]["oid"] == LIFE
    assert samples[0]["profile"] == "kyocera-printer"
    unsupported = client.get(f"/api/printers/{pid}/metrics?state=UNSUPPORTED", headers=admin).json()["samples"]
    assert any(s["metric_key"] == "copy_impressions" for s in unsupported)
    waste = [c for c in client.get(f"/api/printers/{pid}/consumables", headers=admin).json()["current"] if c["supply_type"] == "waste_toner"][0]
    assert waste["level_state"] == "SOME_REMAINING" and waste["percent_remaining"] is None and waste["status"] == "OK"

    a = S.EMULATORS["10.103.52.10"]
    for i in range(1, 9):                            # 8 polls 15 minutes apart, 100 pages each
        clock.advance(minutes=15)
        a.values[LIFE] = 10000 + 100 * i
        a.values[KYO_TOTAL] = 10000 + 100 * i
        run_due()
    u = client.get(f"/api/printers/{pid}/utilization?granularity=hour&start=2026-09-01T08:00:00Z&end=2026-09-01T10:00:00Z",
                   headers=admin).json()
    assert u["meter_kind"] == "impressions_total" and u["summary"]["total"] == 800
    assert all(b["coverage"] == 1.0 for b in u["buckets"]) and u["buckets"][0]["volume"] == 400
    detail = client.get(f"/api/printers/{pid}", headers=admin).json()
    assert detail["monitoring"]["polls"]["successful"] == 9 and detail["identity"]["identity_state"] == "VERIFIED"
    assert detail["monitoring"]["health"]["status"] == "HEALTHY"


def test_reset_rollover_missing_vs_measured_zero_and_unreachable_backoff(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.53.10", "KY-200", impressions=4294967000)
    a = S.EMULATORS["10.103.53.10"]
    run_due()
    clock.advance(minutes=15); a.values[LIFE] = 200; run_due()                         # 32-bit wrap → ROLLOVER
    clock.advance(minutes=15); run_due()                                              # measured zero
    clock.advance(minutes=15); a.values[LIFE] = 50; run_due()                         # decrease → RESET
    a.reachable = False
    clock.advance(minutes=15); r1 = run_due()
    assert r1["failed"] == 1
    from backend.database.session import session_scope
    from backend.database.models_printer import PrinterMeterReading
    from backend.database.models_printer_srs import PrinterCounterInterval, PrinterMonitoring
    from sqlalchemy import select
    with session_scope() as db:
        q = [i.quality for i in db.scalars(select(PrinterCounterInterval).where(PrinterCounterInterval.printer_id == pid)
                                           .order_by(PrinterCounterInterval.start_at))]
        assert q == ["ROLLOVER", "VALID", "RESET"]
        deltas = [i.delta for i in db.scalars(select(PrinterCounterInterval).order_by(PrinterCounterInterval.start_at))]
        assert deltas == [496, 0, None]
        assert db.scalar(select(PrinterMeterReading.counter_value).order_by(PrinterMeterReading.observed_at.desc()).limit(1)) == 50
        mon = db.get(PrinterMonitoring, pid)
        assert mon.consecutive_failures == 1 and mon.reachability == "UNREACHABLE"
        backoff = mon.backoff_until
    clock.advance(minutes=5)
    assert run_due()["claimed"] == 0                                                  # respects backoff
    clock.t = backoff.replace(tzinfo=timezone.utc) if backoff.tzinfo is None else backoff
    assert run_due()["claimed"] == 1
    u = client.get(f"/api/printers/{pid}/utilization?granularity=hour&start=2026-09-01T08:00:00Z&end=2026-09-01T12:00:00Z",
                   headers=admin).json()
    vols = [b["volume"] for b in u["buckets"]]
    assert vols[0] == 496 and vols[2] is None and vols[3] is None                    # missing ≠ zero
    assert u["interval_quality"] == {"ROLLOVER": 1, "VALID": 1, "RESET": 1}
    faults = client.get(f"/api/printers/{pid}/faults", headers=admin).json()
    assert any(h["dimension"] == "REACHABILITY" and h["state"] == "UNREACHABLE" for h in faults["state_history"])


def test_serial_change_is_kept_separate_and_needs_review(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.54.10", "KY-300", impressions=1000)
    run_due()
    S.EMULATORS["10.103.54.10"].values[mib.PRT_SERIAL + ".1"] = "KY-999"
    S.EMULATORS["10.103.54.10"].values[LIFE] = 20
    clock.advance(minutes=15)
    run_due()
    d = client.get(f"/api/printers/{pid}", headers=admin).json()
    assert d["identity"]["identity_state"] == "CONFLICT" and d["device"]["serial_number"] == "KY-300"
    change = [c for c in d["identity_changes"] if c["field"] == "serial_number"][0]
    assert change["decision"] == "NEEDS_REVIEW"
    from backend.database.session import session_scope
    from backend.database.models_printer_srs import PrinterCounterInterval
    from sqlalchemy import select
    with session_scope() as db:
        assert db.scalar(select(PrinterCounterInterval.quality).where(PrinterCounterInterval.printer_id == pid)) == "REPLACEMENT"
    assert client.post(f"/api/printers/{pid}/identity-changes/{change['id']}/decide", headers=admin,
                       json={"decision": "APPLY", "note": "check"}).status_code == 409
    r = client.post(f"/api/printers/{pid}/identity-changes/{change['id']}/decide", headers=admin,
                    json={"decision": "SEPARATE_DEVICE", "note": "swapped unit"})
    assert r.json()["decision"] == "SEPARATE_DEVICE"
    assert client.get(f"/api/printers/{pid}", headers=admin).json()["monitoring"]["enabled"] is False


# ---- supplies, faults, maintenance, reliability ----------------------------------------------------
def test_supply_replacement_detection_confirmation_yield_and_forecast(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.55.10", "KY-400", impressions=1000, supplies=[{"descr": "TK-5345K Black", "level": 50}])
    a = S.EMULATORS["10.103.55.10"]
    lvl = mib.PRT_SUPPLY_LEVEL + ".1.1"
    run_due()
    for day, level in enumerate([40, 30, 20, 10, 8], start=1):
        clock.advance(days=1); a.values[lvl] = level; a.values[LIFE] = 1000 + 500 * day; run_due()
    fc = [f for f in client.get(f"/api/printers/{pid}/consumables", headers=admin).json()["forecasts"]][0]
    assert fc["status"] == "FORECAST" and fc["days_to_empty"] is not None and fc["days_to_empty_low"] <= fc["days_to_empty"]
    clock.advance(days=1); a.values[lvl] = 100; a.values[LIFE] = 4000; run_due()        # clear replacement
    ev = client.get(f"/api/printers/{pid}/consumables", headers=admin).json()["events"]
    auto = [e for e in ev if e["event_type"] == "REPLACEMENT_CONFIRMED"][0]
    assert auto["status"] == "CONFIRMED" and auto["meter_value"] == 4000
    clock.advance(days=3); a.values[lvl] = 55; a.values[LIFE] = 9000; run_due()
    clock.advance(days=1); a.values[lvl] = 95; a.values[LIFE] = 9100; run_due()          # ambiguous rise
    ev = client.get(f"/api/printers/{pid}/consumables", headers=admin).json()["events"]
    sus = [e for e in ev if e["event_type"] == "REPLACEMENT_SUSPECTED"][0]
    assert sus["status"] == "OPEN"
    r = client.post(f"/api/printers/{pid}/supply-events/{sus['id']}/decide", headers=admin,
                    json={"decision": "CONFIRM", "cost_amount": "12000", "currency": "JMD"})
    assert r.json()["status"] == "CONFIRMED"
    client.post(f"/api/printers/{pid}/supply-events/{auto['id']}/decide", headers=admin,
                json={"decision": "CONFIRM", "cost_amount": "10000"})
    y = client.get(f"/api/printers/{pid}/consumables", headers=admin).json()["measured_yields"]
    assert y[0]["measured_yield"] == 5100 and y[0]["cost_per_1000"] == round(10000 * 1000 / 5100, 2)
    assert client.post(f"/api/printers/{pid}/supply-events/{sus['id']}/decide", headers=admin,
                       json={"decision": "CONFIRM"}).status_code == 200      # idempotent confirmation


def test_fault_periods_recurrence_service_desk_link_and_mtbf(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.56.10", "KY-500", impressions=1000)
    a = S.EMULATORS["10.103.56.10"]
    err = mib.HR_PRINTER_ERROR_STATE + ".1"
    run_due()
    for _ in range(3):
        clock.advance(minutes=15); a.values[err] = b"\x04\x00"; run_due()            # jammed
        clock.advance(minutes=30); a.values[err] = b"\x00\x00"; run_due()            # cleared
    f = client.get(f"/api/printers/{pid}/faults", headers=admin).json()
    jams = [x for x in f["faults"] if x["code"] == "JAMMED"]
    assert len(jams) == 3 and all(j["duration_minutes"] == 30.0 and not j["active"] for j in jams)
    assert f["recurring"] == {"JAMMED": 3}
    csv_text = ("ticket_ref,serial_number,reported_at,restored_at,description,cause_category,technician,cost_amount\n"
                "SD-1,KY-500,2026-09-01T08:10:00Z,2026-09-01T12:10:00Z,Fuser jam repeat,MECHANICAL,Tech A,5000\n"
                "SD-2,KY-500,2026-09-01T09:00:00Z,2026-09-01T09:30:00Z,WAN outage,WAN,,\n"
                "SD-1,KY-500,2026-09-01T08:10:00Z,,dup,MECHANICAL,,\n"
                "SD-3,UNKNOWN,2026-09-01T08:10:00Z,,x,MECHANICAL,,\n")
    r = client.post("/api/printers/maintenance/import-service-desk", headers=admin, data={"text": csv_text})
    assert r.status_code == 200 and r.json()["created"] == 2 and len(r.json()["skipped"]) == 2
    f = client.get(f"/api/printers/{pid}/faults?days=30", headers=admin).json()
    assert any(x["service_ticket_ref"] == "SD-1" for x in f["faults"])
    rel = f["reliability"]
    assert rel["qualifying_failures"] == 1 and rel["excluded_non_mechanical"] == 1 and rel["mttr_hours"] == 4.0
    bad = client.post(f"/api/printers/{pid}/maintenance-records", headers=admin, json={
        "component": "network", "event_type": "fault", "description": "WAN", "cause_category": "WAN", "qualifying_failure": True})
    assert bad.status_code == 422                                                    # WAN needs evidence to count


# ---- MIB library: upload → browse → draft → custom profile → auto-selection ------------------------
VENDOR_MIB = '''
ACME-PRINTER-MIB DEFINITIONS ::= BEGIN
IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Counter32, Integer32, enterprises FROM SNMPv2-SMI
    DisplayString FROM SNMPv2-TC;

acme MODULE-IDENTITY
    LAST-UPDATED "202601010000Z"
    ORGANIZATION "ACME"
    CONTACT-INFO "support"
    DESCRIPTION "ACME printer counters -- with a comment inside ::= text"
    ::= { enterprises 64999 }

acmeCounters OBJECT IDENTIFIER ::= { acme 1 }   -- counters subtree

acmeTotalPageCount OBJECT-TYPE
    SYNTAX      Counter32
    UNITS       "impressions"
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Total engine impressions."
    ::= { acmeCounters 1 }

acmeColorPrintCount OBJECT-TYPE
    SYNTAX      Counter32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Colour impressions."
    ::= { acmeCounters 2 }

acmeMonoPrintCount OBJECT-TYPE
    SYNTAX      Counter32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Black and white impressions."
    ::= { acmeCounters 3 }

acmeSerialNumber OBJECT-TYPE
    SYNTAX      DisplayString
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "Serial."
    ::= { acme 2 }

acmeTrayTable OBJECT-TYPE
    SYNTAX SEQUENCE OF AcmeTrayEntry
    MAX-ACCESS not-accessible
    STATUS current
    DESCRIPTION "Trays"
    ::= { acme 3 }

acmeTrayEntry OBJECT-TYPE
    SYNTAX AcmeTrayEntry
    MAX-ACCESS not-accessible
    STATUS current
    DESCRIPTION "Tray row"
    INDEX { acmeTrayIndex }
    ::= { acmeTrayTable 1 }

acmeTraySheetCount OBJECT-TYPE
    SYNTAX Counter32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Sheets fed from tray"
    ::= { acmeTrayEntry 2 }
END
'''


def test_mib_upload_parse_profile_builder_and_auto_selection(env):
    client, admin, clock = env
    viewer = make_user(client, admin, "mib.viewer", "viewer")
    files = {"file": ("ACME-PRINTER-MIB.mib", VENDOR_MIB.encode(), "text/plain")}
    assert client.post("/api/printers/mibs", headers=viewer, files=files).status_code == 403
    assert client.post("/api/printers/mibs", headers=admin,
                       files={"file": ("x.exe", b"MZ\x00\x00", "application/octet-stream")}).status_code == 422
    r = client.post("/api/printers/mibs", headers=admin, files=files)
    assert r.status_code == 201, r.text
    mod = r.json()
    assert mod["module_name"] == "ACME-PRINTER-MIB" and mod["status"] == "RESOLVED" and mod["enterprise"] == 64999
    again = client.post("/api/printers/mibs", headers=admin, files=files).json()
    assert again["id"] == mod["id"]                                                  # identical upload is idempotent
    objs = {o["name"]: o for o in client.get(f"/api/printers/mibs/{mod['id']}/objects", headers=viewer).json()}
    assert objs["acmeTotalPageCount"]["oid"] == "1.3.6.1.4.1.64999.1.1"
    assert objs["acmeTotalPageCount"]["suggested_metric"] == "impressions_total"
    assert objs["acmeColorPrintCount"]["suggested_metric"] == "colour_impressions"
    assert objs["acmeMonoPrintCount"]["suggested_metric"] == "bw_impressions"
    assert objs["acmeSerialNumber"]["suggested_metric"] == "serial_number"
    assert objs["acmeTrayTable"]["kind"] == "table" and objs["acmeTraySheetCount"]["kind"] == "column"
    draft = client.post("/api/printers/mibs/profile-draft", headers=admin, json={"object_ids": [
        objs[n]["id"] for n in ("acmeTotalPageCount", "acmeColorPrintCount", "acmeMonoPrintCount", "acmeTrayTable")]}).json()
    metrics = [m for m in draft["metrics"] if "error" not in m]
    assert {m["metric_key"] for m in metrics} == {"impressions_total", "colour_impressions", "bw_impressions"}
    assert all(m["verification"] == "VENDOR_UNVERIFIED" and m["oid"].endswith(".0") for m in metrics)
    assert any("error" in m for m in draft["metrics"])                               # tables cannot be polled
    prof = client.post("/api/printers/mib-profiles", headers=admin, json={
        "key": "acme-printers", "name": "ACME", "vendor": "ACME", "match_enterprise": 64999, "priority": 50,
        "metrics": [{k: m[k] for k in ("metric_key", "oid", "access", "value_type", "category", "verification")} for m in metrics]})
    assert prof.status_code == 201, prof.text
    assert any(m["metric_key"] == "marker_life_count" for m in prof.json()["metrics"])  # standard MIB retained

    vals = C.emulated_printer(manufacturer="Other", model="ACME 9000", serial="AC-1", impressions=900)
    vals[mib.SYS_OBJECT_ID] = "1.3.6.1.4.1.64999.10"
    vals["1.3.6.1.4.1.64999.1.1.0"] = 900
    vals["1.3.6.1.4.1.64999.1.2.0"] = 300
    vals["1.3.6.1.4.1.64999.1.3.0"] = 600
    S.EMULATORS["10.103.57.10"] = C.EmulatedPrinterAgent(vals)
    pid = client.post("/api/printers", headers=admin, json={"manufacturer": "ACME", "model": "ACME 9000",
                                                            "serial_number": "AC-1", "management_ip": "10.103.57.10"}).json()["id"]
    test = client.post(f"/api/printers/mib-profiles/{prof.json()['id']}/test", headers=admin, json={"printer_id": pid}).json()
    assert test["snmp_ok"] and test["meters"]["colour_impressions"]["value"] == 300
    assert test["capabilities"]["mono_colour_split"]["state"] == "UNVERIFIED"
    client.put(f"/api/printers/{pid}/monitoring", headers=admin, json={"enabled": True})
    run_due()
    d = client.get(f"/api/printers/{pid}", headers=admin).json()
    assert d["monitoring"]["mib_profile"]["key"] == "acme-printers"                   # selected by sysObjectID
    assert client.put(f"/api/printers/mib-profiles/{prof.json()['id']}", headers=admin, json={
        "key": "acme-printers", "name": "ACME v2", "match_enterprise": 64999, "metrics": []}).json()["version"] == 2
    builtin = [p for p in client.get("/api/printers/mib-profiles", headers=admin).json() if p["builtin"]][0]
    assert client.put(f"/api/printers/mib-profiles/{builtin['id']}", headers=admin,
                      json={"key": builtin["key"], "name": "x"}).status_code == 409


def test_mib_unresolved_imports_resolve_when_dependency_uploaded(env):
    client, admin, _ = env
    child = "ACME-EXT-MIB DEFINITIONS ::= BEGIN\nIMPORTS acmeCounters FROM ACME-PRINTER-MIB;\n" \
            "acmeExtCopyCount OBJECT-TYPE SYNTAX Counter32 MAX-ACCESS read-only STATUS current DESCRIPTION \"copies\"\n" \
            "    ::= { acmeCounters 9 }\nEND\n"
    r = client.post("/api/printers/mibs", headers=admin, files={"file": ("ext.mib", child.encode(), "text/plain")}).json()
    assert r["status"] == "UNRESOLVED_IMPORTS" and r["unresolved"] == ["acmeCounters"]
    client.post("/api/printers/mibs", headers=admin, files={"file": ("acme.mib", VENDOR_MIB.encode(), "text/plain")})
    mods = {m["module_name"]: m for m in client.get("/api/printers/mibs", headers=admin).json()}
    assert mods["ACME-EXT-MIB"]["status"] == "RESOLVED"
    objs = client.get(f"/api/printers/mibs/{mods['ACME-EXT-MIB']['id']}/objects", headers=admin).json()
    assert objs[0]["oid"] == "1.3.6.1.4.1.64999.1.9" and objs[0]["suggested_metric"] == "copy_impressions"


# ---- import ----------------------------------------------------------------------------------------
def test_csv_import_preview_validation_dedupe_and_bulk_approval(env):
    client, admin, _ = env
    client.post("/api/printers", headers=admin, json={"manufacturer": "HP", "model": "M428", "serial_number": "HP-1"})
    text = ("Manufacturer,Model,Serial Number,Management IP,Station\n"
            "Kyocera,ECOSYS M2640idw,K-1,10.103.58.10,HQ\n"
            "Kyocera,ECOSYS M2640idw,K-1,10.103.58.11,HQ\n"
            "Xerox,VersaLink B62,X-1,8.8.8.8,HQ\n"
            "HP,M428,HP-1,10.103.58.12,HQ\n"
            "HP,M479,HP-1,,HQ\n"
            "HP,LJ 400,HP-2,,HQ\n")
    pv = client.post("/api/printers/import/preview", headers=admin, data={"text": text}).json()
    acts = [r["action"] for r in pv["rows"]]
    assert acts == ["CREATE", "ERROR", "ERROR", "UPDATE", "ERROR", "CREATE"]
    assert "duplicate" in pv["rows"][1]["errors"][0] and "private" in pv["rows"][2]["errors"][0]
    assert any("not recognised" in w for w in pv["rows"][5]["warnings"])               # abbreviation kept, not expanded
    done = client.post("/api/printers/import/commit", headers=admin, data={"text": text, "rows": "all"}).json()
    assert [d["row"] for d in done["imported"]] == [2, 5, 7] and len(done["skipped"]) == 3
    p = [x for x in client.get("/api/printers?q=HP-2", headers=admin).json()][0]
    assert p["model"] == "LJ 400"
    detail = client.get(f"/api/printers/{p['id']}", headers=admin).json()
    assert "LJ 400" in detail["identity"]["original_import_text"]


# ---- recommendations, benchmarking, reports ------------------------------------------------------
def _month_of_usage(client, admin, clock, pid_ips, pages_per_day, days=95):
    for _ in range(days):
        clock.advance(days=1)
        for ip, ppd in zip(pid_ips, pages_per_day):
            a = S.EMULATORS[ip]
            a.values[LIFE] += ppd
            a.values[KYO_TOTAL] = a.values[LIFE]
        run_due()


def test_duty_cycle_recommendations_review_and_reports(env):
    client, admin, clock = env
    model = client.post("/api/printers/models", headers=admin, json={
        "manufacturer": "Kyocera", "model": "ECOSYS M2640idw", "technology": "laser", "colour_capable": False,
        "recommended_monthly_volume": 3000, "max_monthly_duty_cycle": 60000, "verification": "VERIFIED"}).json()
    ips = ["10.103.59.10", "10.103.59.11", "10.103.59.12"]
    pids = [onboard(client, admin, ip, f"KM-{i}", model="ECOSYS M2640idw", impressions=1000) for i, ip in enumerate(ips)]
    for pid in pids:
        client.patch(f"/api/printers/{pid}/organization", headers=admin, json={"model_id": model["id"], "location_ref": "HQ"})
        client.patch(f"/api/printers/{pid}/location", headers=admin, json={"station": "HQ"})
    run_due()
    _month_of_usage(client, admin, clock, ips, [400, 10, 12])
    ins = client.get(f"/api/printers/{pids[0]}/utilization?granularity=month", headers=admin).json()["insights"]
    assert ins["duty_cycle"]["status"] == "SUSTAINED_HIGH" and ins["model_verified"]
    assert client.get(f"/api/printers/{pids[1]}/utilization?granularity=month", headers=admin).json()["insights"][
        "duty_cycle"]["status"] == "SUSTAINED_LOW"
    run = client.post("/api/printers/recommendations/run", headers=admin).json()
    assert run["created"] >= 3
    recs = client.get("/api/printers/recommendations", headers=admin).json()["items"]
    codes = {r["code"] for r in recs}
    assert {"UNDERSIZED", "CONSOLIDATION", "RELOCATION"} <= codes
    under = [r for r in recs if r["code"] == "UNDERSIZED"][0]
    assert under["reason_codes"] and under["assumptions"] and under["evidence"]["recent_months"]
    again = client.post("/api/printers/recommendations/run", headers=admin).json()
    assert again["created"] == 0                                                         # deduplicated
    viewer = make_user(client, admin, "rec.viewer", "viewer")
    approver = make_user(client, admin, "rec.approver", "approver")
    assert client.post(f"/api/printers/recommendations/{under['id']}/review", headers=viewer,
                       json={"decision": "APPROVED", "note": "ok"}).status_code == 403
    ok = client.post(f"/api/printers/recommendations/{under['id']}/review", headers=approver,
                     json={"decision": "APPROVED", "note": "replace with higher duty model"})
    assert ok.json()["status"] == "APPROVED"
    assert client.post(f"/api/printers/recommendations/{under['id']}/review", headers=approver,
                       json={"decision": "REJECTED", "note": "changed mind"}).status_code == 409
    cmp_ = client.get("/api/printers/models/compare", headers=admin).json()["cohorts"][0]
    assert cmp_["in_service"] == 3 and cmp_["sample"]["devices"] == 3

    for rep in ("fleet_inventory", "daily_utilization", "monthly_utilization", "consumable_efficiency", "model_reliability",
                "high_utilization", "low_utilization", "maintenance", "standardization", "executive_fleet"):
        for fmt in ("csv", "xlsx", "pdf"):
            r = client.post("/api/printers/reports", headers=viewer, json={"report": rep, "format": fmt,
                                                                           "filters": {"manufacturer": "Kyocera"}})
            assert r.status_code == 202, r.text
            job = client.get(f"/api/printers/jobs/{r.json()['id']}", headers=viewer).json()
            assert job["status"] == "SUCCEEDED", (rep, fmt, job["errors"])
            dl = client.get(job["download"], headers=viewer)
            assert dl.status_code == 200
            if fmt == "pdf":
                assert dl.content.startswith(b"%PDF-1.4") and dl.content.rstrip().endswith(b"%%EOF")
            elif fmt == "xlsx":
                assert "xl/worksheets/sheet1.xml" in zipfile.ZipFile(io.BytesIO(dl.content)).namelist()
            else:
                assert b"printer" in dl.content.lower() or b"metric" in dl.content.lower()
    monthly = client.post("/api/printers/reports", headers=admin, json={
        "report": "monthly_utilization", "format": "csv",
        "filters": {"date_from": "2026-09-01", "date_to": "2026-12-01"}}).json()
    meta = client.get(f"/api/printers/jobs/{monthly['id']}", headers=admin).json()["result"]["meta"]
    # Monitoring began 08:00 on 1 Sep: the uncovered 8 h are reported as a gap, never filled in.
    assert meta["reconciled_total"] == pytest.approx((400 + 10 + 12) * (90 + 16 / 24), abs=0.05)
    high = client.post("/api/printers/reports", headers=admin, json={"report": "high_utilization", "format": "csv"}).json()
    assert client.get(f"/api/printers/jobs/{high['id']}", headers=admin).json()["result"]["rows"] == 1
    assert client.post("/api/printers/reports", headers=admin, json={"report": "nope"}).status_code == 422
    assert client.post("/api/printers/reports", headers=admin, json={"report": "fleet_inventory",
                                                                     "filters": {"evil": 1}}).status_code == 422


def test_report_schedule_creates_job_and_notifies(env):
    client, admin, clock = env
    from backend.database.session import session_scope
    from backend.database.models_remediation import NotificationChannel, NotificationDelivery
    from sqlalchemy import select
    with session_scope() as db:
        ch = NotificationChannel(name="ops-syslog", kind="syslog", target="10.103.1.5:514", min_severity="info", created_by="admin")
        db.add(ch)
        db.flush()
        chid = ch.id
    s = client.post("/api/printers/reports/schedules", headers=admin, json={"report": "executive_fleet", "format": "pdf",
                                                                            "cadence": "weekly", "recipients": [chid]})
    assert s.status_code == 201
    assert client.post("/api/printers/reports/schedules", headers=admin, json={"report": "executive_fleet", "cadence": "weekly",
                                                                               "recipients": ["nope"]}).status_code == 422
    clock.advance(minutes=2)
    from backend.infrastructure import scheduled_jobs as J
    r = J.printer_jobs()
    assert r == {"scheduled_reports": 1, "jobs_run": 1}
    with session_scope() as db:
        d = db.scalar(select(NotificationDelivery).where(NotificationDelivery.kind == "printer_report"))
        assert d.payload["download_path"].startswith("/api/printers/reports/jobs/")
    sched = client.get("/api/printers/reports/schedules", headers=admin).json()[0]
    assert sched["last_job_id"] and sched["next_run_at"] > "2026-09-08"


# ---- retention, security, scale --------------------------------------------------------------------
def test_retention_rolls_up_daily_aggregates_before_deleting_raw(env):
    client, admin, clock = env
    pid = onboard(client, admin, "10.103.60.10", "KY-R", impressions=100)
    run_due()
    for _ in range(3):
        clock.advance(days=1); S.EMULATORS["10.103.60.10"].values[LIFE] += 50; run_due()
    client.put("/api/printers/settings", headers=admin, json={"raw_days": 30, "aggregate_years": 5})
    clock.advance(days=40)
    r = client.post("/api/printers/retention/run", headers=admin).json()
    assert r["daily_rows_rolled_up"] >= 3 and r["samples_deleted"] > 0 and r["intervals_deleted"] == 3
    from backend.database.session import session_scope
    from backend.database.models_printer_srs import PrinterDailyAggregate
    from sqlalchemy import select, func
    with session_scope() as db:
        assert db.scalar(select(func.sum(PrinterDailyAggregate.volume))) == 150


def test_security_credentials_never_returned_reads_scoped_and_changes_audited(env):
    client, admin, _ = env
    cid = cred_profile(client, admin)
    pid = onboard(client, admin, "10.103.61.10", "KY-S", impressions=10)
    client.put(f"/api/printers/{pid}/monitoring", headers=admin, json={"enabled": True, "credential_profile_id": cid})
    body = str(client.get(f"/api/printers/{pid}", headers=admin).json()) + str(client.get("/api/printers/live", headers=admin).json())
    assert "auth-secret-1" not in body and "priv-secret-1" not in body
    op = make_user(client, admin, "prn.op", "operator")
    assert client.post(f"/api/printers/{pid}/poll", headers=op).status_code == 202
    assert client.post(f"/api/printers/{pid}/diagnostic-walk", headers=op, json={}).status_code == 403
    w = client.post(f"/api/printers/{pid}/diagnostic-walk", headers=admin, json={"root": "system", "max_rows": 50}).json()
    rows = client.get(f"/api/printers/jobs/{w['id']}", headers=admin).json()["result"]["rows"]
    assert rows and all(r["oid"].startswith("1.3.6.1.2.1.1") for r in rows)
    assert {op_ for op_, _ in S.EMULATORS["10.103.61.10"].requests} <= {"GET", "GETNEXT"}   # never SET
    assert client.get(f"/api/printers/jobs/{w['id']}", headers=op).status_code == 404      # other users' jobs hidden
    log = client.get("/api/audit?limit=500", headers=admin).json()
    actions = {e["action"] for e in (log["items"] if isinstance(log, dict) else log)}
    assert {"printer_monitoring_changed", "printer_poll_requested", "printer_diagnostic_walk"} <= actions
    from backend.database.models import Site
    from backend.database.session import session_scope
    with session_scope() as db:
        s1, s2 = Site(name="Site-A", simulated=True), Site(name="Site-B", simulated=True)
        db.add_all([s1, s2]); db.flush(); a_id, b_id = s1.id, s2.id
    client.patch(f"/api/printers/{pid}/organization", headers=admin, json={"site_id": a_id})
    scoped_a = make_user(client, admin, "scope.a", "viewer", scope="granted", grants=[a_id])
    scoped_b = make_user(client, admin, "scope.b", "viewer", scope="granted", grants=[b_id])
    assert [p["id"] for p in client.get("/api/printers", headers=scoped_a).json()] == [pid]
    assert client.get(f"/api/printers/{pid}/utilization", headers=scoped_b).status_code == 404
    assert client.get("/api/printers/overview", headers=scoped_b).json()["total"] == 0


def test_two_workers_never_claim_the_same_printer_and_1000_printers_poll(env):
    client, admin, clock = env
    from backend.database.session import session_scope
    with session_scope() as db:
        for i in range(1000):
            ip = f"10.104.{i // 250}.{i % 250 + 1}"
            S.EMULATORS[ip] = C.EmulatedPrinterAgent(C.emulated_printer(serial=f"SC-{i}", impressions=i))
            p, _ = S.register_or_update(db, {"manufacturer": "Kyocera", "model": "ECOSYS MA4000cix",
                                             "serial_number": f"SC-{i}", "management_ip": ip}, provenance="TEST")
            m = S.monitoring(db, p.id)
            m.enabled, m.next_poll_at = True, clock.t
    with session_scope() as db:
        a = S.claim_due(db, "worker-a", 600)
        b = S.claim_due(db, "worker-b", 600)
    assert len(a) == 600 and len(b) == 400 and not set(a) & set(b)
    clock.advance(minutes=5)                                  # leases expire (120 s)
    import time
    t0 = time.monotonic()
    total = 0
    while True:
        r = asyncio.run(S.run_due(session_scope, "worker-a", limit=500, concurrency=64))
        if not r["claimed"]:
            break
        total += r["succeeded"]
    elapsed = time.monotonic() - t0
    assert total == 1000
    ops = client.get("/api/printers/operations", headers=admin).json()
    assert ops["monitored"] == 1000 and ops["queue_depth"] == 0 and ops["fresh"] == 1000 and ops["poll_success_rate"] == 1.0
    print(f"1000 emulated printers polled+persisted in {elapsed:.1f}s on SQLite")
