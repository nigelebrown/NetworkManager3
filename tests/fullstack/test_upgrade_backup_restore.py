"""Data-layer evidence for update / backup / restore on real PostgreSQL 16 + TimescaleDB 2.22 (Section 7).

The CentOS Stream 9 scripts (deployment/centos9/*.sh) orchestrate container images that cannot be pulled in this
build environment, so the CentOS host run itself is BLOCKED. These tests exercise exactly the database operations
those scripts perform, on the same engine versions:

* upgrade: an rc1 database (management 0009 + telemetry t001) holding data is upgraded in place to 0010 + t002;
  every row survives, port telemetry is back-filled with its interface identity, and the 1.2.0-rc2 ORM reads it.
* backup/restore: pg_dump -Fc of both databases from the running full stack (after the full-stack suite), restored
  into new databases with timescaledb_pre_restore()/post_restore() as restore.sh does; every table's row count and
  content checksum match, the restored schema is at head, and re-running migrations is a no-op.

Run with tests/fullstack/stack.sh up and INM_FS=1 (after test_fullstack.py for a populated database).
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("INM_FS"), reason="INM_FS not set (needs tests/fullstack/stack.sh up)")

ROOT = Path(__file__).resolve().parents[2]
PGBIN = "/usr/lib/postgresql/16/bin"
SU = "host=/tmp port=56432 user=postgres dbname=postgres"
EVIDENCE = ROOT / "evidence" / "fullstack"


def admin(sql: str) -> None:
    with psycopg.connect(SU, autocommit=True) as c:
        c.execute(sql)


def dsn(db: str, user="postgres") -> str:
    return f"host=/tmp port=56432 user={user} dbname={db}"


def sa_url(db: str) -> str:
    return f"postgresql+psycopg://inm_app:fs-mgmt-pass@127.0.0.1:56432/{db}"


def alembic(db: str, *args: str) -> str:
    env = {**os.environ, "SIMS_DATABASE_URL": sa_url(db)}
    r = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *args],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout + r.stderr


def fresh(db: str, owner: str) -> None:
    admin(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
    admin(f"CREATE DATABASE {db} OWNER {owner}")
    with psycopg.connect(dsn(db), autocommit=True) as c:
        c.execute(f"GRANT ALL ON SCHEMA public TO {owner}")


def fill(c, table: str, overrides: dict) -> None:
    """Insert one row giving every NOT NULL column without a default a type-appropriate value."""
    cols = c.execute("""SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s
                        AND is_nullable='NO' AND column_default IS NULL""", (table,)).fetchall()
    row = {}
    for name, typ in cols:
        row[name] = (str(uuid.uuid4()) if name == "id" else 0 if typ in ("integer", "bigint", "double precision", "real")
                     else False if typ == "boolean" else datetime.now(timezone.utc) if "timestamp" in typ
                     else json.dumps({}) if typ in ("json", "jsonb") else f"v-{name}"[:8])
    row.update(overrides)
    c.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", list(row.values()))


def counts(db: str) -> dict[str, tuple[int, str]]:
    out = {}
    with psycopg.connect(dsn(db)) as c:
        tables = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
        for t in tables:
            n, h = c.execute(f"SELECT count(*), coalesce(md5(string_agg(md5(x::text), '' ORDER BY md5(x::text))), '') "
                             f"FROM {t} x").fetchone()
            out[t] = (n, h)
    return out


def test_rc1_to_rc2_upgrade_preserves_data_on_postgresql_and_timescaledb():
    from backend.infrastructure.telemetry import timescale as T
    # ---- management: build the rc1 schema (0009) with data, then upgrade to head
    fresh("inm_upgrade", "inm_app")
    alembic("inm_upgrade", "upgrade", "0009")
    dev_id = str(uuid.uuid4())
    with psycopg.connect(dsn("inm_upgrade"), autocommit=True) as c:
        fill(c, "inm_devices", {"id": dev_id, "serial_number": "UPG-CX-1", "family": "switch", "source_type": "REAL"})
        fill(c, "inm_alerts", {"device_id": dev_id, "rule_id": "DEVICE_OFFLINE", "severity": "critical",
                               "message": "rc1 alert", "state": "OPEN"})
        plan_id = str(uuid.uuid4())
        fill(c, "collection_plans", {"id": plan_id, "device_id": dev_id, "state": "ACTIVE",
                                     "metric_families": json.dumps(["system"])})
        fill(c, "collection_runs", {"plan_id": plan_id, "device_id": dev_id, "state": "SUCCEEDED",
                                    "dedupe_key": "rc1-run", "metric_family": "system"})
        fill(c, "inm_config_templates", {"name": "rc1 base", "family": "switch", "scope": "global",
                                         })
        before = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("inm_devices", "inm_alerts", "collection_plans", "collection_runs", "inm_config_templates")}
    with psycopg.connect(dsn("inm_upgrade")) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0009"
    alembic("inm_upgrade", "upgrade", "head")
    with psycopg.connect(dsn("inm_upgrade")) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0010"
        after = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
        new = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert after == before
    assert {"event_outbox", "onboarding_workflows", "change_recovery_journal", "device_groups",
            "notification_channels", "notification_deliveries"} <= new
    # the 1.2.0-rc2 ORM reads upgraded rows (new nullable columns default to NULL)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.database.models_phases import InmAlert
    with Session(create_engine(sa_url("inm_upgrade"))) as s:
        a = s.query(InmAlert).one()
        assert (a.message, a.entity, a.correlation_id) == ("rc1 alert", None, None)
    # ---- telemetry: t001 table with per-port rows (one per event, as rc1 could store), then upgrade to t002
    fresh("inm_tel_upgrade", "inm_metrics")
    from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore
    url = "postgresql+psycopg://inm_metrics:fs-ts-pass@127.0.0.1:56432/inm_tel_upgrade"
    saved = list(T._MIGRATIONS)
    try:
        T._MIGRATIONS[:] = [m for m in saved if m[0] == "t001"]
        assert TimescaleTelemetryStore(url).migrate() == ["t001"]
    finally:
        T._MIGRATIONS[:] = saved
    t0 = datetime.now(timezone.utc) - timedelta(hours=1)
    with psycopg.connect(dsn("inm_tel_upgrade"), autocommit=True) as c:
        for i in range(1, 4):
            c.execute("""INSERT INTO telemetry_points (observed_at, device_id, metric_name, value, unit,
                         collection_method, quality, source_type, source_event_id, tags)
                         VALUES (%s, %s, 'port_rx_bps', %s, 'bps', 'REST', 'GOOD', 'REAL', %s, %s)""",
                      (t0, dev_id, 100.0 * i, str(uuid.uuid4()), json.dumps({"port": f"1/1/{i}"})))
        c.execute("""INSERT INTO telemetry_points (observed_at, device_id, metric_name, value, unit, collection_method,
                     quality, source_type, source_event_id) VALUES (%s, %s, 'cpu_percent', 12, '%%', 'REST', 'GOOD',
                     'REAL', %s)""", (t0, dev_id, str(uuid.uuid4())))
    assert TimescaleTelemetryStore(url).migrate() == ["t002"]
    assert TimescaleTelemetryStore(url).migrate() == []                      # idempotent
    with psycopg.connect(dsn("inm_tel_upgrade")) as c:
        rows = c.execute("SELECT entity_type, entity_id, value FROM telemetry_points ORDER BY value").fetchall()
        idx = c.execute("SELECT indexdef FROM pg_indexes WHERE indexname='uq_telemetry_identity'").fetchone()[0]
    assert rows == [("device", "", 12.0), ("port", "1/1/1", 100.0), ("port", "1/1/2", 200.0), ("port", "1/1/3", 300.0)]
    assert "entity_type, entity_id" in idx
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "upgrade_rc1_to_rc2.json").write_text(json.dumps(
        {"management": {"from": "0009", "to": "0010", "rows_before": before, "rows_after": after},
         "telemetry": {"from": "t001", "to": "t002", "rows": rows}}, indent=1, default=str))


def test_backup_and_restore_round_trip_of_the_running_full_stack():
    work = Path("/tmp/inm-stack/backup-test")
    subprocess.run(["rm", "-rf", str(work)], check=True)
    work.mkdir(parents=True)
    os.chmod(work, 0o777)
    for db in ("inm_management", "inm_telemetry"):
        subprocess.run(["su", "postgres", "-c", f"{PGBIN}/pg_dump -h /tmp -p 56432 -d {db} -Fc -f {work}/{db}.dump"],
                       check=True)
    src = {"inm_management": counts("inm_management"), "inm_telemetry": counts("inm_telemetry")}
    assert src["inm_telemetry"]["telemetry_points"][0] > 0 and src["inm_management"]["inm_devices"][0] > 0
    fresh("inm_management_restore", "inm_app")
    subprocess.run(["su", "postgres", "-c", f"{PGBIN}/pg_restore -h /tmp -p 56432 -d inm_management_restore "
                    f"--no-owner --role=inm_app --exit-on-error {work}/inm_management.dump"], check=True)
    fresh("inm_telemetry_restore", "inm_metrics")
    with psycopg.connect(dsn("inm_telemetry_restore"), autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        c.execute("SELECT timescaledb_pre_restore()")
    r = subprocess.run(["su", "postgres", "-c", f"{PGBIN}/pg_restore -h /tmp -p 56432 -d inm_telemetry_restore "
                        f"--no-owner {work}/inm_telemetry.dump"], capture_output=True, text=True)
    notices = r.stderr.strip().splitlines()[-5:]
    with psycopg.connect(dsn("inm_telemetry_restore"), autocommit=True) as c:
        c.execute("SELECT timescaledb_post_restore()")
        hyper = c.execute("SELECT count(*) FROM timescaledb_information.hypertables "
                          "WHERE hypertable_name='telemetry_points'").fetchone()[0]
    assert hyper == 1
    dst = {"inm_management": counts("inm_management_restore"), "inm_telemetry": counts("inm_telemetry_restore")}
    for db in src:
        assert set(dst[db]) == set(src[db]), db
        diff = {t: (src[db][t], dst[db][t]) for t in src[db] if src[db][t] != dst[db][t]}
        assert not diff, (db, diff)
    # restored database is at head; migrations are a no-op
    with psycopg.connect(dsn("inm_management_restore")) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0010"
    alembic("inm_management_restore", "upgrade", "head")                    # no-op at head
    from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore
    assert TimescaleTelemetryStore("postgresql+psycopg://inm_metrics:fs-ts-pass@127.0.0.1:56432/"
                                   "inm_telemetry_restore").migrate() == []
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "backup_restore_round_trip.json").write_text(json.dumps({
        "tables": {db: {t: v[0] for t, v in src[db].items()} for db in src},
        "row_counts_and_checksums_match": True, "pg_restore_notices": notices,
        "restored_management_head": "0010", "telemetry_hypertable_restored": True}, indent=1))


def test_retention_drops_only_points_older_than_the_window_on_timescaledb():
    """P3-07: drop_chunks retention (the worker's fallback when no licensed background policy exists) removes points
    older than the window and leaves current data queryable through the store."""
    from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore
    fresh("inm_tel_retention", "inm_metrics")
    url = "postgresql+psycopg://inm_metrics:fs-ts-pass@127.0.0.1:56432/inm_tel_retention"
    store = TimescaleTelemetryStore(url)
    store.migrate()
    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn("inm_tel_retention"), autocommit=True) as c:
        for age_days, v in ((120, 1.0), (100, 2.0), (30, 3.0), (0, 4.0)):
            c.execute("""INSERT INTO telemetry_points (observed_at, device_id, metric_name, value, unit,
                         collection_method, quality, source_type, source_event_id)
                         VALUES (%s, 'dev-r', 'cpu_percent', %s, '%%', 'REST', 'GOOD', 'REAL', %s)""",
                      (now - timedelta(days=age_days), v, str(uuid.uuid4())))
    store.prune(90)
    with psycopg.connect(dsn("inm_tel_retention")) as c:
        left = sorted(r[0] for r in c.execute("SELECT value FROM telemetry_points").fetchall())
    assert left == [3.0, 4.0]
    recent = store.query(device_id="dev-r", metric_name="cpu_percent", since=now - timedelta(days=40))
    assert sorted(p["value"] for p in recent) == [3.0, 4.0]
