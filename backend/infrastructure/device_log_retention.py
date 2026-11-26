"""Device-log retention and disposal (rc17).

Applies to `device_log_events` (syslog ingest and scheduled device-log collection). OFF by default: nothing is
deleted until an administrator records a retention period and the purpose/approval reference for it.
Events cited by a change attribution (audit evidence) are never deleted by retention. Every run is audited with
counts only (no log content). Deletion is batched to bound transaction size.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_phases import ChangeAttribution, DeviceLogEvent
from ..security.deps import audit

KEY = "device_log_retention"
MIN_DAYS, MAX_DAYS = 30, 3650
BATCH = 5000


def policy(db: Session) -> dict:
    row = db.get(AppSetting, KEY)
    v = row.value if row else {}
    return {"enabled": bool(v.get("enabled")), "days": v.get("days"), "approval_reference": v.get("approval_reference"),
            "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "last_run": v.get("last_run")}


def set_policy(db: Session, *, enabled: bool, days: int | None, approval_reference: str, actor: str, request=None) -> dict:
    if enabled and (days is None or not MIN_DAYS <= days <= MAX_DAYS):
        raise ValueError(f"Retention must be between {MIN_DAYS} and {MAX_DAYS} days")
    if enabled and len(approval_reference.strip()) < 3:
        raise ValueError("Record the policy/approval reference that authorises deletion")
    row = db.get(AppSetting, KEY) or AppSetting(key=KEY, value={})
    previous = dict(row.value or {})
    row.value = {**previous, "enabled": enabled, "days": days if enabled else previous.get("days"),
                 "approval_reference": approval_reference.strip()[:200]}
    row.updated_by, row.updated_at = actor, datetime.now(timezone.utc)
    db.add(row)
    audit(db, "device_log_retention_policy_set", username=actor, target=KEY,
          detail=f"enabled={enabled} days={days} ref={approval_reference.strip()[:80]}", request=request)
    db.flush()
    return policy(db)


def preview(db: Session, *, now: datetime | None = None) -> dict:
    p = policy(db)
    if not p["enabled"]:
        return {"enabled": False, "would_delete": 0, "protected": 0}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=int(p["days"]))
    protected_ids = select(ChangeAttribution.log_event_id).where(ChangeAttribution.log_event_id.is_not(None))
    old = select(func.count()).select_from(DeviceLogEvent).where(DeviceLogEvent.ingested_at < cutoff)
    total_old = db.scalar(old) or 0
    deletable = db.scalar(old.where(DeviceLogEvent.id.not_in(protected_ids))) or 0
    return {"enabled": True, "cutoff": cutoff.isoformat(), "would_delete": deletable, "protected": total_old - deletable}


def apply(db: Session, *, actor: str = "retention-scheduler", now: datetime | None = None) -> dict:
    """Delete events ingested before the cutoff, except attribution evidence. Returns counts."""
    p = policy(db)
    if not p["enabled"]:
        return {"enabled": False, "deleted": 0}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(p["days"]))
    protected_ids = select(ChangeAttribution.log_event_id).where(ChangeAttribution.log_event_id.is_not(None))
    deleted = 0
    while True:
        ids = list(db.scalars(select(DeviceLogEvent.id).where(DeviceLogEvent.ingested_at < cutoff,
                                                               DeviceLogEvent.id.not_in(protected_ids)).limit(BATCH)))
        if not ids:
            break
        deleted += db.execute(delete(DeviceLogEvent).where(DeviceLogEvent.id.in_(ids))).rowcount or 0
        db.flush()
    row = db.get(AppSetting, KEY)
    row.value = {**row.value, "last_run": {"at": now.isoformat(), "deleted": deleted, "cutoff": cutoff.isoformat()}}
    audit(db, "device_log_retention_applied", username=actor, target=KEY,
          detail=f"{deleted} device log events older than {p['days']} days deleted; attribution evidence kept")
    db.flush()
    return {"enabled": True, "deleted": deleted, "cutoff": cutoff.isoformat()}
