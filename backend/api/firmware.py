"""Firmware compliance, firmware images, certificate upload plans, and the VC's download link."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..certs import service as cert_service
from ..configmgmt import service as cfg
from ..database.models import ConfigChange, FirmwareImage, Site, User
from ..database.session import get_db
from ..firmware import compliance, images, upgrade
from ..monitoring.collector import visible_sites
from ..security.deps import audit, require_role
from .config import change_out

router = APIRouter(prefix="/api/firmware", tags=["firmware"])
download_router = APIRouter(tags=["firmware-download"])


def _site(db: Session, site_id: int) -> Site:
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(404, "Site not found")
    return site


def _plan_guard(fn):
    try:
        return fn()
    except (cfg.PlanError, images.ImageError) as exc:
        raise HTTPException(422, str(exc)) from None


def image_out(i: FirmwareImage) -> dict:
    return {"id": i.id, "filename": i.filename, "ap_class": i.ap_class, "version": i.version, "size_bytes": i.size_bytes,
            "sha256": i.sha256, "uploaded_by": i.uploaded_by, "uploaded_at": i.uploaded_at.isoformat()}


# ---- compliance ---------------------------------------------------------------------------------------------------
@router.get("/compliance")
def get_compliance(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    policy = compliance.load_policy(db)
    return {"policy": policy.model_dump(), "sites": [compliance.site_report(db, s, policy) for s in visible_sites(db)]}


@router.post("/policy")
def set_policy(body: compliance.FirmwarePolicy, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_role("admin"))):
    for sid in body.sites:
        if not db.get(Site, int(sid)):
            raise HTTPException(422, f"Unknown site id {sid}")
    compliance.save_policy(db, body, admin.username)
    audit(db, "firmware_policy", username=admin.username, target="firmware",
          detail=f"target={body.target} mode={body.mode} overrides={len(body.sites)}", request=request)
    return {"policy": body.model_dump()}


# ---- images -------------------------------------------------------------------------------------------------------
@router.get("/images")
def list_images(db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return [image_out(i) for i in db.scalars(select(FirmwareImage).order_by(FirmwareImage.uploaded_at.desc()))]


@router.post("/images", status_code=201)
def upload_image(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                 admin: User = Depends(require_role("admin"))):
    try:
        img = images.store_image(db, file.filename or "", file.file, admin.username)
    except images.ImageError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, "firmware_upload", username=admin.username, target=f"image:{img.filename}",
          detail=f"{img.size_bytes} bytes sha256 {img.sha256[:12]}", request=request)
    return image_out(img)


@router.delete("/images/{image_id}")
def delete_image(image_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    img = db.get(FirmwareImage, image_id)
    if not img:
        raise HTTPException(404, "Image not found")
    name = img.filename
    images.delete_image(db, img)
    audit(db, "firmware_delete", username=admin.username, target=f"image:{name}", request=request)
    return {"deleted": name}


class UpgradeBody(BaseModel):
    image_ids: list[int] = Field(min_length=1, max_length=9)
    allow_downgrade: bool = False


@router.post("/sites/{site_id}/upgrade/plan", status_code=201)
def plan_upgrade(site_id: int, body: UpgradeBody, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_role("admin"))):
    site = _site(db, site_id)
    return change_out(_plan_guard(lambda: upgrade.plan_upgrade(db, site, admin.username, body.image_ids,
                                                               allow_downgrade=body.allow_downgrade, request=request)))


# ---- certificate --------------------------------------------------------------------------------------------------
@router.post("/sites/{site_id}/certificate/plan", status_code=201)
def plan_certificate(site_id: int, request: Request, file: UploadFile = File(...), passphrase: str = Form(...),
                     db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    site = _site(db, site_id)
    pem = file.file.read(65 * 1024)
    return change_out(_plan_guard(lambda: cert_service.plan_cert(db, site, admin.username, pem, passphrase, request=request)))


# ---- the Virtual Controller's download (no login: the link itself is the credential) ------------------------------
@download_router.get("/fw/{change_id}/{token}/{filename}", include_in_schema=False)
def download(change_id: int, token: str, filename: str, request: Request, db: Session = Depends(get_db)):
    ch = db.get(ConfigChange, change_id)
    ok = (images.link_valid(token, change_id, filename) and ch is not None and ch.kind == "firmware_stage"
          and ch.status in ("applied", "applied_unverified") and images.link_window_open(ch.applied_at)
          and filename in {i.get("filename") for i in (ch.plan or {}).get("images", [])})
    path = images.image_path(filename) if ok else None
    if path is None:
        raise HTTPException(404, "Not found")  # same answer for every failure: reveals nothing
    audit(db, "firmware_download", username=None, target=f"image:{filename}",
          detail=f"change #{change_id} from {request.client.host if request.client else '?'}", request=request)
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
