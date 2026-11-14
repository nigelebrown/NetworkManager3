"""NOC incident workflow on top of rule-based device alerts (InmAlert).

Adds ownership and closure without changing how alerts are raised:
  * assign / reassign to a named operator (assignee must be an active operator or admin account);
  * link an external ticket (system + reference + optional URL);
  * work notes and a full timeline;
  * resolve manually — either "no ticket required" or "ticket resolved" (a linked ticket must be confirmed closed);
  * AUTO-RECOVERED: the alert engine already closes an alert when monitoring measures the metric back under its
    threshold ("self-healing"); that closure is shown as AUTO_RECOVERED, and if a ticket is still linked the incident
    is flagged so the ticket gets closed too;
  * reopen.
A manually resolved alert whose condition persists is NOT hidden: the engine opens a fresh alert on the next
evaluation and it is marked as a recurrence of the earlier incident.

Workflow metadata lives in app_settings (key incident:<alert id>) so no alert schema migration is needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, User
from ..database.models_inm import InmDevice, InmSite
from ..database.models_phases import InmAlert
from ..database.session import get_db
from ..security.deps import audit, require_role

from ..security.context import get_scope


def require_global_scope():
    """Fleet-wide workspaces are refused to site-scoped users (kept from the production branch)."""
    if get_scope() is not None:
        raise HTTPException(403, "This fleet-wide workspace requires access to all sites.")


router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"], dependencies=[Depends(require_role("viewer")), Depends(require_global_scope)])
RESOLUTIONS = ("NO_TICKET_REQUIRED", "TICKET_RESOLVED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _work(db: Session, alert_id: str) -> dict:
    row = db.get(AppSetting, f"incident:{alert_id}")
    return dict(row.value) if row else {"timeline": []}


def _save(db: Session, alert_id: str, w: dict, actor: str) -> None:
    row = db.get(AppSetting, f"incident:{alert_id}")
    if row is None:
        db.add(AppSetting(key=f"incident:{alert_id}", value=w, updated_by=actor, updated_at=_now()))
    else:
        row.value, row.updated_by, row.updated_at = w, actor, _now()
    db.flush()


def _event(w: dict, actor: str, kind: str, text: str) -> None:
    w.setdefault("timeline", []).append({"at": _now().isoformat(), "by": actor, "kind": kind, "text": text[:1000]})


def _resolution(a: InmAlert, w: dict) -> dict | None:
    if a.state != "RESOLVED":
        return None
    if w.get("resolution"):
        return w["resolution"]
    ticket = w.get("ticket")
    return {"type": "AUTO_RECOVERED", "by": "monitoring", "at": _iso(a.resolved_at),
            "note": "Monitoring measured the condition back within threshold (self-healed).",
            "ticket_needs_closure": bool(ticket and not ticket.get("closed"))}


def _public(a: InmAlert, w: dict, dev: InmDevice | None, sites: dict) -> dict:
    return {"id": a.id, "device_id": a.device_id, "device": (dev.hostname or dev.serial_number) if dev else None,
            "family": dev.family if dev else None, "site": sites.get(a.site_id), "rule_id": a.rule_id,
            "severity": a.severity, "message": a.message, "value": a.value, "threshold": a.threshold,
            "state": a.state, "suppressed": a.suppressed, "entity": a.entity, "source_type": a.source_type,
            "opened_at": _iso(a.opened_at), "last_seen_at": _iso(a.last_seen_at), "resolved_at": _iso(a.resolved_at),
            "acked_by": a.acked_by, "assignee": w.get("assignee"), "assigned_at": w.get("assigned_at"),
            "ticket": w.get("ticket"), "resolution": _resolution(a, w), "recurrence_of": w.get("recurrence_of"),
            "timeline": w.get("timeline", []),
            "age_minutes": int((_aware(a.resolved_at or _now()) - _aware(a.opened_at)).total_seconds() // 60)}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _alert(db: Session, alert_id: str) -> InmAlert:
    a = db.get(InmAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Incident not found.")
    return a


def _link_recurrence(db: Session, a: InmAlert, w: dict) -> dict:
    """First time we see an alert, link it to the most recent manually resolved incident for the same device+rule."""
    if w.get("_checked"):
        return w
    prev = db.scalars(select(InmAlert).where(InmAlert.device_id == a.device_id, InmAlert.rule_id == a.rule_id,
                                             InmAlert.state == "RESOLVED", InmAlert.id != a.id,
                                             InmAlert.opened_at <= a.opened_at)
                      .order_by(InmAlert.opened_at.desc()).limit(1)).first()
    w["_checked"] = True
    if prev is not None:
        pw = _work(db, prev.id)
        if pw.get("resolution"):  # manual closure, yet the condition came back
            w["recurrence_of"] = {"id": prev.id, "resolved_by": pw["resolution"].get("by"),
                                  "resolved_at": pw["resolution"].get("at")}
    return w


@router.get("")
def list_incidents(state: str = Query("OPEN", pattern="^(OPEN|ACKNOWLEDGED|RESOLVED|ALL)$"),
                   assignee: str | None = Query(None, max_length=64), mine: bool = False,
                   unassigned: bool = False, severity: str | None = Query(None, max_length=12),
                   limit: int = Query(500, ge=1, le=2000), db: Session = Depends(get_db),
                   user: User = Depends(require_role("viewer"))):
    q = select(InmAlert).order_by(InmAlert.opened_at.desc()).limit(limit)
    if state == "OPEN":
        q = q.where(InmAlert.state.in_(("OPEN", "ACKNOWLEDGED")))
    elif state != "ALL":
        q = q.where(InmAlert.state == state)
    if severity:
        q = q.where(InmAlert.severity == severity)
    alerts = list(db.scalars(q))
    devs = {d.id: d for d in db.scalars(select(InmDevice).where(InmDevice.id.in_({a.device_id for a in alerts} or {""})))}
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    out = []
    for a in alerts:
        w = _work(db, a.id)
        if not w.get("_checked") and a.state != "RESOLVED":
            w = _link_recurrence(db, a, w)
            _save(db, a.id, w, "system")
        who = w.get("assignee")
        if mine and who != user.username:
            continue
        if assignee and who != assignee:
            continue
        if unassigned and who:
            continue
        out.append(_public(a, w, devs.get(a.device_id), sites))
    return out


@router.get("/assignees")
def assignees(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    return [{"username": u.username, "role": u.role} for u in db.scalars(
        select(User).where(User.is_active.is_(True), User.role.in_(("operator", "admin"))).order_by(User.username))]


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    rows = list_incidents("ALL", None, False, False, None, 2000, db, user)
    open_rows = [r for r in rows if r["state"] != "RESOLVED"]
    res = [r for r in rows if r["state"] == "RESOLVED" and r["resolution"]]
    return {"open": len(open_rows), "unassigned": sum(1 for r in open_rows if not r["assignee"]),
            "mine": sum(1 for r in open_rows if r["assignee"] == user.username),
            "with_ticket": sum(1 for r in open_rows if r["ticket"]),
            "auto_recovered": sum(1 for r in res if r["resolution"]["type"] == "AUTO_RECOVERED"),
            "manually_resolved": sum(1 for r in res if r["resolution"]["type"] != "AUTO_RECOVERED"),
            "tickets_to_close": sum(1 for r in res if r["resolution"].get("ticket_needs_closure")),
            "recurrences": sum(1 for r in open_rows if r["recurrence_of"])}


@router.get("/{alert_id}")
def get_incident(alert_id: str, db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    a = _alert(db, alert_id)
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    return _public(a, _work(db, a.id), db.get(InmDevice, a.device_id), sites)


class AssignIn(BaseModel):
    assignee: str | None = Field(default=None, max_length=64)  # None = unassign
    note: str = Field("", max_length=1000)


@router.post("/{alert_id}/assign")
def assign(alert_id: str, body: AssignIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("operator"))):
    a = _alert(db, alert_id)
    if body.assignee:
        target = db.scalar(select(User).where(User.username == body.assignee))
        if target is None or not target.is_active or target.role not in ("operator", "admin"):
            raise HTTPException(422, "Assignee must be an active operator or admin.")
    w = _work(db, a.id)
    before = w.get("assignee")
    w["assignee"], w["assigned_at"] = body.assignee, _now().isoformat() if body.assignee else None
    _event(w, user.username, "ASSIGNED" if body.assignee else "UNASSIGNED",
           f"{before or 'unassigned'} → {body.assignee or 'unassigned'}" + (f": {body.note}" if body.note else ""))
    if body.assignee and a.state == "OPEN":
        a.state, a.acked_by = "ACKNOWLEDGED", user.username
    _save(db, a.id, w, user.username)
    audit(db, "incident_assigned", username=user.username, target=f"alert:{a.id}",
          detail=f"{before} -> {body.assignee}", request=request)
    return get_incident(alert_id, db, user)


class TicketIn(BaseModel):
    system: str = Field("Service desk", min_length=1, max_length=40)
    reference: str = Field(min_length=1, max_length=64)
    url: str | None = Field(default=None, max_length=300, pattern=r"^https?://")


@router.post("/{alert_id}/ticket")
def link_ticket(alert_id: str, body: TicketIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_role("operator"))):
    a = _alert(db, alert_id)
    w = _work(db, a.id)
    w["ticket"] = {"system": body.system, "reference": body.reference, "url": body.url, "closed": False,
                   "linked_by": user.username, "linked_at": _now().isoformat()}
    _event(w, user.username, "TICKET_LINKED", f"{body.system} {body.reference}")
    if a.state == "OPEN":
        a.state, a.acked_by = "ACKNOWLEDGED", user.username
    _save(db, a.id, w, user.username)
    audit(db, "incident_ticket_linked", username=user.username, target=f"alert:{a.id}",
          detail=f"{body.system} {body.reference}", request=request)
    return get_incident(alert_id, db, user)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/{alert_id}/note")
def add_note(alert_id: str, body: NoteIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("operator"))):
    a = _alert(db, alert_id)
    w = _work(db, a.id)
    _event(w, user.username, "NOTE", body.text)
    _save(db, a.id, w, user.username)
    audit(db, "incident_note", username=user.username, target=f"alert:{a.id}", request=request)
    return get_incident(alert_id, db, user)


class ResolveIn(BaseModel):
    resolution: str = Field(pattern="^(NO_TICKET_REQUIRED|TICKET_RESOLVED)$")
    note: str = Field(min_length=3, max_length=1000)
    ticket_reference: str | None = Field(default=None, max_length=64)


@router.post("/{alert_id}/resolve")
def resolve(alert_id: str, body: ResolveIn, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("operator"))):
    a = _alert(db, alert_id)
    w = _work(db, a.id)
    ticket = w.get("ticket")
    if body.resolution == "NO_TICKET_REQUIRED" and ticket and not ticket.get("closed"):
        raise HTTPException(409, f"Ticket {ticket['reference']} is linked: resolve it as 'Ticket resolved'.")
    if body.resolution == "TICKET_RESOLVED":
        if not ticket and not body.ticket_reference:
            raise HTTPException(422, "Give the ticket reference that was resolved.")
        if not ticket:
            ticket = {"system": "Service desk", "reference": body.ticket_reference, "url": None,
                      "linked_by": user.username, "linked_at": _now().isoformat()}
        ticket.update(closed=True, closed_by=user.username, closed_at=_now().isoformat())
        w["ticket"] = ticket
    if a.state != "RESOLVED":
        a.state, a.resolved_at = "RESOLVED", _now()
        w["resolution"] = {"type": body.resolution, "by": user.username, "at": _now().isoformat(), "note": body.note}
    else:  # auto-recovered earlier; operator now closes the linked ticket / adds closure note
        w["resolution"] = {**(_resolution(a, w) or {}), "ticket_needs_closure": False,
                           "closure_note": body.note, "closed_by": user.username}
    _event(w, user.username, "RESOLVED", f"{body.resolution}: {body.note}")
    _save(db, a.id, w, user.username)
    audit(db, "incident_resolved", username=user.username, target=f"alert:{a.id}",
          detail=f"{body.resolution} ticket={(ticket or {}).get('reference')}", request=request)
    return get_incident(alert_id, db, user)


@router.post("/{alert_id}/reopen")
def reopen(alert_id: str, body: NoteIn, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("operator"))):
    a = _alert(db, alert_id)
    if a.state != "RESOLVED":
        raise HTTPException(409, "Incident is not resolved.")
    a.state, a.resolved_at = "ACKNOWLEDGED", None
    w = _work(db, a.id)
    w.pop("resolution", None)
    if w.get("ticket"):
        w["ticket"]["closed"] = False
    _event(w, user.username, "REOPENED", body.text)
    _save(db, a.id, w, user.username)
    audit(db, "incident_reopened", username=user.username, target=f"alert:{a.id}", request=request)
    return get_incident(alert_id, db, user)
