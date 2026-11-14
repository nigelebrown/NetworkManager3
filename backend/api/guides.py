"""Guided workflows: every intelligence / operations page gets a step-by-step "what to do next" panel whose step
status is computed from the live database (never a static checklist), plus one-click bulk actions that remove
per-device tedium (e.g. enable read-only monitoring for every credentialed switch).

Steps: {id, title, detail, status: DONE | TODO | BLOCKED | INFO, count, total, action: {kind, label, to | api}}.
`sample` describes a safe sample run (DEMO dataset, always labelled and never contacted).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, ClientRecord, Site, User
from ..database.models_inm import InmDevice, RecommendationRecord
from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
from ..database.models_phases import (CollectionPlan, ConfigArtifact, DecisionFeedback, InmAlert, MlModel,
                                      OptimizationEnrollment, OutcomeEvaluation)
from ..database.session import get_db
from ..security.deps import audit, require_role

from ..security.context import get_scope


def require_global_scope():
    """Fleet-wide workspaces are refused to site-scoped users (kept from the production branch)."""
    if get_scope() is not None:
        raise HTTPException(403, "This fleet-wide workspace requires access to all sites.")


router = APIRouter(prefix="/api/v1", tags=["guides"], dependencies=[Depends(require_role("viewer")), Depends(require_global_scope)])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _n(db: Session, stmt) -> int:
    return int(db.scalar(stmt) or 0)


def step(sid, title, detail, ok, *, count=None, total=None, action=None, blocked=False, info=False):
    return {"id": sid, "title": title, "detail": detail,
            "status": "INFO" if info else "BLOCKED" if blocked else "DONE" if ok else "TODO",
            "count": count, "total": total, "action": action}


def _real(db: Session, family: str | None = None):
    q = select(func.count()).select_from(InmDevice).where(InmDevice.source_type == "REAL",
                                                          InmDevice.lifecycle != "merged")
    return q.where(InmDevice.family == family) if family else q


def _monitoring_counts(db: Session) -> dict:
    sw = _n(db, _real(db, "switch"))
    ap = _n(db, _real(db, "access_point"))
    sw_cred = _n(db, select(func.count()).select_from(OnboardingDeviceCredential).join(
        InmDevice, InmDevice.id == OnboardingDeviceCredential.device_id).where(
        InmDevice.family == "switch", InmDevice.source_type == "REAL"))
    sw_plans = _n(db, select(func.count()).select_from(CollectionPlan).join(
        InmDevice, InmDevice.id == CollectionPlan.device_id).where(
        InmDevice.family == "switch", CollectionPlan.state == "ACTIVE"))
    sw_fresh = _n(db, select(func.count()).select_from(CollectionPlan).join(
        InmDevice, InmDevice.id == CollectionPlan.device_id).where(
        InmDevice.family == "switch", CollectionPlan.last_success_at >= _now() - timedelta(minutes=30)))
    ap_polled = _n(db, select(func.count()).select_from(InmDevice).join(Site, Site.id == InmDevice.legacy_site_id).where(
        InmDevice.source_type == "REAL", InmDevice.family == "access_point", InmDevice.lifecycle != "merged",
        Site.last_poll_ok.is_(True)))
    return {"switches": sw, "aps": ap, "sw_cred": sw_cred, "sw_plans": sw_plans, "sw_fresh": sw_fresh,
            "ap_polled": ap_polled}


def _sample(db: Session) -> dict:
    row = db.get(AppSetting, "demo_dataset")
    loaded = bool(row and isinstance(row.value, dict) and row.value.get("loaded"))
    return {"available": True, "loaded": loaded,
            "label": "Remove sample data" if loaded else "Load sample network (DEMO-labelled)",
            "api": "/v1/demo-data/remove" if loaded else "/v1/demo-data/load",
            "detail": "Adds a labelled DEMO estate (sites, APs, switches, clients, telemetry, alerts) so you can walk "
                      "through every step safely. DEMO devices are never contacted and REAL data is untouched."}


def g_monitoring(db: Session) -> dict:
    m = _monitoring_counts(db)
    profiles = _n(db, select(func.count()).select_from(OnboardingCredential).where(OnboardingCredential.enabled.is_(True)))
    return {"title": "Get every device monitored", "steps": [
        step("register", "Register devices", "Save your management subnets and credential profiles, join them in a "
             "discovery rule and run it (or import a CSV).", m["switches"] + m["aps"] > 0, count=m["switches"] + m["aps"],
             action={"kind": "link", "label": "Open discovery", "to": "/discovery"}),
        step("credentials", "Save credential profiles", "One encrypted profile per device family is enough.",
             profiles > 0, count=profiles, action={"kind": "link", "label": "Credential profiles", "to": "/onboarding"}),
        step("aps", "Auto-onboard access points (cluster-aware)",
             "Finds each AP's Instant cluster, registers it once and links every member.",
             m["aps"] and m["ap_polled"] >= m["aps"], count=m["ap_polled"], total=m["aps"],
             action={"kind": "link", "label": "Open AP auto-onboarding", "to": "/aps#autodiscover"}),
        step("switch_plans", "Turn on read-only switch monitoring",
             "Creates a read-only collection plan (system, interfaces, PoE, LLDP every 5 min) for every switch that "
             "has a credential and no plan.", m["switches"] and m["sw_plans"] >= m["sw_cred"] > 0,
             count=m["sw_plans"], total=m["sw_cred"],
             blocked=m["sw_cred"] == 0,
             action={"kind": "api", "label": "Enable monitoring for all credentialed switches",
                     "api": "/v1/guide/actions/auto-plans", "body": {"family": "switch"}}),
        step("fresh", "Confirm fresh telemetry", "Plans run every interval; failures show the category (auth, REST "
             "off, unreachable).", m["sw_fresh"] > 0, count=m["sw_fresh"], total=m["sw_plans"],
             action={"kind": "link", "label": "Live collection", "to": "/collection"}),
    ], "sample": _sample(db)}


def g_insights(db: Session) -> dict:
    m = _monitoring_counts(db)
    recs = _n(db, select(func.count()).select_from(RecommendationRecord))
    fb = _n(db, select(func.count()).select_from(DecisionFeedback))
    return {"title": "From telemetry to an acted-on insight", "steps": [
        step("monitor", "Devices produce telemetry",
             "Insights need two recent REST observations per device. Devices showing NO_TELEMETRY have no active "
             "collection plan or are not in a polled cluster.", m["sw_fresh"] + m["ap_polled"] > 0,
             count=m["sw_fresh"] + m["ap_polled"], total=m["switches"] + m["aps"],
             action={"kind": "api", "label": "Enable monitoring for all credentialed switches",
                     "api": "/v1/guide/actions/auto-plans", "body": {"family": "switch"}}),
        step("analyze", "Run the analysis", "Applies the versioned rules to the latest observations.",
             recs > 0, count=recs, action={"kind": "click", "label": "Analyze now", "target": "analyze"}),
        step("review", "Review each recommendation", "Accept or reject with a reason — that is the training label.",
             fb > 0, count=fb, total=recs, action={"kind": "link", "label": "Recommendations", "to": "/recommendations"}),
        step("act", "Act through change control", "Accepted recommendations become change plans with backup, "
             "approval and rollback.", False, info=True, action={"kind": "link", "label": "Change workflow", "to": "/changes"}),
    ], "sample": _sample(db)}


def g_learning(db: Session) -> dict:
    recs = _n(db, select(func.count()).select_from(RecommendationRecord))
    fb = _n(db, select(func.count()).select_from(DecisionFeedback))
    out = _n(db, select(func.count()).select_from(OutcomeEvaluation))
    models = _n(db, select(func.count()).select_from(MlModel))
    promoted = _n(db, select(func.count()).select_from(MlModel).where(MlModel.status == "PROMOTED"))
    return {"title": "How the platform learns (rules → measured outcomes → model)", "steps": [
        step("recs", "1 · Generate recommendations", "Run analysis on Device insights.", recs > 0, count=recs,
             action={"kind": "link", "label": "Device insights", "to": "/phase4-insights"}),
        step("feedback", "2 · Engineers decide", "Accept / reject each recommendation with a reason.", fb > 0,
             count=fb, total=recs, action={"kind": "link", "label": "Recommendations", "to": "/recommendations"}),
        step("outcomes", "3 · Measure the outcome", "After a change, compare the before/after metric window.",
             out >= 20, count=out, total=20,
             action={"kind": "link", "label": "Recommendations", "to": "/recommendations"}),
        step("train", "4 · Train a candidate model", "Needs at least 20 labelled outcomes; uses a time-separated "
             "holdout.", models > 0, count=models, blocked=out < 20,
             action={"kind": "click", "label": "Train candidate", "target": "train"}),
        step("promote", "5 · Independent promotion", "A different person promotes the candidate if it beats the "
             "rules on the holdout.", promoted > 0, count=promoted, blocked=models == 0),
    ], "sample": _sample(db)}


def g_assistant(db: Session) -> dict:
    from ..config import get_settings
    s = get_settings()
    clients = _n(db, select(func.count()).select_from(ClientRecord).where(ClientRecord.is_connected.is_(True)))
    polled = _n(db, select(func.count()).select_from(Site).where(Site.last_poll_ok.is_(True)))
    return {"title": "Ask the network assistant", "steps": [
        step("enabled", "AI service is enabled", "Set SIMS_OLLAMA_ENABLED=true; it runs on-premises.",
             bool(s.ollama_enabled), blocked=not s.ollama_enabled),
        step("data", "There is measured data to reason over", "At least one polled cluster with clients.",
             polled > 0 and clients > 0, count=clients,
             action={"kind": "link", "label": "Get APs monitored", "to": "/aps#autodiscover"}),
        step("ask", "Ask a question or pick a sample", "Try: “Why are clients on CP-Conference Room poor?” or "
             "“Which APs should I fix first?”", False, info=True),
        step("diagnose", "Drill into a specific AP or client",
             "Click any AP or client on User experience / Access points for a root-cause diagnosis and fix plan.",
             False, info=True, action={"kind": "link", "label": "User experience", "to": "/experience"}),
    ], "sample": _sample(db)}


def g_optimization(db: Session) -> dict:
    from ..config import get_settings
    from ..infrastructure.optimizer import feature_enabled
    from ..infrastructure.phase_services import control
    pending = _n(db, select(func.count()).select_from(OptimizationEnrollment).where(
        OptimizationEnrollment.status == "PENDING_APPROVAL"))
    armed = _n(db, select(func.count()).select_from(OptimizationEnrollment).where(OptimizationEnrollment.status == "ARMED"))
    backups = _n(db, select(func.count(func.distinct(ConfigArtifact.device_id))))
    return {"title": "Optimize a cluster safely", "steps": [
        step("pick", "Pick a cluster and read its diagnosis", "Each AP shows why it performs poorly and the proposed "
             "20/40 MHz channel changes.", False, info=True),
        step("backup", "Capture configuration backups", "Approval requires a sealed backup per AP.", backups > 0,
             count=backups, action={"kind": "link", "label": "Config vault", "to": "/vault"}),
        step("propose", "Propose SHADOW enrollment for the cluster", "Creates one pending enrollment per AP — "
             "nothing is inherited from the cluster.", pending + armed > 0, count=pending + armed),
        step("approve", "Independent approval per AP", "A different user approves each AP.", armed > 0, count=armed,
             total=pending + armed),
        step("unlock", "Platform gates", "Feature flag, write lock and emergency stop must all allow it.",
             feature_enabled() and not get_settings().device_write_lock and not control(db).emergency_stop,
             blocked=not (feature_enabled() and not get_settings().device_write_lock and not control(db).emergency_stop)),
    ], "sample": _sample(db)}


def g_incidents(db: Session) -> dict:
    open_ = _n(db, select(func.count()).select_from(InmAlert).where(InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))
    return {"title": "Work an incident", "steps": [
        step("triage", "Triage", "Sort by severity; open Device 360° to see what changed.", open_ == 0, count=open_),
        step("assign", "Assign an owner", "Assigning also acknowledges the alert.", False, info=True),
        step("ticket", "Link the service-desk ticket (if one is needed)", "Reference + optional URL.", False, info=True),
        step("resolve", "Resolve", "‘No ticket required’, or ‘Ticket resolved’. If monitoring sees the condition clear "
             "first it is closed automatically as AUTO-RECOVERED and any linked ticket is flagged for closure.",
             False, info=True),
    ], "sample": _sample(db)}


GUIDES = {"monitoring": g_monitoring, "insights": g_insights, "learning": g_learning, "assistant": g_assistant,
          "optimization": g_optimization, "incidents": g_incidents}


@router.get("/guide/{key}")
def guide(key: str, db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    fn = GUIDES.get(key)
    if fn is None:
        raise HTTPException(404, "Unknown guide")
    g = fn(db)
    todo = next((s for s in g["steps"] if s["status"] in ("TODO", "BLOCKED")), None)
    return {**g, "key": key, "next_step": todo["id"] if todo else None,
            "done": sum(1 for s in g["steps"] if s["status"] == "DONE"),
            "total": sum(1 for s in g["steps"] if s["status"] != "INFO")}


class AutoPlans(BaseModel):
    family: str = Field("switch", pattern="^(switch|access_point)$")
    interval_seconds: int = Field(300, ge=60, le=3600)


@router.post("/guide/actions/auto-plans")
def auto_plans(body: AutoPlans, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_role("admin"))):
    """Bulk READ-ONLY collection plans (PRTG-style 'auto-apply monitoring template'). Only devices that already have
    a bound credential and a management address, and no plan, are included. APs that are in a polled Instant
    cluster are skipped — the cluster poll already observes them."""
    from ..domain import collection as C
    from ..domain.adapters import resolve
    have = {p.device_id for p in db.scalars(select(CollectionPlan))}
    created, skipped = 0, {"no_credential": 0, "no_address": 0, "has_plan": 0, "cluster_polled": 0}
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL", InmDevice.family == body.family,
                                                InmDevice.lifecycle != "merged")):
        if d.id in have:
            skipped["has_plan"] += 1
            continue
        if body.family == "access_point" and d.legacy_ap_id is not None:
            skipped["cluster_polled"] += 1
            continue
        binding = db.get(OnboardingDeviceCredential, d.id)
        if binding is None:
            skipped["no_credential"] += 1
            continue
        if not d.mgmt_addresses:
            skipped["no_address"] += 1
            continue
        fams = C.METRIC_FAMILIES[body.family]
        db.add(CollectionPlan(device_id=d.id, site_id=d.site_id, adapter_key=resolve(d.vendor, d.family, d.model).key,
                              credential_profile_id=binding.credential_id, metric_families=list(fams),
                              interval_seconds=body.interval_seconds, state="ACTIVE", next_due_at=_now(),
                              max_concurrency=1, created_by=admin.username))
        created += 1
    db.flush()
    audit(db, "collection_plans_bulk_created", username=admin.username, target=body.family,
          detail=f"created={created} skipped={skipped}; READ ONLY", request=request)
    return {"created": created, "skipped": skipped,
            "message": f"{created} read-only monitoring plan(s) created; first polls start within one interval."}
