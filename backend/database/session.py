"""Engine/session management and lightweight versioned schema setup."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import Base
from . import models_printer  # register printer tables with metadata for tests and migrations
from . import models_printer_srs  # noqa: F401  Printer Intelligence SRS tables (0012)
from . import models_intelligence  # noqa: F401  rule catalogue + predictive (0013)
from . import models_templates  # Phase 3 scoped draft templates; no device writes
from . import models_onboarding  # encrypted reusable profiles and staged device onboarding
from . import models_phases  # phases 5-11 (WP-02..WP-12) expand-only tables; writes stay LOCKED
from . import models_remediation  # 0010 outbox, workflows, recovery journal, device groups
from . import models_rc17  # noqa: F401  0015 purchasing, print jobs
from . import models_rc18  # noqa: F401  0016 ML lifecycle, API tokens, channel credentials

log = logging.getLogger("sims.db")

# Tables that existed before Alembic was introduced (the 0001 baseline). Databases created by earlier
# versions of SIMS have exactly these (create_all added the configuration tables when that feature shipped).
BASELINE_TABLES = {
    "users", "sites", "access_points", "clients", "ap_samples", "site_samples", "alerts", "audit_log",
    "schema_version", "config_backups", "config_changes",
}
BASELINE_REVISION = "0001"

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif url.startswith("postgresql") and get_settings().role == "worker":
        # Controller polls and scheduled device plans each hold a connection during I/O.
        kwargs.update(pool_size=8, max_overflow=102)
    if url.startswith("postgresql"):
        # rc18: timestamps are UTC end to end, whatever TimeZone the PostgreSQL server or role defaults to
        # (a non-UTC server returned audit times as -05:00; found by running the contract suite on PostgreSQL).
        kwargs["connect_args"] = {"options": "-c timezone=UTC"}
    _engine = create_engine(url, pool_pre_ping=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FK cascade in SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def create_schema() -> None:
    """Bring the database to the latest schema using Alembic migrations (safe to run on every start)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    engine = get_engine()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and tables & BASELINE_TABLES:
            # Database created before migrations existed: make sure the baseline tables are all present,
            # then record that it is at the baseline so only newer migrations run.
            Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if t.name in BASELINE_TABLES])
            command.stamp(cfg, BASELINE_REVISION)
            log.info("Existing database stamped at migration baseline %s", BASELINE_REVISION)
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            raise RuntimeError(f"Database migration failed: {exc}. Back up sims.db before retrying.") from exc


def assert_schema_current() -> None:
    """Refuse to start unless the database is exactly at this release's Alembic head (no silent migration)."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    import json

    from ..config import ROOT_DIR
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    try:
        accepted = set(json.loads((ROOT_DIR / "release.json").read_text())["accepts_management_schemas"])
    except (OSError, KeyError, ValueError):
        accepted = {head}
    with get_engine().connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
        marker = schema_compat_marker(conn) if current not in accepted else None
    if current not in accepted and marker and marker.get("head") == current \
            and head in (marker.get("compatible_readers") or []) and marker.get("phase") == "expand":
        # Expand/migrate/contract (P11-DEP-03): a newer release migrated the database with an expand-only step
        # and declared this release's schema a compatible reader, so both versions run during the window.
        log.warning("Running against newer expand-only schema %s inside its migration window (this release: %s).",
                    current, head)
        return
    if current not in accepted:
        raise RuntimeError(f"Management schema is at {current!r} but this release accepts {sorted(accepted)} (head {head!r}). "
                           "Run the installer/update (which backs up and migrates) instead of starting the API directly.")


def schema_compat_marker(conn) -> dict | None:
    """The migration-window marker written by expand migrations (app_settings key `schema:compat`)."""
    from sqlalchemy import inspect, text
    import json
    if "app_settings" not in set(inspect(conn).get_table_names()):
        return None
    row = conn.execute(text("SELECT value FROM app_settings WHERE key = 'schema:compat'")).first()
    if row is None:
        return None
    value = row[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
