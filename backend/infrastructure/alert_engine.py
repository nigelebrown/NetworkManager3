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


def apply(db: Session, device: InmDevice, values: dict, *, now: datetime | None = None,
          correlation_id: str | None = None, event_id: str | None = None) -> dict:
    """Evaluate rules for one device observation. correlation_id/event_id link each opened, refreshed or
    resolved alert to the observation event that caused it (E2E-15)."""
    now = now or datetime.now(timezone.utc)
    fired = {f["rule_id"]: f for f in R.evaluate(device.family, values, overrides_for(db, device.site_id))}
    suppressed = in_maintenance(db, device, now)
    opened = resolved = 0
    open_rows = {a.rule_id: a for a in db.scalars(select(InmAlert).where(
        InmAlert.device_id == device.id, InmAlert.entity.is_(None), InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))}
    for rid, f in fired.items():
        a = open_rows.get(rid)
        if a is None:
            db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id=rid, severity=f["severity"],
                            message=f"{device.hostname or device.serial_number}: {f['message']}"[:300],
                            value=f["value"], threshold=f["threshold"], suppressed=suppressed, opened_at=now,
                            last_seen_at=now, source_type=device.source_type, correlation_id=correlation_id,
                            event_id=event_id))
            opened += 1
        else:
            a.value, a.last_seen_at, a.severity = f["value"], now, f["severity"]
            a.correlation_id, a.event_id = correlation_id or a.correlation_id, event_id or a.event_id
    # Only resolve rules whose metric was actually measured this cycle (missing data never clears an alert).
    measured = {r.id for r in R.CATALOGUE if isinstance(values.get(r.metric), (int, float))}
    for rid, a in open_rows.items():
        if rid not in fired and rid in measured:
            a.state, a.resolved_at = "RESOLVED", now
            a.correlation_id, a.event_id = correlation_id or a.correlation_id, event_id or a.event_id
            resolved += 1
    db.flush()
    return {"opened": opened, "resolved": resolved, "suppressed": suppressed, "changed": bool(opened or resolved)}


def apply_entities(db: Session, device: InmDevice, per_entity: dict[str, dict], *, now: datetime | None = None,
                   correlation_id: str | None = None, event_id: str | None = None) -> dict:
    """Per-interface alerts: one alert per (device, rule, port); resolved only when that port's metric is measured
    again below threshold. per_entity: {"1/1/3": {"port_util_percent": 93.0, ...}}."""
    now = now or datetime.now(timezone.utc)
    overrides = overrides_for(db, device.site_id)
    suppressed = in_maintenance(db, device, now)
    open_rows = {(a.rule_id, a.entity): a for a in db.scalars(select(InmAlert).where(
        InmAlert.device_id == device.id, InmAlert.entity.is_not(None), InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))}
    opened = resolved = 0
    for port, values in per_entity.items():
        ent = f"port:{port}"
        for r in R.PORT_CATALOGUE:
            o = overrides.get(r.id, {})
            if o.get("enabled") is False:
                continue
            v = values.get(r.metric)
            if not isinstance(v, (int, float)):
                continue
            thr = float(o.get("threshold", r.threshold))
            hit = v >= thr if r.op == "ge" else v > thr
            a = open_rows.get((r.id, ent))
            if hit and a is None:
                db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id=r.id, entity=ent,
                                severity=o.get("severity", r.severity),
                                message=f"{device.hostname or device.serial_number} port {port}: {r.title}: {v:g} "
                                        f"(threshold {thr:g})"[:300], value=float(v), threshold=thr,
                                suppressed=suppressed, opened_at=now, last_seen_at=now, source_type=device.source_type,
                                correlation_id=correlation_id, event_id=event_id))
                opened += 1
            elif hit:
                a.value, a.last_seen_at = float(v), now
            elif a is not None:
                a.state, a.resolved_at, a.correlation_id = "RESOLVED", now, correlation_id or a.correlation_id
                resolved += 1
    db.flush()
    return {"opened": opened, "resolved": resolved, "changed": bool(opened or resolved)}
