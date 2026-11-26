"""WP-08 — AI learning centre: feedback, measured outcomes, model registry (Phase 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import User
from ..database.models_inm import RecommendationRecord
from ..database.models_phases import DecisionFeedback, MlModel, OutcomeEvaluation
from ..database.session import get_db
from ..domain import learning as L
from ..domain.optimization_policy import shadow_simulation
from ..infrastructure.runtime import telemetry_store
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, utc

router = APIRouter(prefix="/api/v1/ai", tags=["wp08-learning"])


class FeedbackInput(BaseModel):
    recommendation_id: str
    decision: Literal["ACCEPTED", "REJECTED", "DEFERRED"]
    reason: str = Field(min_length=3, max_length=2000)


class OutcomeInput(BaseModel):
    recommendation_id: str
    metric: str = Field(min_length=2, max_length=64)
    higher_is_better: bool = False
    before_start: datetime
    before_end: datetime
    after_start: datetime
    after_end: datetime
    action_applied: bool = False
    confounders: list[str] = []


class ShadowInput(BaseModel):
    proposals: list[dict] = Field(min_length=1, max_length=200)
    cooldown_seconds: int = Field(ge=0, le=7 * 86400)
    degrade_after: int | None = None


def _rec(db: Session, rid: str) -> RecommendationRecord:
    r = db.get(RecommendationRecord, rid)
    if r is None:
        raise HTTPException(404, "Recommendation not found.")
    return r


@router.get("/learning-history")
def learning_history(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    recs = list(db.scalars(select(RecommendationRecord).order_by(RecommendationRecord.created_at.desc()).limit(200)))
    fb = {f.recommendation_id: f for f in db.scalars(select(DecisionFeedback).where(
        DecisionFeedback.recommendation_id.in_([r.id for r in recs])))} if recs else {}
    outs: dict[str, list] = {}
    for o in db.scalars(select(OutcomeEvaluation).where(OutcomeEvaluation.recommendation_id.in_([r.id for r in recs]))) if recs else []:
        outs.setdefault(o.recommendation_id, []).append(o)
    promoted = db.scalar(select(MlModel).where(MlModel.status == "PROMOTED", MlModel.target.is_(None))
                         .order_by(MlModel.decided_at.desc()).limit(1))
    rows = []
    for r in recs:
        q = L.recommendation_quality({"evidence": r.evidence, "kind": "RULE_BASED" if r.engine == "RULE" else None})
        f = fb.get(r.id)
        rows.append({"id": r.id, "created_at": iso(r.created_at), "device_id": r.device_id,
                     "title": r.title, "why": r.explanation, "kind": q["kind"],
                     "analyzer_version": f"{r.rule_id}@{r.rule_version}", "input_window": r.input_window,
                     "evidence_status": q["evidence_status"], "observed_samples": q["observed_samples"],
                     "source_type": r.source_type, "state": r.approval_state,
                     "feedback": {"decision": f.decision, "reason": f.reason, "operator": f.operator,
                                  "at": iso(f.decided_at)} if f else None,
                     "outcomes": [o.result | {"id": o.id, "action_applied": o.action_applied}
                                  for o in outs.get(r.id, [])],
                     "change_occurred": any(o.action_applied for o in outs.get(r.id, []))})
    return {"items": rows, "active_model": {"id": promoted.id, "name": promoted.name, "kind": promoted.kind}
            if promoted else None,
            "statement": "Recommendations are RULE_BASED unless a model was trained on labelled outcomes "
                         "and promoted by an independent approver."}


@router.post("/feedback", status_code=201)
def feedback(body: FeedbackInput, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("operator"))):
    r = _rec(db, body.recommendation_id)
    if db.scalar(select(DecisionFeedback).where(DecisionFeedback.recommendation_id == r.id)):
        raise HTTPException(409, "Feedback already recorded for this recommendation.")
    f = DecisionFeedback(recommendation_id=r.id, operator=user.username, decision=body.decision, reason=body.reason)
    db.add(f); db.flush()
    audit(db, "ai_feedback_recorded", username=user.username, target=r.id,
          detail=f"{body.decision}; no device action", request=request)
    return {"id": f.id, "decision": f.decision}


@router.post("/outcomes", status_code=201)
def outcome(body: OutcomeInput, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("operator"))):
    r = _rec(db, body.recommendation_id)
    bs, be, as_, ae = map(utc, (body.before_start, body.before_end, body.after_start, body.after_end))
    if not (bs < be <= as_ < ae):
        raise HTTPException(422, "Windows must be ordered: before_start < before_end <= after_start < after_end.")
    if not r.device_id:
        raise HTTPException(422, "Outcome evaluation requires a device-scoped recommendation.")
    store = telemetry_store()
    if store is None:
        raise HTTPException(503, "Telemetry store unavailable.")
    rows = store.query(device_id=r.device_id, metric_name=body.metric, since=bs, limit=5000)

    def within(lo, hi):
        out = []
        for x in rows:
            t = x["observed_at"]
            t = datetime.fromisoformat(t.replace("Z", "+00:00")) if isinstance(t, str) else t
            t = utc(t)
            if lo <= t < hi and x.get("source_type") == "REAL":
                out.append(x)
        return out
    before, after = within(bs, be), within(as_, ae)
    res = L.evaluate_outcome(metric=body.metric, higher_is_better=body.higher_is_better, before=before,
                             after=after, action_applied=body.action_applied, confounders=body.confounders)
    o = OutcomeEvaluation(recommendation_id=r.id, action_applied=body.action_applied, metric=body.metric,
                          before_window={"start": iso(bs), "end": iso(be), "n": len(before)},
                          after_window={"start": iso(as_), "end": iso(ae), "n": len(after)},
                          result=res, evaluated_by=user.username)
    db.add(o); db.flush()
    audit(db, "ai_outcome_evaluated", username=user.username, target=r.id,
          detail=f"{res['status']} metric={body.metric}", request=request)
    return {"id": o.id, **res}


@router.get("/outcomes")
def outcomes(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return [{"id": o.id, "recommendation_id": o.recommendation_id, "metric": o.metric,
             "action_applied": o.action_applied, "before": o.before_window, "after": o.after_window,
             "result": o.result, "evaluated_by": o.evaluated_by, "at": iso(o.evaluated_at)}
            for o in db.scalars(select(OutcomeEvaluation).order_by(OutcomeEvaluation.evaluated_at.desc()).limit(300))]


def _model_public(m: MlModel) -> dict:
    return {"id": m.id, "name": m.name, "kind": m.kind, "algorithm": m.algorithm, "status": m.status,
            "dataset_sha256": m.dataset_sha256, "split": m.split, "metrics": m.metrics,
            "parameters": m.parameters, "trained_by": m.trained_by, "trained_at": iso(m.trained_at),
            "decided_by": m.decided_by, "decided_at": iso(m.decided_at), "target": m.target, "version": m.version,
            "dataset_source": m.dataset_source, "production_validated": bool(m.production_validated),
            "validation_note": m.validation_note, "health": m.health, "health_detail": m.health_detail}


@router.get("/models")
def models(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return [_model_public(m) for m in db.scalars(select(MlModel).order_by(MlModel.trained_at.desc()).limit(100))]


@router.post("/models/train")
def train(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    recs = {r.id: r for r in db.scalars(select(RecommendationRecord))}
    rows = []
    for o in db.scalars(select(OutcomeEvaluation)):
        r = recs.get(o.recommendation_id)
        succ = (o.result or {}).get("success")
        if r is not None and isinstance(succ, bool):
            rows.append({"id": o.id, "t": iso(o.evaluated_at), "rule_id": r.rule_id, "label": succ})
    result = L.train_rule_success_model(rows)
    if result["status"] != "CANDIDATE":
        audit(db, "ai_training_refused", username=user.username, detail=result["reason"], request=request)
        return result
    m = MlModel(name=f"rule-success-{now():%Y%m%d%H%M}", kind=result["kind"], algorithm=result["algorithm"],
                status="CANDIDATE", dataset_sha256=result["dataset_sha256"], split=result["split"],
                metrics=result["metrics"], parameters=result["parameters"], trained_by=user.username)
    db.add(m); db.flush()
    audit(db, "ai_model_trained_candidate", username=user.username, target=m.id,
          detail=f"holdout={result['metrics']['holdout']}; not active until independently promoted",
          request=request)
    return _model_public(m)


@router.get("/models/compare")
def compare_models(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Side-by-side holdout metrics against the rule baseline; shows which model is active and why."""
    ms = list(db.scalars(select(MlModel).where(MlModel.target.is_(None)).order_by(MlModel.trained_at.desc()).limit(20)))
    rows = []
    for m in ms:
        hold = (m.metrics or {}).get("holdout", {})
        base = (m.metrics or {}).get("holdout_baseline_brier")
        n_hold = (m.split or {}).get("holdout", 0)
        rows.append({"id": m.id, "name": m.name, "status": m.status, "algorithm": m.algorithm, "trained_by": m.trained_by,
                     "trained_at": iso(m.trained_at), "decided_by": m.decided_by, "dataset_sha256": m.dataset_sha256,
                     "split": m.split, "holdout": hold, "baseline_brier": base,
                     "brier_improvement": (round(base - hold["brier"], 4) if base is not None and hold.get("brier") is not None else None),
                     "beats_baseline": (hold.get("brier") is not None and base is not None and hold["brier"] <= base),
                     "limitations": [x for x in (
                         f"small holdout ({n_hold} rows)" if n_hold < 10 else None,
                         "per-rule success rates only; no device-level features",
                         "labels come from engineer-evaluated outcomes; unevaluated recommendations are excluded") if x]})
    active = next((r for r in rows if r["status"] == "PROMOTED"), None)
    return {"models": rows, "active": active["id"] if active else None,
            "baseline": "Rules only (no model): every recommendation is RULE_BASED"}


