"""Internal printer-supply purchase requests (rc17). No external procurement integration: CSV export and the
platform notification channels are the hand-off. Approve/reject needs approver or admin, never the requester."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_printer import PrinterDevice
from ..database.models_printer_srs import PrinterIdentity
from ..database.models_rc17 import PrinterPurchaseRequest
from ..database.session import get_db
from ..printers import purchasing as P
from ..security.context import get_scope
from ..security.deps import require_any_role, require_role

router = APIRouter(prefix="/api/printer-purchasing", tags=["printer-purchasing"])


def _err(e: P.PurchaseError):
    raise HTTPException(e.status, str(e))


def _in_scope(db: Session, printer_id: str) -> bool:
    scope = get_scope()
    if scope is None:
        return True
    ident = db.get(PrinterIdentity, printer_id)
    return bool(ident and ident.site_id in scope)


class PolicyInput(BaseModel):
    automatic: bool
    lead_time_days: int = Field(ge=1, le=180)
    default_quantity: int = Field(ge=1, le=50)


class RequestInput(BaseModel):
    printer_id: str = Field(max_length=36)
    supply_index: str | None = Field(default=None, max_length=32)
    supply_description: str = Field(min_length=2, max_length=255)
    quantity: int = Field(ge=1, le=50)
    reason: str = Field(min_length=3, max_length=255)
    part_number: str | None = Field(default=None, max_length=100)


class ActionInput(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    order_reference: str | None = Field(default=None, max_length=100)


@router.get("/policy")
def get_policy(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    return P.policy(db)


@router.put("/policy")
def put_policy(body: PolicyInput, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    try:
        v = P.set_policy(db, body.model_dump(), user.username, request)
    except P.PurchaseError as e:
        _err(e)
    db.commit()
    return v


def _rows(db: Session, status: str | None) -> list[dict]:
    q = select(PrinterPurchaseRequest).order_by(PrinterPurchaseRequest.created_at.desc()).limit(2000)
    if status:
        q = q.where(PrinterPurchaseRequest.status == status)
    rows = [r for r in db.scalars(q) if _in_scope(db, r.printer_id)]
    printers = {p.id: p for p in db.scalars(select(PrinterDevice).where(PrinterDevice.id.in_({r.printer_id for r in rows})))}
    return [P.public(r, printers) for r in rows]


@router.get("/requests")
def list_requests(status: str | None = Query(default=None, max_length=20), db: Session = Depends(get_db),
                  _: User = Depends(require_role("viewer"))):
    return _rows(db, status)


@router.get("/requests.csv")
def export_requests(request: Request, status: str | None = Query(default=None, max_length=20),
                    db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..security.deps import audit
    rows = _rows(db, status)
    audit(db, "printer_purchase_requests_exported", username=user.username, target=status or "all",
          detail=f"{len(rows)} rows", request=request)
    db.commit()
    return Response(P.export_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="printer-purchase-requests.csv"'})


@router.post("/requests", status_code=201)
def create_request(body: RequestInput, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("operator"))):
    if not _in_scope(db, body.printer_id):
        raise HTTPException(404, "Printer not found")
    try:
        r = P.create(db, printer_id=body.printer_id, supply_index=body.supply_index, description=body.supply_description,
                     quantity=body.quantity, reason=body.reason, actor=user.username, part_number=body.part_number,
                     request=request)
    except P.PurchaseError as e:
        _err(e)
    db.commit()
    return P.public(r)


def _act(db: Session, request_id: str, action: str, user: User, body: ActionInput, request: Request) -> dict:
    r = db.get(PrinterPurchaseRequest, request_id)
    if r is None or not _in_scope(db, r.printer_id):
        raise HTTPException(404, "Purchase request not found.")
    try:
        r = P.transition(db, request_id, action, user.username, note=body.note,
                         order_reference=body.order_reference, request=request)
    except P.PurchaseError as e:
        _err(e)
    db.commit()
    return P.public(r)


@router.post("/requests/{request_id}/{action}")
def decide(request_id: str, action: Literal["approve", "reject"], body: ActionInput, request: Request,
           db: Session = Depends(get_db), user: User = Depends(require_any_role("approver", "admin"))):
    return _act(db, request_id, action, user, body, request)


@router.post("/requests/{request_id}/progress/{action}")
def progress(request_id: str, action: Literal["order", "receive", "cancel"], body: ActionInput, request: Request,
             db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return _act(db, request_id, action, user, body, request)


@router.post("/generate")
def generate_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    from ..security.deps import audit
    out = P.generate(db, actor=f"purchasing:{user.username}")
    audit(db, "printer_purchase_generation_run", username=user.username, target="purchasing", detail=str(out),
          request=request)
    db.commit()
    return out
