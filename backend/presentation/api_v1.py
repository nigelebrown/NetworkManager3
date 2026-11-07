"""Versioned product API (/api/v1). Legacy /api routes remain mounted unchanged as the compatibility facade."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import AccessPoint, Alert, AuditLog, ClientRecord, ConfigBackup, Site, SiteSample, User
from ..database.models_inm import (
    DeviceCapability, EventRecord, InmCluster, InmDevice, InmSite, RecommendationRecord, ReleaseEvent, SiteAccessGrant,
)
from ..database.session import get_db, session_scope
from ..monitoring.collector import visible_sites
from ..security.context import get_scope
from ..security.crypto import hash_password
from ..security.deps import ROLES, audit, get_current_user, require_any_role, require_role, verify_audit_chain
from ..application.health import collect
from ..infrastructure import probes
from ..infrastructure.runtime import inm_settings, log_store, telemetry_store
from .ws import hub

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _iso(dt):
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _visible_legacy_ids(db: Session) -> set[int]:
    return {s.id for s in visible_sites(db)}


def _not_implemented(phase: int, what: str):
    return JSONResponse(status_code=501, content={
        "status": "NOT_IMPLEMENTED", "phase": phase,
        "detail": f"{what} is delivered in Phase {phase}. Nothing was executed."})


# ---------------------------------------------------------------- system
@router.get("/ready", include_in_schema=False)
def ready():
    """Unauthenticated readiness for the reverse proxy and verify.sh. Booleans and a cursor only, no data."""
    db_ok = True
    try:
        with session_scope() as db:
            db.execute(select(1))
    except Exception:
        db_ok = False
    platform = inm_settings().platform_enabled
    ok = db_ok and (hub.connected_bus or not platform)
    return JSONResponse(status_code=200 if ok else 503, content={
        "ready": ok, "management_db": db_ok, "bus_subscriber": hub.connected_bus, "ws_cursor": hub.cursor})


@router.get("/system/version")
def version(_: User = Depends(get_current_user)):
    s, inm = get_settings(), inm_settings()
    return {"version": inm.version, "git_commit": inm.git_commit, "profile": s.inm_profile, "role": s.role,
            "mode": s.mode, "device_write_lock": s.device_write_lock,
            "device_writes_permitted": s.device_writes_permitted}


@router.get("/system/services")
def services(_: User = Depends(require_any_role("admin", "auditor"))):
    result = collect(probes.all_probes())
    result["websocket_gateway"] = {"bus_connected": hub.connected_bus, "clients": len(hub.clients), "cursor": hub.cursor}
    return result


@router.get("/system/features")
def features(_: User = Depends(get_current_user)):
    """Truthful module states for the navigation. PLANNED pages show their phase; DEMO is labelled."""
    s = get_settings()
    demo = s.mode == "demo"
    live_or_demo = "DEMO" if demo else "LIVE_READ_ONLY"
    return {"device_writes": "LOCKED" if not s.device_writes_permitted else "ENABLED", "modules": [
        {"key": "dashboard", "status": live_or_demo}, {"key": "aps", "status": live_or_demo},
        {"key": "clients", "status": live_or_demo}, {"key": "sites", "status": live_or_demo},
        {"key": "experience", "status": live_or_demo}, {"key": "alerts", "status": live_or_demo},
        {"key": "switches", "status": "DEMO", "phase_live": 2},
        {"key": "configuration", "status": "READ_PLAN_ONLY", "phase_write": 6},
        {"key": "firmware", "status": "READ_PLAN_ONLY", "phase_write": 6},
        {"key": "assistant", "status": "ADVISORY_READ_ONLY"},
        {"key": "logs", "status": "LIVE", "note": "platform events + synthetic syslog; device syslog Phase 3"},
        {"key": "audit", "status": "LIVE"}, {"key": "system_health", "status": "LIVE"},
        {"key": "recovery", "status": "PLANNED", "phase": 5, "note": "legacy backups are NOT RESTORABLE"},
        {"key": "ai_learning", "status": "RULES_ONLY", "phase_ml": 8},
        {"key": "onboarding", "status": "PLANNED", "phase": 2},
        {"key": "change_control", "status": "PLANNED", "phase": 6},
    ]}


class SyntheticBody(BaseModel):
    note: str = Field(default="operator synthetic probe", max_length=120)


@router.post("/system/synthetic-event", status_code=202)
async def synthetic_event(body: SyntheticBody, request: Request, user: User = Depends(require_role("admin"))):
    """Ask the network worker (over NATS) to emit one synthetic DEMO event through the full pipeline."""
    nc = getattr(request.app.state, "nc", None)
    if nc is None or not nc.is_connected:
        raise HTTPException(503, "Event bus unavailable: the synthetic round-trip cannot start (system DEGRADED)")
    from ..infrastructure.bus.nats_bus import SYNTHETIC_JOB_SUBJECT
    cid = str(uuid.uuid4())
    try:
        reply = await nc.request(SYNTHETIC_JOB_SUBJECT, json.dumps(
            {"correlation_id": cid, "requested_by": user.username, "note": body.note}).encode(), timeout=8)
    except Exception as exc:
        raise HTTPException(503, f"Network worker did not accept the job ({type(exc).__name__})")
    out = json.loads(reply.data)

    def _audit():
        with session_scope() as db:
            audit(db, "synthetic_event_requested", username=user.username, target=out.get("event_id"),
                  detail=f"correlation {cid}", request=request, correlation_id=cid)
    await run_in_threadpool(_audit)
    return {"correlation_id": cid, **out}


@router.get("/system/synthetic-event/{correlation_id}")
def synthetic_trace(correlation_id: str, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    try:
        uuid.UUID(correlation_id)
    except ValueError:
        raise HTTPException(422, "correlation_id must be a UUID")
    recs = list(db.scalars(select(EventRecord).where(EventRecord.correlation_id == correlation_id)))
    trace = {"correlation_id": correlation_id,
             "management_db": [{"event_id": r.event_id, "event_type": r.event_type, "ingested_at": _iso(r.ingested_at),
                                "delivery_count": r.delivery_count, "source_type": r.source_type} for r in recs]}
    store = telemetry_store()
    trace["timeseries_db"] = [] if not (store and recs) else store.query(
        device_id=None, metric_name=None, since=datetime.now(timezone.utc) - timedelta(days=2), limit=50,
        source_event_id=recs[0].event_id)
    lstore = log_store()
    try:
        trace["loki"] = [] if not lstore else lstore.query(
            f'{{app="inm"}} |= "{correlation_id}"', datetime.now(timezone.utc) - timedelta(hours=6), 20)
    except Exception as exc:
        trace["loki"], trace["loki_error"] = [], str(exc)[:200]
    trace["websocket"] = hub.traces.get(correlation_id)
    trace["complete"] = bool(trace["management_db"] and trace["timeseries_db"] and trace["loki"] and trace["websocket"])
    return trace


# ---------------------------------------------------------------- inventory
def _device_out(d: InmDevice, caps: list[DeviceCapability] | None = None) -> dict:
    out = {"id": d.id, "source_type": d.source_type, "environment": d.environment, "vendor": d.vendor,
           "family": d.family, "model": d.model, "serial_number": d.serial_number, "hostname": d.hostname,
           "firmware": d.firmware, "status": d.status, "adapter": d.adapter, "site_id": d.site_id,
           "cluster_id": d.cluster_id, "legacy_site_id": d.legacy_site_id, "legacy_ap_id": d.legacy_ap_id,
           "last_observed_at": _iso(d.last_observed_at)}
    if caps is not None:
        out["capabilities"] = [{"operation": c.operation, "status": c.status, "evidence": c.evidence} for c in caps]
    return out


@router.get("/sites")
def v1_sites(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _visible_legacy_ids(db)
    clusters = [c for c in db.scalars(select(InmCluster)) if c.legacy_site_id in ids]
    site_ids = {c.site_id for c in clusters}
    sites = [s for s in db.scalars(select(InmSite)) if s.id in site_ids]
    return {"sites": [{"id": s.id, "name": s.name, "source_type": s.source_type, "description": s.description} for s in sites],
            "clusters": [{"id": c.id, "name": c.name, "kind": c.kind, "source_type": c.source_type, "site_id": c.site_id,
                          "legacy_site_id": c.legacy_site_id} for c in clusters]}


@router.get("/devices")
def v1_devices(family: str | None = Query(None, pattern="^(access_point|switch)$"), db: Session = Depends(get_db),
               _: User = Depends(require_role("viewer"))):
    ids = _visible_legacy_ids(db)
    q = select(InmDevice).order_by(InmDevice.family, InmDevice.hostname)
    if family:
        q = q.where(InmDevice.family == family)
    devs = [d for d in db.scalars(q) if d.legacy_site_id in ids]
    caps: dict[str, list] = {}
    for c in db.scalars(select(DeviceCapability).where(DeviceCapability.device_id.in_([d.id for d in devs]))):
        caps.setdefault(c.device_id, []).append(c)
    return [_device_out(d, caps.get(d.id, [])) for d in devs]


def _visible_device(db: Session, device_id: str) -> InmDevice:
    d = db.get(InmDevice, device_id)
    if not d or d.legacy_site_id not in _visible_legacy_ids(db):
        raise HTTPException(404, "Device not found")
    return d


@router.get("/devices/{device_id}")
def v1_device(device_id: str, db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    d = _visible_device(db, device_id)
    caps = list(db.scalars(select(DeviceCapability).where(DeviceCapability.device_id == d.id)))
    return _device_out(d, caps)


@router.get("/devices/{device_id}/metrics")
def v1_device_metrics(device_id: str, metric: str | None = Query(None, max_length=64), hours: int = Query(6, ge=1, le=168),
                      db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    d = _visible_device(db, device_id)
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Time-series database not configured")
    return {"device_id": d.id, "source_type": d.source_type,
            "points": store.query(device_id=d.id, metric_name=metric, since=datetime.now(timezone.utc) - timedelta(hours=hours), limit=2000)}


@router.get("/devices/{device_id}/snapshots")
def v1_device_snapshots(device_id: str, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    d = _visible_device(db, device_id)
    rows = list(db.scalars(select(ConfigBackup).where(ConfigBackup.site_id == d.legacy_site_id).order_by(ConfigBackup.taken_at.desc()).limit(50)))
    return {"vault": {"status": "PLANNED", "phase": 5},
            "sanitized_snapshots": [{"id": b.id, "taken_at": _iso(b.taken_at), "kind": b.kind, "sha256": b.sha256,
                                     "classification": b.classification, "restorable": False} for b in rows]}


# ---------------------------------------------------------------- events / telemetry / logs
@router.get("/events")
def v1_events(limit: int = Query(100, ge=1, le=500), event_type: str | None = Query(None, max_length=64),
              db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _visible_legacy_ids(db)
    scope = get_scope()
    q = select(EventRecord).order_by(EventRecord.ingested_at.desc()).limit(limit * 3)
    if event_type:
        q = q.where(EventRecord.event_type == event_type)
    out = []
    for r in db.scalars(q):
        if r.legacy_site_id is not None and r.legacy_site_id not in ids:
            continue
        if r.legacy_site_id is None and scope is not None:
            continue
        out.append({"event_id": r.event_id, "event_type": r.event_type, "occurred_at": _iso(r.occurred_at),
                    "ingested_at": _iso(r.ingested_at), "correlation_id": r.correlation_id, "source_type": r.source_type,
                    "source_service": r.source_service, "actor": f"{r.actor_type}:{r.actor_id}", "device_id": r.device_id,
                    "legacy_site_id": r.legacy_site_id, "data_quality": r.data_quality, "summary": r.summary,
                    "delivery_count": r.delivery_count})
        if len(out) >= limit:
            break
    return out


_FILTER_OK = re.compile(r"^[\w .:@/+\-]{0,100}$")


@router.get("/logs")
def v1_logs(stream: str = Query("events", pattern="^(events|syslog)$"), contains: str = Query("", max_length=100),
            minutes: int = Query(60, ge=1, le=10080), limit: int = Query(200, ge=1, le=1000),
            db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "operator"))):
    """Read-only log search. Users choose a stream and a plain-text filter; arbitrary LogQL is not accepted."""
    if not _FILTER_OK.match(contains):
        raise HTTPException(422, "filter may contain letters, digits, spaces and . : @ / + - _ only")
    store = log_store()
    if store is None:
        raise HTTPException(503, "Logging service not configured")
    selector = '{app="inm"}' if stream == "events" else '{source="syslog"}'
    logql = selector + (f' |= "{contains}"' if contains else "")
    try:
        entries = store.query(logql, datetime.now(timezone.utc) - timedelta(minutes=minutes), limit)
    except Exception as exc:
        raise HTTPException(503, f"Loki query failed: {str(exc)[:200]}")
    if stream == "syslog":
        ids = _visible_legacy_ids(db)
        by_host = {d.hostname: d for d in db.scalars(select(InmDevice)) if d.hostname and d.legacy_site_id in ids}
        for e in entries:
            host = e["labels"].get("host") or e["labels"].get("hostname")
            dev = by_host.get(host or "")
            e["device"] = _device_out(dev) if dev else None
            e["correlation"] = "MAPPED" if dev else "UNMAPPED"
            e["unmapped_reason"] = None if dev else (f"no visible device with hostname '{host}'" if host else "message carried no hostname")
    return {"stream": stream, "query": logql, "entries": entries}


@router.get("/telemetry")
def v1_telemetry(metric: str | None = Query(None, max_length=64), hours: int = Query(1, ge=1, le=168),
                 limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Time-series database not configured")
    ids = _visible_legacy_ids(db)
    visible = {d.id for d in db.scalars(select(InmDevice)) if d.legacy_site_id in ids}
    rows = store.query(device_id=None, metric_name=metric, since=datetime.now(timezone.utc) - timedelta(hours=hours), limit=limit)
    return [r for r in rows if r["device_id"] in visible]


# ---------------------------------------------------------------- audit
@router.get("/audit/events")
def v1_audit(limit: int = Query(200, ge=1, le=2000), action: str | None = Query(None, max_length=64),
             username: str | None = Query(None, max_length=64), db: Session = Depends(get_db),
             _: User = Depends(require_any_role("admin", "auditor"))):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        q = q.where(AuditLog.action == action)
    if username:
        q = q.where(AuditLog.username == username)
    return [{"id": a.id, "ts": _iso(a.ts), "actor": a.username, "actor_role": a.actor_role, "actor_kind": a.actor_kind,
             "action": a.action, "target": a.target, "detail": a.detail, "outcome": "success" if a.success else "denied/failed",
             "source_ip": a.source_ip, "request_id": a.request_id, "correlation_id": a.correlation_id,
             "session_id_hash": a.session_id_hash, "channel": a.channel or "LEGACY", "prev_hash": a.prev_hash,
             "event_hash": a.event_hash} for a in db.scalars(q)]


@router.get("/audit/verify")
def v1_audit_verify(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor"))):
    result = verify_audit_chain(db)
    audit(db, "audit_chain_verified", username=user.username, detail=json.dumps({"ok": result["ok"], "checked": result["checked"]}),
          request=request, success=result["ok"])
    return result


@router.get("/audit/export")
def v1_audit_export(request: Request, db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor"))):
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "ts", "username", "actor_role", "actor_kind", "action", "target", "detail", "success", "source_ip",
            "request_id", "correlation_id", "session_id_hash", "channel", "prev_hash", "event_hash"]
    w.writerow(cols)
    for a in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        w.writerow([_iso(a.ts) if c == "ts" else getattr(a, c) for c in cols])
    audit(db, "audit_exported", username=user.username, request=request)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=inm-audit.csv"})


@router.get("/release/events")
def v1_release_events(db: Session = Depends(get_db), _: User = Depends(require_any_role("admin", "auditor"))):
    return [{"id": r.id, "ts": _iso(r.ts), "operation": r.operation, "operator": r.operator, "version": r.version,
             "git_commit": r.git_commit, "environment": r.environment, "outcome": r.outcome,
             "schema_revisions": r.schema_revisions, "image_digests": r.image_digests, "detail": r.detail}
            for r in db.scalars(select(ReleaseEvent).order_by(ReleaseEvent.id.desc()).limit(200))]


# ---------------------------------------------------------------- recommendations (isolated AI service)
def _analysis_context(db: Session) -> dict:
    """Pseudonymised per-site aggregates: no MACs, IPs, hostnames, credentials or device addresses."""
    sites = []
    for i, s in enumerate(visible_sites(db), start=1):
        aps = list(db.scalars(select(AccessPoint).where(AccessPoint.site_id == s.id)))
        utils = [r.get("util_percent") for a in aps for r in (a.radios or []) if r.get("util_percent") is not None]
        sample = db.scalar(select(SiteSample).where(SiteSample.site_id == s.id).order_by(SiteSample.ts.desc()).limit(1))
        clients = list(db.scalars(select(ClientRecord).where(ClientRecord.site_id == s.id, ClientRecord.is_connected.is_(True))))
        crit = db.scalar(select(func.count()).select_from(Alert).where(Alert.site_id == s.id, Alert.status == "active",
                                                                         Alert.severity == "critical")) or 0
        sites.append({"site_id": s.id, "label": f"site-{i}", "aps_total": len(aps),
                      "aps_down": sum(1 for a in aps if a.status != "up"),
                      "max_util_percent": max(utils) if utils else None,
                      "exp_score": sample.exp_score if sample else None, "clients_total": len(clients),
                      "clients_poor": sum(1 for c in clients if c.exp_band == "poor"), "alerts_critical": crit})
    return {"source_type": "DEMO" if get_settings().mode == "demo" else "REAL", "sites": sites}


@router.post("/ai/analyze")
async def v1_ai_analyze(request: Request, user: User = Depends(require_role("operator"))):
    s = inm_settings()
    if not s.ai_url:
        raise HTTPException(503, "AI service not configured")
    def build():  # runs in a copied context that shares this request's RequestCtx (scope already resolved)
        with session_scope() as db:
            return _analysis_context(db)
    ctx = await run_in_threadpool(build)
    cid = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(s.ai_url.rstrip("/") + "/v1/analyze", json={"context": ctx},
                             headers={"Authorization": f"Bearer {s.ai_shared_token}"})
        r.raise_for_status()
        result = r.json()
    except Exception as exc:
        raise HTTPException(503, f"AI service unavailable ({type(exc).__name__})")
    if result.get("engine") != "RULE" or result.get("trained_model"):
        raise HTTPException(502, "AI service returned an unexpected engine claim for Phase 1")

    def store():
        with session_scope() as db:
            rows = []
            for rec in result["recommendations"]:
                row = RecommendationRecord(
                    legacy_site_id=rec.get("legacy_site_id"), engine="RULE", rule_id=rec["rule_id"],
                    rule_version=rec["rule_version"], source_type=ctx["source_type"], risk=rec["risk"], title=rec["title"],
                    explanation=rec["explanation"], evidence=rec["evidence"], input_window="latest poll",
                    approval_state="ADVISORY", requested_by=user.username, correlation_id=cid)
                db.add(row)
                rows.append(row)
            audit(db, "ai_analyze", username=user.username, detail=f"{len(rows)} rule recommendations", request=request,
                  correlation_id=cid)
            return len(rows)
    n = await run_in_threadpool(store)
    nc = getattr(request.app.state, "nc", None)
    if nc is not None and nc.is_connected:
        from ..infrastructure.bus.nats_bus import JetStreamBus
        await JetStreamBus(nc.jetstream()).publish_ui(["recommendations"], legacy_site_ids=None, event_id=cid, correlation_id=cid)
    return {"correlation_id": cid, "engine": "RULE", "ruleset_version": result["ruleset_version"], "trained_model": False,
            "source_type": ctx["source_type"], "created": n}


@router.get("/recommendations")
def v1_recommendations(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _visible_legacy_ids(db)
    names = {s.id: s.name for s in visible_sites(db)}
    rows = db.scalars(select(RecommendationRecord).order_by(RecommendationRecord.created_at.desc()).limit(200))
    return [{"id": r.id, "created_at": _iso(r.created_at), "site": names.get(r.legacy_site_id), "legacy_site_id": r.legacy_site_id,
             "engine": r.engine, "rule_id": r.rule_id, "rule_version": r.rule_version, "source_type": r.source_type,
             "risk": r.risk, "title": r.title, "explanation": r.explanation, "evidence": r.evidence,
             "input_window": r.input_window, "approval_state": r.approval_state, "requested_by": r.requested_by,
             "correlation_id": r.correlation_id, "trained_model": False}
            for r in rows if r.legacy_site_id is None or r.legacy_site_id in ids]


# ---------------------------------------------------------------- recovery (Phase 5/7 planned)
@router.get("/recovery/snapshots")
def v1_recovery(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    ids = _visible_legacy_ids(db)
    rows = [b for b in db.scalars(select(ConfigBackup).order_by(ConfigBackup.taken_at.desc()).limit(200)) if b.site_id in ids]
    return {"vault": {"status": "PLANNED", "phase": 5, "detail": "Encrypted full-fidelity vault arrives in Phase 5."},
            "replacement_wizard": {"status": "PLANNED", "phase": 7},
            "restore_candidates": [],
            "sanitized_snapshots": [{"id": b.id, "site_id": b.site_id, "taken_at": _iso(b.taken_at), "kind": b.kind,
                                     "sha256": b.sha256, "classification": b.classification, "restorable": False,
                                     "reason": "redacted display copy; secrets removed, cannot restore a device"} for b in rows]}


# ---------------------------------------------------------------- users (named identities, site grants)
class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    role: str
    temporary_password: str = Field(min_length=12, max_length=256)
    site_scope: str = Field(default="all", pattern="^(all|granted)$")


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    site_scope: str | None = Field(default=None, pattern="^(all|granted)$")


class Grants(BaseModel):
    site_ids: list[int]


def _user_out(db: Session, u: User) -> dict:
    grants = sorted(db.scalars(select(SiteAccessGrant.site_id).where(SiteAccessGrant.user_id == u.id)))
    return {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "site_scope": u.site_scope,
            "site_grants": grants, "must_change_password": u.must_change_password, "created_at": _iso(u.created_at)}


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return [_user_out(db, u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "username already exists")
    u = User(username=body.username, role=body.role, password_hash=hash_password(body.temporary_password),
             must_change_password=True, site_scope=body.site_scope)
    db.add(u)
    db.flush()
    audit(db, "user_created", username=admin.username, target=body.username, detail=f"role={body.role} scope={body.site_scope}", request=request)
    return _user_out(db, u)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, f"role must be one of {', '.join(ROLES)}")
        if u.id == admin.id and body.role != "admin":
            raise HTTPException(422, "you cannot remove your own admin role")
    if body.is_active is False and u.id == admin.id:
        raise HTTPException(422, "you cannot deactivate yourself")
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    for k, v in changes.items():
        setattr(u, k, v)
    audit(db, "user_updated", username=admin.username, target=u.username, detail=json.dumps(changes), request=request)
    return _user_out(db, u)


@router.put("/users/{user_id}/grants")
def set_grants(user_id: int, body: Grants, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    valid = {s.id for s in db.scalars(select(Site))}
    bad = set(body.site_ids) - valid
    if bad:
        raise HTTPException(422, f"unknown site ids: {sorted(bad)}")
    for g in db.scalars(select(SiteAccessGrant).where(SiteAccessGrant.user_id == u.id)):
        db.delete(g)
    for sid in sorted(set(body.site_ids)):
        db.add(SiteAccessGrant(user_id=u.id, site_id=sid, granted_by=admin.username))
    audit(db, "site_grants_set", username=admin.username, target=u.username, detail=f"sites={sorted(set(body.site_ids))}", request=request)
    db.flush()
    return _user_out(db, u)


class ResetBody(BaseModel):
    temporary_password: str = Field(min_length=12, max_length=256)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    u.password_hash, u.must_change_password, u.failed_logins, u.locked_until = hash_password(body.temporary_password), True, 0, None
    audit(db, "password_reset", username=admin.username, target=u.username, request=request)
    return {"ok": True}


# ---------------------------------------------------------------- later-phase contracts: explicit 501, never fake success
@router.post("/discovery/jobs")
def discovery_create(_: User = Depends(require_role("operator"))):
    return _not_implemented(2, "Credential-profile discovery")


@router.get("/discovery/jobs/{job_id}")
def discovery_get(job_id: str, _: User = Depends(require_role("viewer"))):
    return _not_implemented(2, "Discovery job status")


@router.post("/changes")
def changes_create(_: User = Depends(require_role("operator"))):
    return _not_implemented(6, "Controlled change requests")


@router.post("/changes/{change_id}/approve")
def changes_approve(change_id: str, _: User = Depends(get_current_user)):
    return _not_implemented(6, "Independent change approval")


@router.post("/changes/{change_id}/execute")
def changes_execute(change_id: str, _: User = Depends(get_current_user)):
    return _not_implemented(6, "Change execution")
