"""One operational state per device for every consumer (NOC, Device 360, inventory API, reports) — E2E-11."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import ClientRecord, Site
from ..database.models_inm import InmDevice
from ..config import get_settings
from ..database.models_phases import CollectionPlan
from ..domain import collection as C


def state_of(device, plan: CollectionPlan | None, *, now: datetime | None = None,
             health_band: str | None = None, controller: Site | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if controller and controller.last_poll_at and device.legacy_ap_id is not None:
        interval = get_settings().poll_interval_seconds
        fresh = C.freshness(controller.last_poll_at, interval, now)
        status, reason = C.operational_state(
            monitored=True, freshness=fresh, consecutive_failures=0, last_failure_category=None,
            health_band=health_band, reported_status=device.status,
        )
        if controller.last_poll_ok is not True:
            status, reason = "UNKNOWN", "Latest Instant controller poll failed"
        if controller.last_poll_ok is True and status == "ONLINE":
            reason = "Fresh Instant controller observation"
        return {"state": status, "state_reason": reason, "freshness": fresh,
                "last_persisted_at": controller.last_poll_at.isoformat() if controller.last_poll_ok else None,
                "last_failure": "CONTROLLER_POLL_FAILED" if controller.last_poll_ok is False else None}
    active = plan is not None and plan.state == "ACTIVE"
    fresh = C.freshness(plan.last_success_at, plan.interval_seconds, now) if plan else "NEVER"
    st, why = C.operational_state(monitored=active, freshness=fresh,
                                  consecutive_failures=plan.consecutive_failures if plan else 0,
                                  last_failure_category=plan.last_failure_category if plan else None,
                                  health_band=health_band, reported_status=device.status)
    return {"state": st, "state_reason": why, "freshness": fresh,
            "last_persisted_at": plan.last_success_at.isoformat() if plan and plan.last_success_at else None,
            "last_failure": plan.last_failure_category if plan and plan.consecutive_failures else None}


def inventory_identity(device):
    return (device.source_type, device.family, device.vendor.casefold(), device.serial_number.casefold())


def canonical_inventory(devices):
    devices = [d for d in devices if getattr(d, "lifecycle", None) != "merged"]
    projected = {inventory_identity(d) for d in devices if d.legacy_ap_id is not None}
    return [d for d in devices if d.legacy_ap_id is not None or inventory_identity(d) not in projected]


def dashboard_source() -> str:
    return "DEMO" if get_settings().mode == "demo" else "REAL"


def visible_inventory(db: Session, controller_ids: set[int], *, family: str | None = None,
                      include_unlinked: bool = True, dashboard: bool = False) -> list[InmDevice]:
    from sqlalchemy import or_
    from ..security.context import get_scope

    visible = InmDevice.legacy_site_id.in_(controller_ids)
    if include_unlinked and get_scope() is None:
        visible = or_(visible, (InmDevice.source_type == "REAL") & InmDevice.legacy_site_id.is_(None))
    query = select(InmDevice).where(visible).order_by(InmDevice.family, InmDevice.hostname)
    if dashboard:
        query = query.where(InmDevice.source_type == dashboard_source())
    if family:
        query = query.where(InmDevice.family == family)
    return canonical_inventory(db.scalars(query))


def fresh_controller_ids(controllers, now: datetime) -> set[int]:
    interval = get_settings().poll_interval_seconds
    return {s.id for s in controllers if s.enabled and s.last_poll_ok
            and C.freshness(s.last_poll_at, interval, now) == "FRESH"}


def fresh_wireless_clients(db: Session, controllers, now: datetime) -> list[ClientRecord]:
    interval = get_settings().poll_interval_seconds
    ids = fresh_controller_ids(controllers, now)
    if not ids:
        return []
    query = select(ClientRecord).where(
        ClientRecord.site_id.in_(ids), ClientRecord.is_connected.is_(True),
    ).order_by(ClientRecord.last_seen.desc(), ClientRecord.id.desc())
    latest = {}
    for client in db.scalars(query):
        if C.freshness(client.last_seen, interval, now) == "FRESH":
            latest.setdefault((client.simulated, client.mac.casefold()), client)
    return list(latest.values())


def plans_by_device(db: Session) -> dict[str, CollectionPlan]:
    out: dict[str, CollectionPlan] = {}
    for p in db.scalars(select(CollectionPlan)):
        cur = out.get(p.device_id)
        if cur is None or (p.state == "ACTIVE" and cur.state != "ACTIVE"):
            out[p.device_id] = p
    return out
