from __future__ import annotations

import ipaddress
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..aruba import onboarding, ssh
from ..aruba.authentication import test_connection
from ..aruba.errors import ArubaError
from ..config import get_settings
from ..database.models import Alert, Site, User
from ..database.session import get_db
from ..monitoring.collector import poll_site, visible_sites
from ..security.crypto import encrypt_secret
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/sites", tags=["sites"])

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$")


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    group: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    vc_host: str = Field(min_length=1, max_length=255)
    vc_port: int = Field(default=4343, ge=1, le=65535)
    api_username: str = Field(min_length=1, max_length=100)
    api_password: str = Field(min_length=1, max_length=256)

    @field_validator("vc_host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            if _HOSTNAME.match(v):
                return v
            raise ValueError("vc_host must be an IP address or a valid hostname")


def site_out(s: Site) -> dict:
    return {
        "id": s.id, "name": s.name, "group": s.group, "description": s.description,
        "vc_host": s.vc_host, "vc_port": s.vc_port, "simulated": s.simulated, "enabled": s.enabled,
        "last_poll_at": s.last_poll_at.isoformat() if s.last_poll_at else None,
        "last_poll_ok": s.last_poll_ok, "last_poll_error": s.last_poll_error,
        "ap_count": len(s.aps),
    }


@router.get("")
def list_sites(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    return [site_out(s) for s in visible_sites(db)]


@router.post("", status_code=201)
def create_site(body: SiteCreate, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_role("admin"))):
    if get_settings().mode == "demo":
        raise HTTPException(400, "Switch to live mode (SIMS_MODE=live) to register real sites.")
    if db.scalar(select(Site).where(Site.name == body.name)):
        raise HTTPException(409, "A site with that name already exists.")
    site = Site(
        name=body.name, group=body.group, description=body.description,
        vc_host=body.vc_host, vc_port=body.vc_port, api_username=body.api_username,
        api_password_enc=encrypt_secret(body.api_password), simulated=False,
    )
    db.add(site)
    db.flush()
    audit(db, "site_create", username=admin.username, target=f"site:{site.name}",
          detail=f"host={body.vc_host}:{body.vc_port}", request=request)
    return site_out(site)


@router.delete("/{site_id}")
def delete_site(site_id: int, request: Request, db: Session = Depends(get_db),
                admin: User = Depends(require_role("admin"))):
    site = db.get(Site, site_id)
    if not site or site.simulated:
        raise HTTPException(404, "Site not found")
    for a in db.scalars(select(Alert).where(Alert.site_id == site.id)).all():
        db.delete(a)
    audit(db, "site_delete", username=admin.username, target=f"site:{site.name}", request=request)
    db.delete(site)
    return {"deleted": True}


@router.post("/{site_id}/test")
def test_site(site_id: int, request: Request, db: Session = Depends(get_db),
              admin: User = Depends(require_role("admin"))):
    """Read-only connectivity check: login + show aps/clients/version."""
    site = db.get(Site, site_id)
    if not site or site.simulated:
        raise HTTPException(404, "Site not found")
    try:
        result = test_connection(site)
        audit(db, "site_test", username=admin.username, target=f"site:{site.name}", request=request)
        return result
    except ArubaError as exc:
        audit(db, "site_test", username=admin.username, target=f"site:{site.name}",
              detail=str(exc)[:200], request=request, success=False)
        return {"ok": False, "error": str(exc)}


@router.post("/{site_id}/poll")
def poll_now(site_id: int, db: Session = Depends(get_db),
             _: User = Depends(require_role("operator"))):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    ok = poll_site(db, site)
    return {"ok": ok, "error": site.last_poll_error}


# ---- REST API onboarding (the REST API is off by default on Instant) ------------------------------
class RestEnableBody(BaseModel):
    host_key_sha256: str = Field(min_length=10, max_length=100)
    confirm_text: str = Field(max_length=200)


def _live_site(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    if site.simulated:
        raise HTTPException(422, "Simulated sites have no controller.")
    return site


@router.post("/{site_id}/rest-api/check")
def rest_api_check(site_id: int, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_role("admin"))):
    """Is the REST API on? If not, also read the AP's SSH host key (no credentials sent) for you to confirm."""
    site = _live_site(db, site_id)
    state = onboarding.check_rest_api(site)
    out = {**state, "writes_enabled": get_settings().device_writes_permitted,
           "confirm_phrase": onboarding.confirm_phrase(site), "rollback": ssh.ROLLBACK_TEXT,
           "commands": list(ssh.ENABLE_REST_COMMANDS)}
    if state["rest_enabled"] is not True:
        out["ssh"] = onboarding.scan_ssh(site)
    audit(db, "rest_api_check", username=admin.username, target=f"site:{site.name}",
          detail=f"rest_enabled={state['rest_enabled']}", request=request)
    return out


@router.post("/{site_id}/rest-api/enable")
def rest_api_enable(site_id: int, body: RestEnableBody, request: Request, db: Session = Depends(get_db),
                    admin: User = Depends(require_role("admin"))):
    site = _live_site(db, site_id)
    if not get_settings().device_writes_permitted:
        raise HTTPException(422, "Configuration changes are disabled. Set SIMS_ALLOW_CONFIG_CHANGES=true in .env and restart.")
    if body.confirm_text.strip() != onboarding.confirm_phrase(site):
        raise HTTPException(422, f"Type exactly: {onboarding.confirm_phrase(site)}")
    audit(db, "rest_api_enable", username=admin.username, target=f"site:{site.name}",
          detail=f"requested; host key {body.host_key_sha256}", request=request)
    db.commit()  # keep the audit trail even if the device interaction fails
    result = onboarding.enable_rest_api(site, body.host_key_sha256.strip())
    audit(db, "rest_api_enable", username=admin.username, target=f"site:{site.name}",
          detail=("succeeded" if result["ok"] else f"FAILED: {result['message']}")[:240], request=request, success=result["ok"])
    return {**result, "rollback": ssh.ROLLBACK_TEXT}
