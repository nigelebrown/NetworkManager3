"""Cross-site monitoring groups for selected switch ports (e.g. "AP Uplink", "BWC Dock").

Groups are stored as app settings (no schema change). Measurements come only from the scheduled read-only
collector's per-port telemetry (entity_type="port"); nothing here contacts a device.

Status vocabulary (never shows missing telemetry as healthy):
* ``Critical``    – fresh measurement shows the link down.
* ``Warning``     – a fresh measurement crossed a member threshold.
* ``Stale``       – the last measurement is older than the freshness window (3 × poll interval, ≥180 s).
* ``No data``     – no measurement in the look-back window, or a required metric for a threshold is missing.
* ``Unsupported`` – a threshold needs a measurement this port does not report (e.g. PoE on a non-PoE port).
* ``Healthy``     – fresh measurements, link up, every configured threshold evaluated and satisfied.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, User
from ..database.models_inm import InmDevice, InmSite
from ..database.models_phases import CollectionPlan, DevicePortSnapshot
from ..database.session import get_db
from ..infrastructure.runtime import telemetry_store
from ..security.context import get_scope
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/v1/port-groups", tags=["port-monitoring"])
PREFIX = "port-group:"
METRICS = ("port_link_up", "port_poe_w", "port_errors_delta", "port_link_resets_delta",
           "port_rx_bps", "port_tx_bps", "port_util_percent")
LOOKBACK = timedelta(hours=24)
STATUSES = ("Critical", "Warning", "Unsupported", "Stale", "No data", "Healthy")


class PortMember(BaseModel):
    device_id: str = Field(min_length=1, max_length=36)
    port: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=100)
    minimum_watts: float | None = Field(default=None, ge=0, le=1000)
    error_threshold: int | None = Field(default=None, ge=1)
    reset_threshold: int | None = Field(default=None, ge=1)
    utilization_threshold: float | None = Field(default=None, gt=0, le=100)


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    members: list[PortMember] = Field(default_factory=list, max_length=500)


def _group(db, group_id):
    group = db.get(AppSetting, PREFIX + group_id)
    if group is None:
        raise HTTPException(404, "Monitoring group not found")
    return group


def _visible(db: Session, device: InmDevice | None) -> bool:
    """Same rule as Device 360: a site-scoped user sees only devices on granted legacy sites."""
    if device is None:
        return get_scope() is None
    from ..monitoring.collector import visible_sites
    ids = {s.id for s in visible_sites(db)}
    return device.legacy_site_id in ids or (get_scope() is None and device.legacy_site_id is None)


def _public(group, db: Session | None = None):
    value = dict(group.value)
    members = value.get("members", [])
    if db is not None and get_scope() is not None:
        members = [m for m in members if _visible(db, db.get(InmDevice, m["device_id"]))]
    return {"id": group.key.removeprefix(PREFIX), "name": value.get("name", ""),
            "description": value.get("description", ""), "members": members,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
            "updated_by": group.updated_by}


def _save(db, group, body, user, request):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(422, "Enter a monitoring group name")
    for other in db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX))):
        if other.key != group.key and str(other.value.get("name", "")).casefold() == name.casefold():
            raise HTTPException(409, "A monitoring group with this name already exists")
    keys = {(x.device_id, x.port) for x in body.members}
    if len(keys) != len(body.members):
        raise HTTPException(422, "Each switch port can appear once per group")
    previous = {(m["device_id"], m["port"]) for m in (group.value or {}).get("members", [])}
    for device_id, port in keys - previous:
        device = db.get(InmDevice, device_id)
        snap = db.get(DevicePortSnapshot, device_id)
        if not device or device.family != "switch" or not snap or not any(p["name"] == port for p in snap.ports):
            raise HTTPException(422, "Select a switch port from its collected interface list")
    group.value = {"name": name, "description": body.description.strip(),
                   "members": [m.model_dump() for m in body.members]}
    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = user.username
    db.add(group)
    added, removed = len(keys - previous), len(previous - keys)
    audit(db, "port_monitoring_group_saved", username=user.username, target=group.key,
          detail=f"{name}: {len(keys)} monitored ports (+{added}/-{removed})", request=request)
    db.commit()
    return _public(group)


@router.get("")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    rows = db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX)))
    return sorted((_public(g, db) for g in rows), key=lambda g: (g["name"].casefold(), g["id"]))


@router.post("", status_code=201)
def create_group(body: GroupInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _save(db, AppSetting(key=PREFIX + str(uuid4()), value={}), body, user, request)


@router.put("/{group_id}")
def update_group(group_id: str, body: GroupInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _save(db, _group(db, group_id), body, user, request)


@router.delete("/{group_id}", status_code=204)
def delete_group(group_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    """Removes the group definition only. Collected port telemetry and its history are untouched."""
    group = _group(db, group_id)
    name = group.value.get("name", "")
    db.delete(group)
    audit(db, "port_monitoring_group_deleted", username=user.username, target=group.key,
          detail=f"{name}: definition removed; telemetry history retained", request=request)
    db.commit()


def _store():
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Telemetry store unavailable")
    return store


def _points(member, since, limit):
    store = _store()
    return {metric: store.query(device_id=member["device_id"], metric_name=metric, since=since,
                                entity_type="port", entity_id=member["port"], limit=limit)
            for metric in METRICS}


def _parse(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _port_support(snap: DevicePortSnapshot | None, port: str) -> dict:
    """What the latest faceplate snapshot says this port can report. Unknown is not unsupported."""
    row = next((p for p in (snap.ports if snap else []) or [] if p.get("name") == port), None)
    if row is None:
        return {"present": False}
    poe = "UNKNOWN"
    if row.get("link") == "up":
        poe = "SUPPORTED" if row.get("poe_status") is not None or isinstance(row.get("poe_w"), (int, float)) \
            else "UNSUPPORTED"
    return {"present": True, "poe": poe,
            "utilization": "SUPPORTED" if isinstance(row.get("speed_bps"), (int, float)) and row["speed_bps"] > 0
            else "UNKNOWN",
            "admin": row.get("admin"), "link": row.get("link"), "speed_bps": row.get("speed_bps")}


def evaluate(member: dict, latest: dict, observed_at: datetime | None, fresh_seconds: float, now: datetime,
             support: dict | None = None) -> tuple[str, list[str], list[str]]:
    """Return (status, reasons, unsupported_measurements). Pure; unit-tested."""
    support = support or {}
    unsupported: list[str] = []
    if member.get("minimum_watts") is not None and support.get("poe") == "UNSUPPORTED":
        unsupported.append("PoE power is not reported by this port")
    if observed_at is None:
        return ("Unsupported" if unsupported else "No data"), (unsupported or ["No measurement in the last 24 hours"]), unsupported
    age = (now - observed_at).total_seconds()
    if age > fresh_seconds:
        return "Stale", [f"Last measurement {int(age // 60)} min ago (freshness window {int(fresh_seconds // 60)} min)"], unsupported
    link = latest.get("port_link_up")
    if link == 0:
        if str(support.get("admin") or "").lower() == "down":
            return "Warning", ["Port administratively disabled"], unsupported
        return "Critical", ["Link down"], unsupported
    warnings = []
    for field, metric, title in (
        ("error_threshold", "port_errors_delta", "Errors reached the per-poll threshold"),
        ("reset_threshold", "port_link_resets_delta", "Link resets reached the per-poll threshold"),
        ("utilization_threshold", "port_util_percent", "Utilization reached the threshold"),
    ):
        threshold, value = member.get(field), latest.get(metric)
        if threshold is not None and value is not None and value >= threshold:
            warnings.append(title)
    watts = latest.get("port_poe_w")
    minimum = member.get("minimum_watts")
    if minimum is not None and watts is not None and watts < minimum:
        warnings.append("PoE below minimum")
    if warnings:
        return "Warning", warnings, unsupported
    if unsupported:
        return "Unsupported", unsupported, unsupported
    missing = [label for field, metric, label in (
        ("minimum_watts", "port_poe_w", "PoE power"), ("error_threshold", "port_errors_delta", "error counters"),
        ("reset_threshold", "port_link_resets_delta", "link-reset counters"),
        ("utilization_threshold", "port_util_percent", "utilization"),
    ) if member.get(field) is not None and latest.get(metric) is None]
    if link != 1 or missing:
        what = ", ".join(missing) if missing else "link state"
        return "No data", [f"Missing recent measurement: {what}"], unsupported
    return "Healthy", [], unsupported


def _status(member: dict, latest: dict) -> tuple[str, list[str]]:
    """Pre-rc16 signature kept for callers/tests: evaluates `latest` as a fresh measurement set."""
    now = datetime.now(timezone.utc)
    status, reasons, _ = evaluate(member, latest, now if latest else None, 180, now)
    return status, reasons


@router.get("/{group_id}/dashboard")
def dashboard(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    group = _group(db, group_id)
    now = datetime.now(timezone.utc)
    results = []
    plans = {p.device_id: p for p in db.scalars(select(CollectionPlan))}
    for member in group.value.get("members", []):
        device = db.get(InmDevice, member["device_id"])
        if not _visible(db, device):
            continue
        site = db.get(InmSite, device.site_id) if device and device.site_id else None
        plan = plans.get(member["device_id"])
        fresh = max(180, 3 * plan.interval_seconds) if plan else 180
        points = _points(member, now - LOOKBACK, 1)
        observed = [_parse(rows[0]["observed_at"]) for rows in points.values() if rows]
        observed_at = max((t for t in observed if t), default=None)
        latest = {key: rows[0]["value"] for key, rows in points.items()
                  if rows and _parse(rows[0]["observed_at"]) == observed_at}
        support = _port_support(db.get(DevicePortSnapshot, member["device_id"]), member["port"])
        status, reasons, unsupported = evaluate(member, latest, observed_at, fresh, now, support)
        if device is None:
            status, reasons = "No data", ["Switch removed from inventory"]
        results.append({**member, "switch": device.hostname if device else "Removed switch",
                        "site_id": site.id if site else None, "site": site.name if site else "Unassigned",
                        "source": device.source_type if device else None, "status": status, "reasons": reasons,
                        "unsupported": unsupported, "collection_plan": (plan.state if plan else "NONE"),
                        "freshness_seconds": fresh, "observed_at": observed_at.isoformat() if observed_at else None,
                        "metrics": latest, "support": support})
    locations: dict = {}
    for row in results:
        loc = locations.setdefault(row["site_id"] or "unassigned", {
            "site_id": row["site_id"], "site": row["site"], "ports": 0, "counts": {s: 0 for s in STATUSES}})
        loc["ports"] += 1
        loc["counts"][row["status"]] += 1
    for loc in locations.values():
        loc["status"] = next((s for s in STATUSES if loc["counts"][s]), "No data")
        loc["anomalies"] = loc["counts"]["Critical"] + loc["counts"]["Warning"]
    ordered = sorted(locations.values(), key=lambda l: (STATUSES.index(l["status"]), str(l["site"]).casefold()))
    summary = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    return {"group": {"id": group_id, "name": group.value.get("name", "")}, "generated_at": now.isoformat(),
            "summary": summary, "locations": ordered, "ports": results}


@router.get("/{group_id}/history")
def history(group_id: str, device_id: str, port: str, hours: int = Query(24, ge=1, le=720),
            db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    member = next((m for m in _group(db, group_id).value.get("members", [])
                   if m["device_id"] == device_id and m["port"] == port), None)
    if member is None or not _visible(db, db.get(InmDevice, device_id)):
        raise HTTPException(404, "Port is not in this monitoring group")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    series = _points(member, since, 5000)
    # Ascending for charts; gaps are left as gaps (no interpolation, no zero fill).
    return {"series": {k: sorted(v, key=lambda r: str(r["observed_at"])) for k, v in series.items()},
            "hours": hours, "interpolated": False}
