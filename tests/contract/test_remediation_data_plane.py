"""Independent audit E2E-01/02/03/10/11/15 — data-plane integrity on the REAL collector code path.

Only the TCP socket is replaced by the strict AOS-CX protocol emulator. The tests compare the VALUES persisted
in Timescale, recorded in the event ledger, pushed to the log store, attached to alerts and returned by the API —
not merely HTTP 200s.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.database.session import session_scope
from backend.infrastructure.writers.emulator import CxEmulator
from tests.conftest import login


class CapturingLogs:
    def __init__(self):
        self.lines = []
        self.fail = False

    def push(self, labels, line, ts):
        if self.fail:
            raise ConnectionError("loki down")
        self.lines.append((labels, line, ts))


@pytest.fixture
def plane(app_env, tmp_path, monkeypatch):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.70.0.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    emus = {"10.70.0.24": CxEmulator(model="6100 24G", firmware="PL.10.14.1000", ports=24),
            "10.70.0.48": CxEmulator(model="6200F 48G", firmware="ML.10.13.1020", ports=48)}
    for i, e in enumerate(emus.values()):
        e.state["system"]["serial_number"] = f"EMU-PORTS-{i}"
    real_client = httpx.Client

    def client(*a, **kw):
        host = str(kw.get("base_url", "")).split("//")[-1]
        kw["transport"] = emus[host].transport()
        return real_client(*a, **kw)
    monkeypatch.setattr("backend.infrastructure.writers.aruba_cx.httpx.Client", client)
    logs = CapturingLogs()
    from backend.infrastructure import runtime
    fake_log_store = lambda: logs                      # noqa: E731
    fake_log_store.cache_clear = lambda: None          # conftest clears runtime caches at teardown
    monkeypatch.setattr(runtime, "log_store", fake_log_store)
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c, emus, logs


def add_switch(c, adm, ip, serial, model, fw):
    did = c.post("/api/v1/quick-devices", headers=adm, json={
        "site_name": "HQ", "family": "switch", "hostname": f"sw-{ip.rsplit('.', 1)[-1]}", "serial_number": serial,
        "model": model, "firmware": fw, "management_ip": ip, "status": "up", "metrics": {}}).json()["id"]
    prof = c.post("/api/v1/onboarding/credentials", headers=adm, json={
        "name": f"cx-{serial}", "device_family": "switch", "protocol": "aruba_cx_rest",
        "username": "admin", "password": "emulator-password"}).json()
    from backend.database.models_onboarding import OnboardingDeviceCredential
    with session_scope() as db:
        db.add(OnboardingDeviceCredential(device_id=did, credential_id=prof["id"], bound_by="test"))
    pid = c.post("/api/v1/collection/plans", headers=adm, json={
        "device_id": did, "metric_families": ["system", "interfaces", "poe", "lldp"],
        "interval_seconds": 300}).json()["id"]
    return did, pid


def poll(pid, at):
    from backend.infrastructure.collection_runner import run_due
    with session_scope() as db:
        return run_due(db, now=at, worker="test", plan_ids=[pid])


def test_every_port_of_24_and_48_port_switches_is_stored_and_retrievable(plane):
    """E2E-01/02: identical metric names + timestamps on 24/48 ports -> 24/48 distinct readings, each queryable."""
    c, emus, logs = plane
    adm = login(c)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=12)
    for ip, serial, model, fw, n in (("10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000", 24),
                                    ("10.70.0.48", "EMU-PORTS-1", "CX 6200F 48G", "ML.10.13.1020", 48)):
        did, pid = add_switch(c, adm, ip, serial, model, fw)
        s1, s2 = poll(pid, t0), poll(pid, t0 + timedelta(minutes=5))
        assert s1["succeeded"] == 1 and s2["succeeded"] == 1, (s1, s2)
        from backend.infrastructure.runtime import telemetry_store
        rows = telemetry_store().query(device_id=did, metric_name="port_link_up", since=t0 - timedelta(minutes=1),
                                       limit=5000)
        assert len({r["entity_id"] for r in rows}) == n and len(rows) == 2 * n
        # values match the device: the emulator links every port not divisible by 3
        latest = {r["entity_id"]: r["value"] for r in rows if r["observed_at"].startswith(
            (t0 + timedelta(minutes=5)).isoformat()[:16])}
        assert latest == {f"1/1/{i}": (1.0 if i % 3 else 0.0) for i in range(1, n + 1)}
        # rates exist for every up port after the second poll, one series per port
        rates = telemetry_store().query(device_id=did, metric_name="port_rx_bps", since=t0, limit=5000)
        assert {r["entity_id"] for r in rates} == {f"1/1/{i}" for i in range(1, n + 1)}
        # API: one port's series, and the entity listing
        one = c.get(f"/api/v1/devices/{did}/ports/1/1/7/series?metric=port_link_up&hours=1", headers=adm).json()
        assert [p["v"] for p in one["points"]] == [1.0, 1.0] and one["entity_id"] == "1/1/7"
        ents = c.get(f"/api/v1/devices/{did}/metrics/entities", headers=adm).json()["entities"]
        assert len([k for k in ents if k.startswith("port:")]) == n
        # device-level series never mixes ports in
        dev = c.get(f"/api/v1/devices/{did}/metrics/series?metric=ports_up&hours=1", headers=adm).json()
        assert [p["v"] for p in dev["points"]] == [float(n - n // 3)] * 2
        # replaying the same event stores nothing twice
        from backend.application.ingest import points_from_event
        from backend.database.models_remediation import EventOutbox
        from backend.domain.events import parse_event
        with session_scope() as db:
            ev = parse_event(db.scalars(select(EventOutbox).where(EventOutbox.device_id == did)).first().envelope.encode())
        assert telemetry_store().write_points(points_from_event(ev)) == 0
    # the 10.13 switch was polled on its negotiated API version, with CSRF, and no protocol violations
    assert emus["10.70.0.48"].violations == [] and emus["10.70.0.24"].violations == []


def test_one_correlation_id_spans_run_event_points_log_alert_and_ui(plane):
    """E2E-03/15: poll -> outbox event -> ingest -> Timescale + ledger + Loki + alert -> UI, all one id."""
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    emus["10.70.0.24"].state["subsystems"]["management_module,1/1"]["resource_utilization"]["cpu"] = 97
    notified = []
    from backend.infrastructure import outbox
    outbox.set_ui_notifier(lambda topics, cid, eid: notified.append((tuple(topics), cid, eid)))
    try:
        s = poll(pid, datetime.now(timezone.utc) - timedelta(minutes=1))
    finally:
        outbox.set_ui_notifier(None)
    assert s["succeeded"] == 1 and s["events_queued"] == 1
    from backend.database.models_inm import EventRecord
    from backend.database.models_phases import CollectionRun, InmAlert
    from backend.database.models_remediation import EventOutbox
    from backend.infrastructure.runtime import telemetry_store
    with session_scope() as db:
        runs = list(db.scalars(select(CollectionRun).where(CollectionRun.plan_id == pid)))
        run = runs[0]
        expected = sum(r.expected_points for r in runs)
        assert {r.event_id for r in runs} == {run.event_id} and all(r.state == "SUCCEEDED" for r in runs)
        ob = db.get(EventOutbox, run.event_id)
        rec = db.get(EventRecord, run.event_id)
        alert = db.scalars(select(InmAlert).where(InmAlert.device_id == did, InmAlert.rule_id == "SW_CPU_HIGH")).first() \
            or db.scalars(select(InmAlert).where(InmAlert.device_id == did)).first()
        corr, eid = run.correlation_id, run.event_id
        assert run.state == "SUCCEEDED" and run.points_written > 0
        assert ob.state == "DELIVERED" and ob.correlation_id == corr
        assert rec is not None and rec.correlation_id == corr and rec.source_service == "collector"
        assert alert is not None and alert.correlation_id == corr and alert.event_id == eid
    pts = telemetry_store().query(device_id=did, metric_name=None, since=datetime.now(timezone.utc) - timedelta(hours=1),
                                  limit=5000, source_event_id=eid)
    assert len(pts) == expected and expected > 24     # every system + per-port point of this poll
    import json
    lines = [json.loads(line) for _, line, _ in logs.lines]
    assert any(x["event_id"] == eid and x["correlation_id"] == corr and x["points"] == len(pts) for x in lines)
    assert any(cid == corr and eid2 == eid and "alerts" in topics for topics, cid, eid2 in notified)


def test_no_false_success_when_telemetry_cannot_be_persisted_then_recovers(plane, monkeypatch):
    """E2E-10: datastore unavailable -> run not SUCCEEDED, plan not fresh, event kept PENDING; recovery delivers."""
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    from backend.infrastructure import runtime
    real = runtime.telemetry_store
    none_store = lambda: None                          # noqa: E731
    none_store.cache_clear = lambda: None
    monkeypatch.setattr(runtime, "telemetry_store", none_store)
    s = poll(pid, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert s["succeeded"] == 0 and s["awaiting_persistence"] == 1 and s["points_written"] == 0
    dev = c.get(f"/api/v1/devices/{did}", headers=adm).json()
    assert dev["state"] == "UNKNOWN" and dev["last_persisted_at"] is None
    noc = {t["device_id"]: t for t in c.get("/api/v1/noc/overview", headers=adm).json()["devices"]}
    assert noc[did]["state"] == "UNKNOWN"
    from backend.database.models_remediation import EventOutbox
    with session_scope() as db:
        row = db.scalars(select(EventOutbox).where(EventOutbox.device_id == did)).one()
        assert row.state == "PENDING" and "telemetry store" in row.last_error
    # Loki outage is also a retry, never an ack
    monkeypatch.setattr(runtime, "telemetry_store", real)
    logs.fail = True
    from backend.infrastructure import outbox
    with session_scope() as db:
        assert outbox.relay(db, now=datetime.now(timezone.utc) + timedelta(hours=1))["failed"] == 1
    logs.fail = False
    with session_scope() as db:
        assert outbox.relay(db, now=datetime.now(timezone.utc) + timedelta(hours=2))["delivered"] == 1
    dev = c.get(f"/api/v1/devices/{did}", headers=adm).json()
    assert dev["state"] == "ONLINE" and dev["last_persisted_at"]


def test_status_agrees_across_collector_noc_device360_and_alerts_through_outage_and_recovery(plane):
    """E2E-11: ONLINE -> OFFLINE (no answer) -> ONLINE; auth failure -> UNKNOWN; stale -> STALE -> UNKNOWN."""
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    emu = emus["10.70.0.24"]
    t = datetime.now(timezone.utc)

    def views():
        dev = c.get(f"/api/v1/devices/{did}", headers=adm).json()
        tile = next(x for x in c.get("/api/v1/noc/overview", headers=adm).json()["devices"] if x["device_id"] == did)
        return dev["state"], tile["state"], dev["status"]

    assert poll(pid, t)["succeeded"] == 1
    assert views() == ("ONLINE", "ONLINE", "up")
    # switch stops answering: three failed polls
    orig = emu.handle
    emu.handle = lambda req: (_ for _ in ()).throw(httpx.ConnectError("no route"))
    from backend.database.models_phases import CollectionPlan, InmAlert
    for i in range(1, 4):
        with session_scope() as db:
            db.get(CollectionPlan, pid).next_due_at = None
        poll(pid, t + timedelta(minutes=5 * i))
    assert views() == ("OFFLINE", "OFFLINE", "down")
    with session_scope() as db:
        alert = db.scalars(select(InmAlert).where(InmAlert.device_id == did,
                                                   InmAlert.rule_id == "DEVICE_UNREACHABLE")).one()
        assert alert.state == "OPEN" and alert.correlation_id
    # recovery
    emu.handle = orig
    poll(pid, t + timedelta(minutes=25))
    assert views() == ("ONLINE", "ONLINE", "up")
    with session_scope() as db:
        assert db.scalars(select(InmAlert).where(InmAlert.device_id == did,
                                                  InmAlert.rule_id == "DEVICE_UNREACHABLE")).one().state == "RESOLVED"
    # wrong password: reachable but unreadable -> UNKNOWN, not OFFLINE, not ONLINE
    emu.password = "rotated"
    poll(pid, t + timedelta(minutes=30))
    assert views()[:2] == ("UNKNOWN", "UNKNOWN")
    # stale: no new observation for > 2 intervals (and later > 6) after a good poll
    emu.password = "emulator-password"
    with session_scope() as db:
        p = db.get(CollectionPlan, pid)
        p.consecutive_failures, p.last_failure_category = 0, None
        p.last_success_at = datetime.now(timezone.utc) - timedelta(seconds=300 * 3)
    assert views()[:2] == ("STALE", "STALE")
    with session_scope() as db:
        db.get(CollectionPlan, pid).last_success_at = datetime.now(timezone.utc) - timedelta(seconds=300 * 7)
    assert views()[:2] == ("UNKNOWN", "UNKNOWN")


def test_two_manual_polls_in_the_same_second_are_both_executed(plane):
    """Regression found in the full-stack run: manual 'poll now' was keyed by the second and silently deduped."""
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    a = c.post(f"/api/v1/collection/plans/{pid}/run-now", headers=adm).json()["summary"]
    b = c.post(f"/api/v1/collection/plans/{pid}/run-now", headers=adm).json()["summary"]
    assert a["succeeded"] == 1 and b["succeeded"] == 1 and b["skipped_duplicate"] == 0
