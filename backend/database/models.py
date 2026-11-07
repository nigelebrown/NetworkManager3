"""SQLAlchemy models.

Convention: a NULL metric column means "unavailable" (never estimated).
Every row that can come from demo mode carries `simulated=True` so the UI can label it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | operator | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # INM: forced first-login password change, and site scope ("all" keeps legacy behaviour; "granted" limits
    # the user to rows in site_access_grants).
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    site_scope: Mapped[str] = mapped_column(String(16), default="all", server_default="all")


class Site(Base):
    """One site == one independent Aruba Instant cluster (Virtual Controller)."""
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vc_host: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Virtual Controller IP/host
    vc_port: Mapped[int] = mapped_column(Integer, default=4343)
    api_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet token
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    aps: Mapped[list["AccessPoint"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class AccessPoint(Base):
    __tablename__ = "access_points"
    __table_args__ = (UniqueConstraint("site_id", "mac", name="uq_ap_site_mac"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_master: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # Virtual Controller
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # up | down | unknown
    uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    client_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    radios: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of radio dicts
    exp_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # experience score 0-100 (NULL = unavailable)
    exp_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    site: Mapped[Site] = relationship(back_populates="aps")


class ClientRecord(Base):
    """Latest known state of a wireless client (upserted each poll)."""
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("site_id", "mac", name="uq_client_site_mac"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    ap_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rssi_dbm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snr_db: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speed_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)  # Instant "Speed" column (direction unspecified)
    tx_rate_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_rate_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    connected_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)  # good|fair|poor|unavailable
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True)
    exp_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # experience score 0-100 (NULL = unavailable)
    exp_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    poor_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # consecutive polls in "poor"
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApSample(Base):
    """Time series: one row per AP per poll."""
    __tablename__ = "ap_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    ap_id: Mapped[int] = mapped_column(ForeignKey("access_points.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16))
    client_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    util_2g_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    util_5g_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    exp_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class SiteSample(Base):
    """Time series: one row per site per poll (aggregate)."""
    __tablename__ = "site_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    aps_total: Mapped[int] = mapped_column(Integer, default=0)
    aps_up: Mapped[int] = mapped_column(Integer, default=0)
    clients_total: Mapped[int] = mapped_column(Integer, default=0)
    avg_rssi_dbm: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_snr_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_util_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    exp_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # mean client experience score
    exp_good: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exp_fair: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exp_poor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    ap_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # ap_offline, poll_failed, ...
    severity: Mapped[str] = mapped_column(String(12))  # critical | warning | info
    message: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|acknowledged|resolved
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    subject: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. client MAC; dedup key, not exposed


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # never contains secrets
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    # INM AuditEvent v1 fields (nullable so imported legacy rows stay valid; legacy rows have channel LEGACY).
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    actor_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # human | service | installer
    session_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)  # APP | INSTALLER | LEGACY | SERVICE
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class ClientEvent(Base):
    """Connect / disconnect / roam events INFERRED by comparing successive polls (not device-reported).

    Anything that starts and ends between two polls is invisible; see docs/SCORING.md.
    """
    __tablename__ = "client_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(12))  # connect | disconnect | roam
    ap_from: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ap_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ssid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    """Admin-editable settings stored as JSON (e.g. experience scoring configuration)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConfigBackup(Base):
    """Snapshot of a cluster's running configuration (secrets redacted before storage)."""
    __tablename__ = "config_backups"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # pull | pre-change | post-change | move
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    taken_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    # INM: these snapshots are REDACTED display copies and can never be used for restore (Appendix O.2).
    restorable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    classification: Mapped[str] = mapped_column(String(24), default="SANITIZED_DISPLAY",
                                                server_default="MIGRATED_REDACTED")


class ConfigChange(Base):
    """A planned / applied configuration change. `payload_enc` holds secrets, Fernet-encrypted."""
    __tablename__ = "config_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # ssid_upsert | ap_rename
    summary: Mapped[str] = mapped_column(String(300))
    payload_enc: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSON)  # display-safe: preview, before/after, rollback text
    backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="planned")
    # planned | applied | applied_unverified | failed | cancelled
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FirmwareImage(Base):
    """A firmware image uploaded to SIMS so a Virtual Controller can download it (stored on disk, not in the DB)."""
    __tablename__ = "firmware_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(160), unique=True)
    ap_class: Mapped[str] = mapped_column(String(32))  # e.g. Gemini, Scorpio (from the Aruba file name)
    version: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# INM integration tables live in their own module but share Base/metadata (one Alembic history).
from . import models_inm  # noqa: E402,F401
