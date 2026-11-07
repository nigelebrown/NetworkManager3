"""Application settings, loaded from environment variables / .env (never hardcoded)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger("sims.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_prefix="SIMS_", extra="ignore"
    )

    mode: Literal["demo", "live"] = "demo"
    database_url: str = f"sqlite:///{ROOT_DIR / 'sims.db'}"

    jwt_secret: str = Field(default="CHANGE_ME")
    fernet_key: str = Field(default="CHANGE_ME")

    admin_username: str = "admin"
    admin_password: str = "CHANGE_ME_MIN_12_CHARS"
    session_minutes: int = 60

    aruba_timeout_seconds: float = 10.0
    aruba_verify_tls: bool = True
    aruba_ca_bundle: str = ""

    poll_interval_seconds: int = 60
    telemetry_enabled: bool = True  # per-AP RF + system telemetry (2 extra read-only commands per AP per poll)
    retention_days: int = 30

    allow_config_changes: bool = False

    # Firmware + certificate delivery. The VC downloads firmware from, and the AP fetches a certificate over TFTP
    # from, THIS machine, so SIMS needs an address the APs can reach (e.g. http://10.1.2.3:8000).
    public_base_url: str = ""
    data_dir: str = str(ROOT_DIR / "data")
    max_firmware_mb: int = 400
    firmware_link_hours: int = 6  # how long the VC may download after an upgrade request is applied
    tftp_bind: str = "0.0.0.0"
    tftp_port: int = 69

    # Local AI assistant (Ollama). Off by default; local-only unless explicitly allowed.
    ollama_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""  # optional; an admin can pick one in the UI instead
    ollama_timeout_seconds: float = 180.0
    ollama_allow_remote: bool = False  # permit a non-local Ollama host (data leaves this machine)
    disable_scheduler: bool = False  # used by tests

    # ---- Intelligent Network Manager (INM) integration -------------------------------------
    # standalone = original single-process SIMS behaviour (legacy tests, Windows dev).
    # api / worker = INM service roles: the API never polls; the network worker owns all polling.
    role: Literal["standalone", "api", "worker"] = "standalone"
    # INM deployment profile. "demo" forbids live mode outright (Phase 1 gate).
    inm_profile: Literal["standalone", "demo", "lab-readonly"] = "standalone"
    # Phase 1/2 hard lock: device writes are refused even if SIMS_ALLOW_CONFIG_CHANGES were set.
    device_write_lock: bool = False
    # When false the app refuses to start unless the schema is already at head (installer migrates).
    auto_migrate: bool = True
    # First-login password change for the bootstrap administrator (installer sets true).
    force_initial_password_change: bool = False
    cors_origins: str = "http://localhost:5173"

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_len(cls, v: str) -> str:
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def tls_verify_arg(self) -> bool | str:
        """Value for httpx `verify=`: CA bundle path, True, or False (explicit dev opt-out)."""
        if not self.aruba_verify_tls:
            return False
        return self.aruba_ca_bundle or True

    @property
    def device_writes_permitted(self) -> bool:
        """Single source of truth for every device-write guard (REST, SSH, TFTP, firmware)."""
        return bool(self.allow_config_changes) and not self.device_write_lock

    def validate_profile(self) -> list[str]:
        problems = []
        if self.device_write_lock and self.allow_config_changes:
            problems.append("SIMS_ALLOW_CONFIG_CHANGES=true is not permitted while SIMS_DEVICE_WRITE_LOCK=true "
                            "(device writes are disabled for Phase 1 and Phase 2).")
        if self.inm_profile == "demo" and self.mode != "demo":
            problems.append("SIMS_INM_PROFILE=demo requires SIMS_MODE=demo (no device access in the demo profile).")
        return problems

    def validate_secrets(self) -> list[str]:
        """Return human-readable problems with secrets. Empty list = OK."""
        problems = []
        if self.jwt_secret == "CHANGE_ME" or len(self.jwt_secret) < 32:
            problems.append("SIMS_JWT_SECRET must be set to a random value of 32+ characters.")
        if self.fernet_key == "CHANGE_ME":
            problems.append("SIMS_FERNET_KEY must be set (see .env.example for how to generate).")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
