"""Phase 4 recorded telemetry analysis, rule findings and human review only.

NO network probes, device credentials, hardware writes, ML, or scheduled scans.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import InmDevice, RecommendationRecord
from ..database.session import get_db
from ..domain.phase4_analysis import summarize
from ..infrastructure.runtime import telemetry_store
from ..security.deps import require_role, audit

router = APIRouter(prefix="/api/v1/phase4", tags=["phase4-monitoring"])

def _locked():
    """Read-only/draft module: no device-write path, so it stays usable whether or not the
    operator has enabled manual config changes (device_writes_permitted)."""
    return None

def _store():
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Historical telemetry database is not configured.")
    return store

def _rows(db: Session) -> list:
    return list(db.scalars(select(InmDevice).where(
        InmDevice.source_type == "REAL", InmDevice.vendor == "Aruba",
        InmDevice.family.in_(["access_point", "switch"])
    ).order_by(InmDevice.last_observed_at.desc()).limit(100)))

def _overview(db: Session) -> dict:
    store = _store()
    now = datetime.now(timezone.utc)
    devices = []
    for device in _rows(db):
        try:
            readings = store.query(device_id=device.id, metric_name=None,
                                   since=now-timedelta(hours=24), limit=250)
        except Exception:
            # Never present unavailable telemetry storage as a healthy device.
            raise HTTPException(503, "Telemetry query unavailable; no status can be inferred.")
        devices.append(summarize(device, readings, now=now))
    return {
        "device_count": len(devices), "limit": 100,
        "engine": "RULE", "ml_trained": False,
        "data_source": "RECORDED_TELEMETRY_ONLY",
        "scheduled_device_polling": False,
        "device_writes": "LOCKED",
        "devices": devices,
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db),
             admin: User = Depends(require_role("admin"))):
    _locked()
    return _overview(db)


@router.post("/analyze", status_code=200)
def analyze_now(request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_role("admin"))):
    _locked()
    out = analyze_recorded(db, actor=admin.username, request=request)
    db.commit()
    return out


def analyze_recorded(db: Session, *, actor: str, request: Request | None = None) -> dict:
    """Analyze Now and the hourly scheduled analysis share this function."""
    result = _overview(db)
    now = datetime.now(timezone.utc)
    created = 0
    for device in result["devices"]:
        for finding in device["findings"]:
            old = db.scalar(select(RecommendationRecord).where(
                RecommendationRecord.device_id == device["device_id"],
                RecommendationRecord.rule_id == finding["rule_id"],
                RecommendationRecord.source_type == "REAL",
                RecommendationRecord.created_at >= now-timedelta(minutes=30)
            ).order_by(RecommendationRecord.created_at.desc()).limit(1))
            if old:
                continue
            rec = RecommendationRecord(
                device_id=device["device_id"], legacy_site_id=None,
                engine="RULE", rule_id=finding["rule_id"], rule_version="1.0",
                source_type="REAL", risk=finding["risk"],
                title=finding["title"], explanation=finding["explanation"],
                evidence={"metric": finding["metric"],
                          "samples": finding["samples"],
                          "threshold": finding["threshold"],
                          "collection_method": finding["collection_method"]},
                input_window="30m", approval_state="ADVISORY",
                requested_by=actor, correlation_id=str(uuid.uuid4()))
            db.add(rec)
            created += 1
    audit(db, "phase4_recorded_data_analysis", username=actor,
          detail=f"evaluated={len(result['devices'])} created={created}; RULE, NO writes",
          request=request)
    return {"created": created, "evaluated_devices": len(result["devices"]),
            "engine": "RULE", "ml_trained": False, "device_writes": "LOCKED"}


def _public(rec: RecommendationRecord) -> dict:
    return dict(id=rec.id, device_id=rec.device_id, created_at=rec.created_at.isoformat(),
                title=rec.title, explanation=rec.explanation, risk=rec.risk,
                engine=rec.engine, rule_id=rec.rule_id, rule_version=rec.rule_version,
                evidence=rec.evidence, approval_state=rec.approval_state,
                source_type=rec.source_type, device_writes="LOCKED")


@router.get("/recommendations")
def recommendations(db: Session = Depends(get_db),
                    admin: User = Depends(require_role("admin"))):
    _locked()
    recs = db.scalars(select(RecommendationRecord).where(
        RecommendationRecord.source_type == "REAL",
        RecommendationRecord.rule_id.like("PHASE4_%")
    ).order_by(RecommendationRecord.created_at.desc()).limit(100))
    return [_public(rec) for rec in recs]


class ReviewInput(BaseModel):
    action: Literal["ACKNOWLEDGED", "DISMISSED"]


@router.post("/recommendations/{recommendation_id}/review")
def review_recommendation(recommendation_id: str, body: ReviewInput,
                          request: Request, db: Session = Depends(get_db),
                          admin: User = Depends(require_role("admin"))):
    _locked()
    rec = db.get(RecommendationRecord, recommendation_id)
    if not rec or rec.source_type != "REAL" or not rec.rule_id.startswith("PHASE4_"):
        raise HTTPException(404, "Phase 4 advisory not found.")
    if rec.approval_state != "ADVISORY":
        raise HTTPException(409, "Recommendation already reviewed.")
    rec.approval_state = body.action
    audit(db, "phase4_advisory_reviewed", username=admin.username,
          target=rec.id, detail=f"{body.action}; NO device action", request=request)
    db.commit()
    return _public(rec)
