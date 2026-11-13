"""Domain effects of ingested events, applied in the SAME transaction as the ingest ledger marker (E2E-03/10/11/15).

Only after telemetry was verifiably persisted does a collection run become SUCCEEDED and a plan record its
last success. Device status, alerts and the UI are all driven from here, so the collector, NOC, Device 360 and
the alert engine see one sequence of facts carrying one correlation id.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models_inm import InmDevice
from ..database.models_phases import CollectionPlan, CollectionRun
from ..domain import collection as C
from ..domain.events import EventEnvelope
from .alert_engine import apply as apply_alerts


def apply_effects(db: Session, e: EventEnvelope) -> list[str]:
    if e.payload.get("collector") != "scheduled":
        return []
    if e.event_type == "device.telemetry.observed":
        return _telemetry(db, e)
    if e.event_type == "device.poll.failed":
        return _failure(db, e)
    return []


def _at(e: EventEnvelope) -> datetime:
    return datetime.fromisoformat(e.occurred_at_utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _newer(db: Session, device: InmDevice, at: datetime) -> bool:
    """Use actual collection timestamps, not the manual-registration timestamp, to order device events.

    A device may be manually imported at noon and then have a legitimate ten-minute-old
    collection backfilled. That sample must produce alerts. Conversely, late JetStream
    redeliveries must not overwrite a newer success or failure. All collection plans
    for this device contribute to the authoritative high-water mark.
    """
    timestamps = [
        _aware(ts)
        for success, failure in db.execute(
            select(CollectionPlan.last_success_at, CollectionPlan.last_failure_at)
            .where(CollectionPlan.device_id == device.id)
        )
        for ts in (success, failure) if ts is not None
    ]
    if timestamps:
        return _aware(at) >= max(timestamps)
    previous = device.last_observed_at
    return previous is None or _aware(at) >= _aware(previous)


def _update_observed_at(device: InmDevice, at: datetime) -> None:
    """A backfill must not move the device's public last-observed timestamp backwards."""
    if device.last_observed_at is None or _aware(at) >= _aware(device.last_observed_at):
        device.last_observed_at = at


def _telemetry(db: Session, e: EventEnvelope) -> list[str]:
    at = _at(e)
    per_family: dict[str, int] = {}
    from .collection_runner import METRIC_FAMILY_OF
    for p in e.payload.get("points", []):
        fam = METRIC_FAMILY_OF.get(p["metric"], "system")
        per_family[fam] = per_family.get(fam, 0) + 1
    for r in db.scalars(select(CollectionRun).where(CollectionRun.id.in_(e.payload.get("run_ids") or []))):
        r.state, r.points_written = "SUCCEEDED", per_family.get(r.metric_family, 0)
    plan = db.get(CollectionPlan, e.payload.get("plan_id"))
    if plan is not None:
        last = plan.last_success_at
        if last is None or (last if last.tzinfo else last.replace(tzinfo=at.tzinfo)) <= at:
            plan.last_success_at, plan.consecutive_failures = at, 0
    topics = ["devices", "telemetry", "noc"]
    device = db.get(InmDevice, e.device_id) if e.device_id else None
    if device is None:
        return topics
    if not _newer(db, device, at):
        return topics  # metric samples and event ledger remain persisted; live status/alerts stay authoritative
    device.status = "up"
    _update_observed_at(device, at)
    fw = e.payload.get("firmware")
    if fw and device.firmware and fw != device.firmware:
        from .phase_services import pause_enrollments
        pause_enrollments(db, device.id, "FIRMWARE_CHANGED", actor="collector")
    if fw:
        device.firmware = fw
    res = apply_alerts(db, device, dict(e.payload.get("alert_values") or {}), now=at,
                       correlation_id=e.correlation_id, event_id=e.event_id)
    per_port: dict[str, dict] = {}
    for p in e.payload.get("points", []):
        if p.get("entity_type") == "port" and p.get("value") is not None:
            per_port.setdefault(p["entity_id"], {})[p["metric"]] = p["value"]
    from .alert_engine import apply_entities
    res2 = apply_entities(db, device, per_port, now=at, correlation_id=e.correlation_id, event_id=e.event_id) \
        if per_port else {"changed": False}
    if res.get("changed") or res2.get("changed"):
        topics.append("alerts")
    return topics


def _failure(db: Session, e: EventEnvelope) -> list[str]:
    at = _at(e)
    device = db.get(InmDevice, e.device_id) if e.device_id else None
    if device is None:
        return ["devices"]
    if not _newer(db, device, at):
        return []  # late failure never creates a new outage or alert
    _update_observed_at(device, at)
    cat = e.payload.get("failure_category")
    # The device record reflects what collection proved: no answer = down; answered but unreadable = unknown.
    device.status = "down" if cat in C.UNREACHABLE_CATEGORIES else "unknown"
    res = apply_alerts(db, device, {"consecutive_failures": e.payload.get("consecutive_failures") or 1}, now=at,
                       correlation_id=e.correlation_id, event_id=e.event_id)
    return ["devices", "noc", "dashboard"] + (["alerts"] if res.get("changed") else [])
