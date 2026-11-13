"""Persistent end-to-end onboarding workflow (E2E-06), device groups and multi-device deployments (E2E-20).

The workflow is a resumable record (id) that drives the SAME tested building blocks the individual pages use:
site -> credential -> durable discovery -> identify/recognise existing -> register -> import config + vault
backup -> templates/groups -> preview -> configure NOW or LATER -> independent approval -> certified execution
with readback -> monitoring -> done. Every decision is appended to the workflow history.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import InmDevice, InmSite, InmCluster, SiteAccessGrant
from ..database.models_onboarding import OnboardingDiscovery
from ..database.models_phases import ChangeExecution, ChangePlan, CollectionPlan
from ..database.models_remediation import DeviceGroup, DeviceGroupMember, OnboardingWorkflow
from ..database.session import get_db
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import real_device

router = APIRouter(prefix="/api/v1", tags=["workflows"])

STEPS = ["SITE", "CREDENTIAL", "DISCOVER", "REGISTER", "BASELINE", "TEMPLATES", "PREVIEW", "DECIDE", "APPROVAL",
         "EXECUTE", "MONITOR", "DONE"]


def _now() -> datetime:
    return datetime.now(timezone.utc)



def _authorized_sites(db: Session, user: User) -> set[str] | None:
    """Map legacy per-user grants to the separate physical-site UUIDs (Instant clusters are not physical sites).

    Scoped users have no physical-site access until the grant maps to a cluster or device. Admin/global
    users are unrestricted. Never infer that legacy site integer 123 equals a physical-site UUID.
    """
    if user.role == "admin" or (user.site_scope or "all") == "all":
        return None
    grants = list(db.scalars(select(SiteAccessGrant.site_id).where(SiteAccessGrant.user_id == user.id)))
    if not grants:
        return set()
    mapped = set(db.scalars(select(InmCluster.site_id).where(InmCluster.legacy_site_id.in_(grants),
                                                               InmCluster.site_id.is_not(None))))
    mapped.update(db.scalars(select(InmDevice.site_id).where(InmDevice.legacy_site_id.in_(grants),
                                                              InmDevice.site_id.is_not(None))))
    return mapped


def _may_see_site(db: Session, user: User, site_id: str | None) -> bool:
    allowed = _authorized_sites(db, user)
    return allowed is None or bool(site_id and site_id in allowed)


def _require_site(db: Session, user: User, site_id: str | None) -> None:
    if not _may_see_site(db, user, site_id):
        # Non-disclosing response: do not reveal whether a foreign site or resource exists.
        raise HTTPException(404, "Resource not found.")


def _require_stage(w: OnboardingWorkflow, *stages: str) -> None:
    if w.state in ("CANCELLED", "DONE", "FAILED") or w.step not in stages:
        raise HTTPException(409, f"Workflow is at {w.step}/{w.state}; expected {', '.join(stages)}.")


def _plans_fully_applied(db: Session, w: OnboardingWorkflow) -> bool:
    if w.mode != "NOW" or not w.device_ids or (w.outcome or {}).get("decide_errors"):
        return False
    ids = w.plan_ids or []
    # No plans is valid only when each device was independently shown to be in compliance.
    if not ids:
        return set((w.outcome or {}).get("already_compliant", [])) == set(w.device_ids)
    plans = [db.get(ChangePlan, pid) for pid in ids]
    if any(p is None or p.state != "APPLIED_VERIFIED" or p.device_id not in w.device_ids for p in plans):
        return False
    planned_devices = [p.device_id for p in plans if p is not None]
    compliant = set((w.outcome or {}).get("already_compliant", []))
    # Each selected device must have a verified applied plan OR independent evidence of compliance.
    # Duplicated plans or a missing device cannot silently make the wizard DONE.
    return len(planned_devices) == len(set(planned_devices)) and \
        set(planned_devices).isdisjoint(compliant) and \
        set(planned_devices) | compliant == set(w.device_ids)


# ---- device groups ------------------------------------------------------------------------------------------
class GroupInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    family: str = Field(pattern="^(switch|access_point)$")
    description: str | None = Field(default=None, max_length=255)


class MembersInput(BaseModel):
    device_ids: list[str] = Field(min_length=1, max_length=500)


def _group_out(db: Session, g: DeviceGroup, user: User | None = None) -> dict:
    members = [m.device_id for m in db.scalars(select(DeviceGroupMember).where(DeviceGroupMember.group_id == g.id))]
    if user is not None:
        members = [did for did in members if (d := db.get(InmDevice, did)) is not None
                   and _may_see_site(db, user, d.site_id)]
    return {"id": g.id, "name": g.name, "family": g.family, "description": g.description, "members": members,
            "created_by": g.created_by}


@router.get("/device-groups")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    scope = _authorized_sites(db, user)
    return [_group_out(db, g, user) for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))
            if scope is None or any(_may_see_site(db, user, db.get(InmDevice, m.device_id).site_id)
                                    for m in db.scalars(select(DeviceGroupMember).where(DeviceGroupMember.group_id == g.id))
                                    if db.get(InmDevice, m.device_id) is not None)]


@router.post("/device-groups", status_code=201)
def create_group(body: GroupInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    if db.scalar(select(DeviceGroup).where(DeviceGroup.name == body.name.strip())):
        raise HTTPException(409, "A group with this name exists.")
    g = DeviceGroup(name=body.name.strip(), family=body.family, description=body.description, created_by=user.username)
    db.add(g)
    db.flush()
    audit(db, "device_group_created", username=user.username, target=g.id, detail=g.name, request=request)
    return _group_out(db, g)


@router.post("/device-groups/{group_id}/members")
def add_members(group_id: str, body: MembersInput, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    g = db.get(DeviceGroup, group_id)
    if g is None:
        raise HTTPException(404, "Group not found.")
    for did in body.device_ids:
        d = real_device(db, did)
        if d.family != g.family:
            raise HTTPException(422, f"{d.hostname or d.serial_number} is not a {g.family}.")
        if db.get(DeviceGroupMember, (g.id, d.id)) is None:
            db.add(DeviceGroupMember(group_id=g.id, device_id=d.id, added_by=user.username))
    db.flush()
    audit(db, "device_group_members_added", username=user.username, target=g.id,
          detail=f"{len(body.device_ids)} device(s)", request=request)
    return _group_out(db, g)


@router.delete("/device-groups/{group_id}/members/{device_id}")
def remove_member(group_id: str, device_id: str, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    m = db.get(DeviceGroupMember, (group_id, device_id))
    if m is None:
        raise HTTPException(404, "Not a member.")
    db.delete(m)
    audit(db, "device_group_member_removed", username=user.username, target=group_id, detail=device_id, request=request)
    return {"removed": device_id}


# ---- multi-device deployments ---------------------------------------------------------------------------------
class DeploymentInput(BaseModel):
    device_ids: list[str] = Field(default_factory=list, max_length=500)
    group_id: str | None = None


def _batch_plans(db: Session, batch_id: str) -> list[ChangePlan]:
    return [p for p in db.scalars(select(ChangePlan).where(ChangePlan.source == "TEMPLATE"))
            if (p.rollback_plan or {}).get("batch_id") == batch_id]


def _plan_summary(db: Session, p: ChangePlan) -> dict:
    ex = db.scalar(select(ChangeExecution).where(ChangeExecution.plan_id == p.id)
                   .order_by(ChangeExecution.started_at.desc()).limit(1))
    d = db.get(InmDevice, p.device_id)
    return {"plan_id": p.id, "device_id": p.device_id, "hostname": d.hostname if d else None, "state": p.state,
            "operations": len(p.operations), "approver": p.approver,
            "execution": {"id": ex.id, "outcome": ex.outcome, "environment": ex.environment} if ex else None}


def _progress(rows: list[dict]) -> dict:
    states = [r["state"] for r in rows]
    return {"total": len(rows), "pending_approval": states.count("PENDING_APPROVAL"),
            "approved": states.count("APPROVED"), "applied_verified": states.count("APPLIED_VERIFIED"),
            "failed_rolled_back": states.count("FAILED_ROLLED_BACK"), "rollback_failed": states.count("ROLLBACK_FAILED"),
            "rejected": states.count("REJECTED")}


@router.post("/deployments", status_code=201)
def create_deployment(body: DeploymentInput, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_role("operator"))):
    """One template-deployment plan per device (each needs its own independent approval)."""
    from .config_templates import deploy_plan
    ids = list(body.device_ids)
    if body.group_id:
        ids += [m.device_id for m in db.scalars(select(DeviceGroupMember).where(DeviceGroupMember.group_id == body.group_id))]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(422, "Select devices or a group.")
    for did in ids:
        device = real_device(db, did)
        _require_site(db, user, device.site_id)
    batch = str(uuid.uuid4())
    results = []
    for did in ids:
        try:
            r = deploy_plan(did, request, db, user, batch_id=batch)
            results.append({"device_id": did, "plan_id": r["plan_id"], "in_compliance": r["in_compliance"],
                            "operations": len(r.get("operations") or [])})
        except HTTPException as exc:
            results.append({"device_id": did, "plan_id": None, "error": exc.detail})
    audit(db, "deployment_created", username=user.username, target=batch,
          detail=f"devices={len(ids)} plans={sum(1 for r in results if r.get('plan_id'))}", request=request)
    return {"batch_id": batch, "devices": results}


@router.get("/deployments/{batch_id}")
def get_deployment(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    plans = _batch_plans(db, batch_id)
    if plans and any(not _may_see_site(db, user, db.get(InmDevice, p.device_id).site_id)
                     for p in plans if db.get(InmDevice, p.device_id) is not None):
        raise HTTPException(404, "Deployment not found.")
    rows = [_plan_summary(db, p) for p in plans]
    return {"batch_id": batch_id, "plans": rows, "progress": _progress(rows)}


class ExecuteInput(BaseModel):
    stop_on_failure: bool = True


@router.post("/deployments/{batch_id}/execute")
def execute_deployment(batch_id: str, body: ExecuteInput, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_role("admin"))):
    """Execute every APPROVED plan in the batch, one device at a time, through the certified engine. Stops at
    the first failure by default so a bad template never rolls across the fleet."""
    from ..infrastructure.change_executor import execute_plan
    out, stopped = [], None
    for p in _batch_plans(db, batch_id):
        if p.state != "APPROVED":
            continue
        r = execute_plan(db, p, actor=user.username)
        out.append({"plan_id": p.id, "device_id": p.device_id, "decision": r["decision"], "outcome": r.get("outcome"),
                    "denial_reason": r.get("denial_reason")})
        if body.stop_on_failure and (r["decision"] != "ALLOW" or r.get("outcome") != "APPLIED_VERIFIED"):
            stopped = p.id
            break
    audit(db, "deployment_executed", username=user.username, target=batch_id,
          detail=f"executed={len(out)} stopped_at={stopped}", request=request)
    rows = [_plan_summary(db, p) for p in _batch_plans(db, batch_id)]
    return {"batch_id": batch_id, "executed": out, "stopped_at": stopped, "progress": _progress(rows)}


# ---- persistent onboarding workflow ----------------------------------------------------------------------------
class WorkflowInput(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    device_family: str = Field(pattern="^(switch|access_point)$")
    site_id: str | None = None
    site_name: str | None = Field(default=None, max_length=100)


def _wf(db: Session, wid: str, user: User | None = None) -> OnboardingWorkflow:
    w = db.get(OnboardingWorkflow, wid)
    if w is None:
        raise HTTPException(404, "Workflow not found.")
    if user is not None:
        _require_site(db, user, w.site_id)
    if w.state in ("CANCELLED",):
        raise HTTPException(409, "Workflow is cancelled.")
    return w


def _log(w: OnboardingWorkflow, actor: str, step: str, detail: str, **data) -> None:
    w.history = list(w.history or []) + [{"at": _now().isoformat(), "actor": actor, "step": step, "detail": detail,
                                          **data}]
    w.updated_at = _now()


def _advance(w: OnboardingWorkflow, step: str) -> None:
    if STEPS.index(step) > STEPS.index(w.step):
        w.step = step


def _view(db: Session, w: OnboardingWorkflow) -> dict:
    """The whole task on one screen: every linked object's LIVE state."""
    from ..infrastructure import discovery_jobs as DJ
    from ..infrastructure.device_state import plans_by_device, state_of
    disc = db.get(OnboardingDiscovery, w.discovery_id) if w.discovery_id else None
    plans = [db.get(ChangePlan, pid) for pid in (w.plan_ids or [])]
    cplans = plans_by_device(db)
    devices = []
    for did in w.device_ids or []:
        d = db.get(InmDevice, did)
        if d is None:
            continue
        st = state_of(d, cplans.get(did))
        devices.append({"device_id": d.id, "hostname": d.hostname, "serial_number": d.serial_number, "model": d.model,
                        "firmware": d.firmware, "source_type": d.source_type, "state": st["state"],
                        "state_reason": st["state_reason"], "monitored": did in cplans})
    plan_rows = [_plan_summary(db, p) for p in plans if p is not None]
    idx = STEPS.index(w.step)
    return {"id": w.id, "title": w.title, "state": w.state, "step": w.step, "mode": w.mode,
            "site": {"id": w.site_id, "name": w.site_name}, "device_family": w.device_family,
            "credential_id": w.credential_id,
            "steps": [{"step": s, "status": "done" if i < idx or w.step == "DONE" else "current" if i == idx else "pending"}
                      for i, s in enumerate(STEPS)],
            "discovery": None if disc is None else {"id": disc.id, "status": disc.status, "scope": disc.scope, "results": disc.results,
                                                    "progress": DJ.progress(disc) if disc.targets else None},
            "devices": devices, "plans": plan_rows, "plan_progress": _progress(plan_rows),
            "template_scope": w.template_scope, "outcome": w.outcome, "history": w.history,
            "created_by": w.created_by, "created_at": w.created_at.isoformat() if w.created_at else None}


