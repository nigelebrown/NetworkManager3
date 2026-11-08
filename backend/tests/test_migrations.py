"""Alembic: fresh install, and in-place upgrade of a database created before migrations existed."""
import sqlite3
import json

from sqlalchemy import create_engine, inspect, text

from backend.config import ROOT_DIR, get_settings
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
    expected_head = json.loads((ROOT_DIR / "release.json").read_text())["management_schema_head"]
    assert con.execute("select version_num from alembic_version").fetchone() == (expected_head,)  # latest expand-only migration, including printer tables
    cols = {r[1] for r in con.execute("pragma table_info(clients)")}
    assert {"exp_score", "exp_band", "poor_streak"} <= cols
    assert "client_events" in {r[0] for r in con.execute("select name from sqlite_master where type='table'")}


def test_expand_only_management_0004_to_0006_preserves_existing_site(tmp_path, monkeypatch):
    """The LAN enablement's one-shot migration must safely upgrade an existing 0004 database."""
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    path = _setup(tmp_path, monkeypatch)
    eng = dbs.get_engine()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbs.__file__).parent / "migrations"))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0004")
        conn.execute(text("INSERT INTO sites (name, vc_port, simulated, enabled, created_at) "
                          "VALUES ('Preserved-AP-Site', 4343, 0, 1, '2026-01-01')"))
    con = sqlite3.connect(path)
    assert con.execute("select version_num from alembic_version").fetchone() == ("0004",)
    con.close()

    dbs.create_schema()
    con = sqlite3.connect(path)
    try:
        assert con.execute("select version_num from alembic_version").fetchone() == ("0006",)
        assert con.execute("select name from sites").fetchall() == [("Preserved-AP-Site",)]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "printer_devices" in tables
        assert "onboarding_credentials" in tables
        assert "onboarding_device_credentials" in tables
        assert "onboarding_config_snapshots" in tables
    finally:
        con.close()
