"""Safe onboarding credential profiles, bounded discovery results and config drafts.

All credentials are Fernet-encrypted at rest. No API response includes plaintext
or ciphertext. Device configuration writes are never exposed by this domain.
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utcnow
from .models_inm import new_uuid

class OnboardingCredential(Base):
    __tablename__ = "onboarding_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    device_family: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

class OnboardingDiscovery(Base):
    __tablename__ = "onboarding_discoveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    credential_id: Mapped[str] = mapped_column(ForeignKey("onboarding_credentials.id"), nullable=False, index=True)
    site_name: Mapped[str] = mapped_column(String(100), nullable=False)
    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    device_family: Mapped[str] = mapped_column(String(32), nullable=False)
    # QUEUED | SCANNING | PAUSED | CANCELLED | SCANNED | FAILED   (durable job, E2E-07)
    status: Mapped[str] = mapped_column(String(24), default="SCANNED", nullable=False)
    results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    # 0010 — restart-safe job state: work list, cursor, lease/heartbeat, retry and rate limits
    targets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    next_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_per_s: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

class OnboardingConfigSnapshot(Base):
    __tablename__ = "onboarding_config_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    collected_by: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    completeness: Mapped[str] = mapped_column(String(24), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    warning: Mapped[str | None] = mapped_column(String(400), nullable=True)

class OnboardingConfigDraft(Base):
    __tablename__ = "onboarding_config_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), nullable=False, index=True)
    baseline_id: Mapped[str | None] = mapped_column(ForeignKey("onboarding_config_snapshots.id"), nullable=True)
    proposed_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class OnboardingDeviceCredential(Base):
    """Reusable credential binding: many devices reference one shared profile."""
    __tablename__ = "onboarding_device_credentials"
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), primary_key=True)
    credential_id: Mapped[str] = mapped_column(ForeignKey("onboarding_credentials.id"), nullable=False, index=True)
    bound_by: Mapped[str] = mapped_column(String(64), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
