"""Phase 3: process-driven global -> site -> device templates, application-side only."""
from __future__ import annotations

from ..infrastructure.posture import read_only, write_posture

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import InmDevice, InmSite
from ..database.models_templates import InmConfigTemplate
from ..database.models_onboarding import OnboardingConfigSnapshot, OnboardingConfigDraft
from ..database.session import get_db
from ..domain.template_inheritance import preview
from ..security.deps import require_role, audit
from .onboarding_workflow import _safe_draft

router = APIRouter(prefix="/api/v1/config-templates", tags=["phase3-inheritance"])


class TemplateInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    family: Literal["access_point", "switch"]
    scope: Literal["global", "group", "site", "device"]
    site_id: str | None = None
    device_id: str | None = None
    group_id: str | None = None
    model_contains: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=100, ge=0, le=1000)
    config: dict
    enabled: bool = True

    @model_validator(mode="after")
    def validate_scope(self):
        targets = {k for k in ("site_id", "device_id", "group_id") if getattr(self, k)}
        need = {"global": set(), "group": {"group_id"}, "site": {"site_id"}, "device": {"device_id"}}[self.scope]
        if targets != need:
            raise ValueError("Global: no target; group: only group_id; site: only site_id; device: only device_id.")
        if any(ord(ch) < 32 for ch in self.name):
            raise ValueError("Template name must be printable.")
        return self


class TemplatePatch(BaseModel):
    expected_revision: int = Field(ge=1)
    config: dict | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None


def _locked():
    """Read-only/draft module: no device-write path, so it stays usable whether or not the
    operator has enabled manual config changes (device_writes_permitted)."""
    return None


def _validate(doc: dict, family: str) -> dict:
    from ..domain import desired_state as DS
    if len(__import__("json").dumps(doc)) > 60000:
        raise HTTPException(422, "Template exceeds 60 KB.")
    try:
        return DS.validate_doc(doc, family)
    except DS.TemplateError as exc:
        raise HTTPException(422, str(exc)) from None


def _groups_of(db: Session, device_id: str) -> frozenset:
    from ..database.models_remediation import DeviceGroupMember
    return frozenset(m.group_id for m in db.scalars(select(DeviceGroupMember).where(
        DeviceGroupMember.device_id == device_id)))


def _applicable(db: Session, device: InmDevice) -> list:
    from ..domain.template_inheritance import matches
    groups = _groups_of(db, device.id)
    return [t for t in db.scalars(select(InmConfigTemplate).where(
        InmConfigTemplate.enabled.is_(True), InmConfigTemplate.family == device.family).limit(500))
        if matches(t, device, groups)]


def _installed_facts(device: InmDevice, text: str) -> dict:
    """Structured facts from the captured configuration: CX JSON (vault capture) or CLI text (pasted)."""
    from ..domain import best_practices as BP
    from ..domain import desired_state as DS
    if device.family == "access_point":
        return DS.instant_facts(text)
    try:
        cfg = __import__("json").loads(text)
        if isinstance(cfg, dict):
            return DS.cx_facts(cfg)
    except ValueError:
        pass
    old = BP.installed_facts("switch", BP.normalize_config(text))
    ports = {p: {"vlan_mode": "access" if p in old["access_vlans"] else ("native-untagged" if p in old["trunks"] else None),
                 "access_vlan": old["access_vlans"].get(p), "trunk_vlans": old["trunks"].get(p, []),
                 "admin_state": old["admin"].get(p, "up")} for p in old["admin"]}
    return {"vlans": old["vlans"], "ports": ports, "ntp": [], "syslog": [], "dns": [], "dns_domain": None}


