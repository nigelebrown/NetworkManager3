"""TelemetryStorePort on a SEPARATE TimescaleDB database (role 04). Versioned, idempotent schema.

Works on SQLite too (no hypertable) so unit tests exercise the same SQL; `is_timescale` reports the truth and
readiness checks require it in deployed profiles.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ...domain.model import TelemetryPoint

TELEMETRY_HEAD = "t001"
RETENTION_DAYS = 90

_MIGRATIONS = [
    ("t001", [
        """CREATE TABLE IF NOT EXISTS telemetry_points (
            observed_at TIMESTAMPTZ NOT NULL,
            device_id VARCHAR(36) NOT NULL,
            site_id VARCHAR(36),
            metric_name VARCHAR(64) NOT NULL,
            value DOUBLE PRECISION,
            unit VARCHAR(16) NOT NULL,
            collection_method VARCHAR(32) NOT NULL,
            quality VARCHAR(12) NOT NULL,
            source_type VARCHAR(8) NOT NULL,
            source_event_id VARCHAR(36) NOT NULL,
            sample_period_s INTEGER,
            tags TEXT,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_telemetry_event_metric UNIQUE (source_event_id, device_id, metric_name, observed_at)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_telemetry_device_time ON telemetry_points (device_id, observed_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_telemetry_metric_time ON telemetry_points (metric_name, observed_at DESC)",
        "@timescale SELECT create_hypertable('telemetry_points', 'observed_at', if_not_exists => TRUE, migrate_data => TRUE)",
        # Background retention needs the Timescale community licence (shipped in the official image). On an
        # Apache-only build this is skipped and the worker prunes with drop_chunks() (Apache) instead.
        f"@timescale-optional SELECT add_retention_policy('telemetry_points', INTERVAL '{RETENTION_DAYS} days', if_not_exists => TRUE)",
    ]),
]


def _iso(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class TimescaleTelemetryStore:
    def __init__(self, url: str, engine: Engine | None = None):
        self.url = url
        self.engine = engine or create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5) \
            if not url.startswith("sqlite") else create_engine(url)

    # ---- schema ------------------------------------------------------------------------
    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    def is_timescale(self) -> bool:
        if self.is_sqlite:
            return False
        with self.engine.connect() as c:
            return c.execute(text("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")).first() is not None

    def timescale_version(self) -> str | None:
        if self.is_sqlite:
            return None
        with self.engine.connect() as c:
            return c.execute(text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")).scalar()

    def migrate(self) -> list[str]:
        """Apply missing telemetry revisions. Safe to run repeatedly."""
        applied: list[str] = []
        with self.engine.begin() as c:
            c.execute(text("CREATE TABLE IF NOT EXISTS telemetry_schema_version (version VARCHAR(16) PRIMARY KEY, "
                           "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
            if not self.is_sqlite:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            done = {r[0] for r in c.execute(text("SELECT version FROM telemetry_schema_version"))}
            for rev, stmts in _MIGRATIONS:
                if rev in done:
                    continue
                for s in stmts:
                    optional = s.startswith("@timescale-optional ")
                    if s.startswith(("@timescale ", "@timescale-optional ")):
                        if self.is_sqlite:
                            continue
                        s = s.split(" ", 1)[1]
                    if self.is_sqlite:
                        s = s.replace("TIMESTAMPTZ", "TIMESTAMP").replace("DOUBLE PRECISION", "REAL").replace(" DESC)", ")")
                    if optional:
                        sp = c.begin_nested()
                        try:
                            c.execute(text(s))
                            sp.commit()
                        except Exception:
                            sp.rollback()
                        continue
                    c.execute(text(s))
                c.execute(text("INSERT INTO telemetry_schema_version (version) VALUES (:v)"), {"v": rev})
                applied.append(rev)
        return applied

    def schema_revision(self) -> str | None:
        try:
            with self.engine.connect() as c:
                rows = [r[0] for r in c.execute(text("SELECT version FROM telemetry_schema_version"))]
        except Exception:
            return None
        return max(rows) if rows else None

    # ---- data --------------------------------------------------------------------------
    def write_points(self, points: list[TelemetryPoint]) -> int:
        """Idempotent insert: a redelivered event writes nothing new (unique on event/device/metric/time)."""
        if not points:
            return 0
        rows = [dict(observed_at=_iso(p.observed_at), device_id=p.device_id, site_id=p.site_id, metric_name=p.metric_name,
                     value=p.value, unit=p.unit, collection_method=p.collection_method, quality=p.quality.value,
                     source_type=p.source_type.value, source_event_id=p.source_event_id,
                     sample_period_s=p.sample_period_s, tags=json.dumps(p.tags, sort_keys=True)) for p in points]
        sql = text("""INSERT INTO telemetry_points (observed_at, device_id, site_id, metric_name, value, unit,
                      collection_method, quality, source_type, source_event_id, sample_period_s, tags)
                      VALUES (:observed_at, :device_id, :site_id, :metric_name, :value, :unit, :collection_method,
                      :quality, :source_type, :source_event_id, :sample_period_s, :tags)
                      ON CONFLICT DO NOTHING""")
        with self.engine.begin() as c:
            before = c.execute(text("SELECT COUNT(*) FROM telemetry_points WHERE source_event_id = :e"),
                               {"e": points[0].source_event_id}).scalar()
            c.execute(sql, rows)
            after = c.execute(text("SELECT COUNT(*) FROM telemetry_points WHERE source_event_id = :e"),
                              {"e": points[0].source_event_id}).scalar()
        return int(after - before)

    def query(self, *, device_id: str | None, metric_name: str | None, since: datetime, limit: int = 500,
              source_event_id: str | None = None) -> list[dict]:
        where, params = ["observed_at >= :since"], {"since": _iso(since), "limit": min(max(limit, 1), 5000)}
        if device_id:
            where.append("device_id = :d"); params["d"] = device_id
        if metric_name:
            where.append("metric_name = :m"); params["m"] = metric_name
        if source_event_id:
            where.append("source_event_id = :e"); params["e"] = source_event_id
        sql = text(f"SELECT observed_at, device_id, site_id, metric_name, value, unit, collection_method, quality, "
                   f"source_type, source_event_id FROM telemetry_points WHERE {' AND '.join(where)} "
                   f"ORDER BY observed_at DESC LIMIT :limit")
        with self.engine.connect() as c:
            out = []
            for r in c.execute(sql, params).mappings():
                d = dict(r)
                ts = d["observed_at"]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                d["observed_at"] = _iso(ts).isoformat()
                out.append(d)
            return out

    def has_retention_policy(self) -> bool:
        if self.is_sqlite:
            return False
        with self.engine.connect() as c:
            return c.execute(text("SELECT 1 FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention' "
                                  "AND hypertable_name = 'telemetry_points'")).first() is not None

    def prune(self, days: int = RETENTION_DAYS) -> None:
        """Retention fallback when no background policy exists (Apache-only TimescaleDB, or SQLite tests)."""
        with self.engine.begin() as c:
            if self.is_sqlite:
                c.execute(text("DELETE FROM telemetry_points WHERE observed_at < datetime('now', :d)"), {"d": f"-{days} days"})
            else:
                c.execute(text("SELECT drop_chunks('telemetry_points', older_than => make_interval(days => :d))"), {"d": days})

    def ping(self) -> None:
        with self.engine.connect() as c:
            c.execute(text("SELECT 1"))
