"""Intelligent Network Manager management-database tables (PostgreSQL).

Generic inventory graph (Site / Cluster / Device / Capability) sits beside the legacy SIMS tables. Legacy
`sites` rows are Instant *clusters*; they are linked through `inm_clusters.legacy_site_id` and are never
silently treated as physical sites (Appendix L.4).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow


def new_uuid() -> str:
    return str(uuid.uuid4())


class SiteAccessGrant(Base):
    """Per-user site grant. Only consulted for users whose site_scope is 'granted'."""
    __tablename__ = "site_access_grants"
    __table_args__ = (UniqueConstraint("user_id", "site_id", name="uq_grant_user_site"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    granted_by: Mapped[str] = mapped_column(String(64))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RevokedToken(Base):
    """Signed-out session tokens (JWT ids) so logout is real, not just a client-side forget."""
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditChainHead(Base):
    """Single-row anchor for the append-only audit hash chain (locked FOR UPDATE on PostgreSQL)."""
    __tablename__ = "audit_chain_head"
    id: Mapped[int] = mapped_column(primary_key=True)
    last_hash: Mapped[str] = mapped_column(String(64))
    last_audit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InmSite(Base):
    """Physical site / location (distinct from an Instant cluster)."""
    __tablename__ = "inm_sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(8))  # DEMO | REAL
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("source_type", "name", name="uq_inm_site_src_name"),)


class InmCluster(Base):
    """A controller-managed group (Aruba Instant cluster / VC). Linked to the legacy `sites` row."""
    __tablename__ = "inm_clusters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))  # aruba_instant_cluster
    source_type: Mapped[str] = mapped_column(String(8))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("inm_sites.id"), nullable=True)
    legacy_site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InmDevice(Base):
    """DeviceIdentity v1. `id` is a stable UUID; (source_type, vendor, serial) is unique so DEMO and REAL can
    never collide even when hostnames are identical (SEC-02)."""
    __tablename__ = "inm_devices"
    __table_args__ = (UniqueConstraint("source_type", "vendor", "serial_number", name="uq_device_src_vendor_serial"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(8))  # DEMO | REAL
    environment: Mapped[str] = mapped_column(String(8))  # demo | lab | live
    vendor: Mapped[str] = mapped_column(String(32))
    family: Mapped[str] = mapped_column(String(32))  # access_point | switch
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str] = mapped_column(String(64))
    hostname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mgmt_addresses: Mapped[list | None] = mapped_column(JSON, nullable=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("inm_sites.id"), nullable=True, index=True)
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("inm_clusters.id"), nullable=True, index=True)
    legacy_ap_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    legacy_site_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    lifecycle: Mapped[str] = mapped_column(String(16), default="monitored")
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    adapter: Mapped[str] = mapped_column(String(48))
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceCapability(Base):
    """Per-device operation capability. Status is never inferred from an API schema alone (Appendix M)."""
    __tablename__ = "device_capabilities"
    __table_args__ = (UniqueConstraint("device_id", "operation", name="uq_cap_device_op"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # UNSUPPORTED | UNVERIFIED | READ_ONLY | CERTIFIED_WRITE | DEMO
    evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessedEvent(Base):
    """Consumer-side idempotency: one row per (consumer, event_id) ever applied."""
    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(String(48), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventRecord(Base):
    """Management-side record of each logical event (EventEnvelope v1). Payload stays small; raw bodies go to Loki."""
    __tablename__ = "event_records"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    schema_version: Mapped[str] = mapped_column(String(8))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_service: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(8))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(64))
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    legacy_site_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    data_quality: Mapped[str] = mapped_column(String(12))
    integrity_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    delivery_count: Mapped[int] = mapped_column(Integer, default=1)


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"
    service: Mapped[str] = mapped_column(String(32), primary_key=True)
    instance: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class JobLease(Base):
    """Per-job lease so two workers can never run the same poll interval (INTEG-P1-05)."""
    __tablename__ = "job_leases"
    job: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RecommendationRecord(Base):
    """Recommendation v1 as returned by the isolated AI service. Rules are labelled RULE, never ML."""
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    legacy_site_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    engine: Mapped[str] = mapped_column(String(16))  # RULE (Phase 1). ML only after Phase 8.
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(8))
    risk: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    input_window: Mapped[str] = mapped_column(String(64))
    approval_state: Mapped[str] = mapped_column(String(16), default="ADVISORY")
    requested_by: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))


class ReleaseEvent(Base):
    """Deployment audit: install / update / rollback / backup / restore receipts (F046)."""
    __tablename__ = "release_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    operation: Mapped[str] = mapped_column(String(16))
    operator: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_digests: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    schema_revisions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    environment: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

