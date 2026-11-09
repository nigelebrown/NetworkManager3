"""Phases 5–11 + printer completion (WP-02..WP-12) — management DB tables (schema 0008).

Expand-only. Nothing here grants device-write capability: the write lock and execution gate
are enforced in the application/API layer at execution time.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid

_TS = DateTime(timezone=True)


# ---- WP-02 scheduled read-only collection --------------------------------------------------
class CollectionPlan(Base):
    __tablename__ = "collection_plans"
    __table_args__ = (UniqueConstraint("device_id", name="uq_collection_plan_device"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("inm_devices.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    credential_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    adapter_key: Mapped[str] = mapped_column(String(48))
    metric_families: Mapped[list] = mapped_column(JSON)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    window_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(12), default="ACTIVE")  # ACTIVE | PAUSED
    next_due_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_failure_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plan_id: Mapped[str] = mapped_column(ForeignKey("collection_plans.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    metric_family: Mapped[str] = mapped_column(String(24))
    scheduled_window: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # LEASED | COLLECTED (read, event queued, persistence not yet confirmed) | SUCCEEDED (persisted) | RETRY | DLQ
    state: Mapped[str] = mapped_column(String(12))
    failure_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    points_written: Mapped[int] = mapped_column(Integer, default=0)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)        # 0010
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # 0010
    expected_points: Mapped[int | None] = mapped_column(Integer, nullable=True)               # 0010
    worker: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


# ---- WP-03 syslog / AAA / attribution -----------------------------------------------------
class DeviceLogEvent(Base):
    __tablename__ = "device_log_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    device_serial: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_ip: Mapped[str] = mapped_column(String(45))
    vendor: Mapped[str] = mapped_column(String(32))
    event_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str] = mapped_column(String(12))
    message: Mapped[str] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_config_change: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(_TS, index=True)
    ingested_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    parser_version: Mapped[str] = mapped_column(String(32))
    transport_quality: Mapped[str] = mapped_column(String(20))
    raw_hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuthSessionEvidence(Base):
    __tablename__ = "auth_session_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_serial: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(64))
    start_at: Mapped[datetime] = mapped_column(_TS)
    end_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # RADIUS | TACACS+ | CX_ACCOUNTING
    external_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class ChangeAttribution(Base):
    """Append-only. Corrections create a superseding row; raw evidence is never overwritten."""
    __tablename__ = "change_attributions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    log_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    change_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    change_at: Mapped[datetime] = mapped_column(_TS, index=True)
    level: Mapped[str] = mapped_column(String(12))  # VERIFIED | CORRELATED | UNKNOWN
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_type: Mapped[str] = mapped_column(String(16))  # APPLICATION | DIRECT_DEVICE
    evidence: Mapped[dict] = mapped_column(JSON)
    candidates: Mapped[list] = mapped_column(JSON)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


# ---- WP-05 configuration vault ------------------------------------------------------------
class ConfigArtifact(Base):
    __tablename__ = "config_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    serial: Mapped[str] = mapped_column(String(64))
    vendor: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fidelity: Mapped[str] = mapped_column(String(32))
    capture_source: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(24))  # MANUAL | SCHEDULE | PRE_CHANGE | POST_CHANGE | DRIFT
    object_path: Mapped[str] = mapped_column(String(255))
    wrapped_key: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(32))
    plaintext_sha256: Mapped[str] = mapped_column(String(64), index=True)
    ciphertext_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    restore_state: Mapped[str] = mapped_column(String(20), default="UNVERIFIED")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)


class RestoreValidation(Base):
    __tablename__ = "restore_validations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("config_artifacts.id", ondelete="CASCADE"), index=True)
    target: Mapped[dict] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(16))  # PASS | FAIL
    reasons: Mapped[list] = mapped_column(JSON)
    validated_by: Mapped[str] = mapped_column(String(64))
    validated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


# ---- WP-06 change plans / approvals / execution decisions ---------------------------------
class ChangePlan(Base):
    __tablename__ = "change_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(200))
    operations: Mapped[list] = mapped_column(JSON)
    capability_report: Mapped[list] = mapped_column(JSON)
    plan_hash: Mapped[str] = mapped_column(String(64))
    expected_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware_at_request: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_plan: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT")
    requester: Mapped[str] = mapped_column(String(64))
    approver: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="OPERATOR")  # OPERATOR | AI_DRAFT
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)


class ExecutionDecision(Base):
    """ChangePlan and OptimizationExecutionDecision records — every evaluation is persisted."""
    __tablename__ = "execution_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(16))  # CHANGE_PLAN | OPTIMIZATION
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    enrollment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    proposed_diff_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    global_lock_state: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(8))  # ALLOW | DENY
    guards: Mapped[list] = mapped_column(JSON)
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluated_by: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)


# ---- WP-07 replacement -------------------------------------------------------------------
class ReplacementPlan(Base):
    __tablename__ = "replacement_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    old_device_id: Mapped[str] = mapped_column(String(36), index=True)
    new_device_id: Mapped[str] = mapped_column(String(36))
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32))
    blockers: Mapped[list] = mapped_column(JSON)
    identity_map: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict] = mapped_column(JSON)
    requester: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


# ---- WP-08 learning ---------------------------------------------------------------------
class DecisionFeedback(Base):
    __tablename__ = "decision_feedback"
    __table_args__ = (UniqueConstraint("recommendation_id", name="uq_feedback_recommendation"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recommendation_id: Mapped[str] = mapped_column(String(36), index=True)
    operator: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class OutcomeEvaluation(Base):
    __tablename__ = "outcome_evaluations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recommendation_id: Mapped[str] = mapped_column(String(36), index=True)
    action_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    metric: Mapped[str] = mapped_column(String(64))
    before_window: Mapped[dict] = mapped_column(JSON)
    after_window: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    evaluated_by: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class MlModel(Base):
    __tablename__ = "ml_models"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    algorithm: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))  # CANDIDATE | PROMOTED | REJECTED
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    split: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    parameters: Mapped[dict] = mapped_column(JSON)
    trained_by: Mapped[str] = mapped_column(String(64))
    trained_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


# ---- WP-09 per-device optimization ------------------------------------------------------
class OptimizationEnrollment(Base):
    __tablename__ = "optimization_enrollments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    immutable_serial: Mapped[str] = mapped_column(String(64))
    vendor_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    firmware_bound: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="RECOMMEND_ONLY")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    allowed_operation_types: Mapped[list] = mapped_column(JSON)
    value_constraints: Mapped[dict] = mapped_column(JSON)
    permitted_windows: Mapped[dict] = mapped_column(JSON)
    min_observation_quality: Mapped[str] = mapped_column(String(12), default="OBSERVED")
    baseline_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cooldown_seconds: Mapped[int] = mapped_column(Integer)
    max_actions_per_day: Mapped[int] = mapped_column(Integer)
    impact_stop_threshold: Mapped[dict] = mapped_column(JSON)
    valid_from_utc: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    expires_at_utc: Mapped[datetime] = mapped_column(_TS)
    requester_id: Mapped[str] = mapped_column(String(64))
    independent_approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at_utc: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL")
    standing_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_action_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revoked_at_utc: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class OptimizationControl(Base):
    """Single-row global emergency stop."""
    __tablename__ = "optimization_control"
    id: Mapped[int] = mapped_column(primary_key=True)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


# ---- WP-11 reporting ----------------------------------------------------------------------
class ReportExport(Base):
    __tablename__ = "report_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(200))
    range_start: Mapped[datetime] = mapped_column(_TS)
    range_end: Mapped[datetime] = mapped_column(_TS)
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


# ---- 0009: production read/write --------------------------------------------------------------
class DeviceCertification(Base):
    """Write/read certification of ONE operation for ONE model family + firmware train, with evidence.
    Status CERTIFIED_WRITE requires a passed certification run AND an independent approver."""
    __tablename__ = "device_certifications"
    __table_args__ = (UniqueConstraint("adapter_key", "model_family", "firmware_train", "operation",
                                       name="uq_cert_scope_op"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    adapter_key: Mapped[str] = mapped_column(String(48))
    model_family: Mapped[str] = mapped_column(String(48))
    firmware_train: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # TESTED_PENDING_APPROVAL | CERTIFIED_WRITE | FAILED | UNSUPPORTED | REVOKED
    environment: Mapped[str] = mapped_column(String(16))  # HARDWARE | EMULATED
    run_id: Mapped[str] = mapped_column(String(36))
    device_id: Mapped[str] = mapped_column(String(36))
    evidence: Mapped[dict] = mapped_column(JSON)
    tested_by: Mapped[str] = mapped_column(String(64))
    tested_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class ChangeExecution(Base):
    """Step-by-step record of one hardware execution: backup, checkpoint, apply, readback, rollback, save."""
    __tablename__ = "change_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plan_id: Mapped[str] = mapped_column(String(36), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    environment: Mapped[str] = mapped_column(String(16))  # HARDWARE | EMULATED
    outcome: Mapped[str] = mapped_column(String(24))  # APPLIED_VERIFIED | ROLLED_BACK | ROLLBACK_FAILED | ABORTED
    steps: Mapped[list] = mapped_column(JSON)
    pre_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    post_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    executed_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # 0010


class DeviceAccess(Base):
    """Pinned SSH host key per device (confirmed by an administrator before any SSH write)."""
    __tablename__ = "device_access"
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ssh_fingerprint: Mapped[str] = mapped_column(String(80))
    vc_host: Mapped[str | None] = mapped_column(String(45), nullable=True)  # Instant VC address if not the AP itself
    pinned_by: Mapped[str] = mapped_column(String(64))
    pinned_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class OptimizationSitePolicy(Base):
    """Site-scoped CONSTRAINTS for enrolled devices (allowed channels, widths, power, windows).
    A site policy never enrolls or enables any device."""
    __tablename__ = "optimization_site_policies"
    site_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    allowed_channels_5g: Mapped[list] = mapped_column(JSON)
    allowed_channels_2g: Mapped[list] = mapped_column(JSON)
    allowed_widths_mhz: Mapped[list] = mapped_column(JSON)
    min_tx_dbm: Mapped[int] = mapped_column(Integer, default=9)
    max_tx_dbm: Mapped[int] = mapped_column(Integer, default=18)
    window_start_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_end_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class VaultSchedule(Base):
    """Scheduled full-configuration capture per device."""
    __tablename__ = "vault_schedules"
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    next_due_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_result: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))


class DevicePortSnapshot(Base):
    """Latest per-port state for the switch faceplate; previous counters kept for rate calculation."""
    __tablename__ = "device_port_snapshots"
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collected_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    ports: Mapped[list] = mapped_column(JSON)
    hardware: Mapped[dict] = mapped_column(JSON)
    environment: Mapped[str] = mapped_column(String(16))


class InmAlert(Base):
    """Rule-based alert on a managed device (deduplicated per device+rule; auto-resolves)."""
    __tablename__ = "inm_alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(12))
    message: Mapped[str] = mapped_column(String(300))
    value: Mapped[float | None] = mapped_column(nullable=True)
    threshold: Mapped[float | None] = mapped_column(nullable=True)
    state: Mapped[str] = mapped_column(String(12), default="OPEN", index=True)  # OPEN | ACKNOWLEDGED | RESOLVED
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    acked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_type: Mapped[str] = mapped_column(String(8), default="REAL")
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # 0010
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)                   # 0010
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)                     # 0010 e.g. port 1/1/3


class AlertPolicy(Base):
    """Threshold override for one rule, globally (scope 'global') or for one site (scope = site id)."""
    __tablename__ = "alert_policies"
    __table_args__ = (UniqueConstraint("scope", "rule_id", name="uq_alert_policy_scope_rule"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    scope: Mapped[str] = mapped_column(String(36))
    rule_id: Mapped[str] = mapped_column(String(48))
    threshold: Mapped[float] = mapped_column()
    severity: Mapped[str] = mapped_column(String(12))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(64))


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(_TS)
    ends_at: Mapped[datetime] = mapped_column(_TS)
    reason: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(64))
