"""Network Intelligence completion (0013): versioned rule catalogue with review, predictive runs and their
measured evaluation. Rules and predictions are advisory; nothing here can change a device."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid


class AiRuleDefinition(Base):
    """Effective definition of one analysis rule. Changes are proposed, then approved by a second person."""
    __tablename__ = "ai_rule_definitions"
    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    engine: Mapped[str] = mapped_column(String(24))          # SITE_RULES | DEVICE_INSIGHTS | PREDICTIVE
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    inputs: Mapped[list] = mapped_column(JSON)
    condition: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(String(255))
    risk: Mapped[str] = mapped_column(String(12))
    precedence: Mapped[int] = mapped_column(Integer, default=100)
    default_params: Mapped[dict] = mapped_column(JSON)
    params: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    suppression_minutes: Mapped[int] = mapped_column(Integer, default=30)
    version: Mapped[int] = mapped_column(Integer, default=1)
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # proposed change awaiting review
    pending_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiRuleVersion(Base):
    __tablename__ = "ai_rule_versions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("ai_rule_definitions.rule_id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean)
    suppression_minutes: Mapped[int] = mapped_column(Integer)
    proposed_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PredictionRun(Base):
    __tablename__ = "prediction_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(40))
    horizon_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))            # SUCCEEDED | FAILED | NO_DATA
    summary: Mapped[dict] = mapped_column(JSON)
    evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_state: Mapped[str] = mapped_column(String(24))  # VALIDATED | EXPERIMENTAL | INSUFFICIENT_HISTORY


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_device_metric", "device_id", "metric", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("prediction_runs.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(36))
    metric: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)
    horizon_hours: Mapped[int] = mapped_column(Integer)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24))             # AT_RISK | NO_RISK_EXPECTED | INSUFFICIENT_DATA
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str] = mapped_column(String(16))
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)     # BREACH | NO_BREACH | UNOBSERVED
    observed_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
