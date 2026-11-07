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


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Fresh SQLite management DB + SQLite telemetry store; INM API role with the write lock on."""
    monkeypatch.setenv("SIMS_DATABASE_URL", f"sqlite:///{tmp_path / 'mgmt.db'}")
    monkeypatch.setenv("INM_TELEMETRY_DATABASE_URL", f"sqlite:///{tmp_path / 'ts.db'}")
    monkeypatch.setenv("SIMS_MODE", "demo")
    monkeypatch.setenv("SIMS_INM_PROFILE", "demo")
    monkeypatch.setenv("SIMS_DEVICE_WRITE_LOCK", "true")
    monkeypatch.setenv("SIMS_ROLE", "api")
    for k in ("INM_NATS_URL", "INM_LOKI_URL", "INM_AI_URL"):
        monkeypatch.delenv(k, raising=False)
    from backend.database import session as dbs
    dbs._engine = dbs._SessionLocal = None
    _clear_caches()
    login_limiter.reset()
    api_limiter.reset()
    yield monkeypatch
    _clear_caches()
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
