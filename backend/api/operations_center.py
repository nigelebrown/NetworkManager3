"""NOC + executive dashboards, configuration best-practice compliance and the demo-data switch."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import Alert, AuditLog, ClientRecord, Site, User
from ..database.models_inm import InmDevice, InmSite, RecommendationRecord
from ..database.models_phases import (ChangeAttribution, ChangePlan, CollectionPlan, CollectionRun,
                                      ConfigArtifact, DeviceLogEvent)
from ..database.models_templates import InmConfigTemplate
from ..database.session import get_db
from ..domain import best_practices as BP
from ..domain import collection as C
from ..domain import noc as N
from ..domain import vault as V
from ..domain.template_inheritance import preview as inherit_preview
from ..infrastructure import demo_dataset
from ..infrastructure.phase_services import LocalObjectStore, vault_master_key
from ..infrastructure.runtime import telemetry_store
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, real_device

router = APIRouter(prefix="/api/v1", tags=["operations-center"])


# ---- demo data switch -------------------------------------------------------------------
@router.get("/demo-data")
def demo_status(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return demo_dataset.status(db)


@router.post("/demo-data/load")
def demo_load(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    counts = demo_dataset.load(db, actor=user.username, store=telemetry_store())
    audit(db, "demo_dataset_loaded", username=user.username, detail=str(counts), request=request)
    return {"status": demo_dataset.status(db), "created": counts}


@router.post("/demo-data/remove")
def demo_remove(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    removed = demo_dataset.remove(db, actor=user.username, store=telemetry_store())
    audit(db, "demo_dataset_removed", username=user.username,
          detail=f"{sum(v for v in removed.values())} demo rows removed; REAL data untouched", request=request)
    return {"status": demo_dataset.status(db), "removed": removed}


# ---- compliance -----------------------------------------------------------------------------
def _config_text(db: Session, device_id: str) -> tuple[str | None, ConfigArtifact | None]:
    a = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device_id,
                                               ConfigArtifact.fidelity == V.FIDELITY_FULL)
                  .order_by(ConfigArtifact.created_at.desc()).limit(1))
    if a is None:
        return None, None
    try:
        return V.open_sealed(LocalObjectStore().get(a.object_path), wrapped_key=a.wrapped_key, manifest=a.manifest,
                             signature=a.signature, master_key=vault_master_key()).decode(errors="replace"), a
    except (V.VaultIntegrityError, OSError, ValueError):
        return None, a


def _device_compliance(db: Session, d: InmDevice, templates: list) -> dict:
    text, art = _config_text(db, d.id)
    res = BP.evaluate(d.family, text)
    intended = inherit_preview(d, templates)
    res["intended_vs_installed"] = BP.intended_vs_installed(
        d.family, intended["effective"] if intended["templates"] else None, text)
    res["intended_sources"] = intended["templates"]
    res.update(device_id=d.id, hostname=d.hostname, family=d.family, model=d.model, source_type=d.source_type,
               config_captured_at=iso(art.created_at) if art else None,
               config_integrity="FAIL" if art and text is None else ("PASS" if art else None))
    return res


def _templates(db: Session) -> list:
    return list(db.scalars(select(InmConfigTemplate).where(InmConfigTemplate.enabled.is_(True))))


@router.get("/compliance/devices/{device_id}")
def device_compliance(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return _device_compliance(db, real_device(db, device_id), _templates(db))


@router.get("/compliance/fleet")
def fleet_compliance(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    tpl = _templates(db)
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    rows, rule_fail = [], Counter()
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type.in_(["REAL", "DEMO"]),
                                                InmDevice.family.in_(["access_point", "switch"]))):
        c = _device_compliance(db, d, tpl)
        for r in c["results"]:
            if r["status"] == "FAIL":
                rule_fail[(r["id"], r["title"], r["severity"], r["category"])] += 1
        rows.append({k: c[k] for k in ("device_id", "hostname", "family", "model", "source_type", "score", "grade",
                                        "counts", "config_available", "config_captured_at")}
                    | {"site": sites.get(d.site_id), "drift": c["intended_vs_installed"]["status"],
                       "critical_failures": c.get("critical_failures", False)})
    scored = [r["score"] for r in rows if r["score"] is not None]
    by_cat: dict[str, int] = defaultdict(int)
    for (rid, title, sev, cat), n in rule_fail.items():
        by_cat[cat] += n
    return {"devices": sorted(rows, key=lambda r: (r["score"] is None, r["score"] or 0)),
            "fleet_score": round(sum(scored) / len(scored), 1) if scored else None,
            "devices_without_config": sum(1 for r in rows if not r["config_available"]),
            "grades": dict(Counter(r["grade"] for r in rows)),
            "top_failures": [{"id": k[0], "title": k[1], "severity": k[2], "category": k[3], "devices": n}
                             for k, n in rule_fail.most_common(12)],
            "failures_by_category": dict(by_cat),
            "drift": dict(Counter(r["drift"] for r in rows))}


@router.get("/compliance/rules")
def rules(user: User = Depends(require_role("viewer"))):
    return [{"id": r.id, "family": r.family, "category": r.category, "severity": r.severity, "title": r.title,
             "why": r.why, "fix": r.fix} for r in BP.RULES]


# ---- NOC -----------------------------------------------------------------------------------
@router.get("/noc/overview")
def noc(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    t = now()
    store = telemetry_store()
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    from ..infrastructure.device_state import canonical_inventory, plans_by_device, state_of
    plans = plans_by_device(db)
    tiles = []
    controllers = {s.id: s for s in db.scalars(select(Site))}
    devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type.in_(["REAL", "DEMO"]))
                              .order_by(InmDevice.hostname)))
    for d in canonical_inventory(devices):
        controller = controllers.get(d.legacy_site_id)
        p = plans.get(d.id)
        fresh = C.freshness(p.last_success_at, p.interval_seconds, t) if p else ("MANUAL" if d.last_observed_at else "NEVER")
        rows = store.query(device_id=d.id, metric_name=None, since=t - timedelta(hours=1), limit=2000,
                           entity_type="device") if store else []
        latest = N.latest_by_metric(rows)
        st = state_of(d, p, now=t, controller=controller)
        if controller and d.legacy_ap_id is not None:
            fresh = st["freshness"]
        health_inputs = latest if st["state"] in ("ONLINE", "DEGRADED") else {}
        h = N.health(d.family, health_inputs, fresh, "down" if st["state"] == "OFFLINE" else None)
        st = state_of(d, p, now=t, health_band=h["band"], controller=controller)
        tiles.append({"device_id": d.id, "hostname": d.hostname, "site": sites.get(d.site_id), "family": d.family,
                      "model": d.model, "firmware": d.firmware, "status": d.status, "source_type": d.source_type,
                      "freshness": fresh, "health": h, "state": st["state"], "state_reason": st["state_reason"],
                      "last_persisted_at": st["last_persisted_at"],
                      "metrics": {k: v["value"] for k, v in latest.items()},
                      "last_failure": st["last_failure"]})
    status = Counter(x["status"] for x in tiles)
    fresh = Counter(x["freshness"] for x in tiles)
    since = t - timedelta(hours=1)
    runs = Counter(r for r in db.scalars(select(CollectionRun.state).where(CollectionRun.started_at >= since)))
    events = [{"at": iso(e.observed_at), "device_id": e.device_id, "severity": e.severity, "message": e.message,
               "config_change": e.is_config_change}
              for e in db.scalars(select(DeviceLogEvent).order_by(DeviceLogEvent.observed_at.desc()).limit(25))]
    from ..database.models_phases import InmAlert
    alerts = [{"id": a.id, "severity": a.severity, "message": a.message, "created_at": iso(a.opened_at),
               "simulated": a.source_type == "DEMO", "rule_id": a.rule_id, "device_id": a.device_id,
               "suppressed": a.suppressed}
              for a in db.scalars(select(InmAlert).where(InmAlert.state.in_(("OPEN", "ACKNOWLEDGED")))
                                  .order_by(InmAlert.opened_at.desc()).limit(25))]
    alerts += [{"id": a.id, "severity": a.severity, "message": a.message, "created_at": iso(a.raised_at),
                "simulated": a.simulated}
               for a in db.scalars(select(Alert).where(Alert.status != "resolved").order_by(Alert.raised_at.desc()).limit(25))]
    worst = sorted([x for x in tiles if x["health"]["score"] is not None], key=lambda x: x["health"]["score"])[:10]
    active_sites = {d.legacy_site_id for d in devices if d.legacy_ap_id is not None
                    and state_of(d, plans.get(d.id), now=t, controller=controllers.get(d.legacy_site_id))["freshness"] == "FRESH"
                    and controllers.get(d.legacy_site_id) and controllers[d.legacy_site_id].last_poll_ok}
    clients = db.scalar(select(func.count()).select_from(ClientRecord).where(
        ClientRecord.site_id.in_(active_sites), ClientRecord.is_connected.is_(True))) or 0
    clients += sum(x["metrics"].get("clients", x["metrics"].get("client_count", 0)) or 0
                   for x in tiles if x["state"] in ("ONLINE", "DEGRADED")
                   and next((d.legacy_ap_id for d in devices if d.id == x["device_id"]), None) is None)
    return {"generated_at": iso(t), "devices": tiles,
            "summary": {"devices": len(tiles), "up": status.get("up", 0), "down": status.get("down", 0),
                        "states": dict(Counter(x["state"] for x in tiles)),
                        "unknown": status.get("unknown", 0), "fresh": fresh.get("FRESH", 0),
                        "stale_or_gap": fresh.get("STALE", 0) + fresh.get("GAP", 0),
                        "never_polled": fresh.get("NEVER", 0) + fresh.get("MANUAL", 0),
                        "clients": int(clients), "active_alerts": len(alerts),
                        "health_bands": dict(Counter(x["health"]["band"] for x in tiles))},
            "collection_last_hour": dict(runs), "worst_experience": worst, "events": events, "alerts": alerts}


@router.get("/devices/{device_id}/metrics/series")
def series(device_id: str, metric: str, hours: int = 24, entity_type: str = "device", entity_id: str = "",
           db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """One time series = one (device, entity, metric). Device-level by default; pass entity_type=port and
    entity_id=1/1/3 for a single interface (E2E-02). Series of different entities are never mixed."""
    d = real_device(db, device_id)
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Telemetry store unavailable.")
    hours = max(1, min(hours, 24 * 30))
    rows = store.query(device_id=d.id, metric_name=metric, since=now() - timedelta(hours=hours), limit=5000,
                       entity_type=entity_type, entity_id=entity_id)
    return {"device_id": d.id, "metric": metric, "entity_type": entity_type, "entity_id": entity_id,
            "points": [{"t": r["observed_at"], "v": r["value"], "q": r["quality"], "src": r["source_type"],
                        "event_id": r["source_event_id"]} for r in reversed(rows)]}


@router.get("/devices/{device_id}/ports/{port:path}/series")
def port_series(device_id: str, port: str, metric: str = "port_util_percent", hours: int = 24,
                db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return series(device_id, metric, hours, "port", port, db, user)


@router.get("/devices/{device_id}/metrics/entities")
def entities(device_id: str, hours: int = 1, db: Session = Depends(get_db),
             user: User = Depends(require_role("viewer"))):
    """Latest value per (entity, metric) — e.g. all 48 ports of a switch, each distinct."""
    d = real_device(db, device_id)
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Telemetry store unavailable.")
    latest: dict[tuple, dict] = {}
    for r in store.query(device_id=d.id, metric_name=None, since=now() - timedelta(hours=max(1, min(hours, 24))),
                         limit=20000):
        key = (r["entity_type"], r["entity_id"], r["metric_name"])
        if key not in latest:
            latest[key] = r
    out: dict[str, dict] = {}
    for (et, ei, m), r in sorted(latest.items()):
        out.setdefault(f"{et}:{ei}" if ei else et, {})[m] = {"v": r["value"], "t": r["observed_at"],
                                                            "event_id": r["source_event_id"]}
    return {"device_id": d.id, "entities": out}


# ---- executive trends ----------------------------------------------------------------------
@router.get("/executive/trends")
def trends(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "approver", "operator"))):
    t = now()
    store = telemetry_store()
    # per (hour, device) averages, then fleet sum (clients) or fleet mean (utilisation, CPU)
    buckets: dict[tuple, list] = defaultdict(list)
    if store is not None:
        for metric in ("clients", "util_5g_percent", "cpu_percent"):
            for r in store.query(device_id=None, metric_name=metric, since=t - timedelta(hours=24), limit=5000):
                if r["value"] is not None:
                    buckets[(str(r["observed_at"])[:13], metric, r["device_id"])].append(r["value"])
    per_hour: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for (hk, metric, _dev), vals in buckets.items():
        per_hour[hk][metric].append(sum(vals) / len(vals))
    hours = []
    for hk in sorted(per_hour):
        m = per_hour[hk]
        row = {"hour": hk[11:13] + ":00"}
        if m.get("clients"):
            row["clients"] = round(sum(m["clients"]), 1)
        for k in ("util_5g_percent", "cpu_percent"):
            if m.get(k):
                row[k] = round(sum(m[k]) / len(m[k]), 1)
        hours.append(row)
    days = defaultdict(lambda: {"plans": 0, "approved": 0, "rejected": 0})
    for p in db.scalars(select(ChangePlan).where(ChangePlan.created_at >= t - timedelta(days=14))):
        k = iso(p.created_at)[:10]
        days[k]["plans"] += 1
        if p.state == "APPROVED":
            days[k]["approved"] += 1
        if p.state == "REJECTED":
            days[k]["rejected"] += 1
    attr = Counter(a.level for a in db.scalars(select(ChangeAttribution).where(ChangeAttribution.supersedes_id.is_(None))))
    recs = Counter(r.approval_state for r in db.scalars(select(RecommendationRecord)))
    audit_n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.ts >= t - timedelta(days=1))) or 0
    return {"hourly": hours, "changes_by_day": [dict(day=k, **v) for k, v in sorted(days.items())],
            "attribution": dict(attr), "recommendations": dict(recs), "audit_events_24h": audit_n}
