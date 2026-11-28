"""rc18 tables (migration 0016, expand-only): ML training runs, drift reports and issued inferences; REST API
access tokens. Notification channels gain encrypted per-channel credentials (see models_remediation)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, utcnow
from .models_inm import new_uuid

_TS = DateTime(timezone=True)


class MlTrainingRun(Base):
    __tablename__ = "ml_training_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    target: Mapped[str] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(12))                  # MANUAL | SCHEDULED | DRIFT
    dataset_source: Mapped[str] = mapped_column(String(12))           # LIVE | DEMO | SYNTHETIC
    status: Mapped[str] = mapped_column(String(20))                   # SUCCEEDED | INSUFFICIENT_DATA | FAILED
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    model_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class MlDriftReport(Base):
    __tablename__ = "ml_drift_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    model_id: Mapped[str] = mapped_column(String(36), index=True)
    checked_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)
    state: Mapped[str] = mapped_column(String(20))                    # STABLE | WATCH | DRIFT | INSUFFICIENT_DATA
    max_psi: Mapped[float | None] = mapped_column(Float, nullable=True)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[dict] = mapped_column(JSON, default=dict)


class MlInference(Base):
    """One probability issued by a trained model, later scored against what happened (performance monitoring)."""
    __tablename__ = "ml_inferences"
    __table_args__ = (Index("ix_ml_inferences_open", "outcome", "window_end"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(36), index=True)
    target: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)
    issued_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    window_end: Mapped[datetime] = mapped_column(_TS)
    probability: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(8))
    used_for_prediction: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)   # BREACH | NO_BREACH | UNOBSERVED
    observed_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class ApiToken(Base):
    """Read-only REST API / metrics access token. Only a SHA-256 of the secret is stored; shown once."""
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)                         # ["read"] | ["metrics"] | both
    owner: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(_TS)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
