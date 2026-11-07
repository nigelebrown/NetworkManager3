"""INM runtime wiring: settings for the platform services and factory functions.

Separate from the legacy SIMS_* settings so the original application keeps working unchanged in standalone mode.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ..config import ROOT_DIR


def _version() -> str:
    try:
        return (ROOT_DIR / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0-dev"


class InmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INM_", extra="ignore")

    telemetry_database_url: str = ""
    nats_url: str = ""
    nats_user: str = ""
    nats_password: str = ""
    loki_url: str = ""
    ai_url: str = ""
    ai_shared_token: str = ""
    worker_heartbeat_stale_s: int = 45
    instance: str = os.environ.get("HOSTNAME", "local")
    version: str = _version()
    git_commit: str = os.environ.get("INM_GIT_COMMIT", "unknown")

    @property
    def platform_enabled(self) -> bool:
        return bool(self.nats_url and self.telemetry_database_url and self.loki_url)


@lru_cache
def inm_settings() -> InmSettings:
    return InmSettings()


@lru_cache
def telemetry_store():
    from .telemetry.timescale import TimescaleTelemetryStore
    s = inm_settings()
    return TimescaleTelemetryStore(s.telemetry_database_url) if s.telemetry_database_url else None


@lru_cache
def log_store():
    from .logs.loki import LokiLogStore
    s = inm_settings()
    return LokiLogStore(s.loki_url) if s.loki_url else None


def nats_settings(name: str):
    from .bus.nats_bus import NatsSettings
    s = inm_settings()
    return NatsSettings(url=s.nats_url, user=s.nats_user or None, password=s.nats_password or None, name=name)
