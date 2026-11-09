"""WP-03 — syslog normalization, AAA evidence and change attribution (Phase 3–4)."""
from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import InmDevice
from ..database.models_phases import AuthSessionEvidence, ChangeAttribution, DeviceLogEvent
from ..database.session import get_db
from ..domain import attribution as A
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, real_device, utc

router = APIRouter(prefix="/api/v1", tags=["wp03-attribution"])


class SyslogBatch(BaseModel):
    source_ip: str
    transport: Literal["TLS_MUTUAL", "TLS", "UDP_ALLOWLISTED"] = "UDP_ALLOWLISTED"
    lines: list[str] = Field(min_length=1, max_length=500)
    received_at: datetime | None = None


class AaaSession(BaseModel):
    device_serial: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)
    start_at: datetime
    end_at: datetime | None = None
    source: Literal["RADIUS", "TACACS+", "CX_ACCOUNTING"]
    external_session_id: str | None = Field(default=None, max_length=128)


class Correction(BaseModel):
    level: Literal["CORRELATED", "UNKNOWN"]
    actor: str | None = Field(default=None, max_length=64)
    note: str = Field(min_length=5, max_length=2000)


def _device_by_ip(db: Session, ip: str) -> InmDevice | None:
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL")):
        if ip in [str(a) for a in (d.mgmt_addresses or [])]:
            return d
    return None


def _sessions(db: Session, serial: str) -> list[A.AuthSession]:
    return [A.AuthSession(s.id, s.device_serial, s.username, utc(s.start_at), utc(s.end_at), s.source)
            for s in db.scalars(select(AuthSessionEvidence).where(AuthSessionEvidence.device_serial == serial))]


def _attr_public(a: ChangeAttribution) -> dict:
    return {"id": a.id, "device_id": a.device_id, "log_event_id": a.log_event_id,
            "change_plan_id": a.change_plan_id, "change_at": iso(a.change_at), "level": a.level,
            "actor": a.actor, "change_type": a.change_type, "evidence": a.evidence,
            "candidates": a.candidates, "supersedes_id": a.supersedes_id, "note": a.note,
            "created_by": a.created_by, "created_at": iso(a.created_at)}


@router.post("/syslog/ingest", status_code=201)
def ingest_syslog(body: SyslogBatch, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("admin"))):
    try:
        ipaddress.ip_address(body.source_ip)
    except ValueError:
        raise HTTPException(422, "source_ip must be an IP address") from None
    device = _device_by_ip(db, body.source_ip)
    if device is None and body.transport == "UDP_ALLOWLISTED":
        # Pilot UDP is accepted only from registered device management addresses.
        raise HTTPException(403, "Source IP is not a registered device management address (allowlist).")
    received = utc(body.received_at) or now()
    stored = dup = attributed = 0
    for line in body.lines:
        ev = A.normalize(line, source_ip=body.source_ip, device_serial=device.serial_number if device else None,
                         vendor=device.vendor if device else "unknown", received_at=received,
                         transport=body.transport)
        if db.scalar(select(DeviceLogEvent.id).where(DeviceLogEvent.raw_hash == ev.raw_hash)):
            dup += 1
            continue
        row = DeviceLogEvent(device_id=device.id if device else None, device_serial=ev.device_serial,
                             source_ip=ev.source_ip, vendor=ev.vendor, event_code=ev.event_code,
                             severity=ev.severity, message=ev.message, username=ev.user,
                             is_config_change=ev.is_config_change, observed_at=ev.observed_at,
                             parser_version=ev.parser_version, transport_quality=ev.transport_quality,
                             raw_hash=ev.raw_hash)
        db.add(row); db.flush()
        stored += 1
        if ev.is_config_change and device is not None:
            res = A.attribute(device_serial=device.serial_number, change_at=ev.observed_at,
                              device_event_user=ev.user, sessions=_sessions(db, device.serial_number))
            db.add(ChangeAttribution(device_id=device.id, log_event_id=row.id, change_at=ev.observed_at,
                                     level=res["level"], actor=res["actor"], change_type=res["change_type"],
                                     evidence=res["evidence"], candidates=res["candidates"],
                                     created_by="attribution-engine"))
            attributed += 1
    audit(db, "syslog_batch_ingested", username=user.username, target=device.id if device else body.source_ip,
          detail=f"stored={stored} duplicate={dup} config_changes={attributed} parser={A.PARSER_VERSION}",
          request=request)
    return {"stored": stored, "duplicates": dup, "config_changes_attributed": attributed,
            "device_id": device.id if device else None, "parser_version": A.PARSER_VERSION}


