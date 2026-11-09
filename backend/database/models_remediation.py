"""1.2.0 software remediation (migration 0010, expand-only): transactional event outbox, persistent onboarding
workflows, encrypted change-recovery journal and reusable device groups."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid

_TS = DateTime(timezone=True)


class EventOutbox(Base):
    """Transactional outbox (E2E-03/15): an event is written in the SAME transaction as the state it describes,
    then relayed to JetStream (or delivered in-process in standalone mode). Never lost on a crash."""
    __tablename__ = "event_outbox"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    correlation_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    envelope: Mapped[str] = mapped_column(Text)                       # EventEnvelope v1 JSON (integrity-hashed)
    state: Mapped[str] = mapped_column(String(12), default="PENDING", index=True)   # PENDING | DELIVERED | DLQ
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class OnboardingWorkflow(Base):
    """One operator task from site selection to monitored device (E2E-06), resumable by id."""
    __tablename__ = "onboarding_workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(120))
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    site_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    device_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    step: Mapped[str] = mapped_column(String(32), default="SITE")
    state: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)  # ACTIVE | WAITING | DONE | CANCELLED | FAILED
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)              # NOW | LATER
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    discovery_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_ids: Mapped[list] = mapped_column(JSON, default=list)
    template_scope: Mapped[dict] = mapped_column(JSON, default=dict)               # group ids / options chosen
    plan_ids: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)                       # [{at, step, actor, detail}]
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class ChangeRecoveryJournal(Base):
    """Durable, encrypted pre-change state for one execution (E2E-14). Survives process restarts so an
    interrupted change can be investigated and recovered. Payload is Fernet-encrypted; never returned raw."""
    __tablename__ = "change_recovery_journal"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    environment: Mapped[str] = mapped_column(String(16))
    # OPEN (change in flight) | CLOSED (finished cleanly) | RECOVERY_REQUIRED (interrupted) | RECOVERED
    state: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    strategy: Mapped[str] = mapped_column(String(32))          # DEVICE_CHECKPOINT | INVERSE_OPERATIONS | GUIDED
    payload_enc: Mapped[str] = mapped_column(Text)              # encrypted JSON: checkpoint / prior values / inverse ops
    last_step: Mapped[str | None] = mapped_column(String(48), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DeviceGroup(Base):
    """Reusable device group (configuration scope between site and device, E2E-20/C04)."""
    __tablename__ = "device_groups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    family: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class DeviceGroupMember(Base):
    __tablename__ = "device_group_members"
    group_id: Mapped[str] = mapped_column(ForeignKey("device_groups.id", ondelete="CASCADE"), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class NotificationChannel(Base):
    """Where alert notifications go (E2E-20): webhook (JSON POST), email (SMTP relay) or syslog (UDP 5424).
    Targets never carry credentials; min_severity filters; escalation re-notifies unacknowledged alerts."""
    __tablename__ = "notification_channels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(12))                      # webhook | email | syslog
    target: Mapped[str] = mapped_column(String(300))                   # URL | comma-separated addresses | host:port
    min_severity: Mapped[str] = mapped_column(String(12), default="major")
    escalate_after_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escalation_channel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    include_demo: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class NotificationDelivery(Base):
    """One notification of one alert transition to one channel. dedupe_key makes enqueueing idempotent across
    scheduler/worker replicas; retried with backoff, DEAD after MAX_ATTEMPTS (never silently dropped)."""
    __tablename__ = "notification_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    channel_id: Mapped[str] = mapped_column(String(36), index=True)
    alert_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(12))                      # OPENED | ESCALATED | RESOLVED | TEST
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(10), default="PENDING", index=True)   # PENDING | SENDING | SENT | DEAD
    claim_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    next_attempt_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
