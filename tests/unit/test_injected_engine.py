"""Regression coverage for SQLAlchemy engine injection in telemetry adapters."""
from sqlalchemy import create_engine, text

from backend.infrastructure.telemetry.timescale import TimescaleTelemetryStore


def test_injected_sqlite_engine_is_used_and_migratable():
    engine = create_engine("sqlite:///:memory:")
    store = TimescaleTelemetryStore("sqlite:///:memory:", engine=engine)
    assert store.engine is engine
    assert store.migrate() == ["t001", "t002"]
    assert store.migrate() == []
    assert store.schema_revision() == "t002"

    # If the adapter silently builds a new in-memory engine, this table is absent.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM telemetry_schema_version")).scalar() == 2


def test_engine_injection_wins_over_url_engine_construction():
    engine = create_engine("sqlite:///:memory:")
    store = TimescaleTelemetryStore("postgresql+psycopg://unreachable.invalid/db", engine=engine)
    assert store.engine is engine
    assert store.migrate() == ["t001", "t002"]