@router.post("/aaa/sessions", status_code=201)
def record_aaa(body: AaaSession, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    if body.end_at and utc(body.end_at) < utc(body.start_at):
        raise HTTPException(422, "Session end precedes start.")
    s = AuthSessionEvidence(device_serial=body.device_serial, username=body.username,
                            start_at=utc(body.start_at), end_at=utc(body.end_at), source=body.source,
                            external_session_id=body.external_session_id, recorded_by=user.username)
    db.add(s); db.flush()
    audit(db, "aaa_session_evidence_recorded", username=user.username, target=body.device_serial,
          detail=f"{body.source} user={body.username} session={s.id}", request=request)
    return {"id": s.id}


@router.get("/devices/{device_id}/change-timeline")
def change_timeline(device_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_any_role("admin", "auditor", "operator"))):
    d = real_device(db, device_id)
    attrs = list(db.scalars(select(ChangeAttribution).where(ChangeAttribution.device_id == d.id)
                            .order_by(ChangeAttribution.change_at.desc(), ChangeAttribution.created_at.desc())))
    superseded = {a.supersedes_id for a in attrs if a.supersedes_id}
    logs = list(db.scalars(select(DeviceLogEvent).where(DeviceLogEvent.device_id == d.id)
                           .order_by(DeviceLogEvent.observed_at.desc()).limit(200)))
    sources = set()
    if logs:
        sources.add("syslog")
    if db.scalar(select(AuthSessionEvidence.id).where(AuthSessionEvidence.device_serial == d.serial_number)):
        sources.add("aaa")
    sources.add("app_audit")
    return {"device_id": d.id,
            "attributions": [dict(_attr_public(a), current=a.id not in superseded) for a in attrs],
            "log_events": [{"id": e.id, "observed_at": iso(e.observed_at), "severity": e.severity,
                            "event_code": e.event_code, "message": e.message, "user": e.username,
                            "is_config_change": e.is_config_change, "transport": e.transport_quality,
                            "parser_version": e.parser_version} for e in logs],
            "coverage": A.coverage(sources)}


@router.get("/drift")
def drift(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "operator"))):
    rows = db.scalars(select(ChangeAttribution).where(ChangeAttribution.change_type == "DIRECT_DEVICE")
                      .order_by(ChangeAttribution.change_at.desc()).limit(300))
    return [_attr_public(a) for a in rows]


@router.post("/attributions/{attribution_id}/correct", status_code=201)
def correct(attribution_id: str, body: Correction, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    """Superseding record only. VERIFIED can never be granted by hand (only by matching evidence)."""
    old = db.get(ChangeAttribution, attribution_id)
    if old is None:
        raise HTTPException(404, "Attribution not found.")
    if body.level == "UNKNOWN" and body.actor:
        raise HTTPException(422, "UNKNOWN attribution cannot name an actor.")
    if body.actor and body.actor not in (old.candidates or []):
        raise HTTPException(422, "A corrected actor must be one of the evidence candidates.")
    new = ChangeAttribution(device_id=old.device_id, log_event_id=old.log_event_id,
                            change_plan_id=old.change_plan_id, change_at=old.change_at, level=body.level,
                            actor=body.actor, change_type=old.change_type, evidence=old.evidence,
                            candidates=old.candidates, supersedes_id=old.id, note=body.note,
                            created_by=user.username)
    db.add(new); db.flush()
    audit(db, "attribution_superseded", username=user.username, target=old.id,
          detail=f"new={new.id} level={body.level}; raw evidence preserved", request=request)
    return _attr_public(new)
