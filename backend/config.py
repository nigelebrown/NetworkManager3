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
    # Exact management-IP exception; verify other device certificates.
    aruba_untrusted_tls_hosts: str = ""

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
    # Every device write (legacy SIMS SSID/rename/firmware/cert screens included) needs an approver who is
    # not the requester. Only the legacy protocol regression suite turns this off.
    require_independent_approval: bool = True
    # When false the app refuses to start unless the schema is already at head (installer migrates).
    auto_migrate: bool = True
    # First-login password change for the bootstrap administrator (installer sets true).
    force_initial_password_change: bool = False
    # Deliberately weak convenience credential is allowed ONLY in loopback-bound
    # and device-write-locked DEMO installations, never lab/production.
    demo_simple_login: bool = False
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
        """Aruba-management HTTPS only: self-signed certificates accepted by default.

        Other service TLS and SSH fingerprint checks are NOT affected. Set
        SIMS_ARUBA_VERIFY_TLS=true with a CA bundle to re-enable validation.
        """
        if not self.aruba_verify_tls:
            return False
        return self.aruba_ca_bundle or True

    def tls_verify_for(self, host: str) -> bool | str:
        """Honor explicit, exact-IP TLS exceptions; verify all other device certificates."""
        import ipaddress

        try:
            candidate = ipaddress.ip_address(host)
            exceptions = {
                ipaddress.ip_address(item.strip())
                for item in self.aruba_untrusted_tls_hosts.split(",") if item.strip()
            }
        except ValueError:
            # Malformed configuration cannot silently disable certificate verification.
            return self.tls_verify_arg
        if candidate.version == 4 and candidate.is_private and candidate in exceptions:
            log.warning("TLS certificate verification explicitly waived for device %s; lab exception", host)
            return False
        return self.tls_verify_arg

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
        import ipaddress
        import os
        if self.demo_simple_login:
            bind = os.environ.get("INM_WEB_BIND", "127.0.0.1")
            local_only = bind == "127.0.0.1"
            scoped_lan = False
            # A weak demo credential is allowed on LAN only by explicit
            # administrator acknowledgement, private host-IP binding and a
            # narrow trusted subnet. Nginx independently enforces that CIDR.
            if os.environ.get("INM_LAN_DEMO_ACK") == "true":
                try:
                    ip = ipaddress.IPv4Address(bind)
                    allowed = ipaddress.IPv4Network(
                        os.environ["INM_LAN_CLIENT_CIDR"], strict=False
                    )
                    scoped_lan = (
                        ip.is_private and not ip.is_loopback and
                        not ip.is_link_local and not ip.is_multicast and
                        allowed.is_private and allowed.prefixlen >= 24 and
                        ip in allowed
                    )
                except (ValueError, KeyError, ipaddress.AddressValueError):
                    scoped_lan = False
            if not (self.mode == "demo" and self.inm_profile == "demo" and
                    self.device_write_lock and not self.allow_config_changes and
                    (local_only or scoped_lan) and self.admin_password == "demo123!"):
                problems.append(
                    "SIMS_DEMO_SIMPLE_LOGIN requires a write-locked demo on "
                    "127.0.0.1, or an explicitly approved private /24-or-narrower "
                    "LAN CIDR containing the bound server IP."
                )
        elif self.admin_password == "demo123!":
            problems.append("The demo123! password is prohibited outside explicitly restricted demo mode.")
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
