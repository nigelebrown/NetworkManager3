"""Shared alert helpers: raise (deduplicated) and auto-resolve."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Alert, utcnow

OPEN = ("active", "acknowledged")


def raise_alert(db: Session, *, site_id, ap_id, kind, severity, message, simulated, subject=None) -> None:
    exists = db.scalar(select(Alert).where(
        Alert.kind == kind, Alert.site_id == site_id, Alert.ap_id == ap_id, Alert.subject == subject,
        Alert.status.in_(OPEN),
    ))
    if not exists:
        db.add(Alert(site_id=site_id, ap_id=ap_id, kind=kind, severity=severity, message=message[:500],
                     simulated=simulated, subject=subject))


def resolve_alerts(db: Session, *, site_id, ap_id, kind, subject=None) -> None:
    for a in db.scalars(select(Alert).where(
        Alert.kind == kind, Alert.site_id == site_id, Alert.ap_id == ap_id, Alert.subject == subject,
        Alert.status.in_(OPEN),
    )):
        a.status, a.resolved_at = "resolved", utcnow()
