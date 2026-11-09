"""WP-08 — AI learning centre: feedback, measured outcomes, model registry (Phase 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    promoted = db.scalar(select(MlModel).where(MlModel.status == "PROMOTED").order_by(MlModel.decided_at.desc()).limit(1))
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
            "decided_by": m.decided_by, "decided_at": iso(m.decided_at)}


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
    m.status = "PROMOTED" if action == "promote" else "REJECTED"
    m.decided_by, m.decided_at = user.username, now()
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
