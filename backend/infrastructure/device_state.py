"""One operational state per device for every consumer (NOC, Device 360, inventory API, reports) — E2E-11."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Site
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
    projected = {inventory_identity(d) for d in devices if d.legacy_ap_id is not None}
    return [d for d in devices if d.legacy_ap_id is not None or inventory_identity(d) not in projected]


def plans_by_device(db: Session) -> dict[str, CollectionPlan]:
    out: dict[str, CollectionPlan] = {}
    for p in db.scalars(select(CollectionPlan)):
        cur = out.get(p.device_id)
        if cur is None or (p.state == "ACTIVE" and cur.state != "ACTIVE"):
            out[p.device_id] = p
    return out
