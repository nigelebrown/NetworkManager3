"""Network Intelligence completion API: unified recommendations, AI rule catalogue, predictive health,
operational analytics and the GUI→API/worker inventory.

Everything is advisory/read-only towards devices. Reads honour site scope (legacy site grants); triage needs an
operator; rule changes need a proposer AND a different approver; every decision is audited.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database.models import Site, User
from ..database.models_inm import InmDevice, InmSite, RecommendationRecord
from ..database.models_intelligence import AiRuleDefinition, AiRuleVersion, Prediction, PredictionRun
from ..database.models_phases import ChangePlan, CollectionPlan, DecisionFeedback, InmAlert, OutcomeEvaluation
from ..database.session import get_db
from ..domain import predictive as PR
from ..infrastructure import rule_store as RC
from ..infrastructure import recommendation_store as RS
from ..infrastructure.runtime import telemetry_store
from ..security.context import get_scope
from ..security.deps import audit, require_any_role, require_role

router = APIRouter(prefix="/api/v1/intelligence", tags=["network-intelligence"])
viewer = Depends(require_role("viewer"))
operator = Depends(require_role("operator"))
reviewer = Depends(require_any_role("approver", "admin"))


def _now() -> datetime:
    from ..infrastructure.clock import now
    return now()


def _utc(t):
    if t is None:
        return None
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t):
    return _utc(t).isoformat() if t else None


def _in_scope(legacy_site_id: int | None) -> bool:
    scope = get_scope()
    return scope is None or (legacy_site_id is not None and legacy_site_id in scope)


def _devices(db: Session, *, source: str = "ALL", family: str | None = None, site: str | None = None) -> list[InmDevice]:
    q = select(InmDevice)
    if source in ("REAL", "DEMO"):
        q = q.where(InmDevice.source_type == source)
    if family:
        q = q.where(InmDevice.family == family)
    if site:
        q = q.where(InmDevice.site_id == site)
    return [d for d in db.scalars(q.order_by(InmDevice.hostname)) if _in_scope(d.legacy_site_id)]


# ================================================================ recommendations (unified triage)
def _rec_json(r: RecommendationRecord, sites: dict, devices: dict, rules: dict, fb=None, outcomes=None) -> dict:
    d = devices.get(r.device_id)
    rule = rules.get(r.rule_id)
    seen = _utc(r.last_seen_at or r.created_at)
    return {"id": r.id, "rule_id": r.rule_id, "rule_version": r.rule_version, "engine": r.engine,
            "rule": {"title": rule.title, "version": rule.version, "link": f"/ai-learning?rule={r.rule_id}"} if rule else None,
            "title": r.title, "explanation": r.explanation, "risk": r.risk, "source_type": r.source_type,
            "state": r.approval_state, "evidence": r.evidence, "reason_codes": r.reason_codes or [r.rule_id],
            "affected": r.affected or {}, "device": {"id": d.id, "hostname": d.hostname, "family": d.family} if d else None,
            "site": sites.get(r.legacy_site_id), "legacy_site_id": r.legacy_site_id,
            "created_at": _iso(r.created_at), "last_seen_at": _iso(seen), "occurrences": r.occurrences or 1,
            "freshness": "FRESH" if _now() - seen <= timedelta(hours=2) else "STALE",
            "reviewed_by": r.reviewed_by, "reviewed_at": _iso(r.reviewed_at), "review_note": r.review_note,
            "resolved_at": _iso(r.resolved_at), "input_window": r.input_window, "requested_by": r.requested_by,
            "feedback": {"decision": fb.decision, "reason": fb.reason, "operator": fb.operator} if fb else None,
            "outcomes": [o.result | {"id": o.id} for o in (outcomes or [])],
            "evidence_links": ([{"label": "Device 360°", "href": f"/devices/{r.device_id}/360"}] if r.device_id else []) +
                              ([{"label": "Rule definition", "href": f"/ai-learning?rule={r.rule_id}"}] if rule else [])}


@router.get("/recommendations")
def list_recommendations(state: str | None = None, risk: str | None = None, engine: str | None = None,
                         source: str | None = None, device_id: str | None = None, site_id: int | None = None,
                         rule_id: str | None = None, q: str | None = None, fresh_only: bool = False,
                         limit: int = Query(300, ge=1, le=1000), db: Session = Depends(get_db), user: User = viewer):
    stmt = select(RecommendationRecord).order_by(RecommendationRecord.created_at.desc())
    if state:
        stmt = stmt.where(RecommendationRecord.approval_state.in_(state.split(",")))
    for col, v in ((RecommendationRecord.risk, risk), (RecommendationRecord.engine, engine), (RecommendationRecord.source_type, source),
                   (RecommendationRecord.device_id, device_id), (RecommendationRecord.legacy_site_id, site_id),
                   (RecommendationRecord.rule_id, rule_id)):
        if v is not None and v != "":
            stmt = stmt.where(col == v)
    if q:
        stmt = stmt.where(or_(RecommendationRecord.title.ilike(f"%{q}%"), RecommendationRecord.explanation.ilike(f"%{q}%")))
    rows = [r for r in db.scalars(stmt.limit(limit * 3)) if _in_scope(r.legacy_site_id)][:limit]
    sites = {s.id: s.name for s in db.scalars(select(Site))}
    devices = {d.id: d for d in db.scalars(select(InmDevice).where(InmDevice.id.in_([r.device_id for r in rows if r.device_id] or [""])))}
    rules = {r.rule_id: r for r in db.scalars(select(AiRuleDefinition))}
    fbs = {f.recommendation_id: f for f in db.scalars(select(DecisionFeedback).where(
        DecisionFeedback.recommendation_id.in_([r.id for r in rows] or [""])))}
    outs: dict[str, list] = {}
    for o in db.scalars(select(OutcomeEvaluation).where(OutcomeEvaluation.recommendation_id.in_([r.id for r in rows] or [""]))):
        outs.setdefault(o.recommendation_id, []).append(o)
    items = [_rec_json(r, sites, devices, rules, fbs.get(r.id), outs.get(r.id)) for r in rows]
    if fresh_only:
        items = [i for i in items if i["freshness"] == "FRESH"]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    return {"items": items, "counts": counts,
            "policy": "Advisory only. Triage records a human decision; nothing is sent to a device."}


@router.get("/recommendations/{rec_id}")
def get_recommendation(rec_id: str, db: Session = Depends(get_db), user: User = viewer):
    r = db.get(RecommendationRecord, rec_id)
    if r is None or not _in_scope(r.legacy_site_id):
        raise HTTPException(404, "Recommendation not found")
    data = list_recommendations(rule_id=r.rule_id, device_id=r.device_id, site_id=r.legacy_site_id, db=db, user=user,
                                state=None, risk=None, engine=None, source=None, q=None, fresh_only=False, limit=50)
    me = next((i for i in data["items"] if i["id"] == r.id), None)
    from ..database.models import AuditLog
    trail = [{"at": _iso(a.ts), "action": a.action, "by": a.username, "detail": a.detail}
             for a in db.scalars(select(AuditLog).where(AuditLog.target == r.id).order_by(AuditLog.ts))]
    return (me or {}) | {"history": [i for i in data["items"] if i["id"] != r.id][:20], "audit": trail}


class Triage(BaseModel):
    action: Literal["ACKNOWLEDGE", "DISMISS", "RESOLVE", "REOPEN"]
    note: str = Field(min_length=3, max_length=500)


@router.post("/recommendations/{rec_id}/triage")
def triage(rec_id: str, body: Triage, request: Request, db: Session = Depends(get_db), user: User = operator):
    r = db.get(RecommendationRecord, rec_id)
    if r is None or not _in_scope(r.legacy_site_id):
        raise HTTPException(404, "Recommendation not found")
    try:
        RS.triage(r, body.action, user.username, body.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, "recommendation_triaged", username=user.username, target=r.id,
          detail=f"{body.action} → {r.approval_state}: {body.note}"[:900], request=request)
    return {"id": r.id, "state": r.approval_state, "reviewed_by": r.reviewed_by}


# ================================================================ AI rules
def _rule_json(db: Session, r: AiRuleDefinition, detail: bool = False) -> dict:
    out = {"rule_id": r.rule_id, "engine": r.engine, "title": r.title, "description": r.description, "inputs": r.inputs,
           "condition": r.condition, "output": r.output, "risk": r.risk, "precedence": r.precedence,
           "params": r.params, "default_params": r.default_params,
           "schema": {k: {"type": t, "min": lo, "max": hi} for k, (t, lo, hi) in RC.SCHEMA.get(r.rule_id, {}).items()},
           "enabled": r.enabled, "suppression_minutes": r.suppression_minutes, "version": r.version,
           "pending": r.pending, "pending_by": r.pending_by, "pending_note": r.pending_note, "pending_at": _iso(r.pending_at),
           "updated_by": r.updated_by, "updated_at": _iso(r.updated_at),
           "triggered_total": db.scalar(select(func.count()).select_from(RecommendationRecord)
                                        .where(RecommendationRecord.rule_id == r.rule_id)) or 0,
           "open_findings": db.scalar(select(func.count()).select_from(RecommendationRecord).where(
               RecommendationRecord.rule_id == r.rule_id, RecommendationRecord.approval_state.in_(RS.OPEN_STATES))) or 0}
    if detail:
        out["versions"] = [{"version": v.version, "params": v.params, "enabled": v.enabled, "suppression_minutes": v.suppression_minutes,
                            "proposed_by": v.proposed_by, "approved_by": v.approved_by, "note": v.note, "at": _iso(v.created_at)}
                           for v in db.scalars(select(AiRuleVersion).where(AiRuleVersion.rule_id == r.rule_id)
                                               .order_by(AiRuleVersion.version.desc()))]
        out["recent_findings"] = [{"id": x.id, "title": x.title, "state": x.approval_state, "rule_version": x.rule_version,
                                   "created_at": _iso(x.created_at)} for x in db.scalars(
            select(RecommendationRecord).where(RecommendationRecord.rule_id == r.rule_id)
            .order_by(RecommendationRecord.created_at.desc()).limit(20)) if _in_scope(x.legacy_site_id)]
    return out


@router.get("/rules")
def rules(db: Session = Depends(get_db), user: User = viewer):
    RC.ensure(db)
    return {"rules": [_rule_json(db, r) for r in db.scalars(select(AiRuleDefinition).order_by(AiRuleDefinition.engine, AiRuleDefinition.precedence))],
            "precedence": "Lower precedence numbers are evaluated first. DEVICE_INSIGHTS: NOT_MONITORED suppresses all other "
                          "device rules; STALE/COLLECTION_FAILING findings are raised before metric thresholds, which only "
                          "use fresh trusted samples. SITE_RULES: ap_offline suppresses critical_alerts_open for the same site.",
            "governance": "Changes are proposed, then approved by a different approver/admin. Each approval creates a new version."}


@router.get("/rules/{rule_id}")
def rule_detail(rule_id: str, db: Session = Depends(get_db), user: User = viewer):
    RC.ensure(db)
    r = db.get(AiRuleDefinition, rule_id)
    if r is None:
        raise HTTPException(404, "Rule not found")
    return _rule_json(db, r, detail=True)


class Proposal(BaseModel):
    params: dict = Field(default_factory=dict)
    enabled: bool | None = None
    suppression_minutes: int | None = None
    note: str = Field(min_length=3, max_length=500)


@router.post("/rules/{rule_id}/propose")
def propose_rule(rule_id: str, body: Proposal, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_any_role("operator", "admin"))):
    RC.ensure(db)
    try:
        r = RC.propose(db, rule_id, params=body.params, enabled=body.enabled, suppression_minutes=body.suppression_minutes,
                       actor=user.username, note=body.note)
    except LookupError:
        raise HTTPException(404, "Rule not found") from None
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, "ai_rule_change_proposed", username=user.username, target=rule_id,
          detail=json.dumps({"pending": r.pending, "note": body.note})[:900], request=request)
    return _rule_json(db, r)


class Decision(BaseModel):
    approve: bool
    note: str = Field(min_length=3, max_length=500)


@router.post("/rules/{rule_id}/decide")
def decide_rule(rule_id: str, body: Decision, request: Request, db: Session = Depends(get_db), user: User = reviewer):
    try:
        before = (db.get(AiRuleDefinition, rule_id) or AiRuleDefinition()).pending
        r = RC.decide(db, rule_id, approve=body.approve, actor=user.username, note=body.note)
    except LookupError:
        raise HTTPException(404, "Rule not found") from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, "ai_rule_change_approved" if body.approve else "ai_rule_change_rejected", username=user.username,
          target=rule_id, detail=json.dumps({"version": r.version, "change": before, "note": body.note})[:900], request=request)
    return _rule_json(db, r, detail=True)


# ================================================================ predictive health
def _series(store, device_id: str, metric: str, since: datetime, source_type: str) -> list[tuple[datetime, float]]:
    rows = store.query(device_id=device_id, metric_name=metric, since=since, limit=20000, entity_type="device")
    return [(_utc(r["observed_at"]), float(r["value"])) for r in rows
            if r.get("value") is not None and r.get("source_type") == source_type]


def run_predictions(db: Session, *, actor: str, now: datetime | None = None, store=None) -> PredictionRun:
    now = now or _now()
    store = store if store is not None else telemetry_store()
    rules = {k: v for k, v in RC.effective(db, "PREDICTIVE").items() if v["enabled"]}
    if store is None:
        run = PredictionRun(requested_by=actor, model_version=PR.MODEL_VERSION, horizon_hours=24, status="NO_DATA",
                            summary={"reason": "telemetry store not configured"}, validation_state="INSUFFICIENT_HISTORY",
                            created_at=now)
        db.add(run)
        db.flush()
        return run
    devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type.in_(["REAL", "DEMO"]))))
    horizon = max([int(r["params"]["horizon_hours"]) for r in rules.values()] or [24])
    run = PredictionRun(requested_by=actor, model_version=PR.MODEL_VERSION, horizon_hours=horizon, status="SUCCEEDED",
                        summary={}, validation_state="INSUFFICIENT_HISTORY", created_at=now)
    db.add(run)
    db.flush()
    evaluation, counts = {}, {"AT_RISK": 0, "NO_RISK_EXPECTED": 0, "INSUFFICIENT_DATA": 0}
    per_rule_windows: dict[str, list] = {r: [] for r in rules}
    drift_flags = []
    pending_recs = []
    ml_primary: set = set()
    from ..ml import service as MLS
    for d in devices:
        for rid, rule in rules.items():
            metric = RC.PRED_METRIC[rid]
            if metric.startswith("util_") and d.family != "access_point":
                continue
            p = rule["params"]
            pts = _series(store, d.id, metric, now - timedelta(days=int(p["min_days"]) + 21), d.source_type)
            f = PR.forecast(pts, cutoff=now, threshold=float(p["threshold"]), horizon_hours=int(p["horizon_hours"]),
                            min_days=int(p["min_days"]))
            per_rule_windows[rid] += PR.backtest(pts, threshold=float(p["threshold"]), horizon_hours=int(p["horizon_hours"]),
                                                 min_days=int(p["min_days"]), now=now)
            dr = PR.drift(pts, now=now)
            if dr["state"] == "DRIFT":
                drift_flags.append({"device_id": d.id, "metric": metric, **dr})
            # rc18: a promoted, production-validated trained model is PRIMARY; otherwise it runs in SHADOW and
            # the statistical engine stays the probability of record (fallback).
            ml = MLS.infer(db, d, rid, pts, now=now, horizon=int(p["horizon_hours"]), threshold=float(p["threshold"]),
                           metric=metric)
            if ml and ml.get("used_for_prediction"):
                f = dict(f) | {"probability": ml["probability"], "state": "AT_RISK" if ml["probability"] >= 0.5
                               else "NO_RISK_EXPECTED", "confidence": ml["confidence"]["level"],
                               "reason": f"trained model {ml['model']}"}
                ml_primary.add((d.id, rid))
            if ml:
                f = dict(f) | {"features": (f.get("features") or {}) | {"ml": ml}}
            counts[f["state"]] += 1
            db.add(Prediction(run_id=run.id, device_id=d.id, metric=metric, threshold=float(p["threshold"]),
                              horizon_hours=int(p["horizon_hours"]), window_start=now,
                              window_end=now + timedelta(hours=int(p["horizon_hours"])), state=f["state"],
                              probability=f.get("probability"), predicted_peak=f.get("predicted_peak"),
                              confidence=f.get("confidence", "NONE"), features=(f.get("features") or {}) | {"drift": dr},
                              reason=f.get("reason"), created_at=now))
            if f["state"] == "AT_RISK" and f["probability"] >= float(p["alert_probability"]):
                pending_recs.append((d, rid, rule, metric, f))
    states = []
    for rid, wins in per_rule_windows.items():
        ev = PR.evaluate(wins, alert_probability=float(rules[rid]["params"]["alert_probability"]))
        evaluation[rid] = ev
        states.append(ev["state"])
    run.validation_state = ("VALIDATED" if states and all(s == "VALIDATED" for s in states)
                            else "PARTIALLY_VALIDATED" if "VALIDATED" in states
                            else "EXPERIMENTAL" if "EXPERIMENTAL" in states else "INSUFFICIENT_HISTORY")
    surfaced = 0
    for d, rid, rule, metric, f in pending_recs:
        if evaluation[rid]["state"] != "VALIDATED" and (d.id, rid) not in ml_primary:
            continue   # an unvalidated model never becomes an operational recommendation
        _, outcome = RS.upsert(
            db, rule_id=rid, rule_version=f"{PR.MODEL_VERSION}+v{rule['version']}", engine="PREDICTIVE",
            title=f"{d.hostname or d.id[:8]}: {rule['title']} ({f['probability']:.0%})",
            explanation=f"{metric} is forecast to peak near {f['predicted_peak']} (threshold {rule['params']['threshold']}) "
                        f"within {rule['params']['horizon_hours']} h. Model {(f['features'].get('ml') or {}).get('model') if (d.id, rid) in ml_primary else PR.MODEL_VERSION}, statistical backtest Brier skill "
                        f"{evaluation[rid]['brier_skill']} on {evaluation[rid]['windows']} windows.",
            evidence={"probability": f["probability"], "predicted_peak": f["predicted_peak"], "features": f["features"],
                      "validation": {k: evaluation[rid][k] for k in ("windows", "brier_skill", "precision", "recall")}},
            risk=rule["risk"], source_type=d.source_type, actor=actor, device_id=d.id, legacy_site_id=d.legacy_site_id,
            reason_codes=[rid, "FORECAST_BREACH"], input_window=f"{rule['params']['min_days']}d history",
            suppression_minutes=int(rule["suppression_minutes"]), now=now)
        surfaced += outcome == "CREATED"
    run.summary = {"devices": len(devices), "predictions": counts, "surfaced_recommendations": surfaced,
                   "drift": drift_flags[:50], "rules": list(rules)}
    run.evaluation = evaluation
    db.flush()
    return run


def score_predictions(db: Session, *, now: datetime | None = None, store=None) -> dict:
    """Close the loop: record the observed outcome of every prediction whose window has ended."""
    now = now or _now()
    store = store if store is not None else telemetry_store()
    if store is None:
        return {"scored": 0, "reason": "telemetry store not configured"}
    scored = 0
    for p in db.scalars(select(Prediction).where(Prediction.outcome.is_(None), Prediction.window_end <= now,
                                                 Prediction.probability.is_not(None)).limit(5000)):
        d = db.get(InmDevice, p.device_id)
        pts = _series(store, p.device_id, p.metric, _utc(p.window_start), d.source_type if d else "REAL") if d else []
        peak = PR.observed_peak(pts, _utc(p.window_start), _utc(p.window_end))
        p.observed_peak = peak
        p.outcome = "UNOBSERVED" if peak is None else "BREACH" if peak >= p.threshold else "NO_BREACH"
        p.evaluated_at = now
        scored += 1
    db.flush()
    issued = [{"p": p.probability, "breach": p.outcome == "BREACH"} for p in db.scalars(
        select(Prediction).where(Prediction.outcome.in_(("BREACH", "NO_BREACH"))))]
    return {"scored": scored, "live_evaluation": PR.evaluate(issued)}


@router.post("/predictive/run")
def predictive_run(request: Request, db: Session = Depends(get_db), user: User = operator):
    run = run_predictions(db, actor=user.username)
    audit(db, "predictive_run", username=user.username, target=run.id,
          detail=f"{run.status} {run.validation_state} {json.dumps(run.summary.get('predictions', {}))}; advisory only",
          request=request)
    return _run_json(run)


@router.post("/predictive/score")
def predictive_score(request: Request, db: Session = Depends(get_db), user: User = operator):
    r = score_predictions(db)
    audit(db, "predictive_scored", username=user.username, detail=f"scored={r['scored']}", request=request)
    return r


def _run_json(run: PredictionRun) -> dict:
    return {"id": run.id, "created_at": _iso(run.created_at), "requested_by": run.requested_by, "model_version": run.model_version,
            "horizon_hours": run.horizon_hours, "status": run.status, "validation_state": run.validation_state,
            "summary": run.summary, "evaluation": run.evaluation}


@router.get("/predictive")
def predictive(state: str | None = None, db: Session = Depends(get_db), user: User = viewer):
    run = db.scalar(select(PredictionRun).order_by(PredictionRun.created_at.desc()).limit(1))
    if run is None:
        return {"run": None, "predictions": [], "model": PR.MODEL_VERSION,
                "statement": "No prediction run yet. Run predictions after ≥7 days of recorded telemetry."}
    devices = {d.id: d for d in db.scalars(select(InmDevice))}
    preds = []
    for p in db.scalars(select(Prediction).where(Prediction.run_id == run.id).order_by(Prediction.probability.desc().nulls_last())):
        d = devices.get(p.device_id)
        if d is None or not _in_scope(d.legacy_site_id) or (state and p.state != state):
            continue
        val = (run.evaluation or {}).get(next((k for k, m in RC.PRED_METRIC.items() if m == p.metric), ""), {})
        preds.append({"id": p.id, "device_id": p.device_id, "hostname": d.hostname, "family": d.family, "source_type": d.source_type,
                      "metric": p.metric, "threshold": p.threshold, "window_start": _iso(p.window_start), "window_end": _iso(p.window_end),
                      "state": p.state, "probability": p.probability, "predicted_peak": p.predicted_peak, "confidence": p.confidence,
                      "features": p.features, "reason": p.reason, "outcome": p.outcome, "observed_peak": p.observed_peak,
                      "validation_state": val.get("state", "INSUFFICIENT_HISTORY"),
                      "label": "OPERATIONAL" if val.get("state") == "VALIDATED" else "EXPERIMENTAL — not validated"})
    issued = [{"p": p.probability, "breach": p.outcome == "BREACH"} for p in db.scalars(
        select(Prediction).where(Prediction.outcome.in_(("BREACH", "NO_BREACH"))))]
    return {"run": _run_json(run), "predictions": preds, "model": PR.MODEL_VERSION, "live_evaluation": PR.evaluate(issued),
            "statement": "Statistical forecast (hour-of-week baseline, recent deviation, bounded trend). Predictions are "
                         "OPERATIONAL only when the walk-forward backtest shows skill over the base rate on ≥30 windows."}


@router.get("/predictive/runs")
def predictive_runs(db: Session = Depends(get_db), user: User = viewer):
    return [_run_json(r) for r in db.scalars(select(PredictionRun).order_by(PredictionRun.created_at.desc()).limit(50))]


# ================================================================ operational analytics
WINDOWS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
METRICS = ("cpu_percent", "memory_percent", "util_5g_percent", "util_2g_percent", "clients", "ports_up")


def _p95(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)], 2)


def _stats(vals: list[float]) -> dict:
    return {"n": len(vals), "avg": round(statistics.mean(vals), 2) if vals else None, "max": round(max(vals), 2) if vals else None,
            "p95": _p95(vals)}


def analytics_data(db: Session, *, window: str, site: str | None, family: str | None, source: str) -> dict:
    if window not in WINDOWS:
        raise HTTPException(422, f"window must be one of {', '.join(WINDOWS)}")
    if source not in ("REAL", "DEMO", "ALL"):
        raise HTTPException(422, "source must be REAL, DEMO or ALL")
    now = _now()
    since = now - timedelta(hours=WINDOWS[window])
    store = telemetry_store()
    devices = _devices(db, source=source, family=family, site=site)
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    plans = {p.device_id: p for p in db.scalars(select(CollectionPlan))}
    per_device, by_site = [], {}
    for d in devices:
        rows = store.query(device_id=d.id, metric_name=None, since=since, limit=20000, entity_type="device") if store else []
        rows = [r for r in rows if r.get("source_type") == d.source_type and r.get("value") is not None]
        metrics = {m: _stats([float(r["value"]) for r in rows if r["metric_name"] == m]) for m in METRICS}
        newest = max((_utc(r["observed_at"]) for r in rows), default=None)
        p = plans.get(d.id)
        expected = int(WINDOWS[window] * 3600 / p.interval_seconds) if p and p.interval_seconds else None
        samples = len({r["observed_at"] for r in rows})
        fresh = ("NO_DATA" if newest is None else "FRESH" if now - newest <= timedelta(minutes=30) else "STALE")
        row = {"device_id": d.id, "hostname": d.hostname, "family": d.family, "model": d.model, "source_type": d.source_type,
               "site_id": d.site_id, "site": sites.get(d.site_id) or "Unassigned", "samples": samples,
               "expected_samples": expected, "coverage": round(min(1.0, samples / expected), 3) if expected else None,
               "freshness": fresh, "last_observed": _iso(newest), "metrics": metrics}
        per_device.append(row)
        g = by_site.setdefault(row["site"], {"site": row["site"], "site_id": d.site_id, "devices": 0, "reporting": 0, "stale": 0,
                                             "no_data": 0, "samples": 0, "_vals": {m: [] for m in METRICS}, "last_observed": None})
        g["devices"] += 1
        g["reporting"] += fresh != "NO_DATA"
        g["stale"] += fresh == "STALE"
        g["no_data"] += fresh == "NO_DATA"
        g["samples"] += samples
        for r in rows:
            if r["metric_name"] in g["_vals"]:
                g["_vals"][r["metric_name"]].append(float(r["value"]))
        if newest and (g["last_observed"] is None or newest > _utc(g["last_observed"])):
            g["last_observed"] = newest.isoformat()
    ids = [d.id for d in devices] or [""]
    alerts = {s: n for s, n in db.execute(select(InmAlert.device_id, func.count()).where(
        InmAlert.device_id.in_(ids), InmAlert.opened_at >= since).group_by(InmAlert.device_id))}
    changes = {s: n for s, n in db.execute(select(ChangePlan.device_id, func.count()).where(
        ChangePlan.device_id.in_(ids), ChangePlan.created_at >= since).group_by(ChangePlan.device_id))}
    for row in per_device:
        row["alerts_opened"] = alerts.get(row["device_id"], 0)
        row["changes_requested"] = changes.get(row["device_id"], 0)
    site_rows = []
    for g in by_site.values():
        members = [r for r in per_device if r["site"] == g["site"]]
        site_rows.append({k: v for k, v in g.items() if k != "_vals"} |
                         {"metrics": {m: _stats(v) for m, v in g["_vals"].items()},
                          "alerts_opened": sum(r["alerts_opened"] for r in members),
                          "changes_requested": sum(r["changes_requested"] for r in members)})
    totals = {"devices": len(per_device), "reporting": sum(1 for r in per_device if r["freshness"] != "NO_DATA"),
              "stale": sum(1 for r in per_device if r["freshness"] == "STALE"),
              "no_data": sum(1 for r in per_device if r["freshness"] == "NO_DATA"),
              "samples": sum(r["samples"] for r in per_device), "alerts_opened": sum(alerts.values()),
              "changes_requested": sum(changes.values())}
    reconciliation = {"site_sum_devices": sum(s["devices"] for s in site_rows), "site_sum_samples": sum(s["samples"] for s in site_rows),
                      "inventory_devices_in_scope": len(devices)}
    reconciliation["reconciled"] = (reconciliation["site_sum_devices"] == totals["devices"] == len(devices)
                                    and reconciliation["site_sum_samples"] == totals["samples"])
    return {"window": window, "since": since.isoformat(), "generated_at": now.isoformat(), "source": source,
            "filters": {"site": site, "family": family}, "totals": totals, "sites": sorted(site_rows, key=lambda s: s["site"]),
            "devices": per_device, "reconciliation": reconciliation,
            "telemetry_store": "CONFIGURED" if store else "NOT_CONFIGURED",
            "notes": ["DEMO and REAL telemetry are never mixed for a device; choose source to separate them.",
                      "Devices without samples are counted as NO_DATA, never as healthy or zero."]}


@router.get("/analytics")
def analytics(window: str = "24h", site: str | None = None, family: str | None = None, source: str = "ALL",
              db: Session = Depends(get_db), user: User = viewer):
    out = analytics_data(db, window=window, site=site, family=family, source=source)
    out.pop("devices")
    return out


@router.get("/analytics/devices")
def analytics_devices(window: str = "24h", site: str | None = None, family: str | None = None, source: str = "ALL",
                      site_name: str | None = None, db: Session = Depends(get_db), user: User = viewer):
    out = analytics_data(db, window=window, site=site, family=family, source=source)
    rows = [r for r in out["devices"] if site_name is None or r["site"] == site_name]
    return {"window": window, "source": source, "site_name": site_name, "devices": rows}


@router.get("/analytics/export")
def analytics_export(window: str = "24h", site: str | None = None, family: str | None = None, source: str = "ALL",
                     format: Literal["csv", "xlsx", "pdf"] = "csv", request: Request = None, db: Session = Depends(get_db),
                     user: User = viewer):
    from ..printers import export
    out = analytics_data(db, window=window, site=site, family=family, source=source)
    cols = ["site", "hostname", "family", "model", "source_type", "freshness", "samples", "expected_samples", "coverage",
            "cpu_avg", "cpu_p95", "memory_avg", "memory_p95", "util_5g_p95", "clients_avg", "alerts_opened", "changes_requested",
            "last_observed"]
    rows = [{**{k: r.get(k) for k in cols}, "cpu_avg": r["metrics"]["cpu_percent"]["avg"], "cpu_p95": r["metrics"]["cpu_percent"]["p95"],
             "memory_avg": r["metrics"]["memory_percent"]["avg"], "memory_p95": r["metrics"]["memory_percent"]["p95"],
             "util_5g_p95": r["metrics"]["util_5g_percent"]["p95"], "clients_avg": r["metrics"]["clients"]["avg"]}
            for r in out["devices"]]
    meta = {"window": window, "source": source, "generated": out["generated_at"], "reconciled": out["reconciliation"]["reconciled"],
            "devices": out["totals"]["devices"], "no_data": out["totals"]["no_data"]}
    data, mime, ext = export.render(format, "Operational analytics", cols, rows, meta)
    audit(db, "analytics_exported", username=user.username, detail=f"{format} window={window} source={source} rows={len(rows)}",
          request=request)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="analytics-{window}{ext}"'})


# ================================================================ GUI → API / worker inventory
WORKSPACES = {
    "RF optimization": [("GET", "/api/v1/optimization/enrollments"), ("GET", "/api/v1/devices/{device_id}/optimization"),
                        ("POST", "/api/v1/devices/{device_id}/optimization/preview"),
                        ("POST", "/api/v1/devices/{device_id}/optimization/enroll"),
                        ("POST", "/api/v1/optimization/enrollments/{enrollment_id}/approve"),
                        ("POST", "/api/v1/devices/{device_id}/optimization/disable"),
                        ("POST", "/api/v1/devices/{device_id}/optimization/evaluate"), ("POST", "/api/v1/optimization/run-now"),
                        ("GET", "/api/v1/optimization/decisions"), ("GET", "/api/v1/optimization/control"),
                        ("POST", "/api/v1/optimization/emergency-stop"), ("POST", "/api/v1/optimization/bulk-proposal"),
                        ("JOB", "scheduled_jobs.optimizer (5 min, only when INM_AUTOMATIC_OPTIMIZATION=true)")],
    "Recommendations": [("GET", "/api/v1/intelligence/recommendations"), ("GET", "/api/v1/intelligence/recommendations/{rec_id}"),
                        ("POST", "/api/v1/intelligence/recommendations/{rec_id}/triage"), ("POST", "/api/v1/ai/analyze"),
                        ("POST", "/api/v1/phase4/analyze"), ("JOB", "scheduled_jobs.scheduled_analysis (hourly)")],
    "Predictive health": [("GET", "/api/v1/intelligence/predictive"), ("POST", "/api/v1/intelligence/predictive/run"),
                          ("POST", "/api/v1/intelligence/predictive/score"), ("GET", "/api/v1/intelligence/predictive/runs"),
                          ("JOB", "scheduled_jobs.predictive (6 h: score past windows, then run)")],
    "Operational analytics": [("GET", "/api/v1/intelligence/analytics"), ("GET", "/api/v1/intelligence/analytics/devices"),
                              ("GET", "/api/v1/intelligence/analytics/export"), ("GET", "/api/v1/executive/trends")],
    "AI learning centre": [("GET", "/api/v1/ai/learning-history"), ("POST", "/api/v1/ai/feedback"), ("POST", "/api/v1/ai/outcomes"),
                           ("GET", "/api/v1/ai/models"), ("POST", "/api/v1/ai/models/train"),
                           ("POST", "/api/v1/ai/models/{model_id}/{action}"), ("GET", "/api/v1/ai/models/compare"),
                           ("POST", "/api/v1/ai/evaluation/shadow")],
    "Device insights": [("GET", "/api/v1/phase4/overview"), ("POST", "/api/v1/phase4/analyze"),
                        ("GET", "/api/v1/phase4/recommendations"), ("POST", "/api/v1/phase4/recommendations/{recommendation_id}/review"),
                        ("JOB", "scheduled_jobs.scheduled_analysis (hourly)")],
    "Assistant": [("GET", "/api/ai/status"), ("POST", "/api/ai/ask"), ("POST", "/api/ai/summary"), ("POST", "/api/ai/recommend"),
                  ("POST", "/api/ai/explain/client/{client_id}")],
    "AI rules": [("GET", "/api/v1/intelligence/rules"), ("GET", "/api/v1/intelligence/rules/{rule_id}"),
                 ("POST", "/api/v1/intelligence/rules/{rule_id}/propose"), ("POST", "/api/v1/intelligence/rules/{rule_id}/decide")],
}


@router.get("/inventory")
def inventory(request: Request, user: User = viewer):
    """Each workspace's GUI actions mapped to registered routes / background jobs, checked live."""
    paths = request.app.openapi().get("paths", {})
    routes = {(m.upper(), path) for path, ops in paths.items() for m in ops}
    out = {}
    for ws, items in WORKSPACES.items():
        rows = []
        for method, path in items:
            if method == "JOB":
                rows.append({"kind": "job", "target": path, "registered": True})
            else:
                rows.append({"kind": "route", "method": method, "target": path, "registered": (method, path) in routes})
        out[ws] = {"items": rows, "complete": all(r["registered"] for r in rows)}
    return {"workspaces": out, "complete": all(v["complete"] for v in out.values())}
