"""AOS-CX switch firmware API (rc17). See backend/firmware/cx_firmware.py for the lifecycle and safety rules."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.session import get_db
from ..firmware import cx_firmware as F
from ..security.deps import require_any_role, require_role
from ._phase_common import real_device

router = APIRouter(prefix="/api/v1/switch-firmware", tags=["cx-firmware"])


def _run(db: Session, fn, *a, **kw):
    try:
        out = fn(db, *a, **kw)
    except F.FirmwareError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    db.commit()
    return out


@router.get("/images")
def list_images(db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return F.images(db)


@router.post("/images", status_code=201)
def upload(request: Request, file: UploadFile = File(...), vendor_sha256: str = Form(...), db: Session = Depends(get_db),
           user: User = Depends(require_role("admin"))):
    return _run(db, F.store_image, file.filename or "", file.file, vendor_sha256, user.username, request=request)


class PlanInput(BaseModel):
    device_id: str
    image_id: str
    allow_downgrade: bool = False


@router.get("/plans")
def list_plans(device_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return F.plans(db, device_id)


@router.post("/plans", status_code=201)
def create_plan(body: PlanInput, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    return _run(db, F.plan, real_device(db, body.device_id), body.image_id, user.username,
                allow_downgrade=body.allow_downgrade, request=request)


@router.post("/plans/{plan_id}/approve")
def approve(plan_id: str, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_any_role("approver", "admin"))):
    return _run(db, F.approve, plan_id, user.username, request=request)


@router.post("/plans/{plan_id}/stage")
def stage(plan_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    return _run(db, F.stage, plan_id, user.username, request=request)


class BootInput(BaseModel):
    acknowledge_reboot: bool = False


@router.post("/plans/{plan_id}/boot")
def boot(plan_id: str, body: BootInput, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_role("admin"))):
    return _run(db, F.boot, plan_id, user.username, acknowledge_reboot=body.acknowledge_reboot, request=request)


@router.post("/plans/{plan_id}/recover")
def recover(plan_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    return _run(db, F.recover, plan_id, user.username, request=request)