@router.post("/workflows", status_code=201)
def start_workflow(body: WorkflowInput, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    if body.site_id:
        site = db.get(InmSite, body.site_id)
        if site is None:
            raise HTTPException(404, "Site not found.")
    elif body.site_name and body.site_name.strip():
        site = db.scalar(select(InmSite).where(InmSite.name == body.site_name.strip(), InmSite.source_type == "REAL"))
        if site is None:
            site = InmSite(name=body.site_name.strip(), source_type="REAL")
            db.add(site)
            db.flush()
    else:
        raise HTTPException(422, "Select a site or enter a new site name.")
    w = OnboardingWorkflow(title=body.title.strip(), site_id=site.id, site_name=site.name,
                           device_family=body.device_family, step="CREDENTIAL", state="ACTIVE",
                           device_ids=[], template_scope={}, plan_ids=[], history=[], outcome={},
                           created_by=user.username)
    db.add(w)
    db.flush()
    _log(w, user.username, "SITE", f"Site {site.name}; family {body.device_family}")
    audit(db, "workflow_started", username=user.username, target=w.id, detail=w.title, request=request)
    return _view(db, w)


_READERS = ("admin", "operator", "approver", "auditor")


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db), user: User = Depends(require_any_role(*_READERS))):
    q = select(OnboardingWorkflow).order_by(OnboardingWorkflow.updated_at.desc()).limit(200)
    return [{"id": w.id, "title": w.title, "step": w.step, "state": w.state, "site": w.site_name,
             "devices": len(w.device_ids or []), "updated_at": w.updated_at.isoformat() if w.updated_at else None}
            for w in db.scalars(q) if _may_see_site(db, user, w.site_id)]


