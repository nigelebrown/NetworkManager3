"""The AI role must never receive credentials for device, database, bus or user admin operations."""
import os

import pytest

from ai_service.app import FORBIDDEN_ENV, isolation_violations


@pytest.mark.parametrize("secret_name", [
    "SIMS_FERNET_KEY",
    "SIMS_DATABASE_URL",
    "SIMS_ADMIN_PASSWORD",
    "SIMS_JWT_SECRET",
    "INM_TELEMETRY_DATABASE_URL",
    "MGMT_DB_PASSWORD",
    "TS_DB_PASSWORD",
    "INM_NATS_URL",
    "INM_NATS_USER",
    "INM_NATS_API_PASSWORD",
    "INM_NATS_WORKER_PASSWORD",
])
def test_ai_refuses_platform_secret_env(monkeypatch, secret_name):
    monkeypatch.setenv(secret_name, "should-never-be-mounted-to-ai")
    assert secret_name in isolation_violations()


def test_ai_allows_only_dedicated_service_token(monkeypatch):
    for name in FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INM_AI_SHARED_TOKEN", "test-dedicated-service-token")
    assert isolation_violations() == []
