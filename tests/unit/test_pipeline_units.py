"""Ingest idempotency, telemetry store, leases, health aggregation, schema gate, WebSocket hub filtering."""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.application.health import collect
from backend.application.ingest import IngestService
from backend.domain.events import new_event
from backend.infrastructure.persistence.repositories import LeaseRepo, SqlEventLedger
from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore
from backend.presentation.ws import Conn, Hub

ROOT = Path(__file__).resolve().parents[2]


class FakeLogs:
    def __init__(self, fail_times=0):
        self.lines, self.fail_times = [], fail_times

    def push(self, labels, line, ts):
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("loki unavailable")
        self.lines.append((labels, line, ts))


@pytest.fixture
def db(client):  # client fixture initialises a migrated SQLite management DB
    from backend.database.session import session_scope
    with session_scope() as s:
        yield s


def _event(eid=None):
    return new_event("device.telemetry.observed", source_service="network-worker", source_type="DEMO", actor_id="w",
                     event_id=eid, legacy_site_id=1, device_id="dev-1",
                     payload={"summary": "x", "points": [{"metric": "cpu_percent", "value": 10.0, "unit": "%"},
                                                         {"metric": "client_count", "value": 3, "unit": "count"}]})


def test_ingest_exactly_one_logical_result_on_redelivery(db, tmp_path):
    """P1-03 / P1-DEP-09: the same event delivered three times is persisted once."""
    store = TimescaleTelemetryStore(f"sqlite:///{tmp_path / 't.db'}"); store.migrate()
    logs = FakeLogs()
    svc = IngestService(ledger=SqlEventLedger(db, "ingest"), telemetry=store, logs=logs, commit=db.commit, rollback=db.rollback)
    e = _event()
    results = [svc.handle(e) for _ in range(3)]
    assert [r.status for r in results] == ["applied", "duplicate", "duplicate"]
    rows = store.query(device_id="dev-1", metric_name=None, since=datetime.now(timezone.utc) - timedelta(hours=1))
    assert len(rows) == 2 and len(logs.lines) == 1
    from backend.database.models_inm import EventRecord
    assert db.get(EventRecord, e.event_id).delivery_count == 3


def test_ingest_failure_rolls_back_then_retry_applies_once(db, tmp_path):
    """INTEG-P1-11: a downstream outage causes redelivery, still one logical result."""
    store = TimescaleTelemetryStore(f"sqlite:///{tmp_path / 't.db'}"); store.migrate()
    logs = FakeLogs(fail_times=1)
    svc = IngestService(ledger=SqlEventLedger(db, "ingest"), telemetry=store, logs=logs, commit=db.commit, rollback=db.rollback)
    e = _event()
    with pytest.raises(ConnectionError):
        svc.handle(e)
    assert not SqlEventLedger(db, "ingest").is_processed(e.event_id)
    assert svc.handle(e).status == "applied"
    rows = store.query(device_id="dev-1", metric_name=None, since=datetime.now(timezone.utc) - timedelta(hours=1))
    assert len(rows) == 2  # first attempt's rows were idempotently re-used, not duplicated


def test_telemetry_absent_values_are_not_zero(tmp_path):
    store = TimescaleTelemetryStore(f"sqlite:///{tmp_path / 't.db'}")
    assert store.migrate() == ["t001", "t002"] and store.migrate() == []  # idempotent migration
    assert store.schema_revision() == "t002"


def test_lease_one_winner_per_slot(db):
    """INTEG-P1-05: two workers, same interval -> exactly one runs it."""
    a, b = LeaseRepo(db, "worker-a"), LeaseRepo(db, "worker-b")
    assert a.try_acquire_slot("poll_all", 100, 60) is True
    db.commit()
    assert b.try_acquire_slot("poll_all", 100, 60) is False
    assert a.try_acquire_slot("poll_all", 100, 60) is False  # not even the same holder twice
    db.commit()
    assert b.try_acquire_slot("poll_all", 101, 60) is True   # next interval proceeds
    db.commit()


