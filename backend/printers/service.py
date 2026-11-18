"""Printer Intelligence service: persistence, leased scheduling, jobs, analytics queries and reports.

Everything is read-only towards printers. Each poll result is persisted with provenance; a missed poll
records reachability only — no metric, meter or supply value is fabricated.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import logging
import os
import statistics
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network
from typing import Any, Callable

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..database.models import AppSetting, Site
from ..database.models_printer import (PrinterConsumableCycle, PrinterDevice, PrinterMaintenanceEvent,
                                       PrinterMeterReading, PrinterSupplyReading)
from ..database.models_printer_srs import (
    PrinterCapability, PrinterConsumable, PrinterCounterInterval, PrinterDailyAggregate, PrinterFaultEvent,
    PrinterIdentity, PrinterIdentityChange, PrinterJob, PrinterMaintenanceDetail, PrinterMetricDefinition,
    PrinterMetricSample, PrinterMibModule, PrinterMibObject, PrinterMibProfile, PrinterModel, PrinterModelCost,
    PrinterMonitoring, PrinterRecommendation, PrinterReportSchedule, PrinterStatusPeriod, PrinterSupplyEvent,
)
from . import collector as C
from . import export, fleet, mib, mib_parser
from . import utilization as U

log = logging.getLogger("inm.printers")
PROFILE_PREFIX = "printer-snmp:"
RETENTION_KEY = "printer_retention"
DEFAULT_RETENTION = {"raw_days": 365, "aggregate_years": 5}
MAX_BACKOFF_SECONDS = 6 * 3600
LEASE_SECONDS = 120


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def iso(t):
    return _utc(t).isoformat() if t else None


# ---- emulated agents (isolated tests / demo only) -------------------------------------------------
EMULATORS: dict[str, C.EmulatedPrinterAgent] = {}


def emulation_enabled() -> bool:
    return os.environ.get("INM_PRINTER_EMULATION", "false").lower() in ("1", "true", "yes")


# ---- profiles --------------------------------------------------------------------------------------
def ensure_builtin_profiles(db: Session) -> None:
    existing = {p.key: p for p in db.scalars(select(PrinterMibProfile))}
    for spec in mib.BUILTIN_PROFILES:
        p = existing.get(spec["key"])
        if p is None:
            p = PrinterMibProfile(key=spec["key"], name=spec["name"], vendor=spec["vendor"],
                                  match_enterprise=spec["match_enterprise"], match_descr_regex=spec["match_descr_regex"],
                                  priority=spec["priority"], builtin=True, created_by="system")
            db.add(p)
            db.flush()
        have = {d.metric_key for d in db.scalars(select(PrinterMetricDefinition).where(PrinterMetricDefinition.profile_id == p.id))}
        for m in spec["metrics"]:
            if m["metric_key"] not in have:
                db.add(PrinterMetricDefinition(profile_id=p.id, metric_key=m["metric_key"], oid=m["oid"], access=m["access"],
                                               value_type=m["value_type"], unit=m["unit"], scale=m["scale"],
                                               interval_seconds=m["interval_seconds"], category=m["category"],
                                               verification=m["verification"], description=m["description"]))
    db.flush()


def profile_dicts(db: Session) -> list[dict]:
    ensure_builtin_profiles(db)
    out = []
    for p in db.scalars(select(PrinterMibProfile)):
        defs = list(db.scalars(select(PrinterMetricDefinition).where(PrinterMetricDefinition.profile_id == p.id,
                                                                     PrinterMetricDefinition.enabled.is_(True))))
        out.append({"id": p.id, "key": p.key, "name": p.name, "vendor": p.vendor, "match_enterprise": p.match_enterprise,
                    "match_descr_regex": p.match_descr_regex, "priority": p.priority, "enabled": p.enabled,
                    "version": p.version, "builtin": p.builtin, "default_interval_seconds": p.default_interval_seconds,
                    "metrics": [{"metric_key": d.metric_key, "oid": d.oid, "access": d.access, "value_type": d.value_type,
                                 "unit": d.unit, "scale": d.scale, "interval_seconds": d.interval_seconds,
                                 "category": d.category, "verification": d.verification, "description": d.description}
                                for d in defs]})
    return out


# ---- MIB library -----------------------------------------------------------------------------------
def known_mib_names(db: Session, exclude_module: str | None = None) -> dict[str, str]:
    q = select(PrinterMibObject.name, PrinterMibObject.oid).join(PrinterMibModule, PrinterMibModule.id == PrinterMibObject.module_id) \
        .where(PrinterMibModule.active.is_(True), PrinterMibObject.oid.is_not(None))
    if exclude_module:
        q = q.where(PrinterMibModule.module_name != exclude_module)
    return {n: o for n, o in db.execute(q)}


def install_mib(db: Session, filename: str, text: str, actor: str) -> PrinterMibModule:
    import hashlib
    parsed = mib_parser.parse(text)
    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    same = db.scalar(select(PrinterMibModule).where(PrinterMibModule.module_name == parsed["module"],
                                                    PrinterMibModule.sha256 == digest, PrinterMibModule.active.is_(True)))
    if same:
        return same
    prev = list(db.scalars(select(PrinterMibModule).where(PrinterMibModule.module_name == parsed["module"])))
    for p in prev:
        p.active, p.status = False, "SUPERSEDED"
    db.flush()
    res = mib_parser.resolve(parsed, known_mib_names(db, exclude_module=parsed["module"]))
    mod = PrinterMibModule(module_name=parsed["module"], version=max([p.version for p in prev], default=0) + 1,
                           filename=filename[:200], sha256=digest, source_text=text, status=res["status"],
                           imports=parsed["imports"], unresolved=res["unresolved"], object_count=len(parsed["objects"]),
                           enterprise=parsed["enterprise"], uploaded_by=actor)
    db.add(mod)
    db.flush()
    for o in parsed["objects"]:
        db.add(PrinterMibObject(module_id=mod.id, name=o["name"][:120], oid=o.get("oid"), parent=(o["parent"] or "")[:120] or None,
                                kind=o["kind"], syntax=o["syntax"], access=o["access"], units=o["units"],
                                description=o["description"], suggested_metric=o.get("suggested_metric")))
    db.flush()
    reresolve_mibs(db)
    return mod


def reresolve_mibs(db: Session) -> int:
    """After a new module arrives, modules waiting on its names may now resolve."""
    fixed = 0
    for mod in db.scalars(select(PrinterMibModule).where(PrinterMibModule.active.is_(True),
                                                         PrinterMibModule.status == "UNRESOLVED_IMPORTS")):
        parsed = mib_parser.parse(mod.source_text)
        res = mib_parser.resolve(parsed, known_mib_names(db, exclude_module=mod.module_name))
        rows = {o.name: o for o in db.scalars(select(PrinterMibObject).where(PrinterMibObject.module_id == mod.id))}
        for o in parsed["objects"]:
            r = rows.get(o["name"][:120])
            if r is not None:
                r.oid, r.kind, r.suggested_metric = o.get("oid"), o["kind"], o.get("suggested_metric")
        mod.status, mod.unresolved = res["status"], res["unresolved"]
        fixed += res["status"] == "RESOLVED"
    db.flush()
    return fixed


# ---- monitoring rows / identity ---------------------------------------------------------------------
def monitoring(db: Session, printer_id: str) -> PrinterMonitoring:
    m = db.get(PrinterMonitoring, printer_id)
    if m is None:
        m = PrinterMonitoring(printer_id=printer_id, enabled=False, interval_seconds=900, consecutive_failures=0,
                              total_polls=0, successful_polls=0, reachability="UNKNOWN", snmp_state="UNKNOWN",
                              operational_state="UNKNOWN", discovery_confidence="UNKNOWN", profile_selection="AUTO")
        db.add(m)
        db.flush()
    return m


def identity(db: Session, printer_id: str) -> PrinterIdentity:
    i = db.get(PrinterIdentity, printer_id)
    if i is None:
        i = PrinterIdentity(printer_id=printer_id, identity_state="UNVERIFIED")
        db.add(i)
        db.flush()
    return i


def _credentials(db: Session, profile_id: str | None):
    if not profile_id:
        return None
    row = db.get(AppSetting, PROFILE_PREFIX + profile_id)
    if row is None:
        return None
    from ..security.crypto import decrypt_secret
    return row.value["version"], json.loads(decrypt_secret(row.value["encrypted"]))


def make_reader(db: Session, host: str | None, credential_profile_id: str | None) -> C.SnmpReader:
    if not host:
        raise LookupError("printer has no management IP")
    if emulation_enabled() and host in EMULATORS:
        return EMULATORS[host]
    creds = _credentials(db, credential_profile_id)
    if creds is None:
        raise LookupError("no usable SNMP credential profile assigned")
    version, secrets = creds
    return C.PysnmpReader(host, version, *secrets)


# ---- applying a poll --------------------------------------------------------------------------------
def _period(db: Session, printer_id: str, dimension: str, state: str, at: datetime, evidence: dict | None = None):
    cur = db.scalar(select(PrinterStatusPeriod).where(PrinterStatusPeriod.printer_id == printer_id,
                                                      PrinterStatusPeriod.dimension == dimension,
                                                      PrinterStatusPeriod.ended_at.is_(None)))
    if cur is not None and cur.state == state:
        return
    if cur is not None:
        cur.ended_at = at
    db.add(PrinterStatusPeriod(printer_id=printer_id, dimension=dimension, state=state, started_at=at, evidence=evidence))


def _reconcile_identity(db: Session, printer: PrinterDevice, ident: PrinterIdentity, idres: dict, at: datetime) -> bool:
    """Returns True when the physical identity differs (serial mismatch) — counters must not be joined."""
    conflict = False
    reported_serial = idres.get("serial_number")
    if reported_serial:
        ident.reported_serial = reported_serial
        if reported_serial.casefold() != (printer.serial_number or "").casefold():
            conflict = True
            if ident.identity_state != "CONFLICT":
                db.add(PrinterIdentityChange(printer_id=printer.id, field="serial_number", old_value=printer.serial_number,
                                             new_value=reported_serial, decision="NEEDS_REVIEW", observed_at=at,
                                             reason="Device at this address reports a different serial; possible replacement or re-addressed printer. Kept separate."))
            ident.identity_state = "CONFLICT"
        else:
            ident.identity_state = "VERIFIED"
    for field in ("firmware", "hostname"):
        new = idres.get(field)
        old = getattr(printer, field)
        if new and new != old and not conflict:
            db.add(PrinterIdentityChange(printer_id=printer.id, field=field, old_value=old, new_value=new, decision="APPLIED",
                                         observed_at=at, reason="Reported by the verified device (serial matched)"))
            setattr(printer, field, new[:120 if field == "hostname" else 80])
    exact = idres.get("model")
    if exact and exact.casefold() != (printer.model or "").casefold() and idres.get("model_confidence") == "HIGH":
        last = db.scalar(select(PrinterIdentityChange).where(PrinterIdentityChange.printer_id == printer.id,
                                                             PrinterIdentityChange.field == "model")
                         .order_by(PrinterIdentityChange.id.desc()).limit(1))
        if last is None or last.new_value != exact:
            db.add(PrinterIdentityChange(printer_id=printer.id, field="model", old_value=printer.model, new_value=exact,
                                         decision="NEEDS_REVIEW", observed_at=at,
                                         reason="Device reports a different exact model; registered text preserved until reviewed"))
    ident.reported_model = (idres.get("reported_model") or "")[:200] or ident.reported_model
    ident.system_object_id = idres.get("system_object_id") or ident.system_object_id
    ident.system_description = idres.get("system_description") or ident.system_description
    fam = mib.model_family(exact or printer.model)
    for k_ident, k_fam in (("technology", "technology"), ("is_colour", "colour"), ("is_multifunction", "multifunction")):
        if getattr(ident, k_ident) is None and fam.get(k_fam) is not None:
            setattr(ident, k_ident, fam[k_fam])
    ident.identity_evidence = {"manufacturer_from_oid": idres.get("manufacturer"), "model_reason": idres.get("model_reason"),
                               "mac": idres.get("mac_address"), "at": at.isoformat()}
    ident.updated_at = at
    return conflict


def apply_poll(db: Session, printer: PrinterDevice, res: C.PollResult, *, poll_id: str | None = None) -> dict:
    mon = monitoring(db, printer.id)
    ident = identity(db, printer.id)
    at = res.observed_at
    mon.last_attempt_at = at
    mon.total_polls = (mon.total_polls or 0) + 1
    _period(db, printer.id, "REACHABILITY", "REACHABLE" if res.reachable else "UNREACHABLE", at)
    _period(db, printer.id, "SNMP", "AVAILABLE" if res.snmp_ok else "FAILED", at)
    mon.reachability = "REACHABLE" if res.reachable else "UNREACHABLE"
    mon.snmp_state = "AVAILABLE" if res.snmp_ok else "FAILED"
    summary: dict[str, Any] = {"reachable": res.reachable, "snmp_ok": res.snmp_ok, "samples": 0, "intervals": [],
                               "supply_events": [], "faults_opened": 0, "faults_closed": 0}
    if not res.snmp_ok:
        mon.consecutive_failures = (mon.consecutive_failures or 0) + 1
        mon.last_error = (res.error or "poll failed")[:255]
        backoff = min(MAX_BACKOFF_SECONDS, mon.interval_seconds * 2 ** min(mon.consecutive_failures - 1, 8))
        mon.backoff_until = at + timedelta(seconds=backoff)
        mon.next_poll_at = mon.backoff_until
        printer.status = "down" if not res.reachable else printer.status
        printer.last_checked_at = at
        _update_health(db, printer, mon, at)
        summary["backoff_seconds"] = backoff
        return summary

    mon.consecutive_failures, mon.last_error, mon.backoff_until = 0, res.error, None
    mon.last_success_at = at
    mon.successful_polls = (mon.successful_polls or 0) + 1
    mon.next_poll_at = at + timedelta(seconds=mon.interval_seconds)
    mon.unsupported_oids = res.unsupported_oids[:200]
    mon.discovery_confidence = ("HIGH" if res.identity.get("serial_number") and res.identity.get("model_confidence") == "HIGH"
                                else "MEDIUM" if res.identity.get("serial_number") or res.identity.get("model") else "LOW")
    if mon.profile_selection == "AUTO" and res.profile_key:
        prof = db.scalar(select(PrinterMibProfile).where(PrinterMibProfile.key == res.profile_key))
        mon.mib_profile_id = prof.id if prof else mon.mib_profile_id
    last = dict(mon.metric_last_polled or {})
    for s in res.samples:
        last[s.metric_key] = at.isoformat()
    mon.metric_last_polled = last
    conflict = _reconcile_identity(db, printer, ident, res.identity, at)
    printer.last_checked_at = at

    for s in res.samples:
        db.add(PrinterMetricSample(printer_id=printer.id, poll_id=poll_id, metric_key=s.metric_key, instance=s.instance[:32],
                                   oid=(s.oid or "")[:200] or None, raw_value=s.raw_value, normalized_value=s.normalized_value,
                                   text_value=s.text_value, unit=s.unit, value_state=s.value_state,
                                   interpretation=(s.interpretation or ("vendor mapping unverified" if s.verification == "VENDOR_UNVERIFIED" else None)),
                                   source=res.source, profile_key=res.profile_key, profile_version=res.profile_version,
                                   observed_at=at))
    summary["samples"] = len(res.samples)

    # meters → readings + validated intervals
    current_impressions = None
    for kind, m in res.meters.items():
        prev = db.scalar(select(PrinterMeterReading).where(PrinterMeterReading.printer_id == printer.id,
                                                           PrinterMeterReading.meter_kind == kind,
                                                           PrinterMeterReading.source != "MANUAL")
                         .order_by(PrinterMeterReading.observed_at.desc(), PrinterMeterReading.id.desc()).limit(1))
        db.add(PrinterMeterReading(printer_id=printer.id, meter_kind=kind, counter_value=m["value"], observed_at=at,
                                   source=res.source, quality="REPORTED" if m["verification"] == "STANDARD" else m["verification"],
                                   note=f"OID {m['oid']}"[:255]))
        if kind == "impressions_total":
            current_impressions = m["value"]
        if prev is not None:
            iv = U.derive_interval(prev.counter_value, prev.observed_at, m["value"], at, serial_changed=conflict)
            db.add(PrinterCounterInterval(printer_id=printer.id, meter_kind=kind, start_at=iv["start_at"], end_at=iv["end_at"],
                                          start_value=iv["start_value"], end_value=iv["end_value"], delta=iv["delta"],
                                          quality=iv["quality"], reason=iv["reason"], source=res.source))
            summary["intervals"].append({"meter_kind": kind, "delta": iv["delta"], "quality": iv["quality"]})
    if current_impressions is None and "sheets_total" in res.meters:
        current_impressions = None   # sheets are never relabelled impressions

    # supplies
    existing = {c.supply_index: c for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == printer.id))}
    for s in res.supplies:
        c = existing.get(s["supply_index"])
        before = c.percent_remaining if c else None
        if c is None:
            c = PrinterConsumable(printer_id=printer.id, supply_index=s["supply_index"], installed_at=None)
            db.add(c)
        prev_status = c.status
        for k in ("supply_type", "colour", "description", "unit", "max_capacity", "level", "level_state",
                  "percent_remaining", "status"):
            setattr(c, k, s[k])
        if s["part_number"] and not c.part_number:
            c.part_number = s["part_number"]
        c.last_observed_at = at
        if s["percent_remaining"] is not None:
            db.add(PrinterSupplyReading(printer_id=printer.id, supply_name=(s["description"] or s["supply_type"])[:100],
                                        colour=s["colour"] if s["colour"] in ("black", "cyan", "magenta", "yellow") else "other",
                                        percent_remaining=Decimal(str(s["percent_remaining"])),
                                        reported_remaining=s["level"], reported_capacity=s["max_capacity"],
                                        reported_unit=s["unit"], source=res.source, observed_at=at))
        rep = fleet.detect_replacement(before, s["percent_remaining"]) if not s["receptacle"] else None
        if rep:
            cost = _verified_cost(db, ident.model_id, s["supply_type"], s["colour"])
            db.add(PrinterSupplyEvent(printer_id=printer.id, supply_index=s["supply_index"], supply_type=s["supply_type"],
                                      colour=s["colour"], event_type=rep["event_type"], level_before=before,
                                      level_after=s["percent_remaining"], meter_value=current_impressions,
                                      status=rep["status"], observed_at=at,
                                      cost_amount=cost[0] if cost else None, currency=cost[1] if cost else "JMD",
                                      note=f"confirmation={rep['confirmation']}"))
            if rep["status"] == "CONFIRMED":
                c.installed_at, c.installed_meter = at, current_impressions
            summary["supply_events"].append(rep["event_type"])
        elif s["status"] in ("LOW", "EMPTY", "NEARLY_FULL", "FULL") and prev_status != s["status"]:
            db.add(PrinterSupplyEvent(printer_id=printer.id, supply_index=s["supply_index"], supply_type=s["supply_type"],
                                      colour=s["colour"], event_type=s["status"], level_before=before,
                                      level_after=s["percent_remaining"], meter_value=current_impressions, status="INFO",
                                      observed_at=at))
            summary["supply_events"].append(s["status"])

    # faults: open new, close cleared
    open_faults = {f.fault_code: f for f in db.scalars(select(PrinterFaultEvent).where(
        PrinterFaultEvent.printer_id == printer.id, PrinterFaultEvent.ended_at.is_(None)))}
    active = {f["code"]: f for f in res.faults}
    for code, f in active.items():
        if code not in open_faults:
            db.add(PrinterFaultEvent(printer_id=printer.id, fault_code=code[:48], category=f["category"], severity=f["severity"],
                                     description=f["description"][:255], started_at=at, source=res.source,
                                     raw={"source": f["source"]}))
            summary["faults_opened"] += 1
    for code, row in open_faults.items():
        if res.faults_observed and code not in active:
            row.ended_at = at
            summary["faults_closed"] += 1
    if res.operational_state != "UNKNOWN":
        mon.operational_state = res.operational_state
        _period(db, printer.id, "OPERATIONAL", res.operational_state, at,
                {"device_status": res.device_status, "faults": sorted(active)} if active else {"device_status": res.device_status})
    critical = any(f["severity"] == "critical" for f in res.faults) or any(
        row.ended_at is None and row.severity == "critical" for row in open_faults.values()
    )
    printer.status = "fault" if critical else "up"

    # capabilities
    caps = {c.capability: c for c in db.scalars(select(PrinterCapability).where(PrinterCapability.printer_id == printer.id))}
    for name, v in res.capabilities.items():
        row = caps.get(name)
        if row is None:
            db.add(PrinterCapability(printer_id=printer.id, capability=name, state=v["state"], evidence=v["evidence"], observed_at=at))
        else:
            row.state, row.evidence, row.observed_at = v["state"], v["evidence"], at
    printer.capabilities = {k: v["state"] for k, v in res.capabilities.items()} | {"snmp": "VERIFIED_READ_ONLY"}
    db.flush()
    _update_health(db, printer, mon, at)
    return summary


def _verified_cost(db: Session, model_id: str | None, supply_type: str, colour: str):
    if not model_id:
        return None
    row = db.scalar(select(PrinterModelCost).where(PrinterModelCost.model_id == model_id, PrinterModelCost.supply_type == supply_type,
                                                   PrinterModelCost.colour == colour, PrinterModelCost.verified.is_(True))
                    .order_by(PrinterModelCost.effective_from.desc()).limit(1))
    return (row.unit_cost, row.currency) if row else None


def availability(db: Session, printer_id: str, since: datetime, until: datetime) -> float | None:
    rows = list(db.scalars(select(PrinterStatusPeriod).where(
        PrinterStatusPeriod.printer_id == printer_id, PrinterStatusPeriod.dimension == "REACHABILITY",
        or_(PrinterStatusPeriod.ended_at.is_(None), PrinterStatusPeriod.ended_at >= since),
        PrinterStatusPeriod.started_at <= until)))
    up = total = 0.0
    for r in rows:
        s, e = max(_utc(r.started_at), since), min(_utc(r.ended_at) if r.ended_at else until, until)
        d = (e - s).total_seconds()
        if d > 0:
            total += d
            up += d if r.state == "REACHABLE" else 0
    return round(up / total, 4) if total else None


def _update_health(db: Session, printer: PrinterDevice, mon: PrinterMonitoring, at: datetime) -> None:
    since = at - timedelta(days=7)
    active = [{"severity": f.severity, "code": f.fault_code} for f in db.scalars(select(PrinterFaultEvent).where(
        PrinterFaultEvent.printer_id == printer.id, PrinterFaultEvent.ended_at.is_(None)))]
    recent = db.execute(select(PrinterFaultEvent.fault_code, func.count()).where(
        PrinterFaultEvent.printer_id == printer.id, PrinterFaultEvent.started_at >= since)
        .group_by(PrinterFaultEvent.fault_code)).all()
    supplies = [{"status": c.status} for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == printer.id))]
    h = fleet.health_score(availability=availability(db, printer.id, since, at),
                           snmp_success=(mon.successful_polls / mon.total_polls) if mon.total_polls else None,
                           active_faults=active, supplies=supplies, repeated_faults=sum(1 for _, n in recent if n >= 3),
                           last_success_at=mon.last_success_at, now=at,
                           stale_after_hours=max(2.0, 3 * mon.interval_seconds / 3600))
    mon.health_score, mon.health_detail = h["score"], h


# ---- scheduling (leased, fair, isolated) ------------------------------------------------------------
def due_metrics(mon: PrinterMonitoring, profile: dict | None, at: datetime) -> set[str] | None:
    if profile is None:
        return None
    last = mon.metric_last_polled or {}
    due = set()
    for m in profile["metrics"]:
        iv = m.get("interval_seconds") or mon.interval_seconds
        t = last.get(m["metric_key"])
        if t is None or (at - _utc(datetime.fromisoformat(t))).total_seconds() >= iv - 30:
            due.add(m["metric_key"])
    return due


def claim_due(db: Session, owner: str, limit: int, at: datetime | None = None) -> list[str]:
    at = at or now_utc()
    rows = list(db.scalars(select(PrinterMonitoring.printer_id).where(
        PrinterMonitoring.enabled.is_(True),
        or_(PrinterMonitoring.next_poll_at.is_(None), PrinterMonitoring.next_poll_at <= at),
        or_(PrinterMonitoring.lease_expires_at.is_(None), PrinterMonitoring.lease_expires_at < at))
        .order_by(PrinterMonitoring.next_poll_at.is_not(None), PrinterMonitoring.next_poll_at).limit(limit)))
    claimed = []
    for pid in rows:   # compare-and-set so two workers can never claim the same printer
        r = db.execute(update(PrinterMonitoring).where(
            PrinterMonitoring.printer_id == pid,
            or_(PrinterMonitoring.lease_expires_at.is_(None), PrinterMonitoring.lease_expires_at < at))
            .values(lease_owner=owner, lease_expires_at=at + timedelta(seconds=LEASE_SECONDS)))
        if r.rowcount == 1:
            claimed.append(pid)
    db.commit()
    return claimed


async def poll_printer(session_factory: Callable, printer_id: str, owner: str | None = None, *,
                       force_all_metrics: bool = False) -> dict:
    """Poll one printer in its own transaction(s). Exceptions never escape (failure isolation)."""
    with session_factory() as db:
        p = db.get(PrinterDevice, printer_id)
        if p is None:
            return {"printer_id": printer_id, "error": "not found"}
        mon = monitoring(db, printer_id)
        profiles = profile_dicts(db)
        chosen = next((x for x in profiles if x["id"] == mon.mib_profile_id), None) if mon.profile_selection == "MANUAL" else None
        host, cred = p.management_ip, mon.credential_profile_id
        auto = next((x for x in profiles if x["id"] == mon.mib_profile_id), None)
        metric_filter = None if force_all_metrics else due_metrics(mon, auto, now_utc())
        try:
            reader = make_reader(db, host, cred)
        except (LookupError, ValueError) as exc:
            reader = None
            err = str(exc)
        db.commit()
    if reader is None:
        res = C.PollResult(observed_at=now_utc(), source="NONE", reachable=False, snmp_ok=False, error=err)
    else:
        try:
            res = await asyncio.wait_for(C.poll(reader, [chosen] if chosen else profiles, metric_filter=metric_filter,
                                                 now=now_utc()), timeout=60)
        except asyncio.TimeoutError:
            res = C.PollResult(observed_at=now_utc(), source=reader.source, reachable=False, snmp_ok=False, error="poll timed out")
        except Exception as exc:  # noqa: BLE001 - isolate one device's failure
            res = C.PollResult(observed_at=now_utc(), source=getattr(reader, "source", "SNMP"), reachable=False, snmp_ok=False,
                               error=f"poll failed ({type(exc).__name__})")
        finally:
            try:
                await reader.close()
            except Exception:  # noqa: BLE001
                pass
    import uuid
    with session_factory() as db:
        p = db.get(PrinterDevice, printer_id)
        summary = apply_poll(db, p, res, poll_id=str(uuid.uuid4()))
        mon = monitoring(db, printer_id)
        if owner and mon.lease_owner == owner:
            mon.lease_owner, mon.lease_expires_at = None, None
        db.commit()
    return {"printer_id": printer_id, **summary, "error": res.error}


async def run_due(session_factory: Callable, owner: str, *, limit: int = 200, concurrency: int = 32) -> dict:
    with session_factory() as db:
        ids = claim_due(db, owner, limit)
    sem = asyncio.Semaphore(concurrency)

    async def one(pid):
        async with sem:
            try:
                return await poll_printer(session_factory, pid, owner)
            except Exception as exc:  # noqa: BLE001
                log.warning("printer poll %s failed: %s", pid, type(exc).__name__)
                return {"printer_id": pid, "error": type(exc).__name__}
    results = await asyncio.gather(*(one(p) for p in ids))
    return {"claimed": len(ids), "succeeded": sum(1 for r in results if r.get("snmp_ok")),
            "failed": sum(1 for r in results if not r.get("snmp_ok"))}


def operations_metrics(db: Session, at: datetime | None = None) -> dict:
    at = at or now_utc()
    mons = list(db.scalars(select(PrinterMonitoring).where(PrinterMonitoring.enabled.is_(True))))
    due = sum(1 for m in mons if m.next_poll_at is None or _utc(m.next_poll_at) <= at)
    fresh = stale = never = 0
    for m in mons:
        if m.last_success_at is None:
            never += 1
        elif (at - _utc(m.last_success_at)).total_seconds() <= 3 * m.interval_seconds:
            fresh += 1
        else:
            stale += 1
    total = sum(m.total_polls or 0 for m in mons)
    ok = sum(m.successful_polls or 0 for m in mons)
    return {"monitored": len(mons), "queue_depth": due, "leased": sum(1 for m in mons if m.lease_expires_at and _utc(m.lease_expires_at) > at),
            "fresh": fresh, "stale": stale, "never_polled": never, "in_backoff": sum(1 for m in mons if m.backoff_until and _utc(m.backoff_until) > at),
            "poll_success_rate": round(ok / total, 4) if total else None,
            "worker_errors": [{"printer_id": m.printer_id, "error": m.last_error, "consecutive_failures": m.consecutive_failures}
                              for m in mons if m.consecutive_failures][:50],
            "jobs": {s: n for s, n in db.execute(select(PrinterJob.status, func.count()).group_by(PrinterJob.status))}}


# ---- utilization queries ----------------------------------------------------------------------------
def intervals_for(db: Session, printer_id: str, meter: str, start: datetime, end: datetime) -> list[dict]:
    rows = db.scalars(select(PrinterCounterInterval).where(
        PrinterCounterInterval.printer_id == printer_id, PrinterCounterInterval.meter_kind == meter,
        PrinterCounterInterval.end_at > start, PrinterCounterInterval.start_at < end).order_by(PrinterCounterInterval.start_at))
    return [{"start_at": _utc(r.start_at), "end_at": _utc(r.end_at), "delta": r.delta, "quality": r.quality, "reason": r.reason}
            for r in rows]


def preferred_meter(db: Session, printer_id: str) -> str | None:
    kinds = {k for (k,) in db.execute(select(PrinterCounterInterval.meter_kind).where(
        PrinterCounterInterval.printer_id == printer_id).distinct())}
    for k in ("impressions_total", "sheets_total", "bw_impressions"):
        if k in kinds:
            return k
    return None


def utilization(db: Session, printer_id: str, start: datetime, end: datetime, granularity: str,
                meter: str | None = None) -> dict:
    meter = meter or preferred_meter(db, printer_id)
    if meter is None:
        return {"meter_kind": None, "status": "NO_COUNTER_HISTORY", "buckets": [], "summary": U.period_summary([]),
                "reason": "No validated counter intervals yet (needs two successful polls)"}
    ivs = intervals_for(db, printer_id, meter, start, end)
    if not ivs and (end - start).days > 400:
        ivs = []
    buckets = U.aggregate(ivs, start, end, granularity)
    quality = {}
    for iv in ivs:
        quality[iv["quality"]] = quality.get(iv["quality"], 0) + 1
    return {"meter_kind": meter, "granularity": granularity, "period": {"start": start.isoformat(), "end": end.isoformat()},
            "buckets": buckets, "summary": U.period_summary(buckets), "interval_quality": quality,
            "attribution": "Sampled counter deltas spread over polling intervals by time; not exact job timestamps."}


def monthly_volumes(db: Session, printer_id: str, months: int = 12, at: datetime | None = None) -> list[dict]:
    at = at or now_utc()
    start = U.bucket_start(at - timedelta(days=31 * months), "month")
    meter = preferred_meter(db, printer_id)
    if meter is None:
        return []
    return U.aggregate(intervals_for(db, printer_id, meter, start, at), start, at, "month")


def printer_model(db: Session, printer_id: str) -> PrinterModel | None:
    i = db.get(PrinterIdentity, printer_id)
    return db.get(PrinterModel, i.model_id) if i and i.model_id else None


def ensure_model(db: Session, manufacturer: str, model: str) -> PrinterModel:
    m = db.scalar(select(PrinterModel).where(func.lower(PrinterModel.manufacturer) == manufacturer.lower(),
                                             func.lower(PrinterModel.model) == model.lower()))
    if m is None:
        fam = mib.model_family(model)
        m = PrinterModel(manufacturer=manufacturer, model=model, technology=fam["technology"] or "unknown",
                         colour_capable=fam["colour"], multifunction=fam["multifunction"], verification="UNVERIFIED")
        db.add(m)
        db.flush()
    return m


def utilization_insights(db: Session, printer_id: str, at: datetime | None = None) -> dict:
    at = at or now_utc()
    model = printer_model(db, printer_id)
    monthly = monthly_volumes(db, printer_id, 12, at)
    meter = preferred_meter(db, printer_id)
    daily_start = at - timedelta(days=90)
    daily = U.aggregate(intervals_for(db, printer_id, meter, daily_start, at), daily_start, at, "day") if meter else []
    rec = model.recommended_monthly_volume if model and model.verification == "VERIFIED" else None
    mx = model.max_monthly_duty_cycle if model and model.verification == "VERIFIED" else None
    return {"duty_cycle": U.duty_cycle_assessment(monthly, rec, mx), "anomalies": U.robust_anomalies(daily),
            "daily_coverage": U.period_summary(daily), "model_verified": bool(model and model.verification == "VERIFIED")}


# ---- supplies / faults ------------------------------------------------------------------------------
def supply_forecasts(db: Session, printer_id: str, at: datetime | None = None) -> list[dict]:
    out = []
    for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == printer_id)):
        label = (c.description or c.supply_type)[:100]
        pts = [(r.observed_at, float(r.percent_remaining)) for r in db.scalars(
            select(PrinterSupplyReading).where(PrinterSupplyReading.printer_id == printer_id,
                                               PrinterSupplyReading.supply_name == label,
                                               PrinterSupplyReading.percent_remaining.is_not(None),
                                               PrinterSupplyReading.observed_at >= (at or now_utc()) - timedelta(days=120)))]
        f = fleet.forecast_with_uncertainty(pts, now=at) if c.supply_type not in ("waste_toner", "waste_ink") else {
            "status": "NOT_APPLICABLE", "days_to_empty": None, "reason": "receptacle (fills up)"}
        out.append({"supply_index": c.supply_index, "supply": label, "supply_type": c.supply_type, "colour": c.colour} | f)
    return out


def supply_events(db: Session, printer_id: str) -> list[dict]:
    return [{"id": e.id, "supply_index": e.supply_index, "supply_type": e.supply_type, "colour": e.colour,
             "event_type": e.event_type, "status": e.status, "level_before": e.level_before, "level_after": e.level_after,
             "meter_value": e.meter_value, "cost_amount": float(e.cost_amount) if e.cost_amount is not None else None,
             "currency": e.currency, "confirmed_by": e.confirmed_by, "note": e.note, "observed_at": iso(e.observed_at)}
            for e in db.scalars(select(PrinterSupplyEvent).where(PrinterSupplyEvent.printer_id == printer_id)
                                .order_by(PrinterSupplyEvent.observed_at.desc()).limit(500))]


def recurring_faults(db: Session, printer_id: str, days: int = 30, at: datetime | None = None) -> dict:
    since = (at or now_utc()) - timedelta(days=days)
    return {c: n for c, n in db.execute(select(PrinterFaultEvent.fault_code, func.count()).where(
        PrinterFaultEvent.printer_id == printer_id, PrinterFaultEvent.started_at >= since).group_by(PrinterFaultEvent.fault_code))}


def maintenance_dicts(db: Session, printer_ids: list[str] | None = None) -> list[dict]:
    q = select(PrinterMaintenanceEvent, PrinterMaintenanceDetail).outerjoin(
        PrinterMaintenanceDetail, PrinterMaintenanceDetail.event_id == PrinterMaintenanceEvent.id)
    if printer_ids is not None:
        q = q.where(PrinterMaintenanceEvent.printer_id.in_(printer_ids))
    out = []
    for e, d in db.execute(q.order_by(PrinterMaintenanceEvent.observed_at.desc())):
        out.append({"id": e.id, "printer_id": e.printer_id, "component": e.component, "event_type": e.event_type,
                    "severity": e.severity, "fault_code": e.fault_code, "description": e.description,
                    "cost_amount": float(e.cost_amount) if e.cost_amount is not None else None, "currency": e.currency,
                    "observed_at": iso(e.observed_at), "source": e.source, "logged_by": e.logged_by,
                    "incident_type": d.incident_type if d else None, "cause_category": d.cause_category if d else "UNKNOWN",
                    "qualifying_failure": bool(d and d.qualifying_failure), "reported_at": iso(d.reported_at) if d else None,
                    "restored_at": iso(d.restored_at) if d else None, "parts": d.parts if d else None,
                    "technician": d.technician if d else None, "vendor": d.vendor if d else None,
                    "warranty": d.warranty if d else None, "meter_at_event": d.meter_at_event if d else None,
                    "service_ticket_ref": d.service_ticket_ref if d else None})
    return out


def printer_reliability(db: Session, printer_ids: list[str], start: datetime, end: datetime, window: dict | None = None) -> dict:
    recs = maintenance_dicts(db, printer_ids)
    impressions = 0.0
    for pid in printer_ids:
        meter = preferred_meter(db, pid)
        if meter:
            s = U.period_summary(U.aggregate(intervals_for(db, pid, meter, start, end), start, end, "month"))
            impressions += s["total"] or 0
    return fleet.reliability(recs, period_start=start, period_end=end, impressions=impressions or None,
                             devices=len(printer_ids), window=window)


def get_window(db: Session) -> dict | None:
    row = db.get(AppSetting, "printer_operating_window")
    return row.value if row else None


# ---- fleet: benchmarking / recommendations ----------------------------------------------------------
def fleet_rows(db: Session, printers: list[PrinterDevice], start: datetime, end: datetime) -> list[dict]:
    rows = []
    window = get_window(db)
    for p in printers:
        ident = db.get(PrinterIdentity, p.id)
        model = printer_model(db, p.id)
        monthly = [m for m in monthly_volumes(db, p.id, 12, end) if m["full_bucket_coverage"] >= 0.6]
        vols = [m["estimated_full_volume"] for m in monthly]
        events = supply_events(db, p.id)
        yields = fleet.measured_yields([e | {"observed_at": e["observed_at"]} for e in events])
        rel = printer_reliability(db, [p.id], start, end, window)
        maint_cost = sum((r["cost_amount"] or 0) for r in maintenance_dicts(db, [p.id]))
        cons_cost = sum((y["cost_amount"] or 0) for y in yields)
        rows.append({"printer_id": p.id, "manufacturer": p.manufacturer, "model": model.model if model else p.model,
                     "segment": {"colour": ident.is_colour if ident else None, "multifunction": ident.is_multifunction if ident else None,
                                 "technology": ident.technology if ident else None},
                     "monthly_volumes": vols, "recommended_monthly": model.recommended_monthly_volume if model and model.verification == "VERIFIED" else None,
                     "yields": [y["measured_yield"] for y in yields], "cost_per_1000": [y["cost_per_1000"] for y in yields if y["cost_per_1000"] is not None],
                     "incidents": rel["qualifying_failures"], "mtbf_hours": rel["mtbf_hours"], "mttr_hours": rel["mttr_hours"],
                     "impressions": sum(vols) if vols else None, "maintenance_cost": maint_cost, "consumable_cost": cons_cost,
                     "cost_complete": bool(yields) and all(y["cost_amount"] is not None for y in yields)})
    return rows


def compare_models(db: Session, printers: list[PrinterDevice], start: datetime, end: datetime) -> list[dict]:
    return fleet.benchmark(fleet_rows(db, printers, start, end))


def sync_recommendations(db: Session, printers: list[PrinterDevice], actor: str, at: datetime | None = None) -> dict:
    at = at or now_utc()
    start = at - timedelta(days=365)
    inputs = []
    for p in printers:
        ident = db.get(PrinterIdentity, p.id)
        ui = utilization_insights(db, p.id, at)
        caps = {c.capability: c.state for c in db.scalars(select(PrinterCapability).where(PrinterCapability.printer_id == p.id))}
        share, cmonths = None, 0
        if caps.get("mono_colour_split") == "SUPPORTED":
            cstart = at - timedelta(days=93)
            col = U.period_summary(U.aggregate(intervals_for(db, p.id, "colour_impressions", cstart, at), cstart, at, "month"))
            bw = U.period_summary(U.aggregate(intervals_for(db, p.id, "bw_impressions", cstart, at), cstart, at, "month"))
            if col["total"] is not None and bw["total"] and (col["total"] + bw["total"]) > 0:
                share, cmonths = col["total"] / (col["total"] + bw["total"]), min(col["covered_buckets"], bw["covered_buckets"])
        inputs.append({"id": p.id, "name": p.hostname or f"{p.manufacturer} {p.model} {p.serial_number}",
                       "site": (ident.site_id if ident and ident.site_id else None) or p.station or p.building,
                       "model": p.model, "duty": ui["duty_cycle"], "colour_share": share, "colour_months": cmonths,
                       "colour_capability": caps.get("colour_printing"), "forecasts": supply_forecasts(db, p.id, at),
                       "recurring_faults": recurring_faults(db, p.id, 30, at)})
    cohorts = compare_models(db, printers, start, at)
    recs = fleet.recommend(inputs, cohorts, period=(start, at))
    seen, created, updated = set(), 0, 0
    for r in recs:
        seen.add(r["dedupe_key"])
        row = db.scalar(select(PrinterRecommendation).where(PrinterRecommendation.dedupe_key == r["dedupe_key"],
                                                            PrinterRecommendation.status.in_(("PROPOSED", "UNDER_REVIEW"))))
        if row is None:
            db.add(PrinterRecommendation(dedupe_key=r["dedupe_key"][:160], code=r["code"], title=r["title"][:200],
                                         printer_id=r["printer_id"], model=r["model"], reason_codes=r["reason_codes"],
                                         evidence=json.loads(json.dumps(r["evidence"], default=str)), assumptions=r["assumptions"],
                                         period_start=r["period_start"], period_end=r["period_end"], confidence=r["confidence"],
                                         rule_version=r["rule_version"], status="PROPOSED", created_at=at, last_seen_at=at))
            created += 1
        else:
            row.evidence = json.loads(json.dumps(r["evidence"], default=str))
            row.last_seen_at, row.confidence, row.title = at, r["confidence"], r["title"][:200]
            updated += 1
    superseded = 0
    scope_ids = {p.id for p in printers}
    for row in db.scalars(select(PrinterRecommendation).where(PrinterRecommendation.status == "PROPOSED")):
        if row.dedupe_key not in seen and (row.printer_id is None or row.printer_id in scope_ids):
            row.status, row.decision_note = "SUPERSEDED", "Condition no longer reproduced by the latest analysis"
            superseded += 1
    db.flush()
    return {"created": created, "updated": updated, "superseded": superseded, "rule_version": fleet.RULE_VERSION,
            "evaluated_printers": len(printers), "cohorts": len(cohorts)}


# ---- jobs ------------------------------------------------------------------------------------------
def create_job(db: Session, kind: str, params: dict, actor: str, total: int = 0) -> PrinterJob:
    j = PrinterJob(kind=kind, status="QUEUED", params=params, requested_by=actor, total=total, progress=0, errors=[])
    db.add(j)
    db.flush()
    return j


def claim_job(db: Session, job_id: str, owner: str) -> bool:
    at = now_utc()
    r = db.execute(update(PrinterJob).where(PrinterJob.id == job_id, PrinterJob.status.in_(("QUEUED", "RUNNING")),
                                            or_(PrinterJob.lease_expires_at.is_(None), PrinterJob.lease_expires_at < at))
                   .values(lease_owner=owner, lease_expires_at=at + timedelta(seconds=600), status="RUNNING",
                           started_at=func.coalesce(PrinterJob.started_at, at)))
    db.commit()
    return r.rowcount == 1


def _job_update(session_factory, job_id, **values):
    with session_factory() as db:
        db.execute(update(PrinterJob).where(PrinterJob.id == job_id).values(**values))
        db.commit()


def _cancelled(session_factory, job_id) -> bool:
    with session_factory() as db:
        return bool(db.scalar(select(PrinterJob.cancel_requested).where(PrinterJob.id == job_id)))


async def run_job(session_factory: Callable, job_id: str, owner: str) -> None:
    with session_factory() as db:
        if not claim_job(db, job_id, owner):
            return
        job = db.get(PrinterJob, job_id)
        kind, params = job.kind, dict(job.params)
    try:
        if kind == "DISCOVERY":
            await _run_discovery(session_factory, job_id, params)
        elif kind == "POLL":
            r = await poll_printer(session_factory, params["printer_id"], force_all_metrics=True)
            _job_update(session_factory, job_id, status="SUCCEEDED" if r.get("snmp_ok") else "FAILED", result=r,
                        progress=1, total=1, finished_at=now_utc(), errors=[r["error"]] if r.get("error") else [])
        elif kind == "WALK":
            await _run_walk(session_factory, job_id, params)
        elif kind == "REPORT":
            _run_report(session_factory, job_id, params)
        else:
            _job_update(session_factory, job_id, status="FAILED", errors=[f"unknown job kind {kind}"], finished_at=now_utc())
    except Exception as exc:  # noqa: BLE001
        log.warning("printer job %s failed: %s", job_id, type(exc).__name__)
        _job_update(session_factory, job_id, status="FAILED", errors=[f"{type(exc).__name__}: {str(exc)[:200]}"],
                    finished_at=now_utc())


async def _run_discovery(session_factory, job_id, params):
    hosts = params["hosts"]
    with session_factory() as db:
        profiles = profile_dicts(db)
        cred = params.get("credential_profile_id")
        db.commit()
    results, errors = [], []
    sem = asyncio.Semaphore(16)
    done = 0

    async def one(host):
        nonlocal done
        async with sem:
            if _cancelled(session_factory, job_id):
                return
            try:
                with session_factory() as db:
                    reader = make_reader(db, host, cred)
                r = await asyncio.wait_for(C.poll(reader, profiles, metric_filter={"printer_status", "marker_life_count", "marker_counter_unit", "supply_level"}, now=now_utc()), timeout=20)
                await reader.close()
                if not r.snmp_ok:
                    errors.append({"address": host, "error": r.error or "no SNMP response"})
                elif not (r.meters or r.supplies or any(s.metric_key == "printer_status" and s.value_state == "VALID" for s in r.samples)):
                    errors.append({"address": host, "error": "SNMP responded but no Printer-MIB data (not a printer)"})
                else:
                    with session_factory() as db:
                        match = db.scalar(select(PrinterDevice).where(func.lower(PrinterDevice.serial_number) ==
                                                                      (r.identity.get("serial_number") or "\x00").lower()))
                    results.append({"management_ip": host, **{k: r.identity.get(k) for k in (
                        "manufacturer", "model", "model_confidence", "reported_model", "serial_number", "hostname",
                        "system_object_id", "firmware", "mac_address")},
                        "profile": r.profile_key, "meters": r.meters, "supplies": len(r.supplies),
                        "confidence": "HIGH" if r.identity.get("serial_number") and r.identity.get("model_confidence") == "HIGH"
                        else "MEDIUM" if r.identity.get("serial_number") else "LOW",
                        "unsupported_oids": len(r.unsupported_oids), "source": r.source,
                        "existing_printer_id": match.id if match else None})
            except Exception as exc:  # noqa: BLE001
                errors.append({"address": host, "error": type(exc).__name__})
            done += 1
            if done % 8 == 0:
                _job_update(session_factory, job_id, progress=done)
    await asyncio.gather(*(one(h) for h in hosts))
    cancelled = _cancelled(session_factory, job_id)
    _job_update(session_factory, job_id, progress=done, result={"printers": results, "scanned": done},
                errors=errors[:2000], status="CANCELLED" if cancelled else "SUCCEEDED", finished_at=now_utc())


async def _run_walk(session_factory, job_id, params):
    with session_factory() as db:
        p = db.get(PrinterDevice, params["printer_id"])
        mon = monitoring(db, p.id)
        reader = make_reader(db, p.management_ip, mon.credential_profile_id)
        db.commit()
    try:
        rows = await asyncio.wait_for(C.diagnostic_walk(reader, params["root"], int(params.get("max_rows", 500))), timeout=120)
    finally:
        await reader.close()
    _job_update(session_factory, job_id, status="SUCCEEDED", result={"root": params["root"], "rows": rows, "count": len(rows)},
                progress=len(rows), total=len(rows), finished_at=now_utc())


def _run_report(session_factory, job_id, params):
    with session_factory() as db:
        cols, rows, meta = build_report(db, params["report"], params.get("filters") or {}, scope=params.get("scope"))
        data, mime, ext = export.render(params.get("format", "csv"), REPORTS[params["report"]]["title"], cols, rows, meta)
        name = f"{params['report']}-{now_utc().strftime('%Y%m%d-%H%M')}{ext}"
        db.execute(update(PrinterJob).where(PrinterJob.id == job_id).values(
            status="SUCCEEDED", artifact=base64.b64encode(data).decode(), artifact_name=name, artifact_type=mime,
            result={"rows": len(rows), "columns": cols, "meta": meta}, progress=len(rows), total=len(rows), finished_at=now_utc()))
        if params.get("recipients"):
            notify_report(db, job_id, params, name, len(rows))
        db.commit()


def notify_report(db: Session, job_id: str, params: dict, name: str, rows: int) -> int:
    """Scheduled delivery: a notification with the download location (files are never emailed raw)."""
    from ..database.models_remediation import NotificationChannel
    from ..infrastructure.notifications import _enqueue
    sent = 0
    for ch in db.scalars(select(NotificationChannel).where(NotificationChannel.id.in_(params["recipients"]),
                                                           NotificationChannel.enabled.is_(True))):
        payload = {"schema": "inm.printer.report/v1", "notification": "printer_report", "severity": "info",
                   "message": f"{REPORTS[params['report']]['title']} ready ({rows} rows)", "report": params["report"],
                   "format": params.get("format", "csv"), "job_id": job_id, "artifact": name,
                   "download_path": f"/api/printers/reports/jobs/{job_id}/download",
                   "delivery_key": f"printer-report:{job_id}:{ch.id}"}
        sent += _enqueue(db, ch, None, "printer_report", payload, now_utc(), f"printer-report:{job_id}:{ch.id}")
    return sent


async def tick_jobs(session_factory: Callable, owner: str, limit: int = 5) -> int:
    """Worker/scheduler recovery: run QUEUED jobs (and RUNNING ones whose lease expired after a restart)."""
    at = now_utc()
    with session_factory() as db:
        ids = list(db.scalars(select(PrinterJob.id).where(
            or_(PrinterJob.status == "QUEUED",
                and_(PrinterJob.status == "RUNNING", PrinterJob.lease_expires_at < at)))
            .order_by(PrinterJob.created_at).limit(limit)))
    for jid in ids:
        await run_job(session_factory, jid, owner)
    return len(ids)


def schedule_reports(db: Session, actor: str = "report-scheduler", at: datetime | None = None) -> list[str]:
    at = at or now_utc()
    created = []
    for s in db.scalars(select(PrinterReportSchedule).where(PrinterReportSchedule.enabled.is_(True),
                                                            PrinterReportSchedule.next_run_at <= at)):
        j = create_job(db, "REPORT", {"report": s.report, "format": s.format, "filters": s.filters, "schedule_id": s.id,
                                      "recipients": s.recipients}, actor)
        s.last_job_id = j.id
        step = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}.get(s.cadence)
        s.next_run_at = (_utc(s.next_run_at) + step) if step else U.next_bucket(U.bucket_start(at, "month"), "month")
        created.append(j.id)
    db.flush()
    return created


# ---- CSV import (PRN-001/002/008) -------------------------------------------------------------------
IMPORT_COLUMNS = ("manufacturer", "model", "serial_number", "management_ip", "hostname", "area", "division", "branch",
                  "station", "section_unit", "building", "site")


def preview_import(db: Session, text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    header = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames}
    missing = [c for c in ("manufacturer", "model", "serial_number") if c not in header]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    sites = {s.name.casefold(): s.id for s in db.scalars(select(Site))}
    seen: set[tuple[str, str]] = set()
    rows = []
    for n, raw in enumerate(reader, start=2):
        if n > 5001:
            raise ValueError("Import is limited to 5,000 rows")
        r = {k: (raw.get(header[k]) or "").strip() for k in IMPORT_COLUMNS if k in header}
        errs, warns = [], []
        for k in ("manufacturer", "model", "serial_number"):
            if not r.get(k):
                errs.append(f"{k} is required")
            elif any(ord(ch) < 32 for ch in r[k]) or len(r[k]) > (64 if k == "manufacturer" else 120 if k == "model" else 100):
                errs.append(f"{k} is invalid")
        if r.get("management_ip"):
            try:
                ip = IPv4Address(r["management_ip"])
                if not ip.is_private:
                    errs.append("management_ip must be a private IPv4 address")
            except ValueError:
                errs.append("management_ip is not a valid IPv4 address")
        key = ((r.get("manufacturer") or "").casefold(), (r.get("serial_number") or "").casefold())
        action = "CREATE"
        if key in seen:
            errs.append("duplicate manufacturer/serial within this file")
        seen.add(key)
        existing = db.scalar(select(PrinterDevice).where(func.lower(PrinterDevice.manufacturer) == key[0],
                                                         func.lower(PrinterDevice.serial_number) == key[1])) if all(key) else None
        if existing:
            action = "UPDATE" if existing.model.casefold() == (r.get("model") or "").casefold() else "CONFLICT"
            if action == "CONFLICT":
                errs.append("serial already registered with a different model; review before changing identity")
        site_id = None
        if r.get("site"):
            site_id = sites.get(r["site"].casefold())
            if site_id is None:
                warns.append(f"site '{r['site']}' not found; left unassigned")
        mi = mib.model_from_description(r.get("model"))
        if mi["model"] and mi["model"] != r.get("model"):
            warns.append(f"model text kept as imported; recognised exact model token '{mi['model']}'")
        elif not mi["model"]:
            warns.append("model text not recognised as a complete model; kept as imported, not expanded")
        rows.append({"row": n, "data": r, "site_id": site_id, "action": "ERROR" if errs else action,
                     "existing_printer_id": existing.id if existing else None, "errors": errs, "warnings": warns,
                     "original_text": json.dumps({k: raw.get(v) for k, v in header.items()})[:2000]})
    return {"rows": rows, "valid": sum(1 for r in rows if r["action"] in ("CREATE", "UPDATE")),
            "errors": sum(1 for r in rows if r["action"] == "ERROR"), "total": len(rows)}


def register_or_update(db: Session, data: dict, *, provenance: str, site_id: int | None = None,
                       original_text: str | None = None) -> tuple[PrinterDevice, bool]:
    obj = db.scalar(select(PrinterDevice).where(func.lower(PrinterDevice.manufacturer) == data["manufacturer"].lower(),
                                                func.lower(PrinterDevice.serial_number) == data["serial_number"].lower()))
    created = obj is None
    if created:
        obj = PrinterDevice(manufacturer=data["manufacturer"], model=data["model"], serial_number=data["serial_number"],
                            provenance=provenance, status="unknown",
                            capabilities={"snmpv3": "NOT_TESTED", "ipp": "NOT_TESTED"})
        db.add(obj)
    for f in ("hostname", "management_ip", "area", "division", "branch", "station", "section_unit", "building", "firmware"):
        if data.get(f):
            setattr(obj, f, data[f])
    db.flush()
    ident = identity(db, obj.id)
    if site_id is not None:
        ident.site_id = site_id
    if original_text and not ident.original_import_text:
        ident.original_import_text = original_text
    mi = mib.model_from_description(data["model"])
    if mi["model"] and mi["model"].casefold() == data["model"].casefold():
        ident.model_id = ensure_model(db, data["manufacturer"], mi["model"]).id
    elif ident.model_id is None:
        ident.model_id = ensure_model(db, data["manufacturer"], data["model"]).id
    fam = mib.model_family(data["model"])
    ident.technology = ident.technology or fam["technology"]
    ident.is_colour = ident.is_colour if ident.is_colour is not None else fam["colour"]
    ident.is_multifunction = ident.is_multifunction if ident.is_multifunction is not None else fam["multifunction"]
    monitoring(db, obj.id)
    return obj, created


def discovery_hosts(target: str | list[str]) -> list[str]:
    if isinstance(target, list):
        return list(dict.fromkeys(str(IPv4Address(h)) for h in target))
    net = IPv4Network(target, strict=False)
    if net.prefixlen < 22:
        raise ValueError("Use a /22 or smaller range per discovery job")
    return [str(h) for h in net.hosts()]


# ---- retention -------------------------------------------------------------------------------------
def retention_policy(db: Session) -> dict:
    row = db.get(AppSetting, RETENTION_KEY)
    return DEFAULT_RETENTION | (row.value if row else {})


def apply_retention(db: Session, at: datetime | None = None) -> dict:
    at = at or now_utc()
    pol = retention_policy(db)
    cutoff = at - timedelta(days=int(pol["raw_days"]))
    agg_cutoff = (at - timedelta(days=365 * int(pol["aggregate_years"]))).date().isoformat()
    rolled = 0
    old = list(db.execute(select(PrinterCounterInterval.printer_id, PrinterCounterInterval.meter_kind).where(
        PrinterCounterInterval.end_at < cutoff).distinct()))
    for pid, kind in old:
        first = db.scalar(select(func.min(PrinterCounterInterval.start_at)).where(
            PrinterCounterInterval.printer_id == pid, PrinterCounterInterval.meter_kind == kind))
        ivs = intervals_for(db, pid, kind, _utc(first), cutoff)
        for b in U.aggregate(ivs, _utc(first), cutoff, "day"):
            day = b["bucket_start"][:10]
            row = db.scalar(select(PrinterDailyAggregate).where(PrinterDailyAggregate.printer_id == pid,
                                                                PrinterDailyAggregate.meter_kind == kind,
                                                                PrinterDailyAggregate.day == day))
            if row is None:
                db.add(PrinterDailyAggregate(printer_id=pid, meter_kind=kind, day=day, volume=b["volume"],
                                             coverage=b["coverage"], quality=b["confidence"]))
                rolled += 1
    db.flush()
    n_samples = db.execute(delete(PrinterMetricSample).where(PrinterMetricSample.observed_at < cutoff)).rowcount
    n_intervals = db.execute(delete(PrinterCounterInterval).where(PrinterCounterInterval.end_at < cutoff)).rowcount
    n_agg = db.execute(delete(PrinterDailyAggregate).where(PrinterDailyAggregate.day < agg_cutoff)).rowcount
    return {"raw_cutoff": cutoff.isoformat(), "aggregate_cutoff": agg_cutoff, "daily_rows_rolled_up": rolled,
            "samples_deleted": n_samples, "intervals_deleted": n_intervals, "aggregates_deleted": n_agg}


# ---- reports ---------------------------------------------------------------------------------------
REPORTS = {
    "fleet_inventory": {"title": "Printer fleet inventory"},
    "daily_utilization": {"title": "Daily utilization"},
    "monthly_utilization": {"title": "Monthly utilization"},
    "consumable_efficiency": {"title": "Consumable efficiency"},
    "model_reliability": {"title": "Model reliability"},
    "high_utilization": {"title": "High utilization printers"},
    "low_utilization": {"title": "Low utilization printers"},
    "maintenance": {"title": "Maintenance history"},
    "standardization": {"title": "Standardization (model comparison)"},
    "executive_fleet": {"title": "Executive fleet summary"},
}


def filtered_printers(db: Session, filters: dict, scope: list[int] | None = None) -> list[PrinterDevice]:
    q = select(PrinterDevice).outerjoin(PrinterIdentity, PrinterIdentity.printer_id == PrinterDevice.id)
    if filters.get("manufacturer"):
        q = q.where(func.lower(PrinterDevice.manufacturer) == filters["manufacturer"].lower())
    if filters.get("model"):
        q = q.where(func.lower(PrinterDevice.model) == filters["model"].lower())
    if filters.get("site_id"):
        q = q.where(PrinterIdentity.site_id == int(filters["site_id"]))
    if filters.get("division"):
        q = q.where(or_(PrinterDevice.division == filters["division"], PrinterIdentity.division_ref == filters["division"]))
    if filters.get("location"):
        loc = f"%{filters['location']}%"
        q = q.where(or_(PrinterDevice.station.ilike(loc), PrinterDevice.building.ilike(loc), PrinterDevice.branch.ilike(loc),
                        PrinterDevice.area.ilike(loc), PrinterIdentity.location_ref.ilike(loc)))
    if filters.get("printer_id"):
        q = q.where(PrinterDevice.id == filters["printer_id"])
    if filters.get("category") in ("colour", "mono"):
        q = q.where(PrinterIdentity.is_colour.is_(filters["category"] == "colour"))
    if filters.get("category") in ("multifunction", "print_only"):
        q = q.where(PrinterIdentity.is_multifunction.is_(filters["category"] == "multifunction"))
    if scope is not None:
        q = q.where(PrinterIdentity.site_id.in_(scope))
    return list(db.scalars(q.order_by(PrinterDevice.manufacturer, PrinterDevice.model, PrinterDevice.serial_number)))


def _period_from(filters: dict, default_days: int = 30) -> tuple[datetime, datetime]:
    end = _utc(datetime.fromisoformat(filters["date_to"])) if filters.get("date_to") else now_utc()
    start = _utc(datetime.fromisoformat(filters["date_from"])) if filters.get("date_from") else end - timedelta(days=default_days)
    if end <= start:
        raise ValueError("date_to must be after date_from")
    if (end - start).days > 3660:
        raise ValueError("Reporting period is limited to 10 years")
    return start, end


def build_report(db: Session, key: str, filters: dict, scope: list[int] | None = None) -> tuple[list[str], list[dict], dict]:
    if key not in REPORTS:
        raise ValueError("unknown report")
    printers = filtered_printers(db, filters, scope)
    start, end = _period_from(filters, 365 if key in ("model_reliability", "standardization", "executive_fleet") else 30)
    meta = {"report": REPORTS[key]["title"], "period": f"{start.date()} to {end.date()}",
            "filters": json.dumps({k: v for k, v in filters.items() if v}), "printers_in_scope": len(printers),
            "basis": "Validated counter intervals; missing coverage is reported, never filled with zeros"}
    rows: list[dict] = []
    if key == "fleet_inventory":
        cols = ["printer_id", "manufacturer", "model", "serial_number", "hostname", "management_ip", "site", "location",
                "division", "status", "monitoring", "last_success", "health", "identity", "provenance"]
        site_names = {s.id: s.name for s in db.scalars(select(Site))}
        for p in printers:
            mon, ident = db.get(PrinterMonitoring, p.id), db.get(PrinterIdentity, p.id)
            rows.append({"printer_id": p.id, "manufacturer": p.manufacturer, "model": p.model, "serial_number": p.serial_number,
                         "hostname": p.hostname, "management_ip": p.management_ip,
                         "site": site_names.get(ident.site_id) if ident and ident.site_id else None,
                         "location": " / ".join(x for x in (p.area, p.branch, p.station, p.building) if x) or None,
                         "division": p.division, "status": p.status, "monitoring": "ON" if mon and mon.enabled else "OFF",
                         "last_success": iso(mon.last_success_at) if mon else None,
                         "health": mon.health_score if mon else None, "identity": ident.identity_state if ident else "UNVERIFIED",
                         "provenance": p.provenance})
    elif key in ("daily_utilization", "monthly_utilization", "high_utilization", "low_utilization"):
        gran = "day" if key == "daily_utilization" else "month"
        cols = ["printer_id", "printer", "model", "meter", "bucket", "volume", "coverage", "confidence", "measured_zero"]
        if key in ("high_utilization", "low_utilization"):
            cols = ["printer_id", "printer", "model", "duty_status", "mean_recommended_percent", "months_evaluated", "reason"]
        for p in printers:
            name = p.hostname or p.serial_number
            if key in ("high_utilization", "low_utilization"):
                d = utilization_insights(db, p.id, end)["duty_cycle"]
                want = "SUSTAINED_HIGH" if key == "high_utilization" else "SUSTAINED_LOW"
                if d["status"] == want:
                    rows.append({"printer_id": p.id, "printer": name, "model": p.model, "duty_status": d["status"],
                                 "mean_recommended_percent": d.get("mean_recommended_percent"),
                                 "months_evaluated": d.get("months_evaluated"), "reason": d.get("reason")})
                continue
            u = utilization(db, p.id, start, end, gran)
            for b in u["buckets"]:
                rows.append({"printer_id": p.id, "printer": name, "model": p.model, "meter": u["meter_kind"],
                             "bucket": b["bucket_start"][:10 if gran == "day" else 7], "volume": b["volume"],
                             "coverage": b["coverage"], "confidence": b["confidence"], "measured_zero": b["measured_zero"]})
        if key in ("daily_utilization", "monthly_utilization"):
            covered = [r["volume"] for r in rows if r["volume"] is not None]
            meta["reconciled_total"] = round(sum(covered), 2) if covered else None
            meta["missing_buckets"] = sum(1 for r in rows if r["volume"] is None)
    elif key == "consumable_efficiency":
        cols = ["printer_id", "printer", "model", "supply", "colour", "installed_at", "removed_at", "measured_yield",
                "cost_amount", "currency", "cost_per_1000", "basis"]
        for p in printers:
            for y in fleet.measured_yields(supply_events(db, p.id)):
                if start <= _utc(datetime.fromisoformat(y["removed_at"])) <= end:
                    rows.append({"printer_id": p.id, "printer": p.hostname or p.serial_number, "model": p.model,
                                 "supply": y["supply_type"], "colour": y["colour"], "installed_at": y["installed_at"][:10],
                                 "removed_at": y["removed_at"][:10], "measured_yield": y["measured_yield"],
                                 "cost_amount": y["cost_amount"], "currency": y["currency"], "cost_per_1000": y["cost_per_1000"],
                                 "basis": y["basis"]})
    elif key == "model_reliability":
        cols = ["manufacturer", "model", "devices", "operating_hours", "qualifying_failures", "excluded_non_mechanical",
                "mtbf_hours", "mttr_hours", "incidents_per_100k_impressions", "maintenance_events", "status"]
        groups: dict[tuple, list[str]] = {}
        for p in printers:
            groups.setdefault((p.manufacturer, p.model), []).append(p.id)
        for (manu, model), ids in sorted(groups.items()):
            r = printer_reliability(db, ids, start, end, get_window(db))
            rows.append({"manufacturer": manu, "model": model, "devices": len(ids)} | {k: r.get(k) for k in cols[3:]})
    elif key == "maintenance":
        cols = ["printer_id", "observed_at", "component", "event_type", "incident_type", "cause_category", "qualifying_failure",
                "reported_at", "restored_at", "technician", "vendor", "cost_amount", "currency", "warranty", "service_ticket_ref"]
        ids = {p.id for p in printers}
        for m in maintenance_dicts(db, list(ids)):
            if start <= _utc(datetime.fromisoformat(m["observed_at"])) <= end:
                rows.append({c: m.get(c) for c in cols})
    elif key == "standardization":
        cols = ["manufacturer", "model", "in_service", "workload_band", "median_monthly_volume_per_device",
                "median_recommended_utilization_percent", "median_cost_per_1000", "incidents_per_100k_impressions",
                "median_mtbf_hours", "median_mttr_hours", "tco_per_1000", "evidence", "confidence"]
        for c in compare_models(db, printers, start, end):
            rows.append({k: c.get(k) for k in cols})
    else:   # executive_fleet
        cols = ["metric", "value", "basis"]
        mons = [db.get(PrinterMonitoring, p.id) for p in printers]
        health = [m.health_score for m in mons if m and m.health_score is not None]
        vol_total = 0.0
        cov = []
        for p in printers:
            u = utilization(db, p.id, start, end, "month")
            vol_total += u["summary"]["total"] or 0
            cov.append(u["summary"]["mean_coverage"])
        recs = db.scalar(select(func.count()).select_from(PrinterRecommendation).where(
            PrinterRecommendation.status.in_(("PROPOSED", "UNDER_REVIEW")))) or 0
        open_faults = db.scalar(select(func.count()).select_from(PrinterFaultEvent).where(
            PrinterFaultEvent.ended_at.is_(None), PrinterFaultEvent.printer_id.in_([p.id for p in printers] or [""]))) or 0
        rows = [
            {"metric": "Printers in scope", "value": len(printers), "basis": "inventory"},
            {"metric": "Monitored (read-only SNMP)", "value": sum(1 for m in mons if m and m.enabled), "basis": "monitoring enabled"},
            {"metric": "Median health score", "value": round(statistics.median(health), 1) if health else None,
             "basis": f"{len(health)} printers with evidence"},
            {"metric": "Measured volume in period", "value": round(vol_total, 0), "basis": "validated counter intervals"},
            {"metric": "Mean measurement coverage", "value": round(sum(cov) / len(cov), 3) if cov else None, "basis": "share of period covered"},
            {"metric": "Open printer faults", "value": open_faults, "basis": "device-reported, not yet cleared"},
            {"metric": "Recommendations awaiting review", "value": recs, "basis": "rules " + fleet.RULE_VERSION},
        ]
    return cols, rows, meta