def intent_and_ops(db: Session, device: InmDevice) -> dict:
    """Merged intent (global -> group -> site -> device), the latest full-fidelity vault capture, and the typed
    operations needed to make the device match. Shared by preview, deploy-plan, workflows and batch deploys."""
    from ..database.models_phases import ConfigArtifact
    from ..domain import desired_state as DS
    from ..domain import vault as V
    from ..infrastructure.phase_services import LocalObjectStore, vault_master_key
    chosen = _applicable(db, device)
    eff = DS.merge(chosen)
    art = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id,
                                                 ConfigArtifact.fidelity == V.FIDELITY_FULL)
                    .order_by(ConfigArtifact.created_at.desc()).limit(1))
    out = {"device_id": device.id, "templates": eff["templates"], "effective": {k: v for k, v in eff.items()
           if k not in ("templates", "provenance")}, "provenance": eff["provenance"], "artifact_id": art.id if art else None,
           "operations": None, "reason": None}
    if not chosen:
        out["reason"] = "No template applies to this device."
        return out
    if art is None:
        out["reason"] = "Capture the device's current configuration first (vault)."
        return out
    text = V.open_sealed(LocalObjectStore().get(art.object_path), wrapped_key=art.wrapped_key, manifest=art.manifest,
                         signature=art.signature, master_key=vault_master_key()).decode(errors="replace")
    facts = _installed_facts(device, text)
    try:
        out["operations"] = DS.reconcile(device.family, eff, facts, ports=sorted(facts.get("ports", {})) or None)
    except DS.TemplateError as exc:
        raise HTTPException(422, str(exc)) from None
    out["artifact_sha256"] = art.plaintext_sha256
    out["port_roles"] = {p: w.get("_role") for p, w in DS.port_intent(eff, sorted(facts.get("ports", {}))).items()
                         if w.get("_role")} if device.family == "switch" else {}
    return out


def _public(t):
    return dict(id=t.id, name=t.name, family=t.family, scope=t.scope,
                site_id=t.site_id, device_id=t.device_id, group_id=t.group_id,
                model_contains=t.model_contains, priority=t.priority,
                config=t.config_json, enabled=t.enabled, revision=t.revision)


def _real(db: Session, device_id: str) -> InmDevice:
    device = db.get(InmDevice, device_id)
    # REAL devices, or DEMO devices from the loaded demo dataset (always labelled, never contacted).
    if not device or device.source_type not in ("REAL", "DEMO") or (device.vendor or "").casefold() != "aruba":
        raise HTTPException(404, "Registered Aruba device not found.")
    return device


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    _locked()
    return [_public(t) for t in db.scalars(select(InmConfigTemplate).order_by(
        InmConfigTemplate.scope, InmConfigTemplate.priority, InmConfigTemplate.name).limit(500))]


