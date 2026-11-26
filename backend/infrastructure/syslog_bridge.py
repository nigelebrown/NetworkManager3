"""Device syslog -> device timeline and change attribution, plus logging-health alerts (rc18).

Register P3-01 / INTEG-P3-03 / GAP-T03 / P4-08. Device syslog reaches the platform through Grafana Alloy into Loki
(role 05). Until rc18 only the admin `POST /api/v1/syslog/ingest` endpoint fed the attribution engine, so syslog
received in production never reached a device timeline or a change attribution. This worker job closes that gap:

* reads `{source="syslog"}` from Loki forward from a persisted cursor, in bounded pages (idempotent: an event is
  keyed by Loki timestamp + sender + line, so re-reading a page stores nothing twice);
* maps an event to a device by the SENDER IP that Alloy recorded (`source_ip` label from the TCP/UDP connection),
  which must be a registered management address. Events that can only be matched by the hostname written inside
  the message are stored for the timeline but never used for attribution (a hostname is not proof of origin);
* stores the normalised event and, for configuration-change events, runs the same attribution engine as the
  ingest endpoint (VERIFIED only with a unique named AAA session and a device event naming that user);
* raises SYSLOG_SILENT when a device that has sent syslog stops for longer than the threshold, and
  AAA_EVIDENCE_GAP when a direct device change on a device that normally has AAA accounting was attributed
  UNKNOWN. Both resolve automatically when the evidence returns.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..database.models_phases import AuthSessionEvidence, ChangeAttribution, DeviceLogEvent, InmAlert
from ..domain import attribution as A

log = logging.getLogger("inm.syslog_bridge")
CURSOR = "syslog-bridge:cursor"
PAGE = 500
MAX_PAGES = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _minutes(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def store_event(db: Session, device: InmDevice | None, ev: A.NormalizedEvent, *, attribute: bool = True) -> tuple[str, bool]:
    """Persist one normalised event (dedupe on raw_hash) and attribute configuration changes.
    Returns (outcome, attributed) with outcome STORED | DUPLICATE. Shared with the ingest endpoint."""
    if db.scalar(select(DeviceLogEvent.id).where(DeviceLogEvent.raw_hash == ev.raw_hash)):
        return "DUPLICATE", False
    row = DeviceLogEvent(device_id=device.id if device else None, device_serial=ev.device_serial,
                         source_ip=ev.source_ip, vendor=ev.vendor, event_code=ev.event_code,
                         severity=ev.severity, message=ev.message, username=ev.user,
                         is_config_change=ev.is_config_change, observed_at=ev.observed_at,
                         parser_version=ev.parser_version, transport_quality=ev.transport_quality,
                         raw_hash=ev.raw_hash)
    db.add(row)
    db.flush()
    if not (attribute and ev.is_config_change and device is not None):
        return "STORED", False
    from .device_probes import clock_state, tolerance_s
    clk = clock_state(db, device.id)
    sessions = [A.AuthSession(s.id, s.device_serial, s.username, _utc(s.start_at), _utc(s.end_at), s.source)
                for s in db.scalars(select(AuthSessionEvidence).where(
                    AuthSessionEvidence.device_serial == device.serial_number))]
    res = A.attribute(device_serial=device.serial_number, change_at=ev.observed_at, device_event_user=ev.user,
                      sessions=sessions, clock_offset_s=clk.get("offset_s"), clock_tolerance_s=tolerance_s())
    db.add(ChangeAttribution(device_id=device.id, log_event_id=row.id, change_at=ev.observed_at,
                             level=res["level"], actor=res["actor"], change_type=res["change_type"],
                             evidence=res["evidence"], candidates=res["candidates"],
                             created_by="attribution-engine"))
    return "STORED", True


def _maps(db: Session) -> tuple[dict[str, InmDevice], dict[str, InmDevice]]:
    by_ip: dict[str, InmDevice] = {}
    by_host: dict[str, InmDevice] = {}
    for d in db.scalars(select(InmDevice).where(InmDevice.lifecycle != "merged", InmDevice.source_type == "REAL")):
        for a in d.mgmt_addresses or []:
            by_ip.setdefault(str(a), d)
        for k in (d.hostname, d.serial_number, *(d.mgmt_addresses or [])):
            if k:
                by_host.setdefault(str(k).casefold(), d)
    return by_ip, by_host


def bridge(db: Session, store, *, now: datetime | None = None, lookback_minutes: int = 15) -> dict:
    """One bounded pass Loki -> device_log_events (+ attribution). Cursor persisted after every page."""
    from ..domain.syslog_host import resolve_host
    now = now or _now()
    row = db.get(AppSetting, CURSOR)
    try:
        since = datetime.fromisoformat((row.value or {})["since"]) if row else now - timedelta(minutes=lookback_minutes)
    except (KeyError, TypeError, ValueError):
        since = now - timedelta(minutes=lookback_minutes)
    by_ip, by_host = _maps(db)
    out = {"read": 0, "stored": 0, "duplicates": 0, "attributed": 0, "unmapped": 0, "host_only": 0, "pages": 0}
    for _ in range(MAX_PAGES):
        entries = store.query('{source="syslog"}', since, PAGE, until=now, direction="forward")
        out["pages"] += 1
        if not entries:
            break
        entries.sort(key=lambda e: e["ts"])
        for e in entries:
            out["read"] += 1
            labels = e.get("labels") or {}
            sender = labels.get("source_ip")
            reported = labels.get("host") or labels.get("hostname")
            host, _correction = resolve_host(reported, e.get("line"))
            device = by_ip.get(sender or "")
            verified = device is not None
            if device is None:
                device = by_host.get((host or "").casefold())
                if device is None:
                    out["unmapped"] += 1
                    continue
                out["host_only"] += 1
            ts = datetime.fromisoformat(e["ts"])
            ev = A.normalize(e.get("line") or "", source_ip=sender or (device.mgmt_addresses or ["0.0.0.0"])[0],
                             device_serial=device.serial_number, vendor=device.vendor, received_at=ts,
                             transport="UDP_ALLOWLISTED")
            key = f"loki|{e.get('ns') or e['ts']}|{sender or ''}|{reported or ''}|{e.get('line') or ''}"
            ev = dataclasses.replace(ev, raw_hash=hashlib.sha256(key.encode("utf-8", "replace")).hexdigest())
            outcome, attributed = store_event(db, device, ev, attribute=verified)
            out["stored" if outcome == "STORED" else "duplicates"] += 1
            out["attributed"] += int(attributed)
        last = datetime.fromisoformat(entries[-1]["ts"])
        since = max(since, last + timedelta(microseconds=1))
        _save_cursor(db, since)
        db.flush()
        if len(entries) < PAGE:
            break
    _save_cursor(db, since)
    return out


def _save_cursor(db: Session, since: datetime) -> None:
    row = db.get(AppSetting, CURSOR)
    value = {"since": since.isoformat(), "updated_at": _now().isoformat()}
    if row is None:
        db.add(AppSetting(key=CURSOR, value=value, updated_by="syslog-bridge"))
    else:
        row.value, row.updated_by, row.updated_at = value, "syslog-bridge", _now()


def _open(db: Session, device: InmDevice, rule: str) -> InmAlert | None:
    return db.scalar(select(InmAlert).where(InmAlert.device_id == device.id, InmAlert.rule_id == rule,
                                            InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))


def _raise(db: Session, device: InmDevice, rule: str, severity: str, message: str, now: datetime,
           value: float | None = None, threshold: float | None = None) -> bool:
    a = _open(db, device, rule)
    if a is None:
        db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id=rule, severity=severity,
                        message=message[:300], value=value, threshold=threshold, opened_at=now, last_seen_at=now,
                        source_type=device.source_type, suppressed=False))
        return True
    a.message, a.last_seen_at, a.value = message[:300], now, value
    return False


def _resolve(db: Session, device: InmDevice, rule: str, now: datetime) -> bool:
    a = _open(db, device, rule)
    if a is None:
        return False
    a.state, a.resolved_at = "RESOLVED", now
    return True


def logging_health(db: Session, *, now: datetime | None = None) -> dict:
    """SYSLOG_SILENT and AAA_EVIDENCE_GAP alerts (P4-08). Evidence-based: only devices that have sent syslog are
    expected to keep sending it; only devices with recorded AAA sessions are expected to have them."""
    now = now or _now()
    silence = timedelta(minutes=_minutes("INM_SYSLOG_SILENCE_MINUTES", 60))
    gap_window = timedelta(hours=_minutes("INM_AAA_GAP_WINDOW_HOURS", 24))
    out = {"syslog_silent": 0, "syslog_resolved": 0, "aaa_gap": 0, "aaa_resolved": 0}
    last_seen = dict(db.execute(select(DeviceLogEvent.device_id, func.max(DeviceLogEvent.ingested_at))
                                .where(DeviceLogEvent.device_id.is_not(None))
                                .group_by(DeviceLogEvent.device_id)).all())
    aaa_serials = set(db.scalars(select(AuthSessionEvidence.device_serial).distinct()))
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL", InmDevice.lifecycle != "merged")):
        seen = _utc(last_seen.get(d.id))
        if seen is not None:
            quiet = now - seen
            if quiet > silence:
                mins = int(quiet.total_seconds() // 60)
                out["syslog_silent"] += _raise(
                    db, d, "SYSLOG_SILENT", "major",
                    f"No syslog from {d.hostname or d.serial_number} for {mins} min: device events and change "
                    "attribution are incomplete until it resumes (check the device's syslog target and Alloy).",
                    now, value=float(mins), threshold=silence.total_seconds() / 60)
            else:
                out["syslog_resolved"] += _resolve(db, d, "SYSLOG_SILENT", now)
        if d.serial_number in aaa_serials:
            unknown = db.scalar(select(func.count()).select_from(ChangeAttribution).where(
                ChangeAttribution.device_id == d.id, ChangeAttribution.change_type == "DIRECT_DEVICE",
                ChangeAttribution.level == "UNKNOWN", ChangeAttribution.change_at >= now - gap_window))
            if unknown:
                out["aaa_gap"] += _raise(
                    db, d, "AAA_EVIDENCE_GAP", "major",
                    f"{unknown} direct configuration change(s) on {d.hostname or d.serial_number} in the last "
                    f"{int(gap_window.total_seconds() // 3600)} h have no AAA accounting evidence although this device "
                    "normally reports AAA sessions: technician identity is UNKNOWN (check the AAA/accounting path).",
                    now, value=float(unknown))
            else:
                out["aaa_resolved"] += _resolve(db, d, "AAA_EVIDENCE_GAP", now)
    db.flush()
    return out
