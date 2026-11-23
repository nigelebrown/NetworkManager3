"""Printer Intelligence SRS API (registered before the P0 router so its routes take precedence).

Read endpoints use platform RBAC (viewer and above) with site scoping: a site-scoped user only sees
printers assigned to a granted site. Changes to monitoring, profiles, MIBs, catalogue and records are
admin-only; recommendation decisions need an approver or admin. Every change is audited. Monitoring
polls and diagnostic walks are read-only GET/GETNEXT jobs. Explicit verified configuration actions use a separate journalled service.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network
from typing import Literal

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response,
                     UploadFile)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, Site, User
from ..database.models_printer import PrinterDevice, PrinterMaintenanceEvent, PrinterMeterReading
from ..database.models_printer_srs import (
    PrinterCapability, PrinterConsumable, PrinterFaultEvent, PrinterIdentity, PrinterIdentityChange, PrinterJob,
    PrinterMaintenanceDetail, PrinterMetricDefinition, PrinterMetricSample, PrinterMibModule, PrinterMibObject,
    PrinterMibProfile, PrinterModel, PrinterModelCost, PrinterMonitoring, PrinterRecommendation,
    PrinterReportSchedule, PrinterStatusPeriod, PrinterSupplyEvent,
)
from ..database.session import get_db, session_scope
from ..printers import export, fleet, mib, mib_parser
from ..printers import service as S
from ..printers import utilization as U
from ..printers.snmp import permitted_target
from ..security.context import get_scope
from ..security.deps import ROLE_RANK, audit, require_any_role, require_role

router = APIRouter(prefix="/api/printers", tags=["printer-intelligence-srs"])
viewer = Depends(require_role("viewer"))
operator = Depends(require_role("operator"))
admin = Depends(require_role("admin"))
reviewer = Depends(require_any_role("approver", "admin"))
METRIC_KEYS = ("impressions_total", "sheets_total", "bw_impressions", "colour_impressions", "copy_impressions",
               "scan_count", "fax_impressions", "duplex_sheets", "supply_level_vendor", "serial_number", "firmware")


def _owner() -> str:
    import os
    import socket
    return f"api-{socket.gethostname()}-{os.getpid()}"


def _scope() -> list[int] | None:
    s = get_scope()
    return None if s is None else sorted(s)


def _visible(db: Session, printer_id: str) -> PrinterDevice:
    p = db.get(PrinterDevice, printer_id)
    if p is None:
        raise HTTPException(404, "Printer not found")
    scope = get_scope()
    if scope is not None:
        ident = db.get(PrinterIdentity, printer_id)
        if not ident or ident.site_id not in scope:
            raise HTTPException(404, "Printer not found")   # no existence oracle for out-of-scope printers
    return p


def _parse_dt(v: str | None, default: datetime) -> datetime:
    if not v:
        return default
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, f"Invalid date/time '{v}'") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _summary(db: Session, p: PrinterDevice, site_names: dict[int, str], now: datetime) -> dict:
    mon, ident = db.get(PrinterMonitoring, p.id), db.get(PrinterIdentity, p.id)
    fresh = None
    if mon and mon.last_success_at:
        age = (now - S._utc(mon.last_success_at)).total_seconds()
        fresh = "FRESH" if age <= 3 * mon.interval_seconds else "STALE"
    elif mon and mon.enabled:
        fresh = "NEVER_POLLED"
    return {"id": p.id, "manufacturer": p.manufacturer, "model": p.model, "serial_number": p.serial_number,
            "hostname": p.hostname, "management_ip": p.management_ip, "firmware": p.firmware, "status": p.status,
            "provenance": p.provenance, "area": p.area, "division": p.division, "branch": p.branch, "station": p.station,
            "section_unit": p.section_unit, "building": p.building, "capabilities": p.capabilities or {},
            "last_checked_at": S.iso(p.last_checked_at), "created_at": S.iso(p.created_at),
            "site_id": ident.site_id if ident else None, "site": site_names.get(ident.site_id) if ident and ident.site_id else None,
            "identity_state": ident.identity_state if ident else "UNVERIFIED",
            "monitoring": {"enabled": bool(mon and mon.enabled), "reachability": mon.reachability if mon else "UNKNOWN",
                           "snmp_state": mon.snmp_state if mon else "UNKNOWN",
                           "operational_state": mon.operational_state if mon else "UNKNOWN",
                           "health_score": mon.health_score if mon else None,
                           "health_status": (mon.health_detail or {}).get("status") if mon else None,
                           "last_success_at": S.iso(mon.last_success_at) if mon else None,
                           "freshness": fresh, "discovery_confidence": mon.discovery_confidence if mon else "UNKNOWN"}}


# ---- inventory ------------------------------------------------------------------------------------
@router.get("")
def list_printers(response: Response, manufacturer: str | None = None, model: str | None = None, site_id: int | None = None,
                  location: str | None = None, division: str | None = None, status: str | None = None,
                  monitored: bool | None = None, category: str | None = None, q: str | None = None,
                  limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0),
                  db: Session = Depends(get_db), user: User = viewer):
    printers = S.filtered_printers(db, {"manufacturer": manufacturer, "model": model, "site_id": site_id, "location": location,
                                        "division": division, "category": category}, _scope())
    if status:
        printers = [p for p in printers if p.status == status]
    if monitored is not None:
        enabled = {m for (m,) in db.execute(select(PrinterMonitoring.printer_id).where(PrinterMonitoring.enabled.is_(True)))}
        printers = [p for p in printers if (p.id in enabled) == monitored]
    if q:
        t = q.casefold()
        printers = [p for p in printers if any(t in (v or "").casefold() for v in (p.hostname, p.serial_number, p.model,
                                                                                   p.management_ip, p.manufacturer, p.station))]
    response.headers["X-Total-Count"] = str(len(printers))
    sites = {s.id: s.name for s in db.scalars(select(Site))}
    now = S.now_utc()
    return [_summary(db, p, sites, now) for p in printers[offset:offset + limit]]


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = viewer):
    printers = S.filtered_printers(db, {}, _scope())
    ids = [p.id for p in printers] or [""]
    mons = [m for m in (db.get(PrinterMonitoring, p.id) for p in printers) if m]
    health = [m.health_score for m in mons if m.health_score is not None]
    faults = db.execute(select(PrinterFaultEvent.category, PrinterFaultEvent.severity, func.count()).where(
        PrinterFaultEvent.printer_id.in_(ids), PrinterFaultEvent.ended_at.is_(None))
        .group_by(PrinterFaultEvent.category, PrinterFaultEvent.severity)).all()
    supplies = db.execute(select(PrinterConsumable.status, func.count()).where(PrinterConsumable.printer_id.in_(ids))
                          .group_by(PrinterConsumable.status)).all()
    recs = db.scalar(select(func.count()).select_from(PrinterRecommendation).where(
        PrinterRecommendation.status.in_(("PROPOSED", "UNDER_REVIEW")),
        or_(PrinterRecommendation.printer_id.is_(None), PrinterRecommendation.printer_id.in_(ids)))) or 0
    by_manu: dict[str, int] = {}
    for p in printers:
        by_manu[p.manufacturer] = by_manu.get(p.manufacturer, 0) + 1
    ops = S.operations_metrics(db)
    return {"total": len(printers), "monitored": sum(1 for m in mons if m.enabled),
            "reachable": sum(1 for m in mons if m.reachability == "REACHABLE"),
            "unreachable": sum(1 for m in mons if m.reachability == "UNREACHABLE"),
            "health": {"median": round(sorted(health)[len(health) // 2], 1) if health else None, "with_evidence": len(health),
                       "by_status": {s: sum(1 for m in mons if (m.health_detail or {}).get("status") == s)
                                     for s in ("HEALTHY", "DEGRADED", "CRITICAL", "STALE", "INSUFFICIENT_DATA")}},
            "open_faults": [{"category": c, "severity": s, "count": n} for c, s, n in faults],
            "supplies": {s: n for s, n in supplies}, "recommendations_pending": recs, "by_manufacturer": by_manu,
            "collection": {k: ops[k] for k in ("queue_depth", "fresh", "stale", "never_polled", "in_backoff", "poll_success_rate")},
            "data_status": "READ_ONLY_SNMP_AND_MANUAL", "device_writes": "LOCKED"}


@router.get("/live")
def live(db: Session = Depends(get_db), user: User = viewer):
    printers = S.filtered_printers(db, {}, _scope())
    sites = {s.id: s.name for s in db.scalars(select(Site))}
    now = S.now_utc()
    out = []
    for p in printers:
        mon = db.get(PrinterMonitoring, p.id)
        if not mon or not mon.enabled:
            continue
        row = _summary(db, p, sites, now)
        row["active_faults"] = [{"code": f.fault_code, "category": f.category, "severity": f.severity,
                                 "since": S.iso(f.started_at)} for f in db.scalars(select(PrinterFaultEvent).where(
                                     PrinterFaultEvent.printer_id == p.id, PrinterFaultEvent.ended_at.is_(None)))]
        row["supplies"] = [{"supply": c.description or c.supply_type, "colour": c.colour, "percent": c.percent_remaining,
                            "status": c.status, "level_state": c.level_state}
                           for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == p.id))]
        row["next_poll_at"] = S.iso(mon.next_poll_at)
        row["last_error"] = mon.last_error
        row["consecutive_failures"] = mon.consecutive_failures
        out.append(row)
    return {"printers": out, "operations": S.operations_metrics(db), "observed_at": now.isoformat()}


@router.get("/operations")
def operations(db: Session = Depends(get_db), user: User = operator):
    return S.operations_metrics(db)


# ---- models / benchmarking / recommendations / reports (static paths before /{printer_id}) ----------
class ModelInput(BaseModel):
    manufacturer: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=120)
    technology: Literal["laser", "inkjet", "unknown"] = "unknown"
    colour_capable: bool | None = None
    multifunction: bool | None = None
    duplex_capable: bool | None = None
    recommended_monthly_volume: int | None = Field(None, gt=0, le=10_000_000)
    max_monthly_duty_cycle: int | None = Field(None, gt=0, le=50_000_000)
    expected_useful_life_impressions: int | None = Field(None, gt=0)
    verification: Literal["VERIFIED", "UNVERIFIED"] = "UNVERIFIED"
    notes: str | None = Field(None, max_length=2000)


def _model_json(m: PrinterModel, db: Session) -> dict:
    n = db.scalar(select(func.count()).select_from(PrinterIdentity).where(PrinterIdentity.model_id == m.id)) or 0
    return {k: getattr(m, k) for k in ("id", "manufacturer", "model", "technology", "colour_capable", "multifunction",
                                       "duplex_capable", "recommended_monthly_volume", "max_monthly_duty_cycle",
                                       "expected_useful_life_impressions", "verification", "notes")} | {"in_service": n}


@router.get("/models")
def list_models(db: Session = Depends(get_db), user: User = viewer):
    return [_model_json(m, db) for m in db.scalars(select(PrinterModel).order_by(PrinterModel.manufacturer, PrinterModel.model))]


@router.post("/models", status_code=201)
def create_model(body: ModelInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    if db.scalar(select(PrinterModel).where(func.lower(PrinterModel.manufacturer) == body.manufacturer.lower(),
                                            func.lower(PrinterModel.model) == body.model.lower())):
        raise HTTPException(409, "Model already in catalogue")
    m = PrinterModel(**body.model_dump())
    db.add(m)
    db.flush()
    audit(db, "printer_model_created", username=user.username, target=m.id,
          detail=f"{m.manufacturer} {m.model} verification={m.verification}", request=request)
    return _model_json(m, db)


@router.patch("/models/{model_id}")
def update_model(model_id: str, body: ModelInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    m = db.get(PrinterModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(m, k, v)
    m.updated_at = S.now_utc()
    audit(db, "printer_model_updated", username=user.username, target=m.id, detail=json.dumps(changes, default=str)[:900],
          request=request)
    return _model_json(m, db)


class CostInput(BaseModel):
    supply_type: str = Field(min_length=1, max_length=32)
    colour: Literal["black", "cyan", "magenta", "yellow", "other", "n/a"] = "black"
    part_number: str | None = Field(None, max_length=100)
    unit_cost: Decimal = Field(ge=0)
    currency: Literal["JMD", "USD"] = "JMD"
    declared_yield: int | None = Field(None, gt=0)
    verified: bool = False


@router.get("/models/compare")
def models_compare(manufacturer: str | None = None, model: str | None = None, site_id: int | None = None,
                   category: str | None = None, date_from: str | None = None, date_to: str | None = None,
                   db: Session = Depends(get_db), user: User = viewer):
    end = _parse_dt(date_to, S.now_utc())
    start = _parse_dt(date_from, end - timedelta(days=365))
    printers = S.filtered_printers(db, {"manufacturer": manufacturer, "model": model, "site_id": site_id, "category": category},
                                   _scope())
    return {"period": {"start": start.isoformat(), "end": end.isoformat()},
            "cohorts": S.compare_models(db, printers, start, end),
            "method": "Verified model cohorts; per-device monthly volumes need ≥60% coverage; costs per 1,000 from measured "
                      "yields between confirmed replacements; incidents exclude WAN/power/user causes. Cohorts with fewer "
                      "than 3 devices or 9 device-months are INSUFFICIENT_EVIDENCE.",
            "disclaimer": "Comparison is evidence for human review, not a procurement decision."}


@router.get("/models/{model_id}/costs")
def model_costs(model_id: str, db: Session = Depends(get_db), user: User = viewer):
    return [{"id": c.id, "supply_type": c.supply_type, "colour": c.colour, "part_number": c.part_number,
             "unit_cost": float(c.unit_cost), "currency": c.currency, "declared_yield": c.declared_yield, "verified": c.verified,
             "effective_from": S.iso(c.effective_from), "recorded_by": c.recorded_by}
            for c in db.scalars(select(PrinterModelCost).where(PrinterModelCost.model_id == model_id)
                                .order_by(PrinterModelCost.effective_from.desc()))]


@router.post("/models/{model_id}/costs", status_code=201)
def add_model_cost(model_id: str, body: CostInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    if db.get(PrinterModel, model_id) is None:
        raise HTTPException(404, "Model not found")
    c = PrinterModelCost(model_id=model_id, recorded_by=user.username, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, "printer_model_cost_recorded", username=user.username, target=model_id,
          detail=f"{body.supply_type}/{body.colour} {body.unit_cost} {body.currency} verified={body.verified}", request=request)
    return {"id": c.id}


@router.get("/recommendations")
def recommendations(status: str | None = None, db: Session = Depends(get_db), user: User = viewer):
    scope_ids = {p.id for p in S.filtered_printers(db, {}, _scope())}
    q = select(PrinterRecommendation).order_by(PrinterRecommendation.created_at.desc())
    if status:
        q = q.where(PrinterRecommendation.status == status)
    out = []
    for r in db.scalars(q.limit(1000)):
        if r.printer_id and r.printer_id not in scope_ids:
            continue
        if r.printer_id is None and _scope() is not None and r.code in ("CONSOLIDATION", "RELOCATION"):
            ev = r.evidence or {}
            members = set(ev.get("printers", []) + ev.get("low", []) + ev.get("high", []))
            if not members <= scope_ids:
                continue
        out.append({"id": r.id, "code": r.code, "title": r.title, "printer_id": r.printer_id, "model": r.model,
                    "reason_codes": r.reason_codes, "evidence": r.evidence, "assumptions": r.assumptions,
                    "period_start": S.iso(r.period_start), "period_end": S.iso(r.period_end), "confidence": r.confidence,
                    "rule_version": r.rule_version, "status": r.status, "created_at": S.iso(r.created_at),
                    "last_seen_at": S.iso(r.last_seen_at), "reviewed_by": r.reviewed_by, "reviewed_at": S.iso(r.reviewed_at),
                    "decision_note": r.decision_note})
    return {"items": out, "rule_version": fleet.RULE_VERSION,
            "policy": "Advisory only. Approving records a human decision; it never changes a printer or places an order."}


@router.post("/recommendations/run")
def run_recommendations(request: Request, db: Session = Depends(get_db), user: User = operator):
    printers = S.filtered_printers(db, {}, _scope())
    r = S.sync_recommendations(db, printers, user.username)
    audit(db, "printer_recommendations_run", username=user.username, detail=json.dumps(r), request=request)
    return r


class Review(BaseModel):
    decision: Literal["UNDER_REVIEW", "APPROVED", "REJECTED"]
    note: str = Field(min_length=3, max_length=500)


@router.post("/recommendations/{rec_id}/review")
def review_recommendation(rec_id: str, body: Review, request: Request, db: Session = Depends(get_db), user: User = reviewer):
    r = db.get(PrinterRecommendation, rec_id)
    if r is None or (r.printer_id and r.printer_id not in {p.id for p in S.filtered_printers(db, {}, _scope())}):
        raise HTTPException(404, "Recommendation not found")
    if r.status in ("APPROVED", "REJECTED", "SUPERSEDED"):
        raise HTTPException(409, f"Recommendation already {r.status.lower()}")
    r.status, r.reviewed_by, r.reviewed_at, r.decision_note = body.decision, user.username, S.now_utc(), body.note
    audit(db, "printer_recommendation_reviewed", username=user.username, target=rec_id,
          detail=f"{r.code} → {body.decision}: {body.note}"[:900], request=request)
    return {"id": r.id, "status": r.status}


class ReportRequest(BaseModel):
    report: str
    format: Literal["csv", "xlsx", "pdf"] = "csv"
    filters: dict = Field(default_factory=dict)

    @field_validator("report")
    @classmethod
    def known(cls, v):
        if v not in S.REPORTS:
            raise ValueError(f"report must be one of {', '.join(S.REPORTS)}")
        return v

    @field_validator("filters")
    @classmethod
    def small(cls, v):
        allowed = {"manufacturer", "model", "site_id", "location", "division", "date_from", "date_to", "category", "printer_id"}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"unsupported filters: {sorted(bad)}")
        return {k: (str(x)[:120] if x is not None else None) for k, x in v.items()}


def _job_json(j: PrinterJob) -> dict:
    return {"id": j.id, "kind": j.kind, "status": j.status, "params": {k: v for k, v in j.params.items() if k != "scope"},
            "progress": j.progress, "total": j.total, "result": j.result, "errors": j.errors,
            "cancel_requested": j.cancel_requested, "requested_by": j.requested_by, "created_at": S.iso(j.created_at),
            "started_at": S.iso(j.started_at), "finished_at": S.iso(j.finished_at), "artifact_name": j.artifact_name,
            "download": f"/api/printers/reports/jobs/{j.id}/download" if j.artifact else None}


@router.get("/reports")
def report_catalogue(db: Session = Depends(get_db), user: User = viewer):
    jobs = db.scalars(select(PrinterJob).where(PrinterJob.kind == "REPORT").order_by(PrinterJob.created_at.desc()).limit(50))
    return {"reports": [{"key": k, **v} for k, v in S.REPORTS.items()], "formats": list(export.FORMATS),
            "filters": ["manufacturer", "model", "site_id", "location", "division", "date_from", "date_to", "category", "printer_id"],
            "jobs": [_job_json(j) for j in jobs if user.role == "admin" or j.requested_by == user.username]}


@router.post("/reports", status_code=202)
def generate_report(body: ReportRequest, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = viewer):
    try:
        S._period_from(body.filters)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    j = S.create_job(db, "REPORT", {"report": body.report, "format": body.format, "filters": body.filters,
                                    "scope": _scope()}, user.username)
    audit(db, "printer_report_requested", username=user.username, target=j.id, detail=f"{body.report}.{body.format}",
          request=request)
    db.commit()
    background.add_task(S.run_job, session_scope, j.id, _owner())
    return _job_json(j)


@router.get("/reports/jobs/{job_id}/download")
def download_report(job_id: str, db: Session = Depends(get_db), user: User = viewer):
    j = db.get(PrinterJob, job_id)
    if j is None or j.kind != "REPORT" or (user.role != "admin" and j.requested_by != user.username
                                           and not j.params.get("schedule_id")):
        raise HTTPException(404, "Report not found")
    if j.status != "SUCCEEDED" or not j.artifact:
        raise HTTPException(409, f"Report is {j.status.lower()}")
    return Response(base64.b64decode(j.artifact), media_type=j.artifact_type,
                    headers={"Content-Disposition": f'attachment; filename="{j.artifact_name}"'})


class ScheduleInput(ReportRequest):
    cadence: Literal["daily", "weekly", "monthly"]
    recipients: list[str] = Field(default_factory=list, max_length=20)


@router.get("/reports/schedules")
def list_schedules(db: Session = Depends(get_db), user: User = admin):
    return [{"id": s.id, "report": s.report, "format": s.format, "filters": s.filters, "cadence": s.cadence,
             "recipients": s.recipients, "enabled": s.enabled, "next_run_at": S.iso(s.next_run_at), "last_job_id": s.last_job_id,
             "created_by": s.created_by} for s in db.scalars(select(PrinterReportSchedule))]


@router.post("/reports/schedules", status_code=201)
def create_schedule(body: ScheduleInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    from ..database.models_remediation import NotificationChannel
    known = {c for (c,) in db.execute(select(NotificationChannel.id))}
    if set(body.recipients) - known:
        raise HTTPException(422, "Recipients must be existing notification channel ids")
    s = PrinterReportSchedule(report=body.report, format=body.format, filters=body.filters, cadence=body.cadence,
                              recipients=body.recipients, enabled=True, next_run_at=S.now_utc() + timedelta(minutes=1),
                              created_by=user.username)
    db.add(s)
    db.flush()
    audit(db, "printer_report_scheduled", username=user.username, target=s.id, detail=f"{body.report} {body.cadence}",
          request=request)
    return {"id": s.id, "next_run_at": S.iso(s.next_run_at)}


@router.delete("/reports/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    s = db.get(PrinterReportSchedule, schedule_id)
    if s is None:
        raise HTTPException(404, "Schedule not found")
    db.delete(s)
    audit(db, "printer_report_schedule_deleted", username=user.username, target=schedule_id, request=request)
    return {"deleted": schedule_id}


# ---- jobs ------------------------------------------------------------------------------------------
@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db), user: User = viewer):
    j = db.get(PrinterJob, job_id)
    if j is None or (user.role != "admin" and j.requested_by != user.username):
        raise HTTPException(404, "Job not found")
    return _job_json(j)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db), user: User = operator):
    j = db.get(PrinterJob, job_id)
    if j is None or (user.role != "admin" and j.requested_by != user.username):
        raise HTTPException(404, "Job not found")
    if j.status not in ("QUEUED", "RUNNING"):
        raise HTTPException(409, f"Job already {j.status.lower()}")
    j.cancel_requested = True
    if j.status == "QUEUED":
        j.status, j.finished_at = "CANCELLED", S.now_utc()
    audit(db, "printer_job_cancel_requested", username=user.username, target=job_id, request=request)
    return _job_json(j)


# ---- discovery & import ------------------------------------------------------------------------------
class DiscoveryJobInput(BaseModel):
    credential_profile_id: str = Field(min_length=1, max_length=64)
    management_range: IPv4Network | None = None
    management_ips: list[IPv4Address] | None = Field(None, max_length=1024)


def _snmp_policy(db: Session) -> dict:
    row = db.get(AppSetting, "printer_snmp_policy")
    return {"allow_v2c": True, "allow_v1": False} | (row.value if row else {})


def _check_profile_policy(db: Session, profile_id: str) -> str:
    row = db.get(AppSetting, S.PROFILE_PREFIX + profile_id)
    if row is None:
        raise HTTPException(404, "Printer credential profile not found")
    version = row.value["version"]
    pol = _snmp_policy(db)
    if version == "1" and not pol["allow_v1"]:
        raise HTTPException(403, "SNMPv1 is disabled by policy (Printers → Settings). Prefer SNMPv3 authPriv.")
    if version == "2c" and not pol["allow_v2c"]:
        raise HTTPException(403, "SNMPv2c is disabled by policy. Use an SNMPv3 authPriv profile.")
    return version


@router.get("/discovery/jobs")
def recent_discoveries(db: Session = Depends(get_db), user: User = admin):
    jobs = db.scalars(select(PrinterJob).where(PrinterJob.kind == "DISCOVERY")
                      .order_by(PrinterJob.created_at.desc()).limit(20))
    return [{"id": j.id, "status": j.status, "target": j.params.get("target", ""),
             "created_at": S.iso(j.created_at), "progress": j.progress, "total": j.total,
             "found": len((j.result or {}).get("printers", []))}
            for j in jobs]


@router.post("/discovery", status_code=202)
def start_discovery(body: DiscoveryJobInput, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = admin):
    version = _check_profile_policy(db, body.credential_profile_id)
    if bool(body.management_range) == bool(body.management_ips):
        raise HTTPException(422, "Provide either management_range or management_ips")
    try:
        hosts = S.discovery_hosts(str(body.management_range) if body.management_range else [str(i) for i in body.management_ips])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not all(permitted_target(h) or (S.emulation_enabled() and h in S.EMULATORS) for h in hosts):
        raise HTTPException(403, "Every address must be within approved printer management ranges")
    running = db.scalar(select(func.count()).select_from(PrinterJob).where(PrinterJob.kind == "DISCOVERY",
                                                                           PrinterJob.status.in_(("QUEUED", "RUNNING"))))
    if running and running >= 3:
        raise HTTPException(409, "Three printer discoveries are already running")
    j = S.create_job(db, "DISCOVERY", {"hosts": hosts, "credential_profile_id": body.credential_profile_id,
                                       "target": str(body.management_range or f"{len(hosts)} addresses"),
                                       "snmp_version": version}, user.username, total=len(hosts))
    audit(db, "printer_discovery_started", username=user.username, target=j.id,
          detail=f"read-only SNMPv{version} discovery of {len(hosts)} address(es)", request=request)
    db.commit()
    background.add_task(S.run_job, session_scope, j.id, _owner())
    return _job_json(j) | {"warnings": ["SNMPv2c sends the community in clear text; prefer SNMPv3 authPriv"] if version != "3" else []}


class ApproveItem(BaseModel):
    management_ip: IPv4Address
    manufacturer: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=120)
    serial_number: str = Field(min_length=1, max_length=100)
    hostname: str | None = Field(None, max_length=120)
    site_id: int | None = None
    enable_monitoring: bool = True


class ApproveInput(BaseModel):
    items: list[ApproveItem] = Field(min_length=1, max_length=1024)
    credential_profile_id: str | None = None
    interval_seconds: int = Field(900, ge=300, le=86400)


def _site_allowed(db: Session, site_id: int | None):
    if site_id is None:
        return
    if db.get(Site, site_id) is None:
        raise HTTPException(422, f"Unknown site {site_id}")
    scope = get_scope()
    if scope is not None and site_id not in scope:
        raise HTTPException(403, "Site outside your scope")


@router.post("/discovery/{job_id}/approve")
def approve_discovered(job_id: str, body: ApproveInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    j = db.get(PrinterJob, job_id)
    if j is None or j.kind != "DISCOVERY" or j.status not in ("SUCCEEDED", "CANCELLED"):
        raise HTTPException(409, "Discovery job is not complete")
    found = {r["management_ip"] for r in (j.result or {}).get("printers", [])}
    cred = body.credential_profile_id or j.params.get("credential_profile_id")
    if cred:
        _check_profile_policy(db, cred)
    created, updated = [], []
    for it in body.items:
        if str(it.management_ip) not in found:
            raise HTTPException(422, f"{it.management_ip} was not identified by this discovery job")
        _site_allowed(db, it.site_id)
        p, new = S.register_or_update(db, it.model_dump(mode="json") | {"management_ip": str(it.management_ip)},
                                      provenance="DISCOVERY", site_id=it.site_id)
        if p.model.casefold() != it.model.casefold():
            raise HTTPException(409, f"Serial {it.serial_number} is registered with a different model; review identity first")
        mon = S.monitoring(db, p.id)
        if it.enable_monitoring:
            mon.enabled, mon.approved_by, mon.credential_profile_id = True, user.username, cred
            mon.interval_seconds, mon.next_poll_at = body.interval_seconds, S.now_utc()
        (created if new else updated).append(p.id)
    audit(db, "printer_discovery_approved", username=user.username, target=job_id,
          detail=f"{len(created)} registered, {len(updated)} updated; monitoring is read-only", request=request)
    return {"created": created, "updated": updated}


async def _read_csv(file: UploadFile | None, text: str | None) -> str:
    if file is not None:
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "CSV larger than 5 MB")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, "CSV must be UTF-8") from None
    if text:
        return text
    raise HTTPException(422, "Provide a CSV file or text")


@router.post("/import/preview")
async def import_preview(file: UploadFile | None = File(None), text: str | None = Form(None),
                         db: Session = Depends(get_db), user: User = admin):
    try:
        return S.preview_import(db, await _read_csv(file, text))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/import/commit")
async def import_commit(request: Request, file: UploadFile | None = File(None), text: str | None = Form(None),
                        rows: str = Form(..., description="comma-separated row numbers to approve, or 'all'"),
                        enable_monitoring: bool = Form(False), credential_profile_id: str | None = Form(None),
                        db: Session = Depends(get_db), user: User = admin):
    try:
        preview = S.preview_import(db, await _read_csv(file, text))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    wanted = None if rows.strip() == "all" else {int(x) for x in rows.split(",") if x.strip().isdigit()}
    if enable_monitoring and credential_profile_id:
        _check_profile_policy(db, credential_profile_id)
    done, skipped = [], []
    for r in preview["rows"]:
        if wanted is not None and r["row"] not in wanted:
            continue
        if r["action"] not in ("CREATE", "UPDATE"):
            skipped.append({"row": r["row"], "errors": r["errors"]})
            continue
        if r["site_id"] is not None:
            scope = get_scope()
            if scope is not None and r["site_id"] not in scope:
                skipped.append({"row": r["row"], "errors": ["site outside your scope"]})
                continue
        p, _ = S.register_or_update(db, r["data"], provenance="IMPORT", site_id=r["site_id"], original_text=r["original_text"])
        if enable_monitoring and p.management_ip and credential_profile_id:
            mon = S.monitoring(db, p.id)
            mon.enabled, mon.approved_by, mon.credential_profile_id, mon.next_poll_at = True, user.username, credential_profile_id, S.now_utc()
        done.append({"row": r["row"], "printer_id": p.id, "action": r["action"]})
    audit(db, "printer_csv_import", username=user.username,
          detail=f"{len(done)} imported, {len(skipped)} skipped; monitoring={'on' if enable_monitoring else 'off'}", request=request)
    return {"imported": done, "skipped": skipped}


# ---- MIB library & profiles --------------------------------------------------------------------------
@router.get("/mibs")
def list_mibs(db: Session = Depends(get_db), user: User = viewer):
    return [{"id": m.id, "module_name": m.module_name, "version": m.version, "filename": m.filename, "sha256": m.sha256,
             "status": m.status, "imports": m.imports, "unresolved": m.unresolved, "object_count": m.object_count,
             "enterprise": m.enterprise, "vendor": mib.ENTERPRISES.get(m.enterprise or -1), "uploaded_by": m.uploaded_by,
             "uploaded_at": S.iso(m.uploaded_at), "active": m.active}
            for m in db.scalars(select(PrinterMibModule).order_by(PrinterMibModule.module_name, PrinterMibModule.version.desc()))]


@router.post("/mibs", status_code=201)
async def upload_mib(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = admin):
    name = (file.filename or "upload.mib").rsplit("/", 1)[-1][:200]
    if not name.lower().endswith((".mib", ".my", ".txt", ".smi")):
        raise HTTPException(422, "Upload a MIB text file (.mib, .my, .txt or .smi)")
    raw = await file.read(mib_parser.MAX_BYTES + 1)
    if len(raw) > mib_parser.MAX_BYTES:
        raise HTTPException(413, "MIB file larger than 2 MB")
    if b"\x00" in raw:
        raise HTTPException(422, "Binary files are not accepted; MIBs are text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        mod = S.install_mib(db, name, text, user.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, "printer_mib_uploaded", username=user.username, target=mod.module_name,
          detail=f"{name} v{mod.version} sha256={mod.sha256[:16]} objects={mod.object_count} status={mod.status}", request=request)
    return {"id": mod.id, "module_name": mod.module_name, "version": mod.version, "status": mod.status,
            "object_count": mod.object_count, "unresolved": mod.unresolved, "enterprise": mod.enterprise}


@router.get("/mibs/{module_id}/objects")
def mib_objects(module_id: str, q: str | None = None, kind: str | None = None, suggested_only: bool = False,
                limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db), user: User = viewer):
    if db.get(PrinterMibModule, module_id) is None:
        raise HTTPException(404, "MIB module not found")
    stmt = select(PrinterMibObject).where(PrinterMibObject.module_id == module_id)
    if q:
        stmt = stmt.where(or_(PrinterMibObject.name.ilike(f"%{q}%"), PrinterMibObject.oid.ilike(f"{q}%"),
                              PrinterMibObject.description.ilike(f"%{q}%")))
    if kind:
        stmt = stmt.where(PrinterMibObject.kind == kind)
    if suggested_only:
        stmt = stmt.where(PrinterMibObject.suggested_metric.is_not(None))
    return [{"id": o.id, "name": o.name, "oid": o.oid, "parent": o.parent, "kind": o.kind, "syntax": o.syntax,
             "access": o.access, "units": o.units, "description": (o.description or "")[:600],
             "suggested_metric": o.suggested_metric} for o in db.scalars(stmt.order_by(PrinterMibObject.oid).limit(limit))]


@router.delete("/mibs/{module_id}")
def deactivate_mib(module_id: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    m = db.get(PrinterMibModule, module_id)
    if m is None:
        raise HTTPException(404, "MIB module not found")
    m.active = False
    audit(db, "printer_mib_deactivated", username=user.username, target=m.module_name, request=request)
    return {"id": m.id, "active": False}


class DraftInput(BaseModel):
    object_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/mibs/profile-draft")
def profile_draft(body: DraftInput, db: Session = Depends(get_db), user: User = admin):
    out = []
    for o in db.scalars(select(PrinterMibObject).where(PrinterMibObject.id.in_(body.object_ids))):
        if not o.oid or o.kind not in ("scalar", "column"):
            out.append({"object": o.name, "error": "only resolved scalar or column objects can be polled"})
            continue
        if o.access and "not-accessible" in o.access:
            out.append({"object": o.name, "error": "object is not readable"})
            continue
        d = mib_parser.metric_definition_for({"oid": o.oid, "kind": o.kind, "syntax": o.syntax})
        out.append({"object": o.name, "metric_key": o.suggested_metric or o.name[:64], **d, "unit": o.units,
                    "category": "supply" if (o.suggested_metric or "").startswith("supply") else "identity"
                    if o.suggested_metric in ("serial_number", "firmware") else "usage",
                    "verification": "STANDARD" if o.oid.startswith("1.3.6.1.2.1.") else "VENDOR_UNVERIFIED",
                    "description": f"{o.name}: {(o.description or '')[:180]}"})
    return {"metrics": out, "note": "Suggested mappings are drafts. Confirm each metric's meaning before activating the profile."}


class MetricInput(BaseModel):
    metric_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    oid: str = Field(min_length=3, max_length=160, pattern=r"^\d+(\.\d+)+$")
    access: Literal["scalar", "table"] = "scalar"
    value_type: Literal["counter", "gauge", "text", "enum"] = "counter"
    unit: str | None = Field(None, max_length=24)
    scale: float = Field(1.0, gt=0, le=1e6)
    interval_seconds: int | None = Field(None, ge=300, le=7 * 86400)
    category: Literal["identity", "usage", "supply", "status", "network"] = "usage"
    verification: Literal["STANDARD", "VENDOR_UNVERIFIED", "VENDOR_VERIFIED"] = "VENDOR_UNVERIFIED"
    description: str | None = Field(None, max_length=255)


class ProfileInput(BaseModel):
    key: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    vendor: str | None = Field(None, max_length=64)
    match_enterprise: int | None = Field(None, ge=1)
    match_descr_regex: str | None = Field(None, max_length=200)
    priority: int = Field(50, ge=1, le=999)
    default_interval_seconds: int = Field(900, ge=300, le=86400)
    include_standard_metrics: bool = True
    metrics: list[MetricInput] = Field(default_factory=list, max_length=200)

    @field_validator("match_descr_regex")
    @classmethod
    def valid_regex(cls, v):
        if v:
            import re
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from None
        return v


def _profile_json(db: Session, p: PrinterMibProfile) -> dict:
    defs = list(db.scalars(select(PrinterMetricDefinition).where(PrinterMetricDefinition.profile_id == p.id)))
    used = db.scalar(select(func.count()).select_from(PrinterMonitoring).where(PrinterMonitoring.mib_profile_id == p.id)) or 0
    return {"id": p.id, "key": p.key, "name": p.name, "vendor": p.vendor, "match_enterprise": p.match_enterprise,
            "match_descr_regex": p.match_descr_regex, "priority": p.priority, "builtin": p.builtin, "enabled": p.enabled,
            "version": p.version, "default_interval_seconds": p.default_interval_seconds, "created_by": p.created_by,
            "updated_at": S.iso(p.updated_at), "printers": used,
            "metrics": [{"id": d.id, "metric_key": d.metric_key, "oid": d.oid, "access": d.access, "value_type": d.value_type,
                         "unit": d.unit, "scale": d.scale, "interval_seconds": d.interval_seconds, "category": d.category,
                         "verification": d.verification, "enabled": d.enabled, "description": d.description} for d in defs]}


@router.get("/mib-profiles")
def list_profiles(db: Session = Depends(get_db), user: User = viewer):
    S.ensure_builtin_profiles(db)
    return [_profile_json(db, p) for p in db.scalars(select(PrinterMibProfile).order_by(PrinterMibProfile.priority, PrinterMibProfile.key))]


def _write_metrics(db: Session, p: PrinterMibProfile, metrics: list[MetricInput], include_standard: bool):
    for d in db.scalars(select(PrinterMetricDefinition).where(PrinterMetricDefinition.profile_id == p.id)):
        db.delete(d)
    db.flush()
    rows = ([m for m in mib.GENERIC_METRICS] if include_standard else []) + [m.model_dump() for m in metrics]
    seen = set()
    for m in reversed(rows):   # later (custom) definitions override standard ones with the same key
        if m["metric_key"] in seen:
            continue
        seen.add(m["metric_key"])
        if m["oid"].startswith("1.3.6.1.2.1.") and m.get("verification") == "VENDOR_UNVERIFIED":
            m = m | {"verification": "STANDARD"}
        db.add(PrinterMetricDefinition(profile_id=p.id, metric_key=m["metric_key"], oid=m["oid"], access=m["access"],
                                       value_type=m["value_type"], unit=m.get("unit"), scale=m.get("scale", 1.0),
                                       interval_seconds=m.get("interval_seconds"), category=m["category"],
                                       verification=m.get("verification", "VENDOR_UNVERIFIED"), description=m.get("description")))
    db.flush()


@router.post("/mib-profiles", status_code=201)
def create_profile(body: ProfileInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    if db.scalar(select(PrinterMibProfile).where(PrinterMibProfile.key == body.key)):
        raise HTTPException(409, "Profile key already exists")
    p = PrinterMibProfile(key=body.key, name=body.name, vendor=body.vendor, match_enterprise=body.match_enterprise,
                          match_descr_regex=body.match_descr_regex, priority=body.priority, builtin=False, enabled=True,
                          version=1, default_interval_seconds=body.default_interval_seconds, created_by=user.username)
    db.add(p)
    db.flush()
    _write_metrics(db, p, body.metrics, body.include_standard_metrics)
    audit(db, "printer_profile_definition_created", username=user.username, target=p.key,
          detail=f"{len(body.metrics)} custom metric(s); enterprise={body.match_enterprise}", request=request)
    return _profile_json(db, p)


@router.put("/mib-profiles/{profile_id}")
def update_profile(profile_id: str, body: ProfileInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    p = db.get(PrinterMibProfile, profile_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    if p.builtin:
        raise HTTPException(409, "Built-in profiles are read-only; create a custom profile with a lower priority number")
    for k in ("name", "vendor", "match_enterprise", "match_descr_regex", "priority", "default_interval_seconds"):
        setattr(p, k, getattr(body, k))
    p.version += 1
    p.updated_at = S.now_utc()
    _write_metrics(db, p, body.metrics, body.include_standard_metrics)
    audit(db, "printer_profile_definition_updated", username=user.username, target=p.key, detail=f"version={p.version}",
          request=request)
    return _profile_json(db, p)


class ProfileToggle(BaseModel):
    enabled: bool


@router.patch("/mib-profiles/{profile_id}")
def toggle_profile(profile_id: str, body: ProfileToggle, request: Request, db: Session = Depends(get_db), user: User = admin):
    p = db.get(PrinterMibProfile, profile_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    if p.key == "rfc3805-generic" and not body.enabled:
        raise HTTPException(409, "The generic Printer-MIB fallback profile cannot be disabled")
    p.enabled = body.enabled
    audit(db, "printer_profile_definition_toggled", username=user.username, target=p.key, detail=f"enabled={body.enabled}",
          request=request)
    return _profile_json(db, p)


class ProfileTest(BaseModel):
    printer_id: str


@router.post("/mib-profiles/{profile_id}/test")
async def test_profile(profile_id: str, body: ProfileTest, request: Request, db: Session = Depends(get_db), user: User = admin):
    p = db.get(PrinterMibProfile, profile_id)
    printer = _visible(db, body.printer_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    mon = S.monitoring(db, printer.id)
    try:
        reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    prof = next(x for x in S.profile_dicts(db) if x["id"] == p.id)
    from ..printers import collector as C
    try:
        res = await C.poll(reader, [prof | {"enabled": True, "priority": 0, "match_enterprise": None, "match_descr_regex": None}])
    finally:
        await reader.close()
    audit(db, "printer_profile_tested", username=user.username, target=printer.id, detail=f"profile={p.key} read-only",
          request=request)
    return {"profile": p.key, "reachable": res.reachable, "snmp_ok": res.snmp_ok, "error": res.error,
            "metrics": [{"metric_key": s.metric_key, "oid": s.oid, "instance": s.instance, "value_state": s.value_state,
                         "raw_value": s.raw_value, "normalized_value": s.normalized_value, "verification": s.verification}
                        for s in res.samples],
            "meters": res.meters, "unsupported_oids": res.unsupported_oids, "capabilities": res.capabilities}


# ---- settings / retention ---------------------------------------------------------------------------
class SettingsInput(BaseModel):
    raw_days: int = Field(365, ge=30, le=3650)
    aggregate_years: int = Field(5, ge=1, le=20)
    operating_window: dict | None = None
    allow_v2c: bool = True
    allow_v1: bool = False


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), user: User = admin):
    return {"retention": S.retention_policy(db), "operating_window": S.get_window(db), "snmp_policy": _snmp_policy(db)}


def _put(db, key, value, user):
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=user))
    else:
        row.value, row.updated_by, row.updated_at = value, user, S.now_utc()


@router.put("/settings")
def put_settings(body: SettingsInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    if body.operating_window is not None:
        w = body.operating_window
        try:
            days = [int(d) for d in w.get("days", [])]
            from datetime import time as _t
            _t.fromisoformat(w.get("start", "00:00")); _t.fromisoformat(w.get("end", "23:59"))  # noqa: E702
            assert days and all(0 <= d <= 6 for d in days)
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "operating_window = {days:[0-6], start:'HH:MM', end:'HH:MM'}") from None
        _put(db, "printer_operating_window", {"days": days, "start": w.get("start", "00:00"), "end": w.get("end", "23:59")}, user.username)
    _put(db, S.RETENTION_KEY, {"raw_days": body.raw_days, "aggregate_years": body.aggregate_years}, user.username)
    _put(db, "printer_snmp_policy", {"allow_v2c": body.allow_v2c, "allow_v1": body.allow_v1}, user.username)
    audit(db, "printer_settings_updated", username=user.username, detail=body.model_dump_json()[:900], request=request)
    return get_settings_(db=db, user=user)


@router.post("/retention/run")
def run_retention(request: Request, db: Session = Depends(get_db), user: User = admin):
    r = S.apply_retention(db)
    audit(db, "printer_retention_applied", username=user.username, detail=json.dumps(r)[:900], request=request)
    return r


# ---- maintenance (fleet-wide import) ------------------------------------------------------------------
@router.post("/maintenance/import-service-desk")
async def import_service_desk(request: Request, file: UploadFile | None = File(None), text: str | None = Form(None),
                              db: Session = Depends(get_db), user: User = admin):
    import csv
    import io
    content = await _read_csv(file, text)
    reader = csv.DictReader(io.StringIO(content))
    need = {"ticket_ref", "serial_number", "reported_at", "description"}
    if not reader.fieldnames or not need <= {h.strip() for h in reader.fieldnames}:
        raise HTTPException(422, f"Columns required: {', '.join(sorted(need))} (optional: restored_at, incident_type, "
                                 "cause_category, technician, vendor, cost_amount, currency, parts, warranty, component)")
    created, skipped = 0, []
    for n, r in enumerate(reader, start=2):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if db.scalar(select(PrinterMaintenanceDetail).where(PrinterMaintenanceDetail.service_ticket_ref == r["ticket_ref"][:80])):
            skipped.append({"row": n, "reason": "ticket already imported"})
            continue
        p = db.scalar(select(PrinterDevice).where(func.lower(PrinterDevice.serial_number) == r["serial_number"].lower()))
        if p is None:
            skipped.append({"row": n, "reason": "no printer with this serial"})
            continue
        try:
            rep = _parse_dt(r["reported_at"], S.now_utc())
            res = _parse_dt(r.get("restored_at"), None) if r.get("restored_at") else None
        except HTTPException:
            skipped.append({"row": n, "reason": "invalid date"})
            continue
        cause = (r.get("cause_category") or "UNKNOWN").upper()
        if cause not in ("MECHANICAL", "WAN", "POWER", "USER", "CONSUMABLE", "UNKNOWN"):
            cause = "UNKNOWN"
        ev = PrinterMaintenanceEvent(printer_id=p.id, component=(r.get("component") or "unspecified")[:80], event_type="fault",
                                     severity="warning", description=r["description"][:1500], source="SERVICE_DESK",
                                     logged_by=user.username, observed_at=rep,
                                     cost_amount=Decimal(r["cost_amount"]) if r.get("cost_amount") else None,
                                     currency=(r.get("currency") or "JMD")[:3])
        db.add(ev)
        db.flush()
        db.add(PrinterMaintenanceDetail(event_id=ev.id, incident_type=(r.get("incident_type") or None),
                                        cause_category=cause, qualifying_failure=cause == "MECHANICAL",
                                        reported_at=rep, restored_at=res, technician=r.get("technician") or None,
                                        vendor=r.get("vendor") or None,
                                        parts=[x.strip() for x in r.get("parts", "").split(";") if x.strip()] or None,
                                        warranty=r.get("warranty", "").lower() in ("yes", "true", "1") if r.get("warranty") else None,
                                        service_ticket_ref=r["ticket_ref"][:80], evidence="Imported from Service Desk"))
        _link_faults(db, p.id, ev.id, rep, res, r["ticket_ref"][:80])
        created += 1
    audit(db, "printer_service_desk_import", username=user.username, detail=f"{created} records, {len(skipped)} skipped",
          request=request)
    return {"created": created, "skipped": skipped}


def _link_faults(db: Session, printer_id: str, event_id: int, start: datetime, end: datetime | None, ticket: str | None):
    """Correlate device-reported fault periods overlapping the service incident window."""
    lo = start - timedelta(hours=24)
    hi = (end or start) + timedelta(hours=1)
    n = 0
    for f in db.scalars(select(PrinterFaultEvent).where(PrinterFaultEvent.printer_id == printer_id,
                                                        PrinterFaultEvent.started_at <= hi,
                                                        or_(PrinterFaultEvent.ended_at.is_(None), PrinterFaultEvent.ended_at >= lo))):
        if f.maintenance_record_id is None:
            f.maintenance_record_id, f.service_ticket_ref = event_id, ticket
            n += 1
    return n


@router.get("/fleet/consumables")
def fleet_consumables(db: Session = Depends(get_db), user: User = viewer):
    """Fleet supply view: current levels, nearest forecast and open replacement confirmations."""
    out = []
    for p in S.filtered_printers(db, {}, _scope()):
        rows = list(db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == p.id)))
        if not rows:
            continue
        fc = [f for f in S.supply_forecasts(db, p.id) if f.get("days_to_empty") is not None]
        open_ev = db.scalar(select(func.count()).select_from(PrinterSupplyEvent).where(
            PrinterSupplyEvent.printer_id == p.id, PrinterSupplyEvent.status == "OPEN")) or 0
        out.append({"printer_id": p.id, "printer": p.hostname or p.serial_number, "model": p.model,
                    "supplies": [{"supply": c.description or c.supply_type, "supply_type": c.supply_type, "colour": c.colour,
                                  "percent": c.percent_remaining, "status": c.status, "level_state": c.level_state,
                                  "part_number": c.part_number} for c in rows],
                    "nearest_empty": min(fc, key=lambda f: f["days_to_empty"]) if fc else None,
                    "replacements_to_confirm": open_ev})
    out.sort(key=lambda r: (r["nearest_empty"] or {}).get("days_to_empty", 1e9))
    return {"printers": out}


@router.get("/fleet/maintenance")
def fleet_maintenance(days: int = Query(365, ge=1, le=3650), db: Session = Depends(get_db), user: User = viewer):
    printers = S.filtered_printers(db, {}, _scope())
    ids = [p.id for p in printers]
    names = {p.id: p.hostname or p.serial_number for p in printers}
    end = S.now_utc()
    start = end - timedelta(days=days)
    records = [r | {"printer": names.get(r["printer_id"])} for r in S.maintenance_dicts(db, ids)
               if S._utc(datetime.fromisoformat(r["observed_at"])) >= start]
    groups: dict[tuple, list[str]] = {}
    for p in printers:
        groups.setdefault((p.manufacturer, p.model), []).append(p.id)
    window = S.get_window(db)
    by_model = [{"manufacturer": m, "model": mo, "devices": len(g)} | S.printer_reliability(db, g, start, end, window)
                for (m, mo), g in sorted(groups.items())]
    return {"records": records, "by_model": by_model, "operating_window": window or "24x7",
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "method": "MTBF = operating hours ÷ qualifying MECHANICAL failures; WAN, power and user causes excluded."}


# ---- per-printer -----------------------------------------------------------------------------------
@router.get("/{printer_id}")
def printer_detail(printer_id: str, db: Session = Depends(get_db), user: User = viewer):
    from .printers import printer_details as p0_details
    p = _visible(db, printer_id)
    base = p0_details(printer_id, db=db, user=user)
    sites = {s.id: s.name for s in db.scalars(select(Site))}
    ident = db.get(PrinterIdentity, p.id)
    mon = db.get(PrinterMonitoring, p.id)
    model = S.printer_model(db, p.id)
    base["device"] = _summary(db, p, sites, S.now_utc())
    base["identity"] = None if ident is None else {
        "model_id": ident.model_id, "model": _model_json(model, db) if model else None, "site_id": ident.site_id,
        "location_ref": ident.location_ref, "division_ref": ident.division_ref, "section_ref": ident.section_ref,
        "unit_ref": ident.unit_ref, "original_import_text": ident.original_import_text, "reported_model": ident.reported_model,
        "reported_serial": ident.reported_serial, "system_object_id": ident.system_object_id,
        "system_description": ident.system_description, "identity_state": ident.identity_state,
        "identity_evidence": ident.identity_evidence, "is_colour": ident.is_colour, "is_multifunction": ident.is_multifunction,
        "technology": ident.technology, "in_service_since": S.iso(ident.in_service_since), "warranty_until": S.iso(ident.warranty_until)}
    prof = db.get(PrinterMibProfile, mon.mib_profile_id) if mon and mon.mib_profile_id else None
    base["monitoring"] = None if mon is None else {
        "enabled": mon.enabled, "approved_by": mon.approved_by, "credential_profile_id": mon.credential_profile_id,
        "mib_profile": {"id": prof.id, "key": prof.key, "name": prof.name, "version": prof.version} if prof else None,
        "profile_selection": mon.profile_selection, "interval_seconds": mon.interval_seconds,
        "next_poll_at": S.iso(mon.next_poll_at), "last_attempt_at": S.iso(mon.last_attempt_at),
        "last_success_at": S.iso(mon.last_success_at), "last_error": mon.last_error,
        "consecutive_failures": mon.consecutive_failures, "backoff_until": S.iso(mon.backoff_until),
        "reachability": mon.reachability, "snmp_state": mon.snmp_state, "operational_state": mon.operational_state,
        "discovery_confidence": mon.discovery_confidence, "unsupported_oids": mon.unsupported_oids or [],
        "health": mon.health_detail, "polls": {"total": mon.total_polls, "successful": mon.successful_polls},
        "metric_freshness": mon.metric_last_polled or {}}
    base["identity_changes"] = [{"id": c.id, "field": c.field, "old_value": c.old_value, "new_value": c.new_value,
                                 "decision": c.decision, "reason": c.reason, "observed_at": S.iso(c.observed_at)}
                                for c in db.scalars(select(PrinterIdentityChange).where(PrinterIdentityChange.printer_id == p.id)
                                                    .order_by(PrinterIdentityChange.id.desc()).limit(50))]
    return base


@router.get("/{printer_id}/capabilities")
def capabilities(printer_id: str, db: Session = Depends(get_db), user: User = viewer):
    p = _visible(db, printer_id)
    rows = {c.capability: c for c in db.scalars(select(PrinterCapability).where(PrinterCapability.printer_id == p.id))}
    names = ["impressions", "sheets", "mono_counter", "colour_counter", "mono_colour_split", "colour_printing", "duplex",
             "copy_scan", "copy_counter", "scan_counter", "supply_levels", "supply_identifiers", "trays", "faults", "drum",
             "fuser", "waste_toner", "maintenance_kit", "firmware", "network"]
    return {"printer_id": p.id, "states": ["SUPPORTED", "UNSUPPORTED", "UNVERIFIED", "TEMPORARILY_UNAVAILABLE", "INVALID"],
            "capabilities": [{"capability": n, "state": rows[n].state if n in rows else "UNVERIFIED",
                              "evidence": rows[n].evidence if n in rows else "not yet observed (no successful poll)",
                              "observed_at": S.iso(rows[n].observed_at) if n in rows else None} for n in names]}


@router.get("/{printer_id}/metrics")
def metrics(printer_id: str, metric: str | None = None, since: str | None = None, until: str | None = None,
            state: str | None = None, limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db), user: User = viewer):
    p = _visible(db, printer_id)
    end = _parse_dt(until, S.now_utc())
    start = _parse_dt(since, end - timedelta(days=7))
    q = select(PrinterMetricSample).where(PrinterMetricSample.printer_id == p.id, PrinterMetricSample.observed_at >= start,
                                          PrinterMetricSample.observed_at <= end)
    if metric:
        q = q.where(PrinterMetricSample.metric_key == metric)
    if state:
        q = q.where(PrinterMetricSample.value_state == state)
    rows = db.scalars(q.order_by(PrinterMetricSample.observed_at.desc(), PrinterMetricSample.id.desc()).limit(limit))
    return {"printer_id": p.id, "period": {"start": start.isoformat(), "end": end.isoformat()},
            "samples": [{"metric_key": s.metric_key, "instance": s.instance, "oid": s.oid, "raw_value": s.raw_value,
                         "normalized_value": s.normalized_value, "text_value": s.text_value, "unit": s.unit,
                         "value_state": s.value_state, "interpretation": s.interpretation, "source": s.source,
                         "profile": s.profile_key, "profile_version": s.profile_version, "poll_id": s.poll_id,
                         "observed_at": S.iso(s.observed_at)} for s in rows]}


@router.get("/{printer_id}/utilization")
def printer_utilization(printer_id: str, granularity: Literal["hour", "day", "week", "month", "year"] = "day",
                        start: str | None = None, end: str | None = None, meter: str | None = None,
                        db: Session = Depends(get_db), user: User = viewer):
    p = _visible(db, printer_id)
    e = _parse_dt(end, S.now_utc())
    default = {"hour": 2, "day": 30, "week": 120, "month": 365, "year": 1825}[granularity]
    s = _parse_dt(start, e - timedelta(days=default))
    if e <= s:
        raise HTTPException(422, "end must be after start")
    limit_days = {"hour": 31, "day": 731, "week": 1830, "month": 3660, "year": 3660}[granularity]
    if (e - s).days > limit_days:
        raise HTTPException(422, f"{granularity} buckets are limited to {limit_days} days")
    u = S.utilization(db, p.id, s, e, granularity, meter)
    u["insights"] = S.utilization_insights(db, p.id, e)
    u["available_meters"] = sorted({k for (k,) in db.execute(select(PrinterMeterReading.meter_kind).where(
        PrinterMeterReading.printer_id == p.id).distinct())})
    if u["meter_kind"] and u["buckets"]:
        covered = [b for b in u["buckets"] if b["volume"] is not None]
        days = (e - s).total_seconds() / 86400
        u["averages"] = {"per_day": round(u["summary"]["total"] / max(days * u["summary"]["mean_coverage"], 1e-9), 1)
                         if covered and u["summary"]["mean_coverage"] > 0 else None,
                         "per_month": round(u["summary"]["total"] / max(days * u["summary"]["mean_coverage"], 1e-9) * 30.44, 0)
                         if covered and u["summary"]["mean_coverage"] > 0 else None,
                         "basis": "covered time only; missing time is not counted as zero"}
    return u


@router.get("/{printer_id}/consumables")
def consumables(printer_id: str, db: Session = Depends(get_db), user: User = viewer):
    p = _visible(db, printer_id)
    ident = db.get(PrinterIdentity, p.id)
    events = S.supply_events(db, p.id)
    costs = model_costs(ident.model_id, db=db, user=user) if ident and ident.model_id else []
    return {"printer_id": p.id,
            "current": [{"supply_index": c.supply_index, "supply_type": c.supply_type, "colour": c.colour,
                         "description": c.description, "part_number": c.part_number, "part_number_verified": c.part_number_verified,
                         "unit": c.unit, "max_capacity": c.max_capacity, "level": c.level, "level_state": c.level_state,
                         "percent_remaining": c.percent_remaining, "status": c.status, "installed_at": S.iso(c.installed_at),
                         "installed_meter": c.installed_meter, "last_observed_at": S.iso(c.last_observed_at)}
                        for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.printer_id == p.id)
                                            .order_by(PrinterConsumable.supply_index))],
            "events": events, "measured_yields": fleet.measured_yields(events),
            "forecasts": S.supply_forecasts(db, p.id), "model_costs": costs,
            "special_values": {"-1": "OTHER", "-2": "UNKNOWN", "-3": "SOME_REMAINING (not empty)"}}


class SupplyDecision(BaseModel):
    decision: Literal["CONFIRM", "REJECT"]
    cost_amount: Decimal | None = Field(None, ge=0)
    currency: Literal["JMD", "USD"] = "JMD"
    note: str | None = Field(None, max_length=255)


@router.post("/{printer_id}/supply-events/{event_id}/decide")
def decide_supply_event(printer_id: str, event_id: int, body: SupplyDecision, request: Request,
                        db: Session = Depends(get_db), user: User = admin):
    _visible(db, printer_id)
    e = db.get(PrinterSupplyEvent, event_id)
    if e is None or e.printer_id != printer_id:
        raise HTTPException(404, "Supply event not found")
    if e.event_type not in ("REPLACEMENT_SUSPECTED", "REPLACEMENT_CONFIRMED"):
        raise HTTPException(409, "Only replacement events can be confirmed or rejected")
    e.status = "CONFIRMED" if body.decision == "CONFIRM" else "REJECTED"
    e.event_type = "REPLACEMENT_CONFIRMED" if body.decision == "CONFIRM" else "REPLACEMENT_REJECTED"
    e.confirmed_by, e.note = user.username, (body.note or e.note)
    if body.cost_amount is not None:
        e.cost_amount, e.currency = body.cost_amount, body.currency
    if body.decision == "CONFIRM":
        c = db.scalar(select(PrinterConsumable).where(PrinterConsumable.printer_id == printer_id,
                                                      PrinterConsumable.supply_index == e.supply_index))
        if c is not None:
            c.installed_at, c.installed_meter = e.observed_at, e.meter_value
    audit(db, "printer_supply_replacement_decided", username=user.username, target=printer_id,
          detail=f"event={event_id} {body.decision} cost={body.cost_amount}", request=request)
    return {"id": e.id, "status": e.status, "event_type": e.event_type}


@router.get("/{printer_id}/faults")
def faults(printer_id: str, days: int = Query(90, ge=1, le=3650), db: Session = Depends(get_db), user: User = viewer):
    p = _visible(db, printer_id)
    now = S.now_utc()
    since = now - timedelta(days=days)

    def dur(a, b):
        return round(((S._utc(b) if b else now) - S._utc(a)).total_seconds() / 60, 1)
    fs = list(db.scalars(select(PrinterFaultEvent).where(PrinterFaultEvent.printer_id == p.id,
                                                         or_(PrinterFaultEvent.ended_at.is_(None), PrinterFaultEvent.ended_at >= since))
                         .order_by(PrinterFaultEvent.started_at.desc())))
    periods = list(db.scalars(select(PrinterStatusPeriod).where(PrinterStatusPeriod.printer_id == p.id,
                                                                or_(PrinterStatusPeriod.ended_at.is_(None), PrinterStatusPeriod.ended_at >= since))
                              .order_by(PrinterStatusPeriod.started_at.desc()).limit(1000)))
    counts: dict[str, int] = {}
    for f in fs:
        counts[f.fault_code] = counts.get(f.fault_code, 0) + 1
    window = S.get_window(db)
    return {"printer_id": p.id, "period_days": days,
            "faults": [{"id": f.id, "code": f.fault_code, "category": f.category, "severity": f.severity,
                        "description": f.description, "started_at": S.iso(f.started_at), "ended_at": S.iso(f.ended_at),
                        "duration_minutes": dur(f.started_at, f.ended_at), "active": f.ended_at is None, "source": f.source,
                        "maintenance_record_id": f.maintenance_record_id, "service_ticket_ref": f.service_ticket_ref,
                        "recurrences_in_period": counts[f.fault_code]} for f in fs],
            "state_history": [{"dimension": s.dimension, "state": s.state, "started_at": S.iso(s.started_at),
                               "ended_at": S.iso(s.ended_at), "duration_minutes": dur(s.started_at, s.ended_at),
                               "evidence": s.evidence} for s in periods],
            "availability_7d": S.availability(db, p.id, now - timedelta(days=7), now),
            "recurring": {c: n for c, n in counts.items() if n >= 3},
            "maintenance": S.maintenance_dicts(db, [p.id]),
            "reliability": S.printer_reliability(db, [p.id], since, now, window)}


class MaintenanceRecord(BaseModel):
    component: str = Field(min_length=1, max_length=80)
    event_type: Literal["fault", "inspection", "service", "replacement"]
    severity: Literal["info", "warning", "critical"] = "info"
    description: str = Field(min_length=1, max_length=1500)
    incident_type: str | None = Field(None, max_length=32)
    cause_category: Literal["MECHANICAL", "WAN", "POWER", "USER", "CONSUMABLE", "UNKNOWN"] = "UNKNOWN"
    qualifying_failure: bool | None = None
    reported_at: datetime | None = None
    restored_at: datetime | None = None
    parts: list[str] | None = Field(None, max_length=50)
    technician: str | None = Field(None, max_length=100)
    vendor: str | None = Field(None, max_length=100)
    warranty: bool | None = None
    cost_amount: Decimal | None = Field(None, ge=0)
    currency: Literal["JMD", "USD"] = "JMD"
    meter_at_event: int | None = Field(None, ge=0)
    service_ticket_ref: str | None = Field(None, max_length=80)
    evidence: str | None = Field(None, max_length=255)
    fault_code: str | None = Field(None, max_length=50)


@router.post("/{printer_id}/maintenance-records", status_code=201)
def add_maintenance_record(printer_id: str, body: MaintenanceRecord, request: Request, db: Session = Depends(get_db),
                           user: User = admin):
    p = _visible(db, printer_id)
    if body.restored_at and body.reported_at and body.restored_at < body.reported_at:
        raise HTTPException(422, "restored_at must be after reported_at")
    if body.cause_category in ("WAN", "POWER") and body.qualifying_failure:
        if not body.evidence:
            raise HTTPException(422, "WAN/power interruptions count as failures only with supporting evidence")
    qualifying = body.qualifying_failure if body.qualifying_failure is not None else body.cause_category == "MECHANICAL"
    ev = PrinterMaintenanceEvent(printer_id=p.id, component=body.component, event_type=body.event_type, severity=body.severity,
                                 fault_code=body.fault_code, description=body.description, cost_amount=body.cost_amount,
                                 currency=body.currency, source="MANUAL", logged_by=user.username,
                                 observed_at=body.reported_at or S.now_utc())
    db.add(ev)
    db.flush()
    db.add(PrinterMaintenanceDetail(event_id=ev.id, incident_type=body.incident_type, cause_category=body.cause_category,
                                    qualifying_failure=qualifying, reported_at=body.reported_at, restored_at=body.restored_at,
                                    parts=body.parts, technician=body.technician, vendor=body.vendor, warranty=body.warranty,
                                    meter_at_event=body.meter_at_event, service_ticket_ref=body.service_ticket_ref,
                                    evidence=body.evidence))
    linked = _link_faults(db, p.id, ev.id, S._utc(body.reported_at) if body.reported_at else S.now_utc(),
                          S._utc(body.restored_at) if body.restored_at else None, body.service_ticket_ref) if body.reported_at else 0
    audit(db, "printer_maintenance_recorded", username=user.username, target=p.id,
          detail=f"{body.event_type} {body.component} cause={body.cause_category} qualifying={qualifying} linked_faults={linked}",
          request=request)
    return {"id": ev.id, "qualifying_failure": qualifying, "linked_faults": linked}


class MonitoringInput(BaseModel):
    enabled: bool
    credential_profile_id: str | None = None
    interval_seconds: int = Field(900, ge=300, le=86400)
    mib_profile_id: str | None = None   # None = automatic selection


@router.put("/{printer_id}/monitoring")
def set_monitoring(printer_id: str, body: MonitoringInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    p = _visible(db, printer_id)
    mon = S.monitoring(db, p.id)
    warnings = []
    if body.enabled:
        if not p.management_ip:
            raise HTTPException(422, "Set a management IP before enabling monitoring")
        if not (permitted_target(p.management_ip) or (S.emulation_enabled() and p.management_ip in S.EMULATORS)):
            raise HTTPException(403, "Management IP is outside approved printer management ranges")
        if not (S.emulation_enabled() and p.management_ip in S.EMULATORS):
            if not body.credential_profile_id:
                raise HTTPException(422, "Choose a printer credential profile")
            version = _check_profile_policy(db, body.credential_profile_id)
            if version != "3":
                warnings.append("SNMPv2c is permitted by policy but sends the community in clear text; prefer SNMPv3 authPriv")
    if body.mib_profile_id and db.get(PrinterMibProfile, body.mib_profile_id) is None:
        raise HTTPException(404, "MIB profile not found")
    mon.enabled = body.enabled
    mon.credential_profile_id = body.credential_profile_id or mon.credential_profile_id
    mon.interval_seconds = body.interval_seconds
    mon.profile_selection = "MANUAL" if body.mib_profile_id else "AUTO"
    mon.mib_profile_id = body.mib_profile_id or mon.mib_profile_id
    if body.enabled:
        mon.approved_by, mon.next_poll_at, mon.backoff_until = user.username, S.now_utc(), None
    audit(db, "printer_monitoring_changed", username=user.username, target=p.id,
          detail=f"enabled={body.enabled} interval={body.interval_seconds}s profile={'manual' if body.mib_profile_id else 'auto'}; read-only",
          request=request)
    return {"printer_id": p.id, "enabled": mon.enabled, "interval_seconds": mon.interval_seconds,
            "profile_selection": mon.profile_selection, "warnings": warnings}


@router.post("/{printer_id}/poll", status_code=202)
def poll_now(printer_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
             user: User = operator):
    p = _visible(db, printer_id)
    if not p.management_ip:
        raise HTTPException(422, "Printer has no management IP")
    j = S.create_job(db, "POLL", {"printer_id": p.id}, user.username, total=1)
    audit(db, "printer_poll_requested", username=user.username, target=p.id,
          detail="authenticated read-only diagnostic poll using the assigned profile", request=request)
    db.commit()
    background.add_task(S.run_job, session_scope, j.id, _owner())
    return _job_json(j)


class WalkInput(BaseModel):
    root: Literal["system", "host-resources-printer", "printer-mib"] = "printer-mib"
    max_rows: int = Field(500, ge=10, le=2000)


@router.post("/{printer_id}/diagnostic-walk", status_code=202)
def diagnostic_walk(printer_id: str, body: WalkInput, request: Request, background: BackgroundTasks,
                    db: Session = Depends(get_db), user: User = admin):
    p = _visible(db, printer_id)
    if not p.management_ip:
        raise HTTPException(422, "Printer has no management IP")
    j = S.create_job(db, "WALK", {"printer_id": p.id, "root": mib.DIAGNOSTIC_ROOTS[body.root], "max_rows": body.max_rows},
                     user.username)
    audit(db, "printer_diagnostic_walk", username=user.username, target=p.id,
          detail=f"bounded read-only GETNEXT walk of {body.root} (≤{body.max_rows} rows)", request=request)
    db.commit()
    background.add_task(S.run_job, session_scope, j.id, _owner())
    return _job_json(j)


class OrgInput(BaseModel):
    site_id: int | None = None
    location_ref: str | None = Field(None, max_length=120)
    division_ref: str | None = Field(None, max_length=120)
    section_ref: str | None = Field(None, max_length=120)
    unit_ref: str | None = Field(None, max_length=120)
    model_id: str | None = None
    in_service_since: datetime | None = None
    warranty_until: datetime | None = None
    is_colour: bool | None = None
    is_multifunction: bool | None = None
    technology: Literal["laser", "inkjet", "unknown"] | None = None


@router.patch("/{printer_id}/organization")
def set_organization(printer_id: str, body: OrgInput, request: Request, db: Session = Depends(get_db), user: User = admin):
    p = _visible(db, printer_id)
    changes = body.model_dump(exclude_unset=True)
    if "site_id" in changes:
        _site_allowed(db, changes["site_id"])
    if changes.get("model_id") and db.get(PrinterModel, changes["model_id"]) is None:
        raise HTTPException(404, "Model not found")
    ident = S.identity(db, p.id)
    for k, v in changes.items():
        setattr(ident, k, v)
    ident.updated_at = S.now_utc()
    audit(db, "printer_organization_updated", username=user.username, target=p.id,
          detail=json.dumps(changes, default=str)[:900], request=request)
    return {"printer_id": p.id, **{k: (S.iso(v) if isinstance(v, datetime) else v) for k, v in changes.items()}}


class IdentityDecision(BaseModel):
    decision: Literal["APPLY", "KEEP_REGISTERED", "SEPARATE_DEVICE"]
    note: str = Field(min_length=3, max_length=255)


@router.post("/{printer_id}/identity-changes/{change_id}/decide")
def decide_identity(printer_id: str, change_id: int, body: IdentityDecision, request: Request,
                    db: Session = Depends(get_db), user: User = admin):
    p = _visible(db, printer_id)
    c = db.get(PrinterIdentityChange, change_id)
    if c is None or c.printer_id != p.id:
        raise HTTPException(404, "Identity change not found")
    if c.decision != "NEEDS_REVIEW":
        raise HTTPException(409, "Already decided")
    ident = S.identity(db, p.id)
    if body.decision == "APPLY":
        if c.field == "model":
            ident.original_import_text = ident.original_import_text or json.dumps({"registered_model": p.model})
            p.model = c.new_value[:120]
            ident.model_id = S.ensure_model(db, p.manufacturer, p.model).id
        elif c.field == "serial_number":
            raise HTTPException(409, "A different serial is a different physical device; use SEPARATE_DEVICE")
        c.decision = "APPLIED"
    elif body.decision == "SEPARATE_DEVICE":
        if c.field != "serial_number":
            raise HTTPException(422, "SEPARATE_DEVICE applies to serial conflicts")
        mon = S.monitoring(db, p.id)
        mon.enabled = False
        p.management_ip = None
        ident.identity_state = "VERIFIED"
        c.decision = "SEPARATE_DEVICE"
    else:
        c.decision = "KEPT_REGISTERED"
    c.reason = f"{c.reason} | {user.username}: {body.note}"[:255]
    audit(db, "printer_identity_decided", username=user.username, target=p.id,
          detail=f"{c.field}: {c.old_value} → {c.new_value} decision={c.decision}", request=request)
    return {"id": c.id, "decision": c.decision}


# ---- verified printer configuration ---------------------------------------------------------------
class PrinterLocationChange(BaseModel):
    location: str = Field(max_length=255)

    @field_validator("location")
    @classmethod
    def printable_location(cls, value: str) -> str:
        from ..printers.configuration import validate_location
        return validate_location(value)


@router.get("/{printer_id}/configuration")
async def printer_configuration(printer_id: str, db: Session = Depends(get_db), user: User = admin):
    from ..printers import configuration as PC
    return await PC.view(db, _visible(db, printer_id))


@router.post("/{printer_id}/configuration/preview", status_code=201)
async def printer_configuration_preview(printer_id: str, body: PrinterLocationChange,
                                        db: Session = Depends(get_db), user: User = admin):
    from ..printers import configuration as PC
    return await PC.preview(db, _visible(db, printer_id), body.location, user.username)


@router.post("/{printer_id}/configuration/{plan_id}/apply")
async def printer_configuration_apply(printer_id: str, plan_id: str,
                                      db: Session = Depends(get_db), user: User = admin):
    from ..printers import configuration as PC
    return await PC.execute(db, _visible(db, printer_id), plan_id, user.username)


@router.post("/{printer_id}/configuration/{plan_id}/rollback")
async def printer_configuration_rollback(printer_id: str, plan_id: str,
                                         db: Session = Depends(get_db), user: User = admin):
    from ..printers import configuration as PC
    return await PC.execute(db, _visible(db, printer_id), plan_id, user.username, rollback=True)
