"""Shared fixtures for the INM test suite (unit + contract). No external services required.

Integration tests that need the real stack live in tests/integration and skip unless INM_IT_* is set.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SIMS_JWT_SECRET", "t" * 48)
os.environ.setdefault("SIMS_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SIMS_ADMIN_PASSWORD", "correct-horse-battery")
os.environ["SIMS_DISABLE_SCHEDULER"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.infrastructure import runtime  # noqa: E402
from backend.security.deps import api_limiter, login_limiter  # noqa: E402

ADMIN_PW = "correct-horse-battery"


def _clear_caches():
    get_settings.cache_clear()
    runtime.inm_settings.cache_clear()
    runtime.telemetry_store.cache_clear()
    runtime.log_store.cache_clear()


_PG_TEMPLATE: str | None = None


def _pg_database(tmp_path):
    """rc18: INM_TEST_PG_ADMIN_URL=postgresql+psycopg://user:pw@host:port/postgres runs every contract test against
    its own fresh PostgreSQL management database (column lengths, types and constraints are enforced, unlike
    SQLite). Returns (url, drop)."""
    import uuid as _uuid
    from sqlalchemy import create_engine, text
    admin_url = os.environ["INM_TEST_PG_ADMIN_URL"]
    base = admin_url.rsplit("/", 1)[0]
    name = "inm_ct_" + _uuid.uuid4().hex[:12]
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    global _PG_TEMPLATE
    if _PG_TEMPLATE is None:
        # Migrate once (0001 -> head, exactly as the installer does) into a template; each test then gets an
        # instant copy. The template is dropped at interpreter exit.
        tpl = f"inm_ct_tpl_{os.getpid()}"
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{tpl}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{tpl}"'))
        from backend.database import session as dbs
        old = os.environ.get("SIMS_DATABASE_URL")
        os.environ["SIMS_DATABASE_URL"] = f"{base}/{tpl}"
        get_settings.cache_clear()
        dbs._engine = dbs._SessionLocal = None
        dbs.init_engine(f"{base}/{tpl}")
        dbs.create_schema()
        dbs._engine.dispose()
        dbs._engine = dbs._SessionLocal = None
        if old is None:
            os.environ.pop("SIMS_DATABASE_URL", None)
        else:
            os.environ["SIMS_DATABASE_URL"] = old
        get_settings.cache_clear()
        _PG_TEMPLATE = tpl
        import atexit

        def _drop_template():
            with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{tpl}" WITH (FORCE)'))
        atexit.register(_drop_template)
    import time as _time
    for attempt in range(5):           # a template must have no other sessions (e.g. a pooled connection closing)
        try:
            with eng.connect() as c:
                c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d "
                               "AND pid <> pg_backend_pid()"), {"d": _PG_TEMPLATE})
                c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{_PG_TEMPLATE}"'))
            break
        except Exception:  # noqa: BLE001  retried; the last attempt re-raises
            if attempt == 4:
                raise
            _time.sleep(0.5)
    url = f"{base}/{name}"

    def drop():
        from backend.database import session as dbs
        if dbs._engine is not None:
            dbs._engine.dispose()
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        eng.dispose()
    return url, drop


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Fresh management DB (SQLite, or PostgreSQL with INM_TEST_PG_ADMIN_URL) + SQLite telemetry store; INM API
    role with the write lock on."""
    drop = None
    if os.environ.get("INM_TEST_PG_ADMIN_URL"):
        url, drop = _pg_database(tmp_path)
        monkeypatch.setenv("SIMS_DATABASE_URL", url)
    else:
        monkeypatch.setenv("SIMS_DATABASE_URL", f"sqlite:///{tmp_path / 'mgmt.db'}")
    monkeypatch.setenv("INM_TELEMETRY_DATABASE_URL", f"sqlite:///{tmp_path / 'ts.db'}")
    monkeypatch.setenv("SIMS_MODE", "demo")
    monkeypatch.setenv("SIMS_INM_PROFILE", "demo")
    monkeypatch.setenv("SIMS_DEVICE_WRITE_LOCK", "true")
    monkeypatch.setenv("SIMS_ROLE", "api")
    # rc18: vault objects of a test go to its own temporary directory, never into the repository's data/vault
    monkeypatch.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    # rc18: disabled-by-default modules are mounted for their own contract tests only
    monkeypatch.setenv("INM_FEATURE_PURCHASING", "true")
    monkeypatch.setenv("INM_FEATURE_PRINT_JOBS", "true")
    # Lab-style contract tests mix demo fixtures with live mode; the refusal path is tested explicitly
    # (test_live_mode_refuses_demo_data_without_explicit_opt_in deletes this variable).
    monkeypatch.setenv("INM_ALLOW_DEMO_DATA_IN_LIVE", "true")
    for k in ("INM_NATS_URL", "INM_LOKI_URL", "INM_AI_URL"):
        monkeypatch.delenv(k, raising=False)
    from backend.database import session as dbs
    dbs._engine = dbs._SessionLocal = None
    _clear_caches()
    login_limiter.reset()
    api_limiter.reset()
    yield monkeypatch
    _clear_caches()
    if drop is not None:
        drop()
    dbs._engine = dbs._SessionLocal = None


@pytest.fixture
def client(app_env):
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c


def login(client, username="admin", password=ADMIN_PW) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin(client):
    return login(client)


def make_user(client, admin_headers, username, role, scope="all", grants=None, password="temporary-pass-123"):
    r = client.post("/api/v1/users", headers=admin_headers,
                    json={"username": username, "role": role, "temporary_password": password, "site_scope": scope})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    if grants is not None:
        assert client.put(f"/api/v1/users/{uid}/grants", headers=admin_headers, json={"site_ids": grants}).status_code == 200
    h = login(client, username, password)
    new = password + "-changed"
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": password, "new_password": new}).status_code == 200
    return login(client, username, new)
