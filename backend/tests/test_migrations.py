"""Alembic: fresh install, and in-place upgrade of a database created before migrations existed."""
import sqlite3

from sqlalchemy import create_engine, inspect, text

from backend.config import get_settings
from backend.database import session as dbs
from backend.database.models import Base


def _setup(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("SIMS_DATABASE_URL", url)
    get_settings.cache_clear()
    dbs._engine = dbs._SessionLocal = None
    return tmp_path / "m.db"


def test_fresh_database_gets_all_tables_at_head(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    dbs.create_schema()
    insp = inspect(create_engine(f"sqlite:///{path}"))
    assert set(insp.get_table_names()) >= {t.name for t in Base.metadata.sorted_tables} | {"alembic_version"}
    assert "exp_score" in {c["name"] for c in insp.get_columns("clients")}
    dbs.create_schema()  # idempotent: a second start changes nothing


def test_pre_migration_database_is_upgraded_in_place_without_losing_data(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    # Build the schema exactly as the pre-Alembic app did: baseline tables only, no alembic_version, no Phase 2 columns.
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng, tables=[t for t in Base.metadata.sorted_tables if t.name in dbs.BASELINE_TABLES])
    with eng.begin() as c:
        for tbl in ("clients", "alerts", "access_points", "ap_samples", "site_samples"):  # drop Phase 2 columns
            cols = [x["name"] for x in inspect(c).get_columns(tbl)]
            for col in [x for x in cols if x.startswith("exp_") or x in ("poor_streak", "subject")]:
                c.execute(text(f"ALTER TABLE {tbl} DROP COLUMN {col}"))
        c.execute(text("INSERT INTO sites (name, vc_port, simulated, enabled, created_at) "
                       "VALUES ('Keep-Me', 4343, 0, 1, '2026-01-01')"))
    dbs.create_schema()
    con = sqlite3.connect(path)
    assert con.execute("select name from sites").fetchall() == [("Keep-Me",)]  # data preserved
    assert con.execute("select version_num from alembic_version").fetchone() == ("0004",)  # INM: head advanced by expand-only migration 0004 (docs/intentional_changes.md)
    cols = {r[1] for r in con.execute("pragma table_info(clients)")}
    assert {"exp_score", "exp_band", "poor_streak"} <= cols
    assert "client_events" in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
