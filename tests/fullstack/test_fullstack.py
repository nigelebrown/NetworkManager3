"""Cross-service contract tests against the REAL distributed stack (separate processes):
PostgreSQL (management) · PostgreSQL+TimescaleDB (telemetry) · NATS JetStream (shipped ACLs) · Loki · Alloy ·
API role · worker role · strict CX protocol emulators on real HTTPS sockets.

Run:  bash tests/fullstack/stack.sh up && INM_FS=1 .venv/bin/python -m pytest -q tests/fullstack -p no:cacheprovider
Every assertion compares values actually persisted / logged / delivered — not just HTTP status codes.
Emulated devices: software evidence only, never hardware certification.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("INM_FS"), reason="INM_FS not set (needs tests/fullstack/stack.sh up)")
ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8810/api"
EMU = "http://127.0.0.1:18099"
LOKI = "http://127.0.0.1:3100"
MGMT = "host=127.0.0.1 port=56432 dbname=inm_management user=inm_app password=fs-mgmt-pass"
TS = "host=127.0.0.1 port=56432 dbname=inm_telemetry user=inm_metrics password=fs-ts-pass"
STATE: dict = {}
EVIDENCE = ROOT / "evidence" / "fullstack"


def stack(*args: str) -> None:
    subprocess.run(["bash", str(ROOT / "tests/fullstack/stack.sh"), *args], check=True, capture_output=True, timeout=180)


def wait(fn, timeout=90, every=1.0, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # services may be restarting
            last = exc
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


def sql(dsn: str, q: str, *a):
    with psycopg.connect(dsn) as c:
        return c.execute(q, a).fetchall()


def evidence(name: str, obj) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str))


@pytest.fixture(scope="module")
def c():
    cl = httpx.Client(base_url=API, timeout=60)
    tok = cl.post("/auth/login", json={"username": "admin", "password": "FullStack-Admin-2026!"}).json()["access_token"]
    cl.headers["Authorization"] = f"Bearer {tok}"
    yield cl


def as_user(name: str, role: str, c: httpx.Client) -> httpx.Client:
    pw = "Approver-Temp-2026!x"
    c.post("/v1/users", json={"username": name, "role": role, "temporary_password": pw, "site_scope": "all"})
    u = httpx.Client(base_url=API, timeout=60)
    r = u.post("/auth/login", json={"username": name, "password": pw})
    u.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    new = pw + "-changed"
    assert u.post("/auth/change-password", json={"current_password": pw, "new_password": new}).status_code == 200
    r = u.post("/auth/login", json={"username": name, "password": new})
    u.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return u


def test_01_platform_is_really_distributed(c):
    rows = sql(TS, "select hypertable_name from timescaledb_information.hypertables")
    assert ("telemetry_points",) in rows
    assert sql(TS, "select max(version) from telemetry_schema_version")[0][0] == "t002"
    r = httpx.get("http://127.0.0.1:18222/jsz?streams=true").json()
    names = {s["name"] for a in r.get("account_details", []) for s in a.get("stream_detail", [])}
    assert {"INM_EVENTS", "INM_UI", "INM_DLQ"} <= names
    assert httpx.get(f"{LOKI}/ready").status_code == 200
    beats = sql(MGMT, "select service, instance from service_heartbeats")
    assert any(r[0] == "network_worker" for r in beats), beats
    # ACLs: the API identity cannot publish device events (only the worker can)
    import asyncio
    import nats

    async def try_pub():
        nc = await nats.connect("nats://127.0.0.1:14222", user="inm_api", password="fs-nats-api")
        errs = []
        nc._error_cb = lambda e: errs.append(e)  # noqa: SLF001
        try:
            js = nc.jetstream()
            await js.publish("inm.events.device.telemetry.observed", b"{}", timeout=2)
            return "published"
        except Exception as exc:
            return type(exc).__name__
        finally:
            await nc.close()
    assert asyncio.run(try_pub()) != "published"


def test_02_workflow_discovery_runs_in_the_worker_and_survives_a_worker_crash(c):
    appr = as_user("fs.approver", "approver", c)
    STATE["appr"] = appr
    w = c.post("/v1/workflows", json={"title": "FS clinic", "device_family": "switch", "site_name": "FS Site"}).json()
    wid = STATE["wid"] = w["id"]
    cred = c.post("/v1/onboarding/credentials", json={"name": "fs-cx", "device_family": "switch",
                                                       "protocol": "aruba_cx_rest", "username": "admin",
                                                       "password": "emulator-password"}).json()["id"]
    c.post(f"/v1/workflows/{wid}/credential", json={"credential_id": cred})
    w = c.post(f"/v1/workflows/{wid}/discover", json={"scope": "10.250.0.0/24", "rate_per_s": 4}).json()
    job = w["discovery"]["id"]
    assert w["discovery"]["status"] == "QUEUED" and w["discovery"]["progress"]["total"] == 254
    # the WORKER process leases and runs it; kill the worker mid-scan
    wait(lambda: (c.get(f"/v1/onboarding/discoveries/{job}").json()["progress"]["done"] or 0) >= 8, 60,
         what="worker progress")
    stack("stop", "worker")
    mid = c.get(f"/v1/onboarding/discoveries/{job}").json()
    assert mid["status"] == "SCANNING" and 0 < mid["progress"]["done"] < 254
    with psycopg.connect(MGMT) as db:     # simulate the dead worker's lease expiring (LEASE_S = 60 s)
        db.execute("update onboarding_discoveries set lease_expires_at = now() - interval '1 second' where id = %s", (job,))
    stack("start", "worker")
    done = wait(lambda: (lambda j: j if j["status"] == "SCANNED" else None)(c.get(f"/v1/onboarding/discoveries/{job}").json()),
                180, 2, "discovery after worker restart")
    found = sorted(r["serial_number"] for r in done["results"] if r["status"] == "FOUND")
    assert found == ["EMU-FS-24", "EMU-FS-48"]
    ips = [r["management_ip"] for r in done["results"]]
    assert len(ips) == len(set(ips))
    evidence("discovery_worker_restart", {"mid": mid["progress"], "final": done["progress"], "found": found})
    idx = [i for i, r in enumerate(done["results"]) if r["status"] == "FOUND"]
    w = c.post(f"/v1/workflows/{wid}/register", json={"indices": idx}).json()
    assert len(w["devices"]) == 2
    STATE["devices"] = {d["serial_number"]: d["device_id"] for d in w["devices"]}


def test_03_import_templates_approve_and_hardware_writes_stay_locked(c):
    wid, appr = STATE["wid"], STATE["appr"]
    w = c.post(f"/v1/workflows/{wid}/baseline").json()
    assert all(x["ok"] for x in w["outcome"]["baseline"].values()), w["outcome"]
    w = c.post(f"/v1/workflows/{wid}/templates", json={"new_group_name": "FS access"}).json()
    gid = w["template_scope"]["group_id"]
    assert c.post("/v1/config-templates", json={"name": "FS base", "family": "switch", "scope": "global", "config": {
        "vlans": [10, 20], "port_roles": {"user": {"access_vlan": 10, "bpdu_guard": "enabled"}},
        "port_assignments": [{"ports": "1/1/1-1/1/48", "role": "user"}], "system": {"ntp": ["10.10.0.10"]}}}).status_code == 201
    assert c.post("/v1/config-templates", json={"name": "FS group", "family": "switch", "scope": "group", "group_id": gid,
                                                "config": {"port_roles": {"user": {"access_vlan": 20}}}}).status_code == 201
    pv = c.get(f"/v1/workflows/{wid}/preview").json()["devices"]
    ports = sorted(len(d["port_roles"]) for d in pv)
    assert ports == [24, 48]
    w = c.post(f"/v1/workflows/{wid}/decide", json={"mode": "NOW"}).json()
    for p in w["plans"]:
        plan = next(x for x in c.get("/v1/changes/plans").json() if x["id"] == p["plan_id"])
        assert appr.post(f"/v1/changes/plans/{plan['id']}/approve", json={"expected_revision": plan["revision"]}).status_code == 200
    w = c.post(f"/v1/workflows/{wid}/execute").json()
    assert all(r["decision"] == "DENY" for r in w["outcome"]["execution"])
    for ip in ("10.250.0.24", "10.250.0.25"):
        assert httpx.get(f"{EMU}/{ip}/writes").json() == 0          # no write ever reached the "hardware"
        assert httpx.get(f"{EMU}/{ip}/violations").json() == []
    evidence("workflow_locked_execution", w["outcome"]["execution"])


def _ws_messages(token: str, seconds: float, out: list) -> None:
    from websockets.sync.client import connect
    with connect("ws://127.0.0.1:8810/ws/v1/events", open_timeout=10) as ws:
        ws.send(json.dumps({"type": "auth", "token": token}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                out.append(json.loads(ws.recv(timeout=0.5)))
            except TimeoutError:
                continue


def test_04_monitoring_pipeline_end_to_end_with_one_correlation_id(c):
    import threading
    wid = STATE["wid"]
    msgs: list = []
    t = threading.Thread(target=_ws_messages, args=(c.headers["Authorization"].split()[1], 25, msgs))
    t.start()
    time.sleep(1)
    c.post(f"/v1/workflows/{wid}/monitor")
    devs = STATE["devices"]
    # API (jetstream mode) only enqueued the events; the worker relays -> JetStream -> ingest
    st = wait(lambda: (lambda w: w if all(d["state"] == "ONLINE" for d in w["devices"]) else None)(
        c.get(f"/v1/workflows/{wid}").json()), 60, 1, "devices ONLINE via worker ingest")
    t.join()
    for serial, n in (("EMU-FS-24", 24), ("EMU-FS-48", 48)):
        did = devs[serial]
        rows = sql(TS, "select count(distinct entity_id), count(*) from telemetry_points where device_id=%s and "
                       "metric_name='port_link_up' and entity_type='port'", did)
        assert rows[0][0] == n, rows
        run = sql(MGMT, "select event_id, correlation_id, state, expected_points, points_written from collection_runs "
                        "where device_id=%s order by started_at desc limit 1", did)[0]
        event_id, corr, state = run[0], run[1], run[2]
        assert state == "SUCCEEDED"
        total = sql(TS, "select count(*) from telemetry_points where source_event_id=%s", event_id)[0][0]
        exp = sum(r[0] for r in sql(MGMT, "select expected_points from collection_runs where event_id=%s", event_id))
        assert total == exp > n
        rec = sql(MGMT, "select correlation_id, source_service from event_records where event_id=%s", event_id)
        assert rec == [(corr, "collector")]
        ob = sql(MGMT, "select state from event_outbox where event_id=%s", event_id)
        assert ob == [("DELIVERED",)]
        q = httpx.get(f"{LOKI}/loki/api/v1/query_range", params={"query": f'{{service="collector"}} |= "{event_id}"',
                      "start": str(int((time.time() - 3600) * 1e9)), "end": str(int(time.time() * 1e9))}).json()
        lines = [json.loads(v[1]) for s in q["data"]["result"] for v in s["values"]]
        assert any(x["event_id"] == event_id and x["correlation_id"] == corr and x["points"] == total for x in lines)
        assert any(m.get("correlation_id") == corr and m.get("type") == "invalidate" for m in msgs), msgs[-5:]
        STATE.setdefault("events", {})[serial] = {"event_id": event_id, "correlation_id": corr, "points": total}
    noc = {t_["device_id"]: t_ for t_ in c.get("/v1/noc/overview").json()["devices"]}
    assert all(noc[d]["state"] == "ONLINE" for d in devs.values())
    evidence("pipeline_correlation", {"events": STATE["events"], "ws_messages": len(msgs), "workflow_step": st["step"]})


def _plan_for(c, did):
    return next(p for p in c.get("/v1/collection/plans").json() if p["device_id"] == did)["id"]


def _poll(c, did):
    return c.post(f"/v1/collection/plans/{_plan_for(c, did)}/run-now").json()["summary"]


def _latest_run(did):
    return sql(MGMT, "select event_id, state from collection_runs where device_id=%s order by started_at desc limit 1", did)[0]


def test_05_nats_outage_is_buffered_by_the_outbox_and_delivered_once_after_recovery(c):
    did = STATE["devices"]["EMU-FS-24"]
    stack("stop", "nats")
    time.sleep(1)
    s = _poll(c, did)
    assert s["events_queued"] == 1 and s["succeeded"] == 0          # read from device, persistence not confirmed
    ev, state = _latest_run(did)
    assert state == "COLLECTED"
    time.sleep(4)
    assert sql(MGMT, "select state from event_outbox where event_id=%s", ev) == [("PENDING",)]
    assert sql(TS, "select count(*) from telemetry_points where source_event_id=%s", ev)[0][0] == 0
    stack("start", "nats")
    wait(lambda: _latest_run(did)[1] == "SUCCEEDED", 90, 1, "delivery after NATS restart")
    n1 = sql(TS, "select count(*) from telemetry_points where source_event_id=%s", ev)[0][0]
    time.sleep(5)
    assert sql(TS, "select count(*) from telemetry_points where source_event_id=%s", ev)[0][0] == n1 > 0
    assert sql(MGMT, "select count(*) from event_records where event_id=%s", ev)[0][0] == 1
    evidence("nats_outage", {"event_id": ev, "points": n1})


def test_06_loki_outage_retries_ingest_and_never_acks_unlogged_events(c):
    did = STATE["devices"]["EMU-FS-48"]
    stack("stop", "loki")
    _poll(c, did)
    ev, _ = _latest_run(did)
    time.sleep(8)
    assert _latest_run(did)[1] == "COLLECTED"                     # not SUCCEEDED while logs cannot be written
    assert sql(MGMT, "select count(*) from event_records where event_id=%s", ev)[0][0] == 0
    stack("start", "loki")
    wait(lambda: _latest_run(did)[1] == "SUCCEEDED", 120, 2, "ingest after Loki restart")
    n = sql(TS, "select count(*) from telemetry_points where source_event_id=%s", ev)[0][0]
    exp = sum(r[0] for r in sql(MGMT, "select expected_points from collection_runs where event_id=%s", ev))
    assert n == exp                                                # retried writes were idempotent: no duplicates
    evidence("loki_outage", {"event_id": ev, "points": n})


def test_07_device_offline_then_recovery_and_session_expiry(c):
    did = STATE["devices"]["EMU-FS-24"]
    httpx.post(f"{EMU}/10.250.0.24/down")
    for _ in range(3):
        _poll(c, did)
        time.sleep(0.5)
    wait(lambda: c.get(f"/v1/devices/{did}").json()["state"] == "OFFLINE", 30, 1, "OFFLINE")
    wait(lambda: any(a["rule_id"] == "DEVICE_UNREACHABLE" and a["device_id"] == did for a in c.get("/v1/alerts").json()),
         30, 1, "unreachable alert")
    httpx.post(f"{EMU}/10.250.0.24/up")
    httpx.post(f"{EMU}/10.250.0.24/expire")                       # also: every old session is gone
    _poll(c, did)
    wait(lambda: c.get(f"/v1/devices/{did}").json()["state"] == "ONLINE", 30, 1, "ONLINE after recovery")
    wait(lambda: not any(a["rule_id"] == "DEVICE_UNREACHABLE" and a["device_id"] == did
                         for a in c.get("/v1/alerts").json()), 30, 1, "alert resolved")
    httpx.post(f"{EMU}/10.250.0.24/password/rotated-secret")
    _poll(c, did)
    wait(lambda: c.get(f"/v1/devices/{did}").json()["state"] == "UNKNOWN", 30, 1, "UNKNOWN on auth failure")
    httpx.post(f"{EMU}/10.250.0.24/password/emulator-password")
    _poll(c, did)
    wait(lambda: c.get(f"/v1/devices/{did}").json()["state"] == "ONLINE", 30, 1, "ONLINE again")
    assert httpx.get(f"{EMU}/10.250.0.24/violations").json() == []


def test_08_per_port_alert_is_correlated_end_to_end(c):
    did = STATE["devices"]["EMU-FS-48"]
    _poll(c, did)
    wait(lambda: _latest_run(did)[1] == "SUCCEEDED", 30, 1)
    httpx.post(f"{EMU}/10.250.0.25/errors/9/500")
    _poll(c, did)
    ev = wait(lambda: (lambda r: r[0] if r[1] == "SUCCEEDED" else None)(_latest_run(did)), 30, 1)
    a = wait(lambda: next((x for x in c.get("/v1/alerts").json() if x["device_id"] == did and x["entity"] == "port:1/1/9"), None),
             30, 1, "per-port alert")
    assert a["rule_id"] == "PORT_ERRORS" and a["event_id"] == ev
    corr = sql(MGMT, "select correlation_id from event_records where event_id=%s", ev)[0][0]
    assert a["correlation_id"] == corr


def test_09_database_restart_and_api_restart_lose_nothing(c):
    did = STATE["devices"]["EMU-FS-24"]
    before = sql(TS, "select count(*) from telemetry_points where device_id=%s", did)[0][0]
    stack("stop", "pg")
    time.sleep(2)
    stack("start", "pg")
    stack("restart", "api")
    cl = httpx.Client(base_url=API, timeout=60)
    tok = cl.post("/auth/login", json={"username": "admin", "password": "FullStack-Admin-2026!"}).json()["access_token"]
    cl.headers["Authorization"] = f"Bearer {tok}"
    w = cl.get(f"/v1/workflows/{STATE['wid']}").json()
    assert len(w["devices"]) == 2 and w["history"]                  # workflow state persisted
    s = _poll(cl, did)
    assert s["events_queued"] == 1
    wait(lambda: _latest_run(did)[1] == "SUCCEEDED", 90, 1, "pipeline after DB + API restart")
    assert sql(TS, "select count(*) from telemetry_points where device_id=%s", did)[0][0] > before
    assert sql(MGMT, "select count(*) from event_outbox where state='DLQ'")[0][0] == 0


def test_10_device_syslog_flows_through_alloy_into_loki(c):
    marker = f"inm-fs-{uuid.uuid4().hex[:10]}"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    msg = f"<134>1 {ts} fs-sw-24 hpe-restd 1234 - - Configuration changed by admin {marker}"
    with socket.create_connection(("127.0.0.1", 1514), timeout=5) as s:
        s.sendall(f"{len(msg)} {msg}".encode())
    def found():
        q = httpx.get(f"{LOKI}/loki/api/v1/query_range", params={"query": f'{{source="syslog", host="fs-sw-24"}} |= "{marker}"',
                      "start": str(int((time.time() - 600) * 1e9)), "end": str(int(time.time() * 1e9))}).json()
        return q["data"]["result"]
    res = wait(found, 60, 2, "syslog line in Loki")
    evidence("syslog_alloy", {"marker": marker, "streams": [r["stream"] for r in res]})


def test_11_alert_notification_delivered_once_by_the_worker_with_persisted_values(c):
    """E2E-20: the worker's notification loop delivers the per-port alert from test_08 to a real webhook; the
    payload equals the alert the API serves, the delivery row is SENT, and further sweeps do not resend it."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ch = c.post("/v1/notifications/channels", json={"name": "fs-noc", "kind": "webhook", "min_severity": "minor",
                                                         "target": f"http://127.0.0.1:{srv.server_port}/inm"})
        assert ch.status_code == 201, ch.text
        did = STATE["devices"]["EMU-FS-48"]
        alert = next(x for x in c.get("/v1/alerts").json() if x["device_id"] == did and x["entity"] == "port:1/1/9")
        mine = lambda: [g for g in got if g.get("alert_id") == alert["id"]]
        wait(lambda: mine() or None, 90, 2, "webhook notification from the worker")
        p = mine()[0]
        for k in ("rule_id", "severity", "message", "entity", "correlation_id", "device_id"):
            assert p[k] == alert[k], k
        rows = sql(MGMT, "select state, attempts from notification_deliveries where alert_id=%s and kind='OPENED'", alert["id"])
        assert rows == [("SENT", 1)]
        time.sleep(35)                                   # at least two more worker/scheduler sweeps
        assert len(mine()) == 1
        evidence("notification_delivery", {"alert": alert, "payload": p, "delivery_rows": rows})
    finally:
        srv.shutdown()
