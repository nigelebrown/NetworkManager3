"""WP-02 — scheduled read-only collection plans, jobs and device experience (Phase 2–3)."""
from __future__ import annotations

from ..infrastructure.posture import read_only, write_posture

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import DeviceCapability, InmDevice
from ..database.models_onboarding import OnboardingDeviceCredential
from ..database.models_phases import CollectionPlan, CollectionRun
from ..database.session import get_db
from ..domain import collection as C
from ..domain.adapters import public as adapter_public, resolve
from ..infrastructure.collection_runner import run_due
from ..infrastructure.runtime import telemetry_store
from ..security.deps import audit, require_role
from ._phase_common import device_public, iso, now, real_device, require_write_lock

router = APIRouter(prefix="/api/v1", tags=["wp02-collection"])


class PlanInput(BaseModel):
    device_id: str
    metric_families: list[str] = Field(min_length=1, max_length=6)
    interval_seconds: int = Field(ge=C.MIN_INTERVAL_S, le=C.MAX_INTERVAL_S)
    window_start_hour: int | None = None
    window_end_hour: int | None = None
    max_concurrency: int = 1


def _spec(body: PlanInput, family: str) -> C.PlanSpec:
    try:
        return C.validate_plan(C.PlanSpec(family, tuple(body.metric_families), body.interval_seconds,
                                          body.window_start_hour, body.window_end_hour, body.max_concurrency))
    except C.PlanError as exc:
        raise HTTPException(422, str(exc)) from None


def _public(p: CollectionPlan, db: Session | None = None) -> dict:
    t = now()
    d = db.get(InmDevice, p.device_id) if db is not None else None
    return {"id": p.id, "device_id": p.device_id, "hostname": d.hostname if d else None,
            "source_type": d.source_type if d else None, "adapter_key": p.adapter_key,
            "metric_families": p.metric_families, "interval_seconds": p.interval_seconds,
            "window": [p.window_start_hour, p.window_end_hour], "state": p.state,
            "next_due_at": iso(p.next_due_at), "last_success_at": iso(p.last_success_at),
            "last_failure_at": iso(p.last_failure_at), "last_failure_category": p.last_failure_category,
            "consecutive_failures": p.consecutive_failures,
            "freshness": C.freshness(p.last_success_at, p.interval_seconds, t),
            "credential_bound": bool(p.credential_profile_id), **read_only()}


