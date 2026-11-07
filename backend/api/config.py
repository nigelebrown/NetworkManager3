"""Configuration management API: read config, plan/apply/rollback SSID, VLAN and AP-name changes."""
from __future__ import annotations

import difflib

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..aruba.configuration import SsidChange
from ..aruba.errors import ArubaError
from ..config import get_settings
from ..configmgmt import service
from ..database.models import AccessPoint, ConfigBackup, ConfigChange, Site, User
from ..database.session import get_db
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/config", tags=["configuration"])


def _site(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    return site


def _change(db: Session, change_id: int) -> ConfigChange:
    ch = db.get(ConfigChange, change_id)
    if not ch:
        raise HTTPException(404, "Change not found")
    return ch


def _iso(dt):
    return dt.isoformat() if dt else None


def change_out(c: ConfigChange) -> dict:
    return {"id": c.id, "site_id": c.site_id, "kind": c.kind, "summary": c.summary, "status": c.status,
            "plan": c.plan, "created_by": c.created_by, "created_at": _iso(c.created_at),
            "expires_at": _iso(c.expires_at), "applied_at": _iso(c.applied_at), "result": c.result,
            "rollback_of": c.rollback_of}


def _backup_meta(b: ConfigBackup) -> dict:
    return {"id": b.id, "site_id": b.site_id, "kind": b.kind, "taken_at": _iso(b.taken_at),
            "taken_by": b.taken_by, "sha256": b.sha256[:12], "simulated": b.simulated,
            "ssid_count": len((b.parsed or {}).get("ssids", []))}


def _guard(fn):
    """Map service errors to HTTP errors."""
    try:
        return fn()
    except service.PlanError as exc:
        raise HTTPException(422, str(exc)) from None
    except ArubaError as exc:
        raise HTTPException(502, str(exc)) from None


# ---- read-only ---------------------------------------------------------------------------
@router.get("/sites/{site_id}/ssids")
def site_ssids(site_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("operator"))):
    site = _site(db, site_id)
    b = service.latest_backup(db, site.id)
    if not b:
        return {"backup_id": None, "ssids": [], "vlans": [], "taken_at": None, "simulated": site.simulated}
    return {"backup_id": b.id, "taken_at": _iso(b.taken_at), "simulated": b.simulated,
            "ssids": (b.parsed or {}).get("ssids", []), "vlans": (b.parsed or {}).get("vlans", [])}


@router.post("/sites/{site_id}/pull")
def pull(site_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    """READ-ONLY: pull the site's current configuration from its Virtual Controller."""
    site = _site(db, site_id)
    try:
        b = service.pull_config(db, site, user.username)
    except ArubaError as exc:
        audit(db, "config_pull", username=user.username, target=f"site:{site.name}", detail=str(exc)[:200],
              request=request, success=False)
        db.commit()
        raise HTTPException(502, str(exc)) from None
    audit(db, "config_pull", username=user.username, target=f"site:{site.name}", request=request)
    return {"backup_id": b.id, "taken_at": _iso(b.taken_at), "simulated": b.simulated,
            "ssids": (b.parsed or {}).get("ssids", []), "vlans": (b.parsed or {}).get("vlans", [])}


@router.get("/sites/{site_id}/backups")
def backups(site_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    _site(db, site_id)
    rows = db.scalars(select(ConfigBackup).where(ConfigBackup.site_id == site_id)
                      .order_by(ConfigBackup.id.desc()).limit(50)).all()
    return [_backup_meta(b) for b in rows]


@router.get("/backups/diff")
def diff(a: int, b: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    ba, bb = db.get(ConfigBackup, a), db.get(ConfigBackup, b)
    if not ba or not bb or ba.site_id != bb.site_id:
        raise HTTPException(404, "Backups not found (or from different sites)")
    lines = list(difflib.unified_diff(ba.text.splitlines(), bb.text.splitlines(),
                                      f"backup #{ba.id}", f"backup #{bb.id}", lineterm=""))
    return {"identical": not lines, "diff": "\n".join(lines)}


@router.get("/backups/{backup_id}")
def backup_text(backup_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    b = db.get(ConfigBackup, backup_id)
    if not b:
        raise HTTPException(404, "Backup not found")
    return {**_backup_meta(b), "text": b.text}


@router.get("/changes")
def changes(site_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    q = select(ConfigChange).order_by(ConfigChange.id.desc()).limit(50)
    if site_id:
        q = q.where(ConfigChange.site_id == site_id)
    return [change_out(c) for c in db.scalars(q).all()]


# ---- planning / applying (admin; disabled unless SIMS_ALLOW_CONFIG_CHANGES=true) -----------
class RenameBody(BaseModel):
    ap_id: int
    new_name: str = Field(min_length=1, max_length=32)


class ApplyBody(BaseModel):
    confirm_text: str = Field(max_length=200)


@router.post("/sites/{site_id}/ssid/plan", status_code=201)
def plan_ssid(site_id: int, body: SsidChange, request: Request, db: Session = Depends(get_db),
              admin: User = Depends(require_role("admin"))):
    site = _site(db, site_id)
    change = _guard(lambda: service.plan_ssid(db, site, admin.username, body, request=request))
    return change_out(change)


@router.post("/sites/{site_id}/ap-rename/plan", status_code=201)
def plan_rename(site_id: int, body: RenameBody, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_role("admin"))):
    site = _site(db, site_id)
    ap = db.get(AccessPoint, body.ap_id)
    if not ap:
        raise HTTPException(404, "Access point not found")
    change = _guard(lambda: service.plan_rename(db, site, admin.username, ap, body.new_name, request=request))
    return change_out(change)


@router.post("/changes/{change_id}/apply")
def apply(change_id: int, body: ApplyBody, request: Request, db: Session = Depends(get_db),
          admin: User = Depends(require_role("admin"))):
    change = _change(db, change_id)
    result = _guard(lambda: service.apply_change(db, change, admin.username, body.confirm_text, request=request))
    return {**result, "change": change_out(change)}


@router.post("/changes/{change_id}/rollback-plan", status_code=201)
def rollback_plan(change_id: int, request: Request, db: Session = Depends(get_db),
                  admin: User = Depends(require_role("admin"))):
    change = _change(db, change_id)
    return change_out(_guard(lambda: service.plan_rollback(db, change, admin.username, request=request)))


@router.post("/changes/{change_id}/cancel")
def cancel(change_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    change = _change(db, change_id)
    if change.status != "planned":
        raise HTTPException(409, f"Change is already {change.status}")
    change.status, change.result = "cancelled", "Cancelled by administrator."
    if change.kind == "cert_upload":
        from ..certs.service import _wipe
        _wipe(change)  # private key + passphrase leave the database immediately
    audit(db, "config_cancel", username=admin.username, target=f"change:{change.id}", request=request)
    return change_out(change)
