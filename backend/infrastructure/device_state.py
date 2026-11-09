"""One operational state per device for every consumer (NOC, Device 360, inventory API, reports) — E2E-11."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models_phases import CollectionPlan
from ..domain import collection as C


def state_of(device, plan: CollectionPlan | None, *, now: datetime | None = None,
             health_band: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    active = plan is not None and plan.state == "ACTIVE"
    fresh = C.freshness(plan.last_success_at, plan.interval_seconds, now) if plan else "NEVER"
    st, why = C.operational_state(monitored=active, freshness=fresh,
                                  consecutive_failures=plan.consecutive_failures if plan else 0,
                                  last_failure_category=plan.last_failure_category if plan else None,
                                  health_band=health_band, reported_status=device.status)
    return {"state": st, "state_reason": why, "freshness": fresh,
            "last_persisted_at": plan.last_success_at.isoformat() if plan and plan.last_success_at else None,
            "last_failure": plan.last_failure_category if plan and plan.consecutive_failures else None}


def plans_by_device(db: Session) -> dict[str, CollectionPlan]:
    out: dict[str, CollectionPlan] = {}
    for p in db.scalars(select(CollectionPlan)):
        cur = out.get(p.device_id)
        if cur is None or (p.state == "ACTIVE" and cur.state != "ACTIVE"):
            out[p.device_id] = p
    return out