@router.post("/models/{model_id}/rollback")
def rollback_model(model_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_any_role("approver", "admin"))):
    """Demote the active model and restore the previously promoted one (or rules-only if none)."""
    m = db.get(MlModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found.")
    if m.status != "PROMOTED":
        raise HTTPException(409, "Only the active (promoted) model can be rolled back.")
    m.status, m.decided_by, m.decided_at = "ROLLED_BACK", user.username, now()
    same = MlModel.target == m.target if m.target is not None else MlModel.target.is_(None)
    prev = db.scalar(select(MlModel).where(MlModel.status == "SUPERSEDED", same)
                     .order_by(MlModel.decided_at.desc()).limit(1))
    if prev is not None:
        prev.status = "PROMOTED"
    audit(db, "ai_model_rolled_back", username=user.username, target=m.id,
          detail=f"restored={prev.id if prev else 'RULES_ONLY'}", request=request)
    return {"rolled_back": m.id, "active": prev.id if prev else None, "mode": "MODEL" if prev else "RULES_ONLY"}


@router.post("/models/{model_id}/{action}")
def decide_model(model_id: str, action: Literal["promote", "reject"], request: Request,
                 db: Session = Depends(get_db), user: User = Depends(require_any_role("approver", "admin"))):
    m = db.get(MlModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found.")
    if m.status != "CANDIDATE":
        raise HTTPException(409, "Model already decided.")
    if action == "promote":
        try:
            L.check_promotion(trainer=m.trained_by, approver=user.username, metrics=m.metrics)
        except ValueError as exc:
            raise HTTPException(403 if "independent" in str(exc) else 422, str(exc)) from None
    elif m.trained_by.casefold() == user.username.casefold():
        raise HTTPException(403, "Model decisions require an independent approver.")
    if action == "promote" and m.dataset_source == "SYNTHETIC" and get_settings().mode != "demo":
        raise HTTPException(409, "A model trained on SYNTHETIC data cannot be promoted on a live installation; "
                                 "train on live telemetry.")
    if action == "promote":   # one active model per target: the previous one is kept for rollback
        same = MlModel.target == m.target if m.target is not None else MlModel.target.is_(None)
        for other in db.scalars(select(MlModel).where(MlModel.status == "PROMOTED", MlModel.id != m.id, same)):
            other.status = "SUPERSEDED"
    m.status = "PROMOTED" if action == "promote" else "REJECTED"
    m.decided_by, m.decided_at = user.username, now()
    if m.status == "PROMOTED":
        m.decided_at = now()
        from ..ml import service as MLS
        mode, why = MLS.mode_of(db, m) if m.target else ("RULE_SUCCESS", "")
        audit(db, "ai_model_promoted", username=user.username, target=m.id,
              detail=f"{m.name} {m.dataset_source or ''} mode={mode}: {why}"[:900], request=request)
    else:
        audit(db, f"ai_model_{m.status.lower()}", username=user.username, target=m.id, request=request)
    return _model_public(m)


@router.post("/evaluation/shadow")
def shadow(body: ShadowInput, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("operator"))):
    try:
        props = [{"at": utc(datetime.fromisoformat(str(p["at"]).replace("Z", "+00:00"))), "op": str(p["op"])}
                 for p in body.proposals]
    except (KeyError, ValueError):
        raise HTTPException(422, "Each proposal needs an ISO 'at' and an 'op'.") from None
    res = shadow_simulation(proposals=props, cooldown_seconds=body.cooldown_seconds, degrade_after=body.degrade_after)
    audit(db, "optimization_shadow_simulated", username=user.username,
          detail=f"{len(props)} proposals stopped={res['stopped']}; ZERO device writes", request=request)
    return res


# ================================================================== rc18 trained breach-risk models (2.1)
class MlTrainIn(BaseModel):
    target: str = Field(max_length=64)
    synthetic: bool = False


class MlSettingsIn(BaseModel):
    inference_enabled: bool | None = None
    retrain_interval_days: int | None = Field(default=None, ge=1, le=90)
    history_days: int | None = Field(default=None, ge=14, le=365)
    retrain_on_drift: bool | None = None


def _run_public(r) -> dict:
    return {"id": r.id, "target": r.target, "trigger": r.trigger, "dataset_source": r.dataset_source,
            "status": r.status, "summary": r.summary, "model_id": r.model_id, "requested_by": r.requested_by,
            "started_at": iso(r.started_at), "finished_at": iso(r.finished_at)}


@router.get("/ml/status")
def ml_status(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Per predictive target: which engine produces the probability of record and why (dashboard)."""
    from ..ml import service as MLS
    return {"targets": MLS.status(db), "settings": MLS.settings(db),
            "statement": "Rules and the statistical engine remain the fallback. A model trained on synthetic or demo "
                         "data is never production-validated and never drives a live prediction."}


@router.get("/ml/settings")
def ml_settings(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..ml import service as MLS
    return MLS.settings(db)


@router.put("/ml/settings")
def ml_save_settings(body: MlSettingsIn, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    from ..ml import service as MLS
    out = MLS.save_settings(db, body.model_dump(exclude_none=True), user.username)
    audit(db, "ml_settings_changed", username=user.username, detail=str(out)[:900], request=request)
    return out


@router.post("/ml/train")
def ml_train(body: MlTrainIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("admin"))):
    from ..ml import service as MLS
    try:
        MLS._rule(db, body.target)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    run = MLS.train(db, body.target, actor=user.username, synthetic=body.synthetic)
    audit(db, "ml_training_run", username=user.username, target=run.id, success=run.status == "SUCCEEDED",
          detail=f"{body.target} {run.dataset_source} {run.status} {str(run.summary)[:600]}", request=request)
    return {"run": _run_public(run), "model": _model_public(db.get(MlModel, run.model_id)) if run.model_id else None}


@router.get("/ml/runs")
def ml_runs(target: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_rc18 import MlTrainingRun
    q = select(MlTrainingRun).order_by(MlTrainingRun.started_at.desc()).limit(100)
    if target:
        q = q.where(MlTrainingRun.target == target)
    return [_run_public(r) for r in db.scalars(q)]


@router.get("/ml/models/{model_id}/performance")
def ml_performance(model_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..ml import service as MLS
    m = db.get(MlModel, model_id)
    if m is None or m.target is None:
        raise HTTPException(404, "Trained breach-risk model not found.")
    return MLS.performance(db, m)


@router.get("/ml/models/{model_id}/drift")
def ml_drift(model_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_rc18 import MlDriftReport
    return [{"id": r.id, "checked_at": iso(r.checked_at), "state": r.state, "max_psi": r.max_psi, "samples": r.samples,
             "features": r.features}
            for r in db.scalars(select(MlDriftReport).where(MlDriftReport.model_id == model_id)
                                .order_by(MlDriftReport.checked_at.desc()).limit(30))]


@router.post("/ml/models/{model_id}/drift")
def ml_drift_check(model_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("admin"))):
    from ..ml import service as MLS
    m = db.get(MlModel, model_id)
    if m is None or m.target is None:
        raise HTTPException(404, "Trained breach-risk model not found.")
    rep = MLS.check_drift(db, m)
    audit(db, "ml_drift_checked", username=user.username, target=m.id, detail=f"{rep.state} max_psi={rep.max_psi}",
          request=request)
    return {"state": rep.state, "max_psi": rep.max_psi, "samples": rep.samples, "features": rep.features,
            "health": m.health}


@router.get("/ml/compare")
def ml_compare(target: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Every version of a target evaluated on the same, most recent observations (plus statistical + base rate)."""
    from ..ml import service as MLS
    try:
        MLS._rule(db, target)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return MLS.compare_versions(db, target)
