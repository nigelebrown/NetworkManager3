"""Cross-site monitoring groups for existing switch-port observations."""
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
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/v1/port-groups", tags=["port-monitoring"])
PREFIX = "port-group:"
METRICS = ("port_link_up", "port_poe_w", "port_errors_delta", "port_link_resets_delta")


class PortMember(BaseModel):
    device_id: str = Field(min_length=1, max_length=36)
    port: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=100)
    minimum_watts: float | None = Field(default=None, ge=0, le=1000)
    error_threshold: int | None = Field(default=None, ge=1)
    reset_threshold: int | None = Field(default=None, ge=1)


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    members: list[PortMember] = Field(default_factory=list, max_length=100)


def _group(db, group_id):
    group = db.get(AppSetting, PREFIX + group_id)
    if group is None:
        raise HTTPException(404, "Monitoring group not found")
    return group


def _public(group):
    return {"id": group.key.removeprefix(PREFIX), **group.value}


def _save(db, group, body, user, request):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Enter a monitoring group name")
    keys = {(x.device_id, x.port) for x in body.members}
    if len(keys) != len(body.members):
        raise HTTPException(422, "Each switch port can appear once per group")
    previous = {(m["device_id"], m["port"]) for m in group.value.get("members", [])}
    for device_id, port in keys - previous:
        device = db.get(InmDevice, device_id)
        snap = db.get(DevicePortSnapshot, device_id)
        if not device or device.family != "switch" or not snap or not any(p["name"] == port for p in snap.ports):
            raise HTTPException(422, "Select a switch port from its collected interface list")
    group.value = {"name": name, "members": [m.model_dump() for m in body.members]}
    group.updated_at = datetime.now(timezone.utc)
    group.updated_by = user.username
    db.add(group)
    audit(db, "port_monitoring_group_saved", username=user.username, target=group.key,
          detail=f"{len(keys)} monitored ports", request=request)
    return _public(group)


@router.get("")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    rows = db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX)))
    return sorted((_public(g) for g in rows), key=lambda g: (g["name"].casefold(), g["id"]))


@router.post("", status_code=201)
def create_group(body: GroupInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _save(db, AppSetting(key=PREFIX + str(uuid4()), value={}), body, user, request)


@router.put("/{group_id}")
def update_group(group_id: str, body: GroupInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    return _save(db, _group(db, group_id), body, user, request)


def _points(member, since, limit):
    store = telemetry_store()
    return {metric: store.query(device_id=member["device_id"], metric_name=metric, since=since,
                                entity_type="port", entity_id=member["port"], limit=limit)
            for metric in METRICS}


def _status(member, latest):
    link = latest.get("port_link_up")
    if link == 0:
        return "Critical", ["Link down"]
    warnings = []
    for field, metric, title in (
        ("error_threshold", "port_errors_delta", "Errors reached the per-poll threshold"),
        ("reset_threshold", "port_link_resets_delta", "Link resets reached the per-poll threshold"),
    ):
        threshold, value = member.get(field), latest.get(metric)
        if threshold is not None and value is not None and value >= threshold:
            warnings.append(title)
    watts = latest.get("port_poe_w")
    minimum = member.get("minimum_watts")
    if minimum is not None and watts is not None and watts < minimum:
        warnings.append("PoE below minimum")
    if warnings:
        return "Warning", warnings
    if link != 1 or (minimum is not None and watts is None) or any(
        member.get(field) is not None and latest.get(metric) is None
        for field, metric in (("error_threshold", "port_errors_delta"), ("reset_threshold", "port_link_resets_delta"))
    ):
        return "No data", ["Missing recent measurements"]
    return "Healthy", []


@router.get("/{group_id}/dashboard")
def dashboard(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    group = _group(db, group_id)
    now = datetime.now(timezone.utc)
    results = []
    for member in group.value["members"]:
        device = db.get(InmDevice, member["device_id"])
        site = db.get(InmSite, device.site_id) if device and device.site_id else None
        plan = db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == member["device_id"]))
        since = now - timedelta(seconds=max(180, 3 * plan.interval_seconds) if plan else 180)
        points = _points(member, since, 1)
        observed_at = max((rows[0]["observed_at"] for rows in points.values() if rows), default=None)
        latest = {key: rows[0]["value"] for key, rows in points.items()
                  if rows and rows[0]["observed_at"] == observed_at}
        status, reasons = _status(member, latest)
        results.append({**member, "switch": device.hostname if device else "Removed switch",
                        "site_id": site.id if site else None, "site": site.name if site else "Unassigned",
                        "source": device.source_type if device else None, "status": status, "reasons": reasons,
                        "observed_at": observed_at, "metrics": latest})
    return {"ports": results}


@router.get("/{group_id}/history")
def history(group_id: str, device_id: str, port: str, hours: int = Query(24, ge=1, le=168),
            db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    member = next((m for m in _group(db, group_id).value["members"]
                   if m["device_id"] == device_id and m["port"] == port), None)
    if member is None:
        raise HTTPException(404, "Port is not in this monitoring group")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return {"series": _points(member, since, 2500)}