@router.post("/collection/plans/preview")
def preview_plan(body: PlanInput, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    require_write_lock()
    d = real_device(db, body.device_id)
    spec = _spec(body, d.family)
    adapter = resolve(d.vendor, d.family, d.model)
    binding = db.get(OnboardingDeviceCredential, d.id)
    caps = {c.operation: c.status for c in db.scalars(
        select(DeviceCapability).where(DeviceCapability.device_id == d.id))}
    warnings = []
    if not binding:
        warnings.append("No saved credential profile is bound to this device; polls will fail AUTH_REJECTED.")
    if not d.mgmt_addresses:
        warnings.append("No management address recorded; polls will fail TARGET_NOT_ALLOWED.")
    if adapter.operations.get("collect") not in ("IMPLEMENTED", "IMPLEMENTED_INVERSE"):
        warnings.append(f"Adapter {adapter.key} does not implement collection; device stays manual-only.")
    return {"device": device_public(d), "adapter": adapter_public(adapter),
            "metric_families": list(spec.metric_families), "interval_seconds": spec.interval_seconds,
            "polls_per_day": int(86400 // spec.interval_seconds), "capabilities": caps,
            "warnings": warnings, "mode": "READ_ONLY", **read_only(),
            "note": "Preview only. Nothing is scheduled until you approve the plan."}


@router.post("/collection/plans", status_code=201)
def create_plan(body: PlanInput, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    require_write_lock()
    d = real_device(db, body.device_id)
    spec = _spec(body, d.family)
    if db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == d.id)):
        raise HTTPException(409, "This device already has a collection plan; pause or edit it.")
    binding = db.get(OnboardingDeviceCredential, d.id)
    p = CollectionPlan(device_id=d.id, site_id=d.site_id, adapter_key=resolve(d.vendor, d.family, d.model).key,
                       credential_profile_id=binding.credential_id if binding else None,
                       metric_families=list(spec.metric_families), interval_seconds=spec.interval_seconds,
                       window_start_hour=spec.window_start_hour, window_end_hour=spec.window_end_hour,
                       max_concurrency=spec.max_concurrency, state="ACTIVE", next_due_at=now(),
                       created_by=user.username)
    db.add(p); db.flush()
    audit(db, "collection_plan_created", username=user.username, target=d.id,
          detail=f"plan={p.id} every {p.interval_seconds}s {','.join(p.metric_families)}; READ ONLY",
          request=request)
    return _public(p)


@router.get("/collection/plans")
def list_plans(db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return [_public(p, db) for p in db.scalars(select(CollectionPlan).order_by(CollectionPlan.created_at.desc()).limit(500))]


def _plan(db: Session, plan_id: str) -> CollectionPlan:
    p = db.get(CollectionPlan, plan_id)
    if p is None:
        raise HTTPException(404, "Collection plan not found.")
    return p


@router.post("/collection/plans/{plan_id}/pause")
def pause_plan(plan_id: str, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    p = _plan(db, plan_id)
    p.state, p.updated_at = "PAUSED", now()
    audit(db, "collection_plan_paused", username=user.username, target=p.device_id, detail=p.id, request=request)
    return _public(p)


@router.post("/collection/plans/{plan_id}/resume")
def resume_plan(plan_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    require_write_lock()
    p = _plan(db, plan_id)
    p.state, p.updated_at, p.next_due_at = "ACTIVE", now(), now()
    audit(db, "collection_plan_resumed", username=user.username, target=p.device_id, detail=p.id, request=request)
    return _public(p)


@router.post("/collection/plans/{plan_id}/run-now")
def run_now(plan_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    require_write_lock()
    p = _plan(db, plan_id)
    if p.state != "ACTIVE":
        raise HTTPException(409, "Resume the plan first.")
    summary = run_due(db, store=telemetry_store(), worker=f"api:{user.username}", plan_ids=[p.id], manual=True)
    audit(db, "collection_plan_run_now", username=user.username, target=p.device_id,
          detail=f"plan={p.id} {summary}; READ ONLY", request=request)
    return {"summary": summary, "plan": _public(p, db)}


@router.get("/collection/jobs")
def list_jobs(plan_id: str | None = None, db: Session = Depends(get_db),
              user: User = Depends(require_role("operator"))):
    q = select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(200)
    if plan_id:
        q = q.where(CollectionRun.plan_id == plan_id)
    return [{"id": r.id, "plan_id": r.plan_id, "device_id": r.device_id, "metric_family": r.metric_family,
             "scheduled_window": r.scheduled_window, "attempt": r.attempt, "state": r.state,
             "failure_category": r.failure_category, "points_written": r.points_written,
             "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
             "retry_at": iso(r.retry_at), "worker": r.worker} for r in db.scalars(q)]


@router.get("/devices/{device_id}/experience")
def device_experience(device_id: str, window: str = "1h", db: Session = Depends(get_db),
                      user: User = Depends(require_role("viewer"))):
    d = real_device(db, device_id)
    spans = {"1m": 60, "5m": 300, "1h": 3600, "24h": 86400}
    if window not in spans:
        raise HTTPException(422, "window must be one of 1m, 5m, 1h, 24h")
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Telemetry store is not configured; no experience can be inferred.")
    rows = store.query(device_id=d.id, metric_name=None, since=now() - timedelta(seconds=spans[window]), limit=2000)
    plan = db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == d.id))
    k = C.experience_kpis(rows)
    k.update({"device_id": d.id, "window": window,
              "freshness": C.freshness(plan.last_success_at, plan.interval_seconds, now()) if plan else "NO_PLAN",
              "last_updated": max((r["observed_at"] for r in rows), default=None) if rows else None,
              "sources": sorted({f"{r.get('collection_method')}/{r.get('quality')}" for r in rows})})
    if k["last_updated"] is not None and not isinstance(k["last_updated"], str):
        k["last_updated"] = iso(k["last_updated"])
    return k
