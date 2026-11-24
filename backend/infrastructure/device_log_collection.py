"""Opt-in, bounded, read-only device-log collection.

Existing DeviceLogEvent storage is reused without a schema migration. Polled
records are kept separate from syslog attribution; collection time is not a
verified device-event timestamp. Never infer a configuration-change actor
from an authenticated transport or controller-wide log entry.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..config import get_settings
from ..database.models import AppSetting, Site
from ..database.models_inm import InmDevice
from ..database.models_phases import DeviceAccess, DeviceLogEvent
from . import change_executor as X
from .device_audit import sanitize
from .writers import WriterError

PREFIX = "device-log-poll:"
TRANSPORT = "AUTHENTICATED_POLL"
MAX_RECORDS = 100


def now():
    return datetime.now(timezone.utc)


def scope_key(db, device):
    """Controller logs are controller-scoped, not evidence about a member AP."""
    if device.family == "access_point":
        site = db.get(Site, device.legacy_site_id) if device.legacy_site_id else None
        if site is not None:
            if site.simulated or not site.enabled:
                raise WriterError("PRECONDITION", "Controller is disabled or simulated.")
            return "controller", str(ipaddress.IPv4Address(site.vc_host))
        access = db.get(DeviceAccess, device.id)
        address = access.vc_host if access and access.vc_host else None
        address = address or (device.mgmt_addresses or [None])[0]
        return "controller", str(ipaddress.IPv4Address(address))
    return "device", str(ipaddress.IPv4Address((device.mgmt_addresses or [None])[0]))


def eligible(device):
    return (device is not None and device.source_type == "REAL"
            and device.vendor.lower() == "aruba"
            and device.family in ("switch", "access_point")
            and device.lifecycle not in ("merged", "retired", "decommissioned")
            and bool(device.mgmt_addresses))


def setting(db, device_id):
    row = db.get(AppSetting, PREFIX + device_id)
    return dict(row.value or {}) if row else {}


def set_schedule(db, device_id, enabled, interval_minutes, actor):
    row = db.get(AppSetting, PREFIX + device_id)
    value = {**setting(db, device_id), "enabled": enabled,
             "interval_minutes": interval_minutes, "next_due_at": now().isoformat()}
    if row is None:
        db.add(AppSetting(key=PREFIX + device_id, value=value, updated_by=actor))
    else:
        row.value, row.updated_by, row.updated_at = value, actor, now()
    db.flush()
    return value


def persist(db, device, entries, kind, scope, address, collected):
    """Atomic dedupe includes device/controller scope and normalized fields."""
    count = 0
    dialect = db.get_bind().dialect.name
    if dialect not in ("sqlite", "postgresql"):
        raise RuntimeError("Unsupported log persistence dialect")
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    for entry in entries[:MAX_RECORDS]:
        timestamp = sanitize(entry.get("timestamp"))
        message = sanitize(entry.get("message"))
        actor = sanitize(entry.get("actor"))[:64] or None
        action = sanitize(entry.get("action"))
        level = sanitize(entry.get("level")).lower().removeprefix("log_")
        level = {"warn": "warning", "err": "error", "crit": "critical",
                 "noti": "notice", "emerg": "emergency"}.get(level, level)
        if level not in ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"):
            level = "unknown"
        if not message:
            continue
        identity = [device.id, address, scope, kind,
                    timestamp, message, actor, action, level]
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
        text = ("[Device-reported time: " + timestamp[:100] + "] " if timestamp else "") + message
        stmt = insert(DeviceLogEvent).values(
            id=str(uuid.uuid4()), device_id=device.id,
            device_serial=device.serial_number if scope == "device" else None,
            source_ip=address, vendor=device.vendor, event_code=kind,
            severity=level, message=text, username=actor, is_config_change=False,
            observed_at=collected, ingested_at=collected,
            parser_version="polled-" + scope + "-v1", transport_quality=TRANSPORT,
            raw_hash=digest).on_conflict_do_nothing(index_elements=["raw_hash"])
        if db.execute(stmt.returning(DeviceLogEvent.id)).scalar_one_or_none():
            count += 1
    return count


def collect(db, device, *, reader_factory=None, clock=now):
    """Use existing credential/target allowlist. Never invoke an adapter write."""
    if not eligible(device):
        raise WriterError("PRECONDITION", "Only active real Aruba devices may be scheduled.")
    scope, address = scope_key(db, device)
    factory = reader_factory or X.reader_for
    reader, environment = factory(db, device)
    if environment != "HARDWARE":
        reader.close()
        raise WriterError("PRECONDITION", "Emulated logs are not accepted as real evidence.")
    result = {"records_added": 0, "categories": {}, "scope": scope}
    try:
        reader.open()
        if scope == "device":
            identity = reader.identity()
            if identity.get("serial_number") != device.serial_number:
                raise WriterError("IDENTITY_UNVERIFIED", "Device serial did not match inventory.")
        kinds = ("audit", "event") if device.family == "switch" else ("system", "security", "user")
        for kind in kinds:
            try:
                rows = reader.read_audit_logs(kind=kind, limit=MAX_RECORDS)
            except WriterError as exc:
                result["categories"][kind] = {"state": "FAILED", "reason": exc.category}
                continue
            stored = persist(db, device, rows, kind, scope, address, clock())
            result["records_added"] += stored
            result["categories"][kind] = {"state": "COLLECTED", "received": len(rows),
                                          "added": stored, "possibly_truncated": len(rows) >= MAX_RECORDS}
    finally:
        reader.close()
    states = [c["state"] for c in result["categories"].values()]
    result["state"] = ("COLLECTED" if states and all(s == "COLLECTED" for s in states)
                       else "PARTIAL" if "COLLECTED" in states else "FAILED")
    return result


def tick(session_factory, owner):
    """One due device per tick; durable scheduling and fail-closed overlap guard."""
    if get_settings().mode != "live" or os.environ.get("INM_SCHEDULED_DEVICE_LOGS", "false").lower() != "true":
        return {"state": "DISABLED"}
    holder = owner[:30] + "-" + uuid.uuid4().hex[:8]
    lease = "device-log-scheduler"
    with session_factory() as db:
        if not X._lease(db, lease, holder, ttl=300):
            return {"state": "BUSY"}
    try:
        at = now()
        with session_factory() as db:
            due = []
            for row in db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX))):
                value = row.value or {}
                if not value.get("enabled"):
                    continue
                try:
                    when = datetime.fromisoformat(value.get("next_due_at") or at.isoformat())
                except (TypeError, ValueError):
                    when = at
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when <= at:
                    due.append((when, row.key[len(PREFIX):]))
            if not due:
                return {"state": "IDLE"}
            device_id = min(due)[1]
            row = db.get(AppSetting, PREFIX + device_id)
            interval = max(5, min(1440, int(row.value.get("interval_minutes", 15))))
            row.value = {**row.value, "last_attempt_at": at.isoformat(),
                         "next_due_at": (at + timedelta(minutes=interval)).isoformat()}
        try:
            with session_factory() as db:
                device = db.get(InmDevice, device_id)
                if not setting(db, device_id).get("enabled"):
                    return {"state": "DISABLED"}
                result = collect(db, device)
        except Exception as exc:
            result = {"state": "FAILED", "reason": exc.category if isinstance(exc, WriterError)
                      else type(exc).__name__}
        with session_factory() as db:
            row = db.get(AppSetting, PREFIX + device_id)
            if row:
                row.value = {**row.value, "last_result": result, "last_completed_at": now().isoformat()}
        return result
    finally:
        with session_factory() as db:
            X._release(db, lease, holder)


def history(db, device_id, limit=100):
    rows = db.scalars(select(DeviceLogEvent).where(DeviceLogEvent.device_id == device_id,
                        DeviceLogEvent.transport_quality == TRANSPORT)
                      .order_by(DeviceLogEvent.ingested_at.desc(), DeviceLogEvent.id.desc()).limit(limit))
    return [{"id": r.id, "collected_at": r.ingested_at.isoformat(), "kind": r.event_code,
             "scope": "controller" if r.parser_version == "polled-controller-v1" else "device",
             "level": r.severity, "reported_actor": r.username, "message": r.message}
            for r in rows]