@router.get("/workflows/{wid}")
def get_workflow(wid: str, db: Session = Depends(get_db), user: User = Depends(require_any_role(*_READERS))):
    w = db.get(OnboardingWorkflow, wid)
    if w is None:
        raise HTTPException(404, "Workflow not found.")
    _require_site(db, user, w.site_id)
    view = _view(db, w)
    # Monitoring confirmation arrives asynchronously (worker ingest): complete the task once every device has a
    # fresh persisted observation.
    if w.step == "MONITOR" and w.state == "ACTIVE" and _plans_fully_applied(db, w) and view["devices"] and \
            all(d["state"] in ("ONLINE", "DEGRADED") for d in view["devices"]):
        w.step, w.state = "DONE", "DONE"
        _log(w, "system", "DONE", "All devices have fresh persisted observations: "
             + ", ".join(f"{d['hostname']} {d['state']}" for d in view["devices"]))
        view = _view(db, w)
    return view


class CredentialChoice(BaseModel):
    credential_id: str


@router.post("/workflows/{wid}/credential")
def wf_credential(wid: str, body: CredentialChoice, db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    from .onboarding_workflow import _profile
    w = _wf(db, wid, user)
    _require_stage(w, "CREDENTIAL", "DISCOVER")
    prof = _profile(db, body.credential_id, w.device_family)
    w.credential_id = prof.id
    _advance(w, "DISCOVER")
    _log(w, user.username, "CREDENTIAL", f"Using credential profile {prof.name}")
    return _view(db, w)


class DiscoverChoice(BaseModel):
    scope: str = Field(min_length=1, max_length=48)
    rate_per_s: int = Field(default=16, ge=1, le=64)


@router.post("/workflows/{wid}/discover")
def wf_discover(wid: str, body: DiscoverChoice, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    from .onboarding_workflow import DiscoveryRequest, discover_devices
    w = _wf(db, wid, user)
    _require_stage(w, "DISCOVER", "REGISTER")
    if not w.credential_id:
        raise HTTPException(409, "Choose credentials first.")
    r = discover_devices(DiscoveryRequest(credential_id=w.credential_id, scope=body.scope, site_name=w.site_name,
                                          rate_per_s=body.rate_per_s, workflow_id=w.id), request, db, user)
    w.discovery_id = r["id"]
    _advance(w, "REGISTER")
    _log(w, user.username, "DISCOVER", f"Discovery {r['id'][:8]} of {body.scope}: {r['status']}", discovery_id=r["id"])
    return _view(db, w)


class RegisterChoice(BaseModel):
    indices: list[int] = Field(min_length=1, max_length=64)


@router.post("/workflows/{wid}/register")
def wf_register(wid: str, body: RegisterChoice, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))):
    from .onboarding_workflow import RegisterDiscovered, register_discovered
    w = _wf(db, wid, user)
    _require_stage(w, "REGISTER")
    disc = db.get(OnboardingDiscovery, w.discovery_id) if w.discovery_id else None
    if disc is None or disc.status not in ("SCANNED",):
        raise HTTPException(409, "Wait for discovery to finish (or retry it) before registering devices.")
    r = register_discovered(disc.id, RegisterDiscovered(indices=body.indices), request, db, user)
    ids = [x["device_id"] for x in r["registered"]]
    w.device_ids = list(dict.fromkeys(list(w.device_ids or []) + ids))
    _advance(w, "BASELINE")
    _log(w, user.username, "REGISTER", f"Registered {len(ids)} device(s) (existing devices are recognised and "
                                       "updated, never duplicated)", device_ids=ids)
    return _view(db, w)


