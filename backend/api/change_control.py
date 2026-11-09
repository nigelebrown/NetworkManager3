"""WP-06 — typed change plans, two-person approval and execution gate (Phase 6).

`execute` evaluates every guard at execution time and records the decision. In this release the
global write lock is on and no adapter is CERTIFIED_WRITE, so the decision is DENY and no adapter
is called. There is no code path from this module to a device driver.
"""
from __future__ import annotations

from ..infrastructure.posture import read_only, write_posture

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import DeviceCapability
from ..database.models_phases import ChangePlan, ConfigArtifact, ExecutionDecision
from ..database.session import get_db
from ..domain import change_control as CC
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, real_device

router = APIRouter(prefix="/api/v1/changes", tags=["wp06-change-control"])


class PlanInput(BaseModel):
    device_id: str
    title: str = Field(min_length=3, max_length=200)
    operations: list[dict] = Field(min_length=1, max_length=50)
    excluded_channels: list[int] = []
    allow_dfs: bool = False
    protected_uplinks: list[str] = []
    submit: bool = True


class Decision(BaseModel):
    expected_revision: int = Field(ge=1)
    note: str = Field(default="", max_length=2000)


def _latest_hash(db: Session, device_id: str) -> str | None:
    a = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device_id)
                  .order_by(ConfigArtifact.created_at.desc()).limit(1))
    return a.plaintext_sha256 if a else None


def _public(p: ChangePlan) -> dict:
    return {"id": p.id, "device_id": p.device_id, "title": p.title, "operations": p.operations,
            "capability_report": p.capability_report, "plan_hash": p.plan_hash,
            "expected_config_hash": p.expected_config_hash, "firmware_at_request": p.firmware_at_request,
            "rollback_plan": p.rollback_plan, "state": p.state, "requester": p.requester,
            "approver": p.approver, "approved_at": iso(p.approved_at), "decision_note": p.decision_note,
            "source": p.source, "revision": p.revision, "created_at": iso(p.created_at),
            **read_only()}


@router.post("/plans", status_code=201)
def create_plan(body: PlanInput, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("operator"))):
    d = real_device(db, body.device_id)
    try:
        ops = [CC.validate_operation(o, family=d.family, excluded_channels=set(body.excluded_channels),
                                     allow_dfs=body.allow_dfs, uplink_ports=set(body.protected_uplinks))
               for o in body.operations]
    except CC.PolicyViolation as exc:
        audit(db, "change_plan_policy_rejected", username=user.username, target=d.id,
              detail=f"{exc}; rejected before any adapter call", request=request, success=False)
        db.commit()
        raise HTTPException(422, str(exc)) from None
    caps = {c.operation: c.status for c in db.scalars(select(DeviceCapability).where(DeviceCapability.device_id == d.id))}
    # Write certification for this model/firmware/environment wins; a validated operation the writers implement
    # but nobody has certified is UNCERTIFIED (refused at the execution gate), not UNSUPPORTED.
    from backend.infrastructure.change_executor import certification_map
    certs = certification_map(db, d)
    caps = {o["op"]: certs.get(o["op"]) or caps.get(o["op"]) or "UNCERTIFIED" for o in ops}
    report = CC.capability_check(ops, caps)
    base = _latest_hash(db, d.id)
    p = ChangePlan(device_id=d.id, title=body.title.strip(), operations=ops, capability_report=report,
                   plan_hash=CC.plan_hash(d.id, ops, base), expected_config_hash=base,
                   firmware_at_request=d.firmware,
                   rollback_plan={"strategy": "RESTORE_PRE_CHANGE_VAULT_ARTIFACT", "artifact_required": True},
                   state="PENDING_APPROVAL" if body.submit else "DRAFT", requester=user.username)
    db.add(p); db.flush()
    audit(db, "change_plan_created", username=user.username, target=d.id,
          detail=f"plan={p.id} ops={len(ops)} state={p.state} unsupported="
                 f"{sum(r['capability'] == 'UNSUPPORTED' for r in report)}", request=request)
    return _public(p)


