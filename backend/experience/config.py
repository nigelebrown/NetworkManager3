"""Experience-score configuration: validated, admin-editable, stored in the database (`app_settings`)."""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..database.models import AppSetting, utcnow

log = logging.getLogger("sims.experience")
KEY = "experience"


class Range(BaseModel):
    """Linear scale: value at/below `worst` scores 0, at/above `best` scores 100 (works for either direction)."""
    worst: float
    best: float

    @model_validator(mode="after")
    def _distinct(self):
        if self.worst == self.best:
            raise ValueError("worst and best must differ")
        return self


class Weights(BaseModel):
    model_config = {"extra": "forbid"}

    def total(self) -> float:
        return sum(self.model_dump().values())

    @model_validator(mode="after")
    def _positive_total(self):
        if self.total() <= 0:
            raise ValueError("at least one weight must be greater than 0")
        return self


class ClientWeights(Weights):
    signal: float = Field(0.45, ge=0, le=1)
    rate: float = Field(0.20, ge=0, le=1)
    stability: float = Field(0.25, ge=0, le=1)
    retries: float = Field(0.10, ge=0, le=1)


class ApWeights(Weights):
    clients: float = Field(0.50, ge=0, le=1)
    utilization: float = Field(0.25, ge=0, le=1)
    load: float = Field(0.15, ge=0, le=1)
    resources: float = Field(0.10, ge=0, le=1)


class Stability(BaseModel):
    window_hours: int = Field(24, ge=1, le=168)
    worst_disconnects: int = Field(6, ge=1, le=100)  # this many disconnects in the window scores 0
    min_observation_minutes: int = Field(30, ge=0, le=1440)  # no stability score for clients observed for less


class ClientCfg(BaseModel):
    weights: ClientWeights = ClientWeights()
    snr: Range = Range(worst=10, best=35)          # dB, AP-reported
    rssi: Range = Range(worst=-85, best=-60)       # dBm, AP-reported; used only when SNR is unavailable
    rate_mbps: Range = Range(worst=12, best=150)   # AP-reported "Speed"
    retries_pct: Range = Range(worst=30, best=5)   # AP-reported retransmission rate (not collected yet)
    stability: Stability = Stability()


class ApCfg(BaseModel):
    weights: ApWeights = ApWeights()
    utilization_pct: Range = Range(worst=80, best=40)
    clients_per_ap: Range = Range(worst=60, best=20)
    cpu_pct: Range = Range(worst=95, best=60)
    memory_pct: Range = Range(worst=95, best=60)


class Bands(BaseModel):
    good_min: int = Field(75, ge=1, le=100)
    fair_min: int = Field(50, ge=1, le=99)

    @model_validator(mode="after")
    def _ordered(self):
        if self.fair_min >= self.good_min:
            raise ValueError("fair_min must be lower than good_min")
        return self


class AlertRules(BaseModel):
    poor_polls: int = Field(5, ge=1, le=200)                 # consecutive polls in "poor" before alerting
    disconnects_threshold: int = Field(5, ge=1, le=100)
    disconnects_window_minutes: int = Field(60, ge=5, le=1440)
    utilization_pct: float = Field(80, ge=1, le=100)
    utilization_polls: int = Field(5, ge=1, le=200)


class ExperienceConfig(BaseModel):
    bands: Bands = Bands()
    min_coverage: float = Field(0.30, ge=0, le=1)  # share of the model's weight that must be measurable to score at all
    client: ClientCfg = ClientCfg()
    ap: ApCfg = ApCfg()
    alerts: AlertRules = AlertRules()


def load_config(db: Session) -> ExperienceConfig:
    row = db.get(AppSetting, KEY)
    if row is None:
        return ExperienceConfig()
    try:
        return ExperienceConfig.model_validate(row.value)
    except Exception:  # corrupted/old settings must never stop polling
        log.warning("Stored experience configuration is invalid; using defaults.")
        return ExperienceConfig()


def save_config(db: Session, cfg: ExperienceConfig, username: str) -> None:
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=cfg.model_dump(), updated_by=username))
    else:
        row.value, row.updated_by, row.updated_at = cfg.model_dump(), username, utcnow()
