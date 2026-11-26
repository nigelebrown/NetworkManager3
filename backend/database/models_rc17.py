"""rc17 tables (migration 0015, expand-only): printer purchase requests, print-job metadata and inspection findings."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid

_TS = DateTime(timezone=True)


class PrinterPurchaseRequest(Base):
    """Internal supply purchase request. One OPEN request per printer + supply (dedupe_key unique while open)."""
    __tablename__ = "printer_purchase_requests"
    __table_args__ = (Index("ix_purchase_open_key", "dedupe_key", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printer_devices.id", ondelete="CASCADE"), index=True)
    supply_index: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supply_description: Mapped[str] = mapped_column(String(255))
    colour: Mapped[str | None] = mapped_column(String(16), nullable=True)
    part_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    estimated_unit_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="JMD")
    reason: Mapped[str] = mapped_column(String(255))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(12))                 # AUTOMATIC | MANUAL
    status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL")  # PENDING_APPROVAL|APPROVED|REJECTED|ORDERED|RECEIVED|CANCELLED
    dedupe_key: Mapped[str] = mapped_column(String(120))
    requested_by: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class PrintJob(Base):
    """Print-job metadata submitted by an authorised print-server agent. No document content is stored."""
    __tablename__ = "print_jobs"
    __table_args__ = (Index("ix_print_jobs_printer_time", "printer_id", "completed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[str] = mapped_column(String(24))               # CUPS | WINDOWS_PRINT_SERVER | IPP | OTHER
    source_job_id: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    printer_id: Mapped[str | None] = mapped_column(ForeignKey("printer_devices.id", ondelete="SET NULL"), nullable=True, index=True)
    printer_ref: Mapped[str] = mapped_column(String(120))            # as reported (queue / serial / IP)
    user_ref: Mapped[str] = mapped_column(String(128))              # account name, or HMAC pseudonym when configured
    user_pseudonymised: Mapped[bool] = mapped_column(Boolean, default=False)
    document_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    colour: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duplex: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(_TS, index=True)
    inspection_state: Mapped[str] = mapped_column(String(16), default="NOT_INSPECTED")  # NOT_INSPECTED|CLEAN|FINDINGS|SKIPPED
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class PrintJobFinding(Base):
    """A content-inspection rule match. Stores the rule and a short redacted excerpt only, never the document."""
    __tablename__ = "print_job_findings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("print_jobs.id", ondelete="CASCADE"), index=True)
    rule_name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(12))
    match_count: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_state: Mapped[str] = mapped_column(String(16), default="OPEN")   # OPEN | DISMISSED | CONFIRMED
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