class AttachChoice(BaseModel):
    device_ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/workflows/{wid}/attach")
def wf_attach(wid: str, body: AttachChoice, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    """Bring devices already in inventory into this workflow (recognised existing devices, or continuing an
    earlier 'configure later')."""
    w = _wf(db, wid, user)
    _require_stage(w, "CREDENTIAL", "DISCOVER", "REGISTER", "BASELINE")
    for did in body.device_ids:
        d = real_device(db, did)
        if d.family != w.device_family or d.site_id != w.site_id:
            raise HTTPException(422, "Device family or physical site does not match this workflow.")
    w.device_ids = list(dict.fromkeys(list(w.device_ids or []) + body.device_ids))
    if STEPS.index(w.step) < STEPS.index("BASELINE"):
        w.step = "BASELINE"
    _log(w, user.username, "REGISTER", f"Attached {len(body.device_ids)} existing device(s) from inventory",
         device_ids=body.device_ids)
    return _view(db, w)


@router.post("/workflows/{wid}/baseline")
def wf_baseline(wid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    """Import the current configuration of every device as a full-fidelity, encrypted vault backup (read-only)."""
    from .production_ops import capture_from_device
    w = _wf(db, wid, user)
    _require_stage(w, "BASELINE")
    if not w.device_ids:
        raise HTTPException(409, "Register devices first.")
    results = {}
    for did in w.device_ids:
        try:
            r = capture_from_device(did, request, db, user)
            results[did] = {"ok": True, "artifact": r.get("artifact_id"), "sha256": r.get("sha256")}
        except HTTPException as exc:
            results[did] = {"ok": False, "error": exc.detail}
    w.outcome = {**(w.outcome or {}), "baseline": results}
    if all(x["ok"] for x in results.values()):
        _advance(w, "TEMPLATES")
    _log(w, user.username, "BASELINE", f"Configuration imported for {sum(x['ok'] for x in results.values())}/"
                                       f"{len(results)} device(s)", results=results)
    return _view(db, w)


class TemplateChoice(BaseModel):
    group_id: str | None = None
    new_group_name: str | None = Field(default=None, max_length=100)


@router.post("/workflows/{wid}/templates")
def wf_templates(wid: str, body: TemplateChoice, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    """Choose the configuration scope: devices join a group (group templates apply) on top of global, site,
    model and device templates."""
    w = _wf(db, wid, user)
    _require_stage(w, "TEMPLATES")
    gid = body.group_id
    if body.new_group_name:
        gid = create_group(GroupInput(name=body.new_group_name, family=w.device_family), request, db, user)["id"]
    if gid:
        add_members(gid, MembersInput(device_ids=w.device_ids), request, db, user)
    w.template_scope = {"group_id": gid}
    _advance(w, "PREVIEW")
    _log(w, user.username, "TEMPLATES", f"Group {gid[:8] if gid else 'none'}; global/site/model/device templates apply")
    return _view(db, w)


@router.get("/workflows/{wid}/preview")
def wf_preview(wid: str, db: Session = Depends(get_db), user: User = Depends(require_any_role(*_READERS))):
    from ..infrastructure.change_executor import certification_map
    from .config_templates import intent_and_ops
    w = _wf(db, wid, user)
    _require_stage(w, "PREVIEW", "DECIDE")
    out = []
    for did in w.device_ids or []:
        d = real_device(db, did)
        r = intent_and_ops(db, d)
        certs = certification_map(db, d)
        r["certification"] = {o["op"]: certs.get(o["op"], "UNCERTIFIED") for o in r["operations"] or []}
        r["hostname"] = d.hostname
        out.append(r)
    if w.step == "PREVIEW":
        w.step = "DECIDE"
    return {"workflow_id": w.id, "devices": out}


class DecideChoice(BaseModel):
    mode: str = Field(pattern="^(NOW|LATER)$")


@router.post("/workflows/{wid}/decide")
def wf_decide(wid: str, body: DecideChoice, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_role("admin"))):
    """NOW: create one template plan per device and submit it for independent approval.
    LATER: skip configuration for now (resume any time); monitoring is still set up."""
    from .config_templates import deploy_plan
    w = _wf(db, wid, user)
    _require_stage(w, "DECIDE")
    w.mode = body.mode
    if body.mode == "LATER":
        _advance(w, "MONITOR")
        _log(w, user.username, "DECIDE", "Configure later — devices stay as imported; resume from this workflow")
        return _view(db, w)
    created = []
    for did in w.device_ids or []:
        try:
            r = deploy_plan(did, request, db, user, workflow_id=w.id)
        except HTTPException as exc:
            created.append({"device_id": did, "error": exc.detail})
            continue
        if r.get("plan_id"):
            created.append({"device_id": did, "plan_id": r["plan_id"], "operations": len(r["operations"])})
    w.plan_ids = list(dict.fromkeys(list(w.plan_ids or []) + [c["plan_id"] for c in created if c.get("plan_id")]))
    w.outcome = {**(w.outcome or {}), "decide_errors": [c for c in created if c.get("error")],
                 "already_compliant": [c["device_id"] for c in created
                                       if not c.get("error") and not c.get("plan_id")]}
    _advance(w, "APPROVAL" if w.plan_ids or w.outcome["decide_errors"] else "MONITOR")
    _log(w, user.username, "DECIDE", f"Configure now: {len(w.plan_ids)} plan(s) submitted for independent approval; "
                                     f"{sum(1 for c in created if not c.get('plan_id') and not c.get('error'))} "
                                     "device(s) already compliant", plans=created)
    return _view(db, w)


@router.post("/workflows/{wid}/execute")
def wf_execute(wid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    """Apply every APPROVED plan through the certified engine (backup, checkpoint, apply, readback, rollback)."""
    from ..infrastructure.change_executor import execute_plan
    w = _wf(db, wid, user)
    _require_stage(w, "APPROVAL", "EXECUTE", "MONITOR")
    results = []
    for pid in w.plan_ids or []:
        p = db.get(ChangePlan, pid)
        if p is None or p.state != "APPROVED":
            continue
        r = execute_plan(db, p, actor=user.username)
        results.append({"plan_id": pid, "decision": r["decision"], "outcome": r.get("outcome"),
                        "denial_reason": r.get("denial_reason"), "environment": r.get("environment")})
    if not results:
        raise HTTPException(409, "No approved plans to execute. An independent approver must approve them first.")
    states = [db.get(ChangePlan, pid).state for pid in w.plan_ids]
    if states and all(s == "APPLIED_VERIFIED" for s in states):
        _advance(w, "MONITOR")
    w.outcome = {**(w.outcome or {}), "execution": results}
    _log(w, user.username, "EXECUTE", "; ".join(f"{r['plan_id'][:8]} {r['outcome'] or r['decision']}" for r in results),
         results=results)
    return _view(db, w)


@router.post("/workflows/{wid}/monitor")
def wf_monitor(wid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    """Create read-only collection plans (if missing), poll once now and report each device's persisted state."""
    from ..infrastructure.collection_runner import run_due
    from ..infrastructure.device_state import plans_by_device, state_of
    w = _wf(db, wid, user)
    # Configuration refusal (e.g. uncertified HARDWARE, global write lock) must
    # not prevent read-only monitoring. It must also NEVER advance that workflow
    # to DONE or imply configuration was applied. Leave plans independently
    # approved/pending so they may be re-evaluated after certification.
    fully_applied = _plans_fully_applied(db, w)
    attempts = (w.outcome or {}).get("execution") or []
    monitoring_while_locked = (w.mode == "NOW" and w.step in ("APPROVAL", "EXECUTE")
        and bool(attempts) and all(r.get("decision") == "DENY" and
                                   r.get("environment") == "HARDWARE" for r in attempts))
    _require_stage(w, "MONITOR", "APPROVAL" if monitoring_while_locked else "MONITOR",
                   "EXECUTE" if monitoring_while_locked else "MONITOR")
    if w.mode == "NOW" and not fully_applied and not monitoring_while_locked:
        raise HTTPException(409, "Configuration is not fully applied and verified. Resolve pending, failed, "
                                 "rejected or uncertified plans before completing monitoring.")
    if w.mode not in ("NOW", "LATER"):
        raise HTTPException(409, "Choose configure now or configure later first.")
    fams = {"switch": ["system", "interfaces", "poe", "lldp"], "access_point": ["system", "radios", "clients"]}
    plan_ids = []
    existing = plans_by_device(db)
    for did in w.device_ids or []:
        d = real_device(db, did)
        p = existing.get(did)
        if p is None:
            from ..database.models_onboarding import OnboardingDeviceCredential
            from ..domain.adapters import resolve
            binding = db.get(OnboardingDeviceCredential, d.id)
            p = CollectionPlan(device_id=d.id, site_id=d.site_id, adapter_key=resolve(d.vendor, d.family, d.model).key,
                               credential_profile_id=binding.credential_id if binding else None,
                               metric_families=fams[d.family], interval_seconds=300, max_concurrency=1,
                               state="ACTIVE", next_due_at=None, consecutive_failures=0, created_by=user.username)
            db.add(p)
            db.flush()
        plan_ids.append(p.id)
    summary = run_due(db, worker=f"workflow-{w.id[:8]}", plan_ids=plan_ids, manual=True)
    cplans = plans_by_device(db)
    states = {did: state_of(db.get(InmDevice, did), cplans.get(did))["state"] for did in w.device_ids or []}
    w.outcome = {**(w.outcome or {}), "monitoring": {"poll": summary, "states": states,
                      "configuration_pending": monitoring_while_locked}}
    if not monitoring_while_locked:
        _advance(w, "MONITOR")
    if states and all(s in ("ONLINE", "DEGRADED") for s in states.values()):
        if w.mode == "NOW" and fully_applied:
            _advance(w, "DONE")
            w.state = "DONE"
        else:
            w.state = "WAITING"  # monitoring only; locked/uncertified plans remain pending

    names = ", ".join(f"{(lambda d: (d.hostname or d.serial_number) if d else did[:8])(db.get(InmDevice, did))} {st}"
                      for did, st in states.items())
    waiting = summary.get("awaiting_persistence", 0)
    _log(w, user.username, "MONITOR", f"Monitoring active for {len(plan_ids)} device(s); first poll: "
         f"{summary.get('succeeded', 0)} persisted, {waiting} awaiting persistence. Now: {names}")
    audit(db, "workflow_monitoring_started", username=user.username, target=w.id,
          detail=f"plans={len(plan_ids)} succeeded={summary['succeeded']}", request=request)
    return _view(db, w)


@router.post("/workflows/{wid}/resume")
def wf_resume(wid: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    """Re-open a configure-LATER workflow for review, planning, approval and verified execution.

    A deferred workflow keeps its earlier inventory, vault, history and monitoring plans. Resuming does not
    silently execute anything; the operator must preview again and submit a fresh independently approved plan.
    """
    w = _wf(db, wid, user)
    if w.mode != "LATER" or w.step != "MONITOR" or w.state not in ("ACTIVE", "WAITING"):
        raise HTTPException(409, "Only a deferred monitoring workflow can resume configuration.")
    if w.plan_ids:
        raise HTTPException(409, "Resolve existing change plans before resuming this workflow.")
    w.mode, w.step, w.state = None, "PREVIEW", "ACTIVE"
    _log(w, user.username, "PREVIEW", "Configuration resumed; preview and fresh approval required")
    return _view(db, w)


@router.post("/workflows/{wid}/cancel")
def wf_cancel(wid: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    w = _wf(db, wid, user)
    if any((p := db.get(ChangePlan, pid)) is not None and p.state == "EXECUTING" for pid in w.plan_ids or []):
        raise HTTPException(409, "A configuration change is executing. Stop/recover it before cancellation.")
    revoked = 0
    for pid in w.plan_ids or []:
        # Conditional update avoids revoking an EXECUTING plan that started after the check above.
        result = db.execute(update(ChangePlan).where(ChangePlan.id == pid,
            ChangePlan.state.in_(("PENDING_APPROVAL", "APPROVED", "DRAFT")))
            .values(state="REJECTED", approver=None, approved_at=None)
            .execution_options(synchronize_session="fetch"))
        revoked += result.rowcount
        current = db.get(ChangePlan, pid)
        if current is not None and current.state == "EXECUTING":
            raise HTTPException(409, "A configuration change began executing during cancellation; retry after recovery.")
    w.state = "CANCELLED"
    _log(w, user.username, w.step, f"Workflow cancelled; revoked {revoked} unexecuted change plans")
    return _view(db, w)