@router.post("", status_code=201)
def create_template(body: TemplateInput, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    _locked()
    doc = _validate(body.config, body.family)
    if db.scalar(select(InmConfigTemplate).where(
        InmConfigTemplate.scope == body.scope,
        func.lower(InmConfigTemplate.name) == body.name.strip().lower())):
        raise HTTPException(409, "A template with this scope and name exists.")
    if body.scope == "site":
        site = db.get(InmSite, body.site_id)
        if not site or site.source_type != "REAL":
            raise HTTPException(422, "Select an existing real physical site.")
    if body.scope == "device" and _real(db, body.device_id or "").family != body.family:
        raise HTTPException(422, "Selected device does not match template family.")
    if body.scope == "group":
        from ..database.models_remediation import DeviceGroup
        g = db.get(DeviceGroup, body.group_id)
        if g is None or g.family != body.family:
            raise HTTPException(422, "Select an existing device group of the same family.")
    t = InmConfigTemplate(
        name=body.name.strip(), family=body.family, scope=body.scope,
        site_id=body.site_id, device_id=body.device_id, group_id=body.group_id,
        model_contains=body.model_contains.strip() if body.model_contains else None,
        priority=body.priority, config_json=doc, enabled=body.enabled, created_by=user.username)
    db.add(t); db.flush()
    audit(db, "phase3_template_created", username=user.username, target=t.id,
          detail=f"{t.name} {t.scope}/{t.family}; hardware writes LOCKED", request=request)
    db.commit()
    return _public(t)


@router.patch("/{template_id}")
def revise_template(template_id: str, body: TemplatePatch, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    _locked()
    t = db.get(InmConfigTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found.")
    if t.revision != body.expected_revision:
        raise HTTPException(409, "Template changed since you opened it; reload.")
    if body.config is not None:
        t.config_json = _validate(body.config, t.family)
    if body.priority is not None:
        t.priority = body.priority
    if body.enabled is not None:
        t.enabled = body.enabled
    t.revision += 1
    t.updated_at = datetime.now(timezone.utc)
    audit(db, "phase3_template_revised", username=user.username, target=t.id,
          detail=f"{t.name} rev={t.revision}; device writes LOCKED", request=request)
    db.commit()
    return _public(t)


@router.get("/devices/{device_id}/preview")
def preview_device(device_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    _locked()
    device = _real(db, device_id)
    entries = list(db.scalars(select(InmConfigTemplate).where(
        InmConfigTemplate.enabled.is_(True),
        InmConfigTemplate.family == device.family).limit(500)))
    return preview(device, entries, _groups_of(db, device.id))


@router.get("/devices/{device_id}/plan-preview")
def plan_preview(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    """Effective intent, per-port roles and the exact operations a deploy plan would contain (no plan created)."""
    from ..infrastructure.change_executor import certification_map
    device = _real(db, device_id)
    out = intent_and_ops(db, device)
    certs = certification_map(db, device)
    out["certification"] = {o["op"]: certs.get(o["op"], "UNCERTIFIED") for o in out["operations"] or []}
    return out


@router.post("/devices/{device_id}/draft", status_code=201)
def save_inherited_draft(device_id: str, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_role("admin"))):
    _locked()
    device = _real(db, device_id)
    baseline = db.scalar(select(OnboardingConfigSnapshot).where(
        OnboardingConfigSnapshot.device_id == device.id
    ).order_by(OnboardingConfigSnapshot.captured_at.desc()))
    if not baseline:
        raise HTTPException(409, "Capture the existing read-only device baseline first.")
    entries = list(db.scalars(select(InmConfigTemplate).where(
        InmConfigTemplate.enabled.is_(True),
        InmConfigTemplate.family == device.family).limit(500)))
    current = preview(device, entries, _groups_of(db, device.id))
    if not current["templates"]:
        raise HTTPException(409, "No applicable global, site or device templates.")
    draft = OnboardingConfigDraft(
        device_id=device.id, baseline_id=baseline.id, state="DRAFT",
        proposed_json=_validate(current["effective"], device.family),
        created_by=user.username)
    db.add(draft); db.flush()
    audit(db, "phase3_inherited_draft_saved", username=user.username, target=device.id,
          detail=f"draft={draft.id}, sources={len(current['templates'])}; NO HARDWARE WRITE",
          request=request)
    db.commit()
    return {"draft_id": draft.id, "state": "DRAFT", "baseline_id": baseline.id,
            "template_count": len(current["templates"]), "hardware_writes": write_posture()["hardware_writes"]}


@router.post("/devices/{device_id}/deploy-plan", status_code=201)
def deploy_plan(device_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("operator")), workflow_id: str | None = None,
                batch_id: str | None = None):
    """Turn the inherited intended configuration into an approvable CHANGE PLAN containing only the
    operations needed to make the installed configuration match (C04/C05/C08)."""
    from ..database.models_phases import ChangePlan
    from ..domain import change_control as CC
    from ..infrastructure.change_executor import certification_map
    from ._phase_common import real_device
    device = real_device(db, device_id)
    out = intent_and_ops(db, device)
    if out["operations"] is None:
        raise HTTPException(409, out["reason"])
    ops = out["operations"]
    if not ops:
        return {"in_compliance": True, "plan_id": None, "templates": out["templates"], "operations": []}
    try:
        ops = [CC.validate_operation(o, family=device.family) for o in ops]
    except CC.PolicyViolation as exc:
        raise HTTPException(422, f"Template produces an invalid operation: {exc}") from None
    certs = certification_map(db, device)
    title = f"Apply templates: {', '.join(t['name'] for t in out['templates'])}"
    p = ChangePlan(device_id=device.id, title=title[:200], operations=ops,
                   capability_report=[{"op": o["op"], "capability": certs.get(o["op"], "UNCERTIFIED")} for o in ops],
                   plan_hash=CC.plan_hash(device.id, ops, out["artifact_sha256"]),
                   expected_config_hash=out["artifact_sha256"], firmware_at_request=device.firmware,
                   rollback_plan={"strategy": "DEVICE_CHECKPOINT" if device.family == "switch" else "INVERSE_OPERATIONS",
                                  "workflow_id": workflow_id, "batch_id": batch_id},
                   state="PENDING_APPROVAL", requester=user.username, source="TEMPLATE")
    db.add(p)
    db.flush()
    audit(db, "template_deploy_plan_created", username=user.username, target=device.id,
          detail=f"plan={p.id} ops={len(ops)} templates={[t['name'] for t in out['templates']]}"
                 + (f" workflow={workflow_id}" if workflow_id else "") + (f" batch={batch_id}" if batch_id else ""),
          request=request)
    return {"in_compliance": False, "plan_id": p.id, "operations": ops, "templates": out["templates"]}
