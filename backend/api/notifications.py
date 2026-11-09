"""Alert notification channels, delivery history and test sends (E2E-20). Admin manages channels; any reader
can see delivery state. A test send reports the real outcome of the attempt."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_remediation import NotificationChannel, NotificationDelivery
from ..database.models_phases import InmAlert
from ..database.session import get_db
from ..infrastructure import notifications as N
from ..security.deps import audit, require_role
from ._phase_common import iso

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str
    target: str = Field(min_length=1, max_length=300)
    min_severity: str = "major"
    escalate_after_minutes: int | None = Field(default=None, ge=1, le=10080)
    escalation_channel_id: str | None = None
    include_demo: bool = False
    enabled: bool = True


def _ch(c: NotificationChannel) -> dict:
    from urllib.parse import urlsplit
    if c.kind == "webhook":
        u = urlsplit(c.target)
        # Always mask path and query; webhook auth tokens often live in either one.
        display_target = f"{u.scheme}://{u.hostname or '[unconfigured]'}:{u.port}" if u.port else \
                         f"{u.scheme}://{u.hostname or '[unconfigured]'}"
        if u.path not in ("", "/") or u.query:
            display_target += "/[redacted]"
    else:
        display_target = c.target
    return {"id": c.id, "name": c.name, "kind": c.kind, "target": display_target,
            "target_configured": True, "min_severity": c.min_severity,
            "escalate_after_minutes": c.escalate_after_minutes, "escalation_channel_id": c.escalation_channel_id,
            "include_demo": c.include_demo, "enabled": c.enabled, "created_by": c.created_by,
            "created_at": iso(c.created_at)}


def _dl(d: NotificationDelivery) -> dict:
    return {"id": d.id, "channel_id": d.channel_id, "alert_id": d.alert_id, "kind": d.kind, "state": d.state,
            "attempts": d.attempts, "last_error": d.last_error, "correlation_id": d.correlation_id,
            "created_at": iso(d.created_at), "next_attempt_at": iso(d.next_attempt_at), "sent_at": iso(d.sent_at),
            "message": (d.payload or {}).get("message"), "severity": (d.payload or {}).get("severity")}


def _check(db: Session, body: ChannelIn, self_id: str | None = None) -> None:
    try:
        N.validate_channel(body.kind, body.target, body.min_severity)
    except N.ChannelError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.escalate_after_minutes and not body.escalation_channel_id:
        raise HTTPException(422, "escalate_after_minutes needs an escalation_channel_id")
    if body.escalation_channel_id:
        if body.escalation_channel_id == self_id or db.get(NotificationChannel, body.escalation_channel_id) is None:
            raise HTTPException(422, "escalation channel not found (and cannot be the channel itself)")


@router.get("/channels")
def list_channels(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return [_ch(c) for c in db.scalars(select(NotificationChannel).order_by(NotificationChannel.name))]


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    _check(db, body)
    if db.scalar(select(NotificationChannel.id).where(NotificationChannel.name == body.name)):
        raise HTTPException(409, "A channel with this name exists.")
    c = NotificationChannel(**body.model_dump(), created_by=user.username)
    db.add(c)
    db.flush()
    audit(db, "notification_channel_created", username=user.username, target=c.id,
          detail=f"{c.kind} {c.name} >= {c.min_severity}", request=request)
    return _ch(c)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: str, body: ChannelIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    c = db.get(NotificationChannel, channel_id)
    if c is None:
        raise HTTPException(404, "Channel not found.")
    if body.target.endswith("/[redacted]"):
        raise HTTPException(422, "Re-enter the complete destination when editing a channel.")
    _check(db, body, self_id=channel_id)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    audit(db, "notification_channel_updated", username=user.username, target=c.id, detail=c.name, request=request)
    return _ch(c)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    c = db.get(NotificationChannel, channel_id)
    if c is None:
        raise HTTPException(404, "Channel not found.")
    for other in db.scalars(select(NotificationChannel).where(NotificationChannel.escalation_channel_id == channel_id)):
        other.escalation_channel_id, other.escalate_after_minutes = None, None
    db.delete(c)
    audit(db, "notification_channel_deleted", username=user.username, target=channel_id, request=request)


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    c = db.get(NotificationChannel, channel_id)
    if c is None:
        raise HTTPException(404, "Channel not found.")
    out = N.test_send(db, c, user.username)
    audit(db, "notification_channel_tested", username=user.username, target=c.id,
          detail="delivered" if out["delivered"] else f"failed: {out['error']}", request=request)
    return out


@router.get("/deliveries")
def deliveries(state: str | None = None, alert_id: str | None = None, limit: int = 200,
               db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    q = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc()).limit(min(limit, 1000))
    if state:
        q = q.where(NotificationDelivery.state == state)
    if alert_id:
        q = q.where(NotificationDelivery.alert_id == alert_id)
    from .workflows import _may_see_site
    filtered = []
    for d in db.scalars(q):
        alert = db.get(InmAlert, d.alert_id) if d.alert_id else None
        if alert is not None and not _may_see_site(db, user, alert.site_id):
            continue
        if alert is None and user.role != "admin" and (user.site_scope or "all") != "all":
            continue  # non-alert test sends have no site ownership; admin-only for scoped users
        filtered.append(_dl(d))
    return filtered


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("operator"))):
    d = db.get(NotificationDelivery, delivery_id)
    if d is None or d.state != "DEAD" or d.kind == "TEST":
        raise HTTPException(409, "Only a DEAD alert notification can be retried.")
    from .workflows import _require_site
    alert = db.get(InmAlert, d.alert_id) if d.alert_id else None
    if alert is None:
        raise HTTPException(404, "Notification not found.")
    _require_site(db, user, alert.site_id)
    d.state, d.attempts, d.next_attempt_at = "PENDING", 0, None
    audit(db, "notification_retry", username=user.username, target=d.id, request=request)
    return _dl(d)
