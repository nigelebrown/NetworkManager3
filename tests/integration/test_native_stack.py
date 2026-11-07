"""Integration tests against a REAL running stack (PostgreSQL, TimescaleDB, NATS JetStream, Loki, Alloy, AI, API,
worker), started by dev/native_stack.sh. Skipped unless INM_IT=1.

Covers: P1-02 (role failure detected + recovery), P1-03/P1-DEP-09 (durable replay, exactly-once), INTEG-P1-11
(NATS outage), P1-04, P1-DEP-08 (syslog mapped/unmapped), P1-09 (persistence across restart), SEC-01
(unauthenticated / least-privilege bus and DB access), INTEG-P1-05 (two workers, one poll per interval),
INTEG-P1-12 (live WebSocket cross-site isolation).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("INM_IT") != "1", reason="INM_IT=1 and a running native stack required")
ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("INM_IT_API", "http://127.0.0.1:58000")
NATS_URL = "nats://127.0.0.1:4222"
STACK = str(ROOT / "dev" / "native_stack.sh")
ADMIN_PW = os.environ.get("INM_IT_ADMIN_PASSWORD", "e2e-Admin-Password-2026!")


def sh(*args):
    subprocess.run([STACK, *args], check=True, capture_output=True, timeout=240)


def wait(pred, timeout=60, step=1.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = pred()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def token(user="admin", pw=None):
    r = httpx.post(API + "/api/auth/login", json={"username": user, "password": pw or ADMIN_PW}, timeout=10)
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def role_status(name):
    from backend.application.health import run_probe
    from backend.infrastructure import probes
    from backend.infrastructure.runtime import inm_settings, log_store, telemetry_store
    inm_settings.cache_clear(); telemetry_store.cache_clear(); log_store.cache_clear()
    return run_probe(name, name, probes.all_probes()[name]).status


def mgmt_count(sql, **params):
    from sqlalchemy import create_engine, text
    eng = create_engine(os.environ["SIMS_DATABASE_URL"])
    with eng.connect() as c:
        return c.execute(text(sql), params).scalar()


@pytest.fixture(scope="module", autouse=True)
def stack_up():
    # Probes run in this process with the API's bus identity (as they do inside the api container).
    os.environ["INM_NATS_USER"] = "inm_api"
    os.environ["INM_NATS_PASSWORD"] = os.environ["INM_NATS_API_PASSWORD"]
    assert role_status("event_bus") == "UP", "event-bus probe must be UP with valid credentials before outage tests"
    sh("start")
    assert wait(lambda: httpx.get(API + "/api/v1/ready", timeout=3).json()["ready"], 90)
    from backend.database.session import init_engine
    init_engine()
    yield
    sh("start")


# ------------------------------------------------------------------ SEC-01
def test_unauthenticated_and_wrong_role_bus_access_denied():
    import nats

    async def go():
        with pytest.raises(Exception):
            await nats.connect(NATS_URL, connect_timeout=3, max_reconnect_attempts=0, allow_reconnect=False)
        with pytest.raises(Exception):
            await nats.connect(NATS_URL, user="inm_api", password="wrong", connect_timeout=3, max_reconnect_attempts=0, allow_reconnect=False)
        errors = []

        async def eh(e):
            errors.append(str(e))
        nc = await nats.connect(NATS_URL, user="inm_api", password=os.environ["INM_NATS_API_PASSWORD"], error_cb=eh,
                                allow_reconnect=False)
        await nc.publish("inm.events.device.telemetry.observed", b"{}")  # API identity may not inject device events
        await nc.flush(); await asyncio.sleep(0.5)
        await nc.close()
        return errors
    errors = asyncio.run(go())
    assert any("permissions violation" in e.lower() for e in errors), errors


def test_database_rejects_bad_credentials():
    from sqlalchemy import create_engine
    url = os.environ["SIMS_DATABASE_URL"].replace(os.environ["MGMT_PW"], "wrong-password")
    with pytest.raises(Exception, match="password authentication failed"):
        create_engine(url).connect()


# ------------------------------------------------------------------ P1-03 / P1-DEP-09 durable replay
def test_event_published_while_consumer_offline_is_replayed_exactly_once():
    from backend.domain.events import new_event
    from backend.infrastructure.bus.nats_bus import JetStreamBus, connect, NatsSettings
    sh("stop-role", "worker")
    e = new_event("synthetic.probe", source_service="it-test", source_type="DEMO", actor_id="it",
                  payload={"summary": "durable replay test", "points": [{"metric": "synthetic_probe", "value": 1.0, "unit": "bool"}]},
                  device_id=str(uuid.uuid4()))

    async def publish_twice():
        nc = await connect(NatsSettings(NATS_URL, "inm_worker", os.environ["INM_NATS_WORKER_PASSWORD"]))
        js = nc.jetstream()
        a = await js.publish(e.subject, e.to_json(), headers={"Nats-Msg-Id": e.event_id})
        b = await js.publish(e.subject, e.to_json(), headers={"Nats-Msg-Id": e.event_id})  # producer retry
        await nc.close()
        return a, b
    a, b = asyncio.run(publish_twice())
    assert b.duplicate is True and a.seq == b.seq
    time.sleep(3)
    assert mgmt_count("select count(*) from event_records where event_id=:e", e=e.event_id) == 0  # consumer offline
    sh("start")
    assert wait(lambda: mgmt_count("select count(*) from event_records where event_id=:e", e=e.event_id) == 1, 60)
    time.sleep(3)
    assert mgmt_count("select count(*) from event_records where event_id=:e", e=e.event_id) == 1


# ------------------------------------------------------------------ INTEG-P1-11 NATS outage
def test_nats_outage_degrades_then_recovers_without_duplicates():
    sh("stop-role", "nats")
    assert wait(lambda: role_status("event_bus") == "DOWN", 30)
    assert wait(lambda: httpx.get(API + "/api/v1/ready", timeout=3).status_code == 503, 30)
    h = token()
    r = httpx.post(API + "/api/v1/system/synthetic-event", headers=h, json={}, timeout=20)
    assert r.status_code == 503  # reported, not faked
    sh("start")
    assert wait(lambda: role_status("event_bus") == "UP", 60)
    assert wait(lambda: httpx.get(API + "/api/v1/ready", timeout=3).json()["ready"], 60)
    r = wait(lambda: (x := httpx.post(API + "/api/v1/system/synthetic-event", headers=h, json={}, timeout=20)).status_code == 202 and x, 90, 3)
    assert r, "synthetic round-trip did not become available after NATS recovered"
    cid = r.json()["correlation_id"]
    trace = wait(lambda: (t := httpx.get(f"{API}/api/v1/system/synthetic-event/{cid}", headers=h, timeout=10).json())["complete"] and t, 60)
    assert trace and len(trace["management_db"]) == 1 and len(trace["timeseries_db"]) == 1


# ------------------------------------------------------------------ P1-02 each role failure is visible
@pytest.mark.parametrize("role,proc", [("logging", "loki"), ("ai", "ai"), ("network_worker", "worker"),
                                       ("timeseries_db", "pg-ts"), ("management_db", "pg-mgmt")])
def test_stopping_a_role_is_detected_and_recovers(role, proc):
    assert wait(lambda: role_status(role) == "UP", 90), f"{role} not UP before the test"
    sh("stop-role", proc)
    assert wait(lambda: role_status(role) in ("DOWN", "DEGRADED"), 120, 3), f"{role} still reported UP after stop"
    sh("start")
    assert wait(lambda: role_status(role) == "UP", 120, 3), f"{role} did not recover"


# ------------------------------------------------------------------ P1-DEP-08 syslog correlation
def test_syslog_mapped_and_unmapped():
    tag = uuid.uuid4().hex[:10]
    for host in ("demo-hq-cx6100-01", "unknown-host-xyz"):
        msg = f"<134>1 {datetime.now(timezone.utc).isoformat()} {host} it-test - - - it-{tag} DEMO line"
        with socket.create_connection(("127.0.0.1", 1514), timeout=5) as s:
            s.sendall(f"{len(msg.encode())} {msg}".encode())
    h = token()
    res = wait(lambda: (r := httpx.get(f"{API}/api/v1/logs?stream=syslog&contains=it-{tag}&minutes=10", headers=h, timeout=10).json())
               and len(r["entries"]) == 2 and r, 40)
    assert res
    by = {e["labels"]["host"]: e for e in res["entries"]}
    assert by["demo-hq-cx6100-01"]["correlation"] == "MAPPED" and by["demo-hq-cx6100-01"]["device"]["source_type"] == "DEMO"
    assert by["unknown-host-xyz"]["correlation"] == "UNMAPPED" and "unknown-host-xyz" in by["unknown-host-xyz"]["unmapped_reason"]


# ------------------------------------------------------------------ INTEG-P1-05 duplicate workers
def test_second_worker_does_not_duplicate_polls():
    env = {**os.environ, "SIMS_ROLE": "worker", "INM_NATS_USER": "inm_worker", "INM_NATS_PASSWORD": os.environ["INM_NATS_WORKER_PASSWORD"]}
    p = subprocess.Popen(["python", "-m", "workers.network_worker.main"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        interval = int(os.environ.get("SIMS_POLL_INTERVAL_SECONDS", "15"))
        time.sleep(interval * 3 + 5)
        from sqlalchemy import create_engine, text
        eng = create_engine(os.environ["SIMS_DATABASE_URL"])
        with eng.connect() as c:
            rows = c.execute(text("select correlation_id, count(*) from event_records where event_type='site.poll.completed' "
                                  "and ingested_at > now() - interval '2 minutes' group by 1")).all()
            samples = c.execute(text("select site_id, count(*) from site_samples where ts > now() - (:s || ' seconds')::interval group by 1"),
                                {"s": interval * 3}).all()
    finally:
        p.terminate(); p.wait(30)
    assert rows and all(n == 3 for _, n in rows), rows          # 3 sites per interval, never doubled
    assert samples and all(n <= 4 for _, n in samples), samples  # at most one legacy poll per interval per site


# ------------------------------------------------------------------ INTEG-P1-12 live WS isolation
def test_live_websocket_only_delivers_granted_site_notices():
    import websockets
    h = token()
    sites = httpx.get(API + "/api/sites", headers=h, timeout=10).json()
    keep = sites[0]["id"]
    name = f"it.scoped.{uuid.uuid4().hex[:6]}"
    r = httpx.post(API + "/api/v1/users", headers=h, json={"username": name, "role": "operator", "temporary_password": "temporary-pass-123", "site_scope": "granted"}, timeout=10)
    uid = r.json()["id"]
    httpx.put(f"{API}/api/v1/users/{uid}/grants", headers=h, json={"site_ids": [keep]}, timeout=10)
    th = token(name, "temporary-pass-123")
    httpx.post(API + "/api/auth/change-password", headers=th, json={"current_password": "temporary-pass-123", "new_password": "it-scoped-password-1"}, timeout=10)
    tok = token(name, "it-scoped-password-1")["Authorization"][7:]

    async def listen():
        got = []
        async with websockets.connect(API.replace("http", "ws") + "/ws/v1/events") as ws:
            await ws.send(json.dumps({"type": "auth", "token": tok, "since": None}))
            end = time.time() + int(os.environ.get("SIMS_POLL_INTERVAL_SECONDS", "15")) * 2 + 5
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                except asyncio.TimeoutError:
                    continue
                if m["type"] == "invalidate":
                    got.append(m)
        return got
    got = asyncio.run(listen())
    assert got, "scoped user received no notices at all"
    assert all(m.get("legacy_site_ids") in (None, [keep]) for m in got), [m.get("legacy_site_ids") for m in got]
    assert all(set(m["topics"]) <= {"system"} for m in got if m.get("legacy_site_ids") is None)


# ------------------------------------------------------------------ P1-09 persistence across full restart
def test_state_persists_across_full_restart():
    h = token()
    before = {t: mgmt_count(f"select count(*) from {t}") for t in ("audit_log", "event_records", "users", "inm_devices")}
    from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore
    ts = TimescaleTelemetryStore(os.environ["INM_TELEMETRY_DATABASE_URL"])
    tsc = len(ts.query(device_id=None, metric_name=None, since=datetime.now(timezone.utc) - timedelta(days=1), limit=5000))
    sh("stop")
    sh("start")
    assert wait(lambda: httpx.get(API + "/api/v1/ready", timeout=3).json()["ready"], 120)
    after = {t: mgmt_count(f"select count(*) from {t}") for t in before}
    assert all(after[t] >= before[t] for t in before), (before, after)
    assert len(ts.query(device_id=None, metric_name=None, since=datetime.now(timezone.utc) - timedelta(days=1), limit=5000)) >= min(tsc, 5000)
    v = httpx.get(API + "/api/v1/audit/verify", headers=token(), timeout=20).json()
    assert v["ok"] is True
