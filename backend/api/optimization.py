"""WP-09 / C5a — per-device opt-in optimization (Phase 9). DEFAULT AUTO OFF.

No endpoint here enables automation for more than one explicitly identified device, and there is
no global 'auto on'. Evaluations are persisted; with the global write lock on, every one is DENY.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import User
from ..database.models_phases import (ConfigArtifact, ExecutionDecision,
                                      OptimizationEnrollment)
from ..database.session import get_db
from ..domain import optimization_policy as P
from ..domain.change_control import PolicyViolation
from ..infrastructure.phase_services import ACTIVE_ENROLLMENT, control
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import device_public, iso, now, real_device, utc

router = APIRouter(prefix="/api/v1", tags=["wp09-optimization"])


class EnrollInput(BaseModel):
    serial: str
    mode: str = "SHADOW"
    allowed_operation_types: list[str] = Field(min_length=1, max_length=3)
    value_constraints: dict = {}
    permitted_windows: dict = {}
    cooldown_seconds: int = 3600
    max_actions_per_day: int = 1
    impact_stop_threshold: dict = {}
    expires_at_utc: datetime
    standing_approval: bool = False


class BulkInput(BaseModel):
    devices: list[dict] = Field(min_length=1, max_length=50)  # [{device_id, serial}]
    template: EnrollInput


class EvaluateInput(BaseModel):
    op: dict
    recommendation_id: str | None = None


class StopInput(BaseModel):
    active: bool
    reason: str = Field(min_length=3, max_length=300)


def _current(db: Session, device_id: str) -> OptimizationEnrollment | None:
    return db.scalar(select(OptimizationEnrollment).where(
        OptimizationEnrollment.device_id == device_id,
        OptimizationEnrollment.status.in_(ACTIVE_ENROLLMENT + ("PAUSED",)))
        .order_by(OptimizationEnrollment.created_at.desc()).limit(1))


def _enr_public(e: OptimizationEnrollment) -> dict:
    return {"id": e.id, "device_id": e.device_id, "immutable_serial": e.immutable_serial,
            "vendor_model": e.vendor_model, "firmware_bound": e.firmware_bound, "mode": e.mode,
            "policy_version": e.policy_version, "allowed_operation_types": e.allowed_operation_types,
            "value_constraints": e.value_constraints, "cooldown_seconds": e.cooldown_seconds,
            "max_actions_per_day": e.max_actions_per_day, "expires_at_utc": iso(e.expires_at_utc),
            "requester_id": e.requester_id, "independent_approver_id": e.independent_approver_id,
            "approved_at_utc": iso(e.approved_at_utc), "status": e.status, "status_reason": e.status_reason,
            "standing_approval": e.standing_approval,
            "revoked_at_utc": iso(e.revoked_at_utc), "revoked_by": e.revoked_by}


def _state(db: Session, d) -> dict:
    e = _current(db, d.id)
    lock = get_settings().device_write_lock
    stop = control(db).emergency_stop
    base = P.default_state(d.id) if e is None else {
        "device_id": d.id, "mode": e.mode, "status": e.status, "inherited": False,
        "badge": {"ARMED": "ARMED", "PENDING_APPROVAL": "PENDING APPROVAL", "PAUSED": "PAUSED"}.get(e.status, "AUTO OFF")}
    if e is not None and e.status == "ARMED" and (lock or stop):
        base["display"] = "Configured for future automation — currently locked"
    return dict(base, device=device_public(d), enrollment=_enr_public(e) if e else None,
                global_write_lock=lock, emergency_stop=stop,
                native_vendor_optimization="Not changed by this setting (e.g. Aruba ARM remains as configured on the device).")


@router.get("/devices/{device_id}/optimization")
def get_state(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return _state(db, real_device(db, device_id))


def _validated(body: EnrollInput, d) -> dict:
    from backend.infrastructure.optimizer import feature_enabled
    if body.mode == "DEVICE_OPT_IN" and not feature_enabled():
        raise HTTPException(422, "Automatic optimization is disabled on this platform (INM_AUTOMATIC_OPTIMIZATION=false); "
                                 "only SHADOW enrollment is available.")
    try:
        return P.validate_enrollment_request(dict(body.model_dump(), device_id=d.id, expires_at_utc=utc(body.expires_at_utc)),
                                             device={"id": d.id, "serial": d.serial_number, "family": d.family},
                                             now=now())
    except PolicyViolation as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/devices/{device_id}/optimization/preview")
def preview(device_id: str, body: EnrollInput, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    req = _validated(body, d)
    backup = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id).limit(1))
    return {"device": device_public(d), "affected_devices": [d.id], "mode": req["mode"],
            "summary": f"{d.hostname or d.serial_number}: {req['mode']} for {', '.join(req['allowed_operation_types'])} "
                       f"until {req['expires_at_utc']:%Y-%m-%d}; cooldown {req['cooldown_seconds']}s; "
                       f"max {req['max_actions_per_day']}/day; widths {req['value_constraints'].get('allowed_widths_mhz', [20])} MHz.",
            "backup_readiness": "VAULT_ARTIFACT_PRESENT" if backup else "MISSING",
            "global_write_lock": get_settings().device_write_lock,
            "dry_run": "No device or other enrollment is changed by preview."}


def _create(db: Session, d, req: dict, user: User) -> OptimizationEnrollment:
    if _current(db, d.id) is not None:
        raise HTTPException(409, f"Device {d.id} already has an active or pending enrollment.")
    e = OptimizationEnrollment(device_id=d.id, immutable_serial=d.serial_number,
                               vendor_model=f"{d.vendor} {d.model or ''}".strip(), firmware_bound=d.firmware,
                               mode=req["mode"], allowed_operation_types=req["allowed_operation_types"],
                               value_constraints=req.get("value_constraints") or {},
                               permitted_windows=req.get("permitted_windows") or {},
                               cooldown_seconds=req["cooldown_seconds"], max_actions_per_day=req["max_actions_per_day"],
                               impact_stop_threshold=req.get("impact_stop_threshold") or {},
                               expires_at_utc=req["expires_at_utc"], requester_id=user.username,
                               standing_approval=bool(req.get("standing_approval")) and req["mode"] == "DEVICE_OPT_IN",
                               status="PENDING_APPROVAL")
    db.add(e); db.flush()
    return e


@router.post("/devices/{device_id}/optimization/enroll", status_code=201)
def enroll(device_id: str, body: EnrollInput, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    e = _create(db, d, _validated(body, d), user)
    audit(db, "optimization_enrollment_requested", username=user.username, target=d.id,
          detail=f"enrollment={e.id} mode={e.mode} ops={e.allowed_operation_types}; needs independent approval",
          request=request)
    return _enr_public(e)


@router.post("/optimization/bulk-proposal", status_code=201)
def bulk(body: BulkInput, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_role("admin"))):
    """Batch PROPOSAL only: every device is named explicitly and receives its own pending record."""
    out = []
    for item in body.devices:
        d = real_device(db, str(item.get("device_id", "")))
        tmpl = body.template.model_copy(update={"serial": str(item.get("serial", ""))})
        out.append(_enr_public(_create(db, d, _validated(tmpl, d), user)))
    audit(db, "optimization_bulk_proposal", username=user.username,
          detail=f"{len(out)} separate pending enrollments: {[o['device_id'] for o in out]}", request=request)
    return {"proposals": out, "note": "Each device requires its own independent approval."}


def _enrollment(db: Session, eid: str) -> OptimizationEnrollment:
    e = db.get(OptimizationEnrollment, eid)
    if e is None:
        raise HTTPException(404, "Enrollment not found.")
    return e


@router.post("/optimization/enrollments/{enrollment_id}/approve")
def approve(enrollment_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_any_role("approver", "admin"))):
    e = _enrollment(db, enrollment_id)
    try:
        P.check_independent_approval(e.requester_id, user.username)
    except PolicyViolation as exc:
        audit(db, "optimization_self_approval_denied", username=user.username, target=e.device_id,
              detail=str(exc), request=request, success=False)
        db.commit()
        raise HTTPException(403, str(exc)) from None
    if e.status != "PENDING_APPROVAL":
        raise HTTPException(409, f"Enrollment is {e.status}.")
    d = real_device(db, e.device_id)
    if d.serial_number != e.immutable_serial or d.firmware != e.firmware_bound:
        e.status, e.status_reason = "PAUSED", "Device identity/firmware changed before approval"
        raise HTTPException(409, "Device identity or firmware changed since request; re-request.")
    if utc(e.expires_at_utc) <= now():
        e.status = "EXPIRED"
        raise HTTPException(409, "Enrollment request expired.")
    if not db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id).limit(1)):
        raise HTTPException(409, "Backup readiness not recorded: seal a vault artifact for this device first.")
    e.status, e.independent_approver_id, e.approved_at_utc = "ARMED", user.username, now()
    audit(db, "optimization_enrollment_armed", username=user.username, target=d.id,
          detail=f"enrollment={e.id} requester={e.requester_id}; global write lock still applies", request=request)
    return _enr_public(e)


@router.post("/devices/{device_id}/optimization/disable")
def disable(device_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    n = 0
    for e in db.scalars(select(OptimizationEnrollment).where(
            OptimizationEnrollment.device_id == d.id,
            OptimizationEnrollment.status.in_(ACTIVE_ENROLLMENT + ("PAUSED",)))):
        e.status, e.revoked_at_utc, e.revoked_by, e.status_reason = "REVOKED", now(), user.username, "DISABLED_BY_OPERATOR"
        n += 1
    audit(db, "optimization_disabled", username=user.username, target=d.id,
          detail=f"revoked={n}; immediate, no second approval", request=request)
    return _state(db, d)


@router.post("/devices/{device_id}/optimization/evaluate")
def evaluate(device_id: str, body: EvaluateInput, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("operator"))):
    """Execution-time authorization check for one proposed automated action (no device I/O)."""
    from ..infrastructure.optimizer import evaluate as opt_eval
    d = real_device(db, device_id)
    res, dec = opt_eval(db, d, body.op, actor=user.username, recommendation_id=body.recommendation_id)
    audit(db, "optimization_action_evaluated", username=user.username, target=d.id,
          detail=f"decision={res['decision']} op={body.op.get('op')}; evaluation only", request=request,
          success=res["decision"] == "ALLOW")
    return dict(res, decision_id=dec.id, device_writes=0, adapter_called=False)


@router.post("/optimization/run-now")
def run_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    from ..infrastructure.optimizer import run_optimizer
    out = run_optimizer(db, actor=f"optimizer(run-now:{user.username})")
    audit(db, "optimizer_run", username=user.username, detail=str(out), request=request)
    return out


class SitePolicyInput(BaseModel):
    allowed_channels_5g: list[int] = []
    allowed_channels_2g: list[int] = []
    allowed_widths_mhz: list[int] = [20, 40]
    min_tx_dbm: int = Field(default=9, ge=3, le=30)
    max_tx_dbm: int = Field(default=18, ge=3, le=30)
    window_start_hour: int | None = None
    window_end_hour: int | None = None


@router.get("/optimization/site-policies")
def site_policies(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import OptimizationSitePolicy
    return [{"site_id": p.site_id, "allowed_channels_5g": p.allowed_channels_5g, "allowed_channels_2g": p.allowed_channels_2g,
             "allowed_widths_mhz": p.allowed_widths_mhz, "min_tx_dbm": p.min_tx_dbm, "max_tx_dbm": p.max_tx_dbm,
             "window": [p.window_start_hour, p.window_end_hour], "updated_by": p.updated_by}
            for p in db.scalars(select(OptimizationSitePolicy))]


@router.put("/optimization/site-policies/{site_id}")
def put_site_policy(site_id: str, body: SitePolicyInput, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role("admin"))):
    """Site CONSTRAINTS only. A site policy never enrolls a device or enables automation."""
    from ..database.models_inm import InmSite
    from ..database.models_phases import OptimizationSitePolicy
    from ..domain.change_control import DFS_5G, VALID_2G, VALID_5G
    if db.get(InmSite, site_id) is None:
        raise HTTPException(404, "Site not found.")
    if any(w not in (20, 40) for w in body.allowed_widths_mhz):
        raise HTTPException(422, "Widths may only be 20 and/or 40 MHz.")
    if any(c not in VALID_5G for c in body.allowed_channels_5g) or any(c not in VALID_2G for c in body.allowed_channels_2g):
        raise HTTPException(422, "Invalid channel in policy.")
    if body.min_tx_dbm > body.max_tx_dbm:
        raise HTTPException(422, "Minimum power exceeds maximum.")
    p = db.get(OptimizationSitePolicy, site_id) or OptimizationSitePolicy(site_id=site_id, updated_by=user.username)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    p.updated_by, p.updated_at = user.username, now()
    db.add(p)
    audit(db, "optimization_site_policy_set", username=user.username, target=site_id,
          detail=f"5G={body.allowed_channels_5g} DFS={[c for c in body.allowed_channels_5g if c in DFS_5G]} widths={body.allowed_widths_mhz}",
          request=request)
    return {"site_id": site_id, "saved": True}


@router.get("/optimization/enrollments")
def enrollments(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "approver"))):
    return [_enr_public(e) for e in db.scalars(select(OptimizationEnrollment)
                                               .order_by(OptimizationEnrollment.created_at.desc()).limit(500))]


@router.get("/optimization/decisions")
def opt_decisions(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "approver"))):
    return [{"id": x.id, "enrollment_id": x.enrollment_id, "device_id": x.device_id, "decision": x.decision,
             "guards": x.guards, "denial_reason": x.denial_reason, "evaluated_by": x.evaluated_by,
             "evaluated_at": iso(x.evaluated_at)}
            for x in db.scalars(select(ExecutionDecision).where(ExecutionDecision.kind == "OPTIMIZATION")
                                .order_by(ExecutionDecision.evaluated_at.desc()).limit(300))]


@router.get("/optimization/control")
def get_control(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    c = control(db)
    from ..infrastructure.optimizer import feature_enabled
    return {"emergency_stop": c.emergency_stop, "changed_by": c.changed_by, "changed_at": iso(c.changed_at),
            "reason": c.reason, "global_write_lock": get_settings().device_write_lock,
            "automatic_optimization_feature": feature_enabled()}


@router.post("/optimization/emergency-stop")
def emergency_stop(body: StopInput, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    c = control(db)
    c.emergency_stop, c.changed_by, c.changed_at, c.reason = body.active, user.username, now(), body.reason
    paused = 0
    if body.active:
        # Emergency stop pauses every armed device; each must be reviewed again (not auto-resumed).
        for e in db.scalars(select(OptimizationEnrollment).where(OptimizationEnrollment.status.in_(ACTIVE_ENROLLMENT))):
            e.status, e.status_reason = "PAUSED", "EMERGENCY_STOP"
            paused += 1
    audit(db, "optimization_emergency_stop", username=user.username,
          detail=f"active={body.active} paused={paused} reason={body.reason}", request=request)
    return {"emergency_stop": c.emergency_stop, "changed_by": c.changed_by, "enrollments_paused": paused}
