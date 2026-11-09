"""Applies alert rule results to the inm_alerts table: open/dedupe/refresh, auto-resolve, maintenance-window
suppression and per-site/global threshold overrides."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database.models_inm import InmDevice
from ..database.models_phases import AlertPolicy, InmAlert, MaintenanceWindow
from ..domain import alert_rules as R


def overrides_for(db: Session, site_id: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for scope in ("global", site_id):
        if not scope:
            continue
        for p in db.scalars(select(AlertPolicy).where(AlertPolicy.scope == scope)):
            out[p.rule_id] = {"threshold": p.threshold, "severity": p.severity, "enabled": p.enabled}
    return out


def in_maintenance(db: Session, device: InmDevice, now: datetime) -> bool:
    q = select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now,
                                        or_(MaintenanceWindow.device_id == device.id,
                                            (MaintenanceWindow.site_id == device.site_id) & (MaintenanceWindow.device_id.is_(None)),
                                            (MaintenanceWindow.site_id.is_(None)) & (MaintenanceWindow.device_id.is_(None))))
    return db.scalar(q.limit(1)) is not None


def apply(db: Session, device: InmDevice, values: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    fired = {f["rule_id"]: f for f in R.evaluate(device.family, values, overrides_for(db, device.site_id))}
    suppressed = in_maintenance(db, device, now)
    opened = resolved = 0
    open_rows = {a.rule_id: a for a in db.scalars(select(InmAlert).where(
        InmAlert.device_id == device.id, InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))}
    for rid, f in fired.items():
        a = open_rows.get(rid)
        if a is None:
            db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id=rid, severity=f["severity"],
                            message=f"{device.hostname or device.serial_number}: {f['message']}"[:300],
                            value=f["value"], threshold=f["threshold"], suppressed=suppressed, opened_at=now,
                            last_seen_at=now, source_type=device.source_type))
            opened += 1
        else:
            a.value, a.last_seen_at, a.severity = f["value"], now, f["severity"]
    # Only resolve rules whose metric was actually measured this cycle (missing data never clears an alert).
    measured = {r.id for r in R.CATALOGUE if isinstance(values.get(r.metric), (int, float))}
    for rid, a in open_rows.items():
        if rid not in fired and rid in measured:
            a.state, a.resolved_at = "RESOLVED", now
            resolved += 1
    db.flush()
    return {"opened": opened, "resolved": resolved, "suppressed": suppressed}
