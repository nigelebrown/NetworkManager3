"""Phase 3: process-driven global -> site -> device templates, application-side only."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import get_settings
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
    scope: Literal["global", "site", "device"]
    site_id: str | None = None
    device_id: str | None = None
    model_contains: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=100, ge=0, le=1000)
    config: dict
    enabled: bool = True

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.scope == "global" and (self.site_id or self.device_id)
            or self.scope == "site" and (not self.site_id or self.device_id)
            or self.scope == "device" and (not self.device_id or self.site_id)):
            raise ValueError("Global: neither target; site: only site_id; device: only device_id.")
        if any(ord(ch) < 32 for ch in self.name):
            raise ValueError("Template name must be printable.")
        return self


class TemplatePatch(BaseModel):
    expected_revision: int = Field(ge=1)
    config: dict | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None


def _locked():
    if not get_settings().device_write_lock:
        raise HTTPException(403, "Hardware-write lock must remain enabled.")


def _validate(doc: dict, family: str) -> dict:
    from ..api.onboarding_workflow import _safe_draft
    if family == "switch" and doc.get("ssids"):
        raise HTTPException(422, "SSID configuration belongs to APs, not switches.")
    if family == "access_point" and doc.get("interfaces"):
        raise HTTPException(422, "Interface configuration belongs to switches, not APs.")
    safe = _safe_draft(doc)
    for key, identity in (("ssids", "profile"), ("interfaces", "name")):
        rows = safe.get(key, [])
        seen = [str(x.get(identity, "")).strip().casefold() for x in rows]
        if any(not x for x in seen) or len(seen) != len(set(seen)):
            raise HTTPException(422, f"{key} require unique, nonempty {identity}.")
    vlans = safe.get("vlans", [])
    if any(type(v) is not int for v in vlans) or len(vlans) != len(set(vlans)):
        raise HTTPException(422, "VLAN IDs must be unique integers.")
    for port in safe.get("interfaces", []):
        if any(type(x) is not int or x < 1 or x > 4094 for x in port.get("trunk_vlans", [])):
            raise HTTPException(422, "Trunk VLANs must be integers from 1 to 4094.")
        if port.get("admin_state") not in (None, "up", "down"):
            raise HTTPException(422, "Interface admin state must be up or down.")
    return safe


def _public(t):
    return dict(id=t.id, name=t.name, family=t.family, scope=t.scope,
                site_id=t.site_id, device_id=t.device_id,
                model_contains=t.model_contains, priority=t.priority,
                config=t.config_json, enabled=t.enabled, revision=t.revision)


def _real(db: Session, device_id: str) -> InmDevice:
    device = db.get(InmDevice, device_id)
    if not device or device.source_type != "REAL" or device.vendor != "Aruba":
        raise HTTPException(404, "Real Aruba device not found.")
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
    t = InmConfigTemplate(
        name=body.name.strip(), family=body.family, scope=body.scope,
        site_id=body.site_id, device_id=body.device_id,
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
    return preview(device, entries)


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
    current = preview(device, entries)
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
            "template_count": len(current["templates"]), "hardware_writes": "LOCKED"}