@router.get("/plans")
def list_plans(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return [_public(p) for p in db.scalars(select(ChangePlan).order_by(ChangePlan.created_at.desc()).limit(300))]


def _plan(db: Session, plan_id: str) -> ChangePlan:
    p = db.get(ChangePlan, plan_id)
    if p is None:
        raise HTTPException(404, "Change plan not found.")
    return p


def _transition(db: Session, p: ChangePlan, body: Decision, new_state: str, user: User) -> None:
    """Compare-and-set on (state, revision) so two concurrent approvers cannot both win."""
    res = db.execute(update(ChangePlan).where(ChangePlan.id == p.id, ChangePlan.state == "PENDING_APPROVAL",
                                              ChangePlan.revision == body.expected_revision)
                     .values(state=new_state, approver=user.username, approved_at=now(),
                             decision_note=body.note, revision=ChangePlan.revision + 1)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise HTTPException(409, "Plan is not pending at this revision (already decided or changed).")
    db.refresh(p)


@router.post("/plans/{plan_id}/submit")
def submit(plan_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("operator"))):
    p = _plan(db, plan_id)
    if p.state != "DRAFT" or p.requester != user.username:
        raise HTTPException(409, "Only the requester can submit a DRAFT plan.")
    p.state, p.revision = "PENDING_APPROVAL", p.revision + 1
    audit(db, "change_plan_submitted", username=user.username, target=p.device_id, detail=p.id, request=request)
    return _public(p)


@router.post("/plans/{plan_id}/approve")
def approve(plan_id: str, body: Decision, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_any_role("approver", "admin"))):
    p = _plan(db, plan_id)
    try:
        CC.check_approval(requester=p.requester, approver=user.username, approver_role=user.role)
    except CC.PolicyViolation as exc:
        audit(db, "change_plan_self_approval_denied", username=user.username, target=p.id,
              detail=str(exc), request=request, success=False)
        db.commit()
        raise HTTPException(403, str(exc)) from None
    _transition(db, p, body, "APPROVED", user)
    audit(db, "change_plan_approved", username=user.username, target=p.device_id,
          detail=f"plan={p.id} requester={p.requester}; approval is not a write", request=request)
    return _public(p)


@router.post("/plans/{plan_id}/reject")
def reject(plan_id: str, body: Decision, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_any_role("approver", "admin"))):
    p = _plan(db, plan_id)
    if p.requester.casefold() == user.username.casefold():
        raise HTTPException(403, "Requester cannot decide their own plan.")
    _transition(db, p, body, "REJECTED", user)
    audit(db, "change_plan_rejected", username=user.username, target=p.device_id, detail=p.id, request=request)
    return _public(p)


@router.post("/plans/{plan_id}/execute")
def execute(plan_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    """Run the certified execution engine. Every guard is evaluated server-side at execution time."""
    from ..infrastructure.change_executor import execute_plan
    p = _plan(db, plan_id)
    result = execute_plan(db, p, actor=user.username)
    audit(db, "change_plan_execution", username=user.username, target=p.device_id,
          detail=f"plan={p.id} decision={result['decision']} outcome={result.get('outcome')} "
                 f"env={result.get('environment')} writes={result.get('device_writes')}",
          request=request, success=result.get("outcome") == "APPLIED_VERIFIED")
    return result


@router.get("/plans/{plan_id}/render")
def render(plan_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Exactly what would be sent to the device for each operation (no device I/O)."""
    from ..infrastructure.change_executor import certification_map, environment
    from ..database.models_inm import InmDevice
    from ..infrastructure.writers import Credentials, WriterError
    from ..infrastructure.writers.aruba_cx import ArubaCxWriter
    from ..infrastructure.writers.aruba_instant import ArubaInstantWriter
    p = _plan(db, plan_id)
    d = db.get(InmDevice, p.device_id)
    creds = Credentials("device", "user", "***", ssh_fingerprint="pinned")
    w = ArubaCxWriter(creds) if d.family == "switch" else ArubaInstantWriter(creds, client=object(), ssh_runner=None, snapshot=lambda: [])
    out = []
    for o in p.operations:
        try:
            out.append({"op": o, "requests": [{"restore_artifact": o["artifact_id"]}] if o["op"] == "config.restore" else w.render(o)})
        except WriterError as exc:
            out.append({"op": o, "error": str(exc)})
    certs = certification_map(db, d)
    return {"plan_id": p.id, "environment": environment(d), "operations": out,
            "certification": {o["op"]: certs.get(o["op"], "UNCERTIFIED") for o in p.operations}}


@router.get("/plans/{plan_id}/executions")
def executions(plan_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import ChangeExecution
    return [{"id": e.id, "environment": e.environment, "outcome": e.outcome, "steps": e.steps,
             "pre_artifact_id": e.pre_artifact_id, "post_artifact_id": e.post_artifact_id,
             "executed_by": e.executed_by, "started_at": iso(e.started_at), "finished_at": iso(e.finished_at)}
            for e in db.scalars(select(ChangeExecution).where(ChangeExecution.plan_id == plan_id)
                                .order_by(ChangeExecution.started_at.desc()))]


@router.get("/decisions")
def decisions(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "approver"))):
    return [{"id": x.id, "kind": x.kind, "plan_id": x.plan_id, "enrollment_id": x.enrollment_id,
             "device_id": x.device_id, "decision": x.decision, "guards": x.guards,
             "denial_reason": x.denial_reason, "global_lock_state": x.global_lock_state,
             "evaluated_by": x.evaluated_by, "evaluated_at": iso(x.evaluated_at)}
            for x in db.scalars(select(ExecutionDecision).order_by(ExecutionDecision.evaluated_at.desc()).limit(300))]
