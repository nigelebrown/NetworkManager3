"""Print-job metadata and document inspection API (rc17). Off by default; see backend/printers/print_jobs.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_printer_srs import PrinterIdentity
from ..database.models_rc17 import PrintJob, PrintJobFinding
from ..database.session import get_db
from ..printers import print_jobs as J
from ..security.context import get_scope
from ..security.deps import audit, require_any_role, require_role

router = APIRouter(prefix="/api/print-jobs", tags=["print-jobs"])
restricted = Depends(require_any_role("admin", "auditor"))


def _err(e: J.PrintJobError):
    raise HTTPException(e.status, str(e))


class KeywordRule(BaseModel):
    name: str = Field(max_length=40)
    keywords: list[str] = Field(min_length=1, max_length=50)
    severity: Literal["info", "minor", "major"] = "minor"


class PolicyInput(BaseModel):
    enabled: bool
    inspection_enabled: bool = False
    purpose: str = Field(default="", max_length=1000)
    approval_reference: str = Field(default="", max_length=120)
    retention_days: int = Field(default=90, ge=7, le=730)
    pseudonymise_users: bool = True
    store_document_names: bool = False
    keyword_rules: list[KeywordRule] = Field(default_factory=list, max_length=50)


class JobInput(BaseModel):
    source: Literal["CUPS", "WINDOWS_PRINT_SERVER", "IPP", "OTHER"]
    source_job_id: str = Field(min_length=1, max_length=64)
    printer_ref: str = Field(min_length=1, max_length=120)
    user: str = Field(min_length=1, max_length=128)
    document_name: str | None = Field(default=None, max_length=1024)
    pages: int | None = Field(default=None, ge=0, le=100_000)
    copies: int = Field(default=1, ge=1, le=9999)
    colour: bool | None = None
    duplex: bool | None = None
    submitted_at: datetime | None = None
    completed_at: datetime
    content_text: str | None = Field(default=None, max_length=J.MAX_TEXT + 1)


class IngestInput(BaseModel):
    jobs: list[JobInput] = Field(min_length=1, max_length=500)


class TokenInput(BaseModel):
    name: str = Field(min_length=3, max_length=60)


class ReviewInput(BaseModel):
    state: Literal["DISMISSED", "CONFIRMED"]


@router.get("/policy")
def get_policy(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    p = J.policy(db)
    return p | {"max_inspected_characters": J.MAX_TEXT,
                "built_in_rules": ["PAYMENT_CARD_NUMBER (Luhn-valid, major)", "JM_TAXPAYER_REGISTRATION_NUMBER "
                                   "(after 'TRN', major)", "CLASSIFICATION_MARKING (minor)"],
                "content_storage": "NONE — text is inspected in memory and discarded; SHA-256 and masked tokens kept"}


@router.put("/policy")
def put_policy(body: PolicyInput, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    try:
        v = J.set_policy(db, body.model_dump(), user.username, request)
    except J.PrintJobError as e:
        _err(e)
    db.commit()
    return v


@router.get("/ingest-tokens")
def list_tokens(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return J.tokens(db)


@router.post("/ingest-tokens", status_code=201)
def new_token(body: TokenInput, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_role("admin"))):
    try:
        t = J.create_token(db, body.name, user.username, request)
    except J.PrintJobError as e:
        _err(e)
    db.commit()
    return t | {"note": "Shown once. Configure it on the print server agent as X-INM-Ingest-Token."}


@router.delete("/ingest-tokens/{token_id}", status_code=204)
def revoke(token_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    try:
        J.revoke_token(db, token_id, user.username, request)
    except J.PrintJobError as e:
        _err(e)
    db.commit()


@router.post("/ingest")
def ingest(body: IngestInput, request: Request, db: Session = Depends(get_db),
           x_inm_ingest_token: str | None = Header(default=None)):
    tid = J.authenticate(db, x_inm_ingest_token)
    if tid is None:
        audit(db, "print_jobs_ingest_denied", username="anonymous", target="print_jobs", success=False,
              detail="missing, unknown or revoked ingestion token", request=request)
        db.commit()
        raise HTTPException(401, "A valid ingestion token is required.")
    try:
        out = J.ingest(db, tid, [j.model_dump() for j in body.jobs])
    except J.PrintJobError as e:
        db.commit()            # keep last_used_at
        _err(e)
    db.commit()
    return out


def _scope_ids(db: Session) -> set[str] | None:
    scope = get_scope()
    if scope is None:
        return None
    return {i.printer_id for i in db.scalars(select(PrinterIdentity).where(PrinterIdentity.site_id.in_(scope)))}


@router.get("/summary")
def summary(days: int = Query(default=30, ge=1, le=730), db: Session = Depends(get_db),
            _: User = Depends(require_role("operator"))):
    """Per-printer totals without any user identity or document information."""
    return {"days": days, "printers": J.summary(db, days, _scope_ids(db)), "policy_enabled": J.policy(db)["enabled"]}


@router.get("")
def list_jobs(request: Request, days: int = Query(default=7, ge=1, le=730),
              state: str | None = Query(default=None, max_length=16), printer_id: str | None = Query(default=None, max_length=36),
              user_ref: str | None = Query(default=None, max_length=128), limit: int = Query(default=200, ge=1, le=1000),
              db: Session = Depends(get_db), user: User = restricted):
    q = (select(PrintJob).where(PrintJob.completed_at >= datetime.now(timezone.utc) - timedelta(days=days))
         .order_by(PrintJob.completed_at.desc()).limit(limit))
    if state:
        q = q.where(PrintJob.inspection_state == state)
    if printer_id:
        q = q.where(PrintJob.printer_id == printer_id)
    if user_ref:
        q = q.where(PrintJob.user_ref == user_ref)
    allowed = _scope_ids(db)
    jobs = [j for j in db.scalars(q) if allowed is None or j.printer_id in allowed]
    fs: dict[str, list] = {}
    if jobs:
        for f in db.scalars(select(PrintJobFinding).where(PrintJobFinding.job_id.in_([j.id for j in jobs]))):
            fs.setdefault(f.job_id, []).append(f)
    audit(db, "print_jobs_viewed", username=user.username, target="print_jobs", request=request,
          detail=f"days={days} state={state} printer={printer_id} user_ref={'set' if user_ref else 'none'} rows={len(jobs)}")
    db.commit()
    return [J.job_dict(j, fs.get(j.id)) for j in jobs]


@router.post("/findings/{finding_id}/review")
def review(finding_id: str, body: ReviewInput, request: Request, db: Session = Depends(get_db), user: User = restricted):
    try:
        f = J.review_finding(db, finding_id, body.state, user.username, request)
    except J.PrintJobError as e:
        _err(e)
    db.commit()
    return {"id": f.id, "review_state": f.review_state, "reviewed_by": f.reviewed_by}


@router.post("/purge")
def purge_now(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    out = J.purge(db, actor=user.username)
    db.commit()
    return out