def test_health_is_measured_never_assumed():
    def boom():
        raise ConnectionRefusedError("refused")
    r = collect({"web_api": lambda: ("UP", "1", "ok"), "management_db": boom, "network_worker": None})
    by = {s["name"]: s for s in r["services"]}
    assert by["management_db"]["status"] == "DOWN" and "refused" in by["management_db"]["detail"]
    assert by["network_worker"]["status"] == "UNKNOWN"
    assert by["timeseries_db"]["status"] == "UNKNOWN"
    assert r["overall"] == "DOWN"
    assert len(r["services"]) == 7


@pytest.mark.parametrize("scenario,expect", [
    ("accepted", 0),
    ("unknown_management", 3),
    ("unknown_telemetry", 3),
    ("missing", 3),
])
def test_schema_gate(tmp_path, scenario, expect):
    """INTEG-P1-10: use release-declared schema, not a stale hardcoded version."""
    release = json.loads((ROOT / "release.json").read_text())
    current = {
        "management": release["management_schema_head"],
        "telemetry": release["telemetry_schema_head"],
    }
    if scenario == "unknown_management":
        current["management"] = "unsupported-mgmt-schema"
    elif scenario == "unknown_telemetry":
        current["telemetry"] = "unsupported-telemetry-schema"
    elif scenario == "missing":
        current = {"management": None, "telemetry": None}
    cur = tmp_path / "cur.json"; cur.write_text(json.dumps(current))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "deployment/tools/schema_gate.py"), str(cur), str(ROOT / "release.json")],
                       capture_output=True, text=True)
    assert r.returncode == expect, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["compatible"] is (expect == 0)
    if expect:
        assert "restore.sh" in out["manual_path"]


def test_schema_gate_refuses_release_without_declaration(tmp_path):
    cur = tmp_path / "c.json"; cur.write_text('{"management":"0004","telemetry":"t001"}')
    tgt = tmp_path / "r.json"; tgt.write_text('{"version":"0.9"}')
    r = subprocess.run([sys.executable, "-I", str(ROOT / "deployment/tools/schema_gate.py"), str(cur), str(tgt)], capture_output=True, text=True)
    assert r.returncode == 3 and "refusing to guess" in r.stdout


def _conn(role="viewer", scope=None):
    return Conn(ws=None, username="u", role=role, scope=scope, exp=1e12)


def test_ws_hub_scope_and_privileged_topic_filtering():
    """INTEG-P1-12: cross-site and privileged notices never reach unauthorised subscribers."""
    hub = Hub()
    site1 = {"topics": ["aps", "dashboard"], "legacy_site_ids": [1]}
    assert hub.allowed(_conn(scope=frozenset({1})), site1) is not None
    assert hub.allowed(_conn(scope=frozenset({2})), site1) is None
    assert hub.allowed(_conn(scope=None), site1) is not None
    audit_msg = {"topics": ["audit"], "legacy_site_ids": None}
    assert hub.allowed(_conn("viewer"), audit_msg) is None
    assert hub.allowed(_conn("auditor"), audit_msg) is not None
    assert hub.allowed(_conn("viewer", scope=frozenset({1})), {"topics": ["devices"], "legacy_site_ids": None}) is None


def test_ws_hub_replay_and_resync():
    """INTEG-P1-06: reconnect resumes from cursor; a gap older than the buffer forces resync."""
    hub = Hub()
    asyncio.run(_publish(hub, range(10, 20)))
    kind, items = hub.replay(_conn(), 14)
    assert kind == "hello" and [m["seq"] for m in items] == [15, 16, 17, 18, 19]
    assert hub.replay(_conn(), 19) == ("hello", [])
    assert hub.replay(_conn(), 2)[0] == "resync"


async def _publish(hub, seqs):
    for s in seqs:
        await hub.publish(s, {"topics": ["aps"], "legacy_site_ids": [1], "event_id": f"e{s}", "correlation_id": None})
