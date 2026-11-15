"""Printer Intelligence SRS (Nov 2026) — continuous read-only monitoring, utilization, supplies,
reliability, benchmarking, recommendations and reporting.

Expand-only additions on top of the P0 foundation in ``models_printer``. Every measured value keeps
its provenance (raw OID/value, source, time, validation). Missing, unsupported and inconsistent values
are stored as such and never coerced to zero. No print content, document titles or per-user job data
is stored. Printer configuration is never written.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid

# Capability / value states used across the module (PRN-016–021, capability matrix).
CAP_STATES = ("SUPPORTED", "UNSUPPORTED", "UNVERIFIED", "TEMPORARILY_UNAVAILABLE", "INVALID")
VALUE_STATES = ("VALID", "MISSING", "UNSUPPORTED", "INCONSISTENT", "INVALID", "UNKNOWN_SPECIAL")


class PrinterModel(Base):
    """Verified model catalogue entry. Original imported text is preserved separately on the device."""
    __tablename__ = "printer_models"
    __table_args__ = (UniqueConstraint("manufacturer", "model", name="uq_printer_model"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    manufacturer: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(120))
    technology: Mapped[str] = mapped_column(String(16), default="unknown")   # laser | inkjet | unknown
    colour_capable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multifunction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duplex_capable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recommended_monthly_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_monthly_duty_cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_useful_life_impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verification: Mapped[str] = mapped_column(String(24), default="UNVERIFIED")  # VERIFIED | UNVERIFIED
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterMibProfile(Base):
    """Administrator-defined (or built-in) polling profile. Matching rules select it automatically."""
    __tablename__ = "printer_mib_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    vendor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_enterprise: Mapped[int | None] = mapped_column(Integer, nullable=True)   # sysObjectID enterprise number
    match_descr_regex: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)    # lower = preferred
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    default_interval_seconds: Mapped[int] = mapped_column(Integer, default=900)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterMetricDefinition(Base):
    """One normalised metric within a profile: scalar GET or bounded indexed table walk."""
    __tablename__ = "printer_metric_definitions"
    __table_args__ = (UniqueConstraint("profile_id", "metric_key", name="uq_printer_metric_def"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("printer_mib_profiles.id", ondelete="CASCADE"), index=True)
    metric_key: Mapped[str] = mapped_column(String(64))       # normalised name e.g. impressions_total
    oid: Mapped[str] = mapped_column(String(160))
    access: Mapped[str] = mapped_column(String(8), default="scalar")   # scalar | table
    value_type: Mapped[str] = mapped_column(String(16), default="counter")  # counter | gauge | text | enum
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(24), default="usage")   # identity|usage|supply|status|network
    verification: Mapped[str] = mapped_column(String(20), default="STANDARD")  # STANDARD|VENDOR_UNVERIFIED|VENDOR_VERIFIED
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PrinterMonitoring(Base):
    """Per-device monitoring state: schedule, lease, backoff and freshness (1:1 with printer_devices)."""
    __tablename__ = "printer_monitoring"
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mib_profile_id: Mapped[str | None] = mapped_column(ForeignKey("printer_mib_profiles.id", ondelete="SET NULL"), nullable=True)
    profile_selection: Mapped[str] = mapped_column(String(16), default="AUTO")   # AUTO | MANUAL
    interval_seconds: Mapped[int] = mapped_column(Integer, default=900)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reachability: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # REACHABLE | UNREACHABLE | UNKNOWN
    snmp_state: Mapped[str] = mapped_column(String(16), default="UNKNOWN")    # AVAILABLE | FAILED | UNKNOWN
    operational_state: Mapped[str] = mapped_column(String(24), default="UNKNOWN")  # idle|printing|warmup|error|...
    discovery_confidence: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # HIGH|MEDIUM|LOW|UNKNOWN
    unsupported_oids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metric_last_polled: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # per-metric schedule state
    total_polls: Mapped[int] = mapped_column(Integer, default=0)
    successful_polls: Mapped[int] = mapped_column(Integer, default=0)


class PrinterIdentity(Base):
    """Verified identity and organisational references (PRN-003/005/007/010)."""
    __tablename__ = "printer_identity"
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), primary_key=True)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("printer_models.id", ondelete="SET NULL"), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    location_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    division_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    section_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    unit_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    original_import_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reported_serial: Mapped[str | None] = mapped_column(String(100), nullable=True)
    system_object_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    system_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_state: Mapped[str] = mapped_column(String(24), default="UNVERIFIED")  # VERIFIED|UNVERIFIED|CONFLICT
    identity_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_colour: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_multifunction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    technology: Mapped[str | None] = mapped_column(String(16), nullable=True)
    in_service_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warranty_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterIdentityChange(Base):
    """Reconciliation history: IP / firmware / model / serial changes observed on re-discovery."""
    __tablename__ = "printer_identity_changes"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str] = mapped_column(String(24))   # APPLIED | NEEDS_REVIEW | SEPARATE_DEVICE
    reason: Mapped[str] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterMetricSample(Base):
    """Raw + normalised sample with provenance. Missing values are rows with value_state != VALID."""
    __tablename__ = "printer_metric_samples"
    __table_args__ = (Index("ix_printer_samples_lookup", "printer_id", "metric_key", "observed_at"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"))
    poll_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(64))
    instance: Mapped[str] = mapped_column(String(32), default="")   # table index suffix ("" for scalars)
    oid: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    value_state: Mapped[str] = mapped_column(String(20), default="VALID")
    interpretation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(24))   # SNMPV1|SNMPV2C|SNMPV3|MANUAL|IMPORT
    profile_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterCounterInterval(Base):
    """Validated delta between consecutive cumulative readings of one meter (PRN-031–040)."""
    __tablename__ = "printer_counter_intervals"
    __table_args__ = (UniqueConstraint("printer_id", "meter_kind", "start_at", "end_at", name="uq_printer_interval"),
                      Index("ix_printer_interval_lookup", "printer_id", "meter_kind", "end_at"))
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"))
    meter_kind: Mapped[str] = mapped_column(String(40))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_value: Mapped[int] = mapped_column(BigInteger)
    end_value: Mapped[int] = mapped_column(BigInteger)
    delta: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quality: Mapped[str] = mapped_column(String(24))   # VALID|ROLLOVER|RESET|REPLACEMENT|IMPLAUSIBLE|GAP
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(24))


class PrinterConsumable(Base):
    """Current state of one supply/component row (prtMarkerSuppliesTable index)."""
    __tablename__ = "printer_consumables"
    __table_args__ = (UniqueConstraint("printer_id", "supply_index", name="uq_printer_consumable"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    supply_index: Mapped[str] = mapped_column(String(32))
    supply_type: Mapped[str] = mapped_column(String(32))   # toner|ink|drum|waste_toner|fuser|maintenance_kit|...
    colour: Mapped[str] = mapped_column(String(32), default="other")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    part_number_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    max_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level_state: Mapped[str] = mapped_column(String(20), default="UNKNOWN")  # VALID|OTHER|UNKNOWN|SOME_REMAINING|...
    percent_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")   # OK|LOW|EMPTY|UNKNOWN
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installed_meter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class PrinterSupplyEvent(Base):
    __tablename__ = "printer_supply_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    supply_index: Mapped[str] = mapped_column(String(32))
    supply_type: Mapped[str] = mapped_column(String(32))
    colour: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(32))  # REPLACEMENT_SUSPECTED|REPLACEMENT_CONFIRMED|REJECTED|LOW|EMPTY
    level_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    level_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    meter_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    yield_impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="JMD")
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN | CONFIRMED | REJECTED | INFO
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterStatusPeriod(Base):
    """Persisted state transitions with durations, per dimension (reachability, snmp, operational, fault)."""
    __tablename__ = "printer_status_periods"
    __table_args__ = (Index("ix_printer_status_lookup", "printer_id", "dimension", "started_at"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"))
    dimension: Mapped[str] = mapped_column(String(16))   # REACHABILITY | SNMP | OPERATIONAL
    state: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PrinterFaultEvent(Base):
    """Printer faults (paper jam, low/empty supply, door open, service requested...) with durations."""
    __tablename__ = "printer_fault_events"
    __table_args__ = (Index("ix_printer_fault_lookup", "printer_id", "started_at"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"))
    fault_code: Mapped[str] = mapped_column(String(48))
    category: Mapped[str] = mapped_column(String(24))   # PAPER|SUPPLY|HARDWARE|SERVICE|DOOR|NETWORK|POWER|OTHER
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    description: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(24))
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    maintenance_record_id: Mapped[int | None] = mapped_column(ForeignKey("printer_maintenance_events.id", ondelete="SET NULL"), nullable=True)
    service_ticket_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class PrinterMaintenanceDetail(Base):
    """SRS extension of printer_maintenance_events (1:1). Keeps the original event untouched."""
    __tablename__ = "printer_maintenance_details"
    event_id: Mapped[int] = mapped_column(ForeignKey("printer_maintenance_events.id", ondelete="CASCADE"), primary_key=True)
    incident_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cause_category: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # MECHANICAL|WAN|POWER|USER|CONSUMABLE|UNKNOWN
    qualifying_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    technician: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    warranty: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    meter_at_event: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    service_ticket_ref: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PrinterModelCost(Base):
    """Verified purchase/replacement costs per model consumable (cost per 1,000 impressions basis)."""
    __tablename__ = "printer_model_costs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("printer_models.id", ondelete="CASCADE"), index=True)
    supply_type: Mapped[str] = mapped_column(String(32))
    colour: Mapped[str] = mapped_column(String(32), default="black")
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="JMD")
    declared_yield: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    recorded_by: Mapped[str] = mapped_column(String(64), default="unknown")


class PrinterCapability(Base):
    __tablename__ = "printer_capabilities"
    __table_args__ = (UniqueConstraint("printer_id", "capability", name="uq_printer_capability"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    capability: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(24), default="UNVERIFIED")
    evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrinterRecommendation(Base):
    __tablename__ = "printer_recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dedupe_key: Mapped[str] = mapped_column(String(160), index=True)
    code: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(200))
    printer_id: Mapped[str | None] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason_codes: Mapped[list] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    assumptions: Mapped[list] = mapped_column(JSON)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16))   # HIGH|MEDIUM|LOW|INSUFFICIENT
    rule_version: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")  # PROPOSED|UNDER_REVIEW|APPROVED|REJECTED|SUPERSEDED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PrinterJob(Base):
    """Asynchronous job contract: DISCOVERY | POLL | WALK | IMPORT | REPORT. Leased for worker safety."""
    __tablename__ = "printer_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)  # QUEUED|RUNNING|SUCCEEDED|FAILED|CANCELLED|PARTIAL
    params: Mapped[dict] = mapped_column(JSON)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_by: Mapped[str] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact: Mapped[str | None] = mapped_column(Text, nullable=True)   # base64 report payload
    artifact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    artifact_type: Mapped[str | None] = mapped_column(String(80), nullable=True)


class PrinterReportSchedule(Base):
    __tablename__ = "printer_report_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    report: Mapped[str] = mapped_column(String(40))
    format: Mapped[str] = mapped_column(String(8))
    filters: Mapped[dict] = mapped_column(JSON)
    cadence: Mapped[str] = mapped_column(String(12))   # daily | weekly | monthly
    recipients: Mapped[list] = mapped_column(JSON)     # notification channel ids / emails
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PrinterDailyAggregate(Base):
    """Validated daily rollup kept long-term (≥5 years) after raw samples age out."""
    __tablename__ = "printer_daily_aggregates"
    __table_args__ = (UniqueConstraint("printer_id", "meter_kind", "day", name="uq_printer_daily"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    meter_kind: Mapped[str] = mapped_column(String(40))
    day: Mapped[str] = mapped_column(String(10))   # YYYY-MM-DD (UTC)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)   # fraction of the day covered by VALID intervals
    quality: Mapped[str] = mapped_column(String(16))


class PrinterMibModule(Base):
    """Uploaded SMI MIB module (text only). Parsed into objects; never compiled or executed."""
    __tablename__ = "printer_mib_modules"
    __table_args__ = (UniqueConstraint("module_name", "version", name="uq_printer_mib_module_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    module_name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    source_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))   # RESOLVED | UNRESOLVED_IMPORTS | SUPERSEDED
    imports: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # {module: [names]}
    unresolved: Mapped[list | None] = mapped_column(JSON, nullable=True)
    object_count: Mapped[int] = mapped_column(Integer, default=0)
    enterprise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PrinterMibObject(Base):
    __tablename__ = "printer_mib_objects"
    __table_args__ = (Index("ix_printer_mib_object_oid", "oid"),
                      UniqueConstraint("module_id", "name", name="uq_printer_mib_object"))
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    module_id: Mapped[str] = mapped_column(ForeignKey("printer_mib_modules.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    oid: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))    # node | scalar | table | entry | column
    syntax: Mapped[str | None] = mapped_column(String(200), nullable=True)
    access: Mapped[str | None] = mapped_column(String(32), nullable=True)
    units: Mapped[str | None] = mapped_column(String(40), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
