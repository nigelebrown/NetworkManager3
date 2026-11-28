"""Emergency (break-glass) privileged access, audited as a distinct workflow (rc18; register P4-09).

When no second approver is reachable during an outage, an administrator can open a time-boxed emergency session
(reason and ticket required, at most 4 hours). It waives exactly one control: the two-person rule, and only for
change plans the same administrator explicitly approves "under emergency access". Every other governance step is
unchanged (per-model certification, verified pre-change backup, readback, automatic rollback, write posture,
emergency stop). Opening a session notifies every alert connection at critical severity; every audit row the holder
writes during the window is linked to the session; a DIFFERENT administrator must review the session afterwards,
and an unreviewed session older than 24 hours is reported as overdue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, AuditLog, User
from ..database.session import get_db
from ..security.deps import audit, require_role
from ._phase_common import iso

router = APIRouter(prefix="/api/v1/emergency-access", tags=["emergency-access"])
PREFIX = "emergency-access:"
MAX_MINUTES = 240
REVIEW_DUE = timedelta(hours=24)


class StartIn(BaseModel):
    reason: str = Field(min_length=20, max_length=500)
    ticket: str = Field(min_length=3, max_length=64)
    minutes: int = Field(default=60, ge=5, le=MAX_MINUTES)


class ReviewIn(BaseModel):
    note: str = Field(min_length=10, max_length=1000)


def _utc(t: str | None) -> datetime | None:
    if not t:
        return None
    d = datetime.fromisoformat(t)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    from ..infrastructure.clock import now
    return now()


def _state(v: dict, now: datetime) -> str:
    if v.get("ended_at") or _utc(v["expires_at"]) <= now:
        return "REVIEWED" if v.get("reviewed_by") else (
            "REVIEW_OVERDUE" if now - (_utc(v.get("ended_at")) or _utc(v["expires_at"])) > REVIEW_DUE else "ENDED_PENDING_REVIEW")
    return "ACTIVE"


def active_session(db: Session, username: str) -> dict | None:
    now = _now()
    for row in db.scalars(select(AppSetting).where(AppSetting.key.like(PREFIX + "%"))):
        if row.value.get("holder") == username and _state(row.value, now) == "ACTIVE":
            return row.value | {"id": row.key.removeprefix(PREFIX)}
    return None


def _public(db: Session, row: AppSetting, *, with_actions: bool = False) -> dict:
    v = row.value
    now = _now()
    out = {"id": row.key.removeprefix(PREFIX), "holder": v["holder"], "reason": v["reason"], "ticket": v["ticket"],
           "started_at": v["started_at"], "expires_at": v["expires_at"], "ended_at": v.get("ended_at"),
           "ended_by": v.get("ended_by"), "reviewed_by": v.get("reviewed_by"), "reviewed_at": v.get("reviewed_at"),
           "review_note": v.get("review_note"), "state": _state(v, now)}
    if with_actions:
        end = _utc(v.get("ended_at")) or min(_utc(v["expires_at"]), now)
        rows = db.scalars(select(AuditLog).where(AuditLog.username == v["holder"], AuditLog.ts >= _utc(v["started_at"]),
                                                 AuditLog.ts <= end).order_by(AuditLog.id))
        out["actions"] = [{"at": iso(a.ts), "action": a.action, "target": a.target, "success": a.success,
                           "detail": (a.detail or "")[:200]} for a in rows]
    return out


@router.get("")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    rows = sorted(db.scalars(select(AppSetting).where(AppSetting.key.like(PREFIX + "%"))),
                  key=lambda r: r.value["started_at"], reverse=True)
    sessions = [_public(db, r) for r in rows]
    return {"sessions": sessions, "mine_active": active_session(db, user.username) is not None,
            "overdue_reviews": sum(s["state"] == "REVIEW_OVERDUE" for s in sessions),
            "policy": "Waives only the two-person rule, for plans the holder explicitly approves under emergency "
                      "access; certification, backup, readback, rollback and write posture still apply."}


@router.get("/{sid}")
def get_session(sid: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    row = db.get(AppSetting, PREFIX + sid)
    if row is None:
        raise HTTPException(404, "Emergency session not found.")
    return _public(db, row, with_actions=True)


@router.post("", status_code=201)
def start(body: StartIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    if active_session(db, user.username):
        raise HTTPException(409, "You already hold an active emergency session.")
    now = _now()
    sid = uuid.uuid4().hex
    v = {"holder": user.username, "reason": body.reason.strip(), "ticket": body.ticket.strip(),
         "started_at": now.isoformat(), "expires_at": (now + timedelta(minutes=body.minutes)).isoformat()}
    row = AppSetting(key=PREFIX + sid, value=v, updated_by=user.username)
    db.add(row)
    db.flush()
    audit(db, "emergency_access_started", username=user.username, target=sid,
          detail=f"ticket={v['ticket']} until={v['expires_at']} reason={v['reason'][:200]}", request=request)
    try:
        from ..infrastructure.notifications import enqueue_event
        enqueue_event(db, kind="emergency_access", severity="critical", ref=sid, event="STARTED",
                      message=f"Emergency privileged access opened by {user.username} (ticket {v['ticket']}) "
                              f"until {v['expires_at']}")
    except Exception as exc:  # noqa: BLE001  the session stays audited even if a channel is misconfigured
        audit(db, "emergency_access_notification_failed", username=user.username, target=sid,
              detail=type(exc).__name__, request=request, success=False)
    return _public(db, row)


@router.post("/{sid}/end")
def end(sid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    row = db.get(AppSetting, PREFIX + sid)
    if row is None:
        raise HTTPException(404, "Emergency session not found.")
    if _state(row.value, _now()) != "ACTIVE":
        raise HTTPException(409, "Session is not active.")
    row.value = row.value | {"ended_at": _now().isoformat(), "ended_by": user.username}
    audit(db, "emergency_access_ended", username=user.username, target=sid, detail=f"holder={row.value['holder']}",
          request=request)
    return _public(db, row)


@router.post("/{sid}/review")
def review(sid: str, body: ReviewIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin"))):
    row = db.get(AppSetting, PREFIX + sid)
    if row is None:
        raise HTTPException(404, "Emergency session not found.")
    if row.value["holder"].casefold() == user.username.casefold():
        raise HTTPException(403, "A different administrator must review an emergency session.")
    if _state(row.value, _now()) == "ACTIVE":
        raise HTTPException(409, "End the session before reviewing it.")
    if row.value.get("reviewed_by"):
        raise HTTPException(409, "Session already reviewed.")
    row.value = row.value | {"reviewed_by": user.username, "reviewed_at": _now().isoformat(), "review_note": body.note}
    audit(db, "emergency_access_reviewed", username=user.username, target=sid,
          detail=f"holder={row.value['holder']} note={body.note[:200]}", request=request)
    return _public(db, row, with_actions=True)
