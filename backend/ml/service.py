"""Trained-model lifecycle for predictive breach risk (rc18, directive 2.1).

  train()          dataset (LIVE/DEMO telemetry or clearly SYNTHETIC series) -> walk-forward labelled samples ->
                   temporal train/validation/holdout split with a horizon embargo -> L2 selection on validation ->
                   holdout metrics vs climatology AND vs the statistical engine -> versioned CANDIDATE model
  promotion        existing WP-08 rule: an independent approver, holdout must beat the baseline. Additionally a
                   SYNTHETIC model is never promotable in live mode, and only a production-validated LIVE model
                   drives predictions (PRIMARY); any other promoted model runs in SHADOW (recorded and scored,
                   never used)
  infer()          called by the predictive run for every device/rule; statistical + rule engines stay the
                   fallback whenever no model is PRIMARY, inference is disabled, the model drifted or degraded
  score/monitor    issued inferences are scored against observed peaks; live Brier skill < 0 on >= 50 scored
                   windows -> DEGRADED (automatic fallback); PSI drift >= 0.25 -> DRIFT (automatic fallback)
  retrain          scheduled (default weekly) and on drift; never auto-promotes (independent approval stays)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..database.models_phases import MlModel
from ..database.models_rc18 import MlDriftReport, MlInference, MlTrainingRun
from ..domain import learning as L
from ..domain import predictive as PR
from ..infrastructure.clock import now as clock_now
from . import breach_model as B

log = logging.getLogger("inm.ml")
SETTINGS_KEY = "ml:settings"
DEFAULTS = {"inference_enabled": True, "retrain_interval_days": 7, "history_days": 60, "retrain_on_drift": True}
MIN_SAMPLES = 200
MIN_POSITIVES = 10
VALIDATION_CRITERIA = {"holdout_n": 100, "holdout_positives": 10, "brier_skill": 0.05, "auc": 0.65}
DEGRADED_MIN_SCORED = 50
STAT_BASELINE_SAMPLES = 400


def settings(db: Session) -> dict:
    row = db.get(AppSetting, SETTINGS_KEY)
    return DEFAULTS | (row.value if row else {})


def save_settings(db: Session, values: dict, actor: str) -> dict:
    merged = settings(db) | {k: v for k, v in values.items() if k in DEFAULTS}
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=merged, updated_by=actor))
    else:
        row.value, row.updated_by = merged, actor
    db.flush()
    return merged


def _utc(t):
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def _rule(db: Session, target: str) -> dict:
    from ..infrastructure import rule_store as RC
    rules = RC.effective(db, "PREDICTIVE")
    if target not in rules:
        raise ValueError(f"Unknown predictive target {target!r}; choose one of {sorted(rules)}")
    return rules[target] | {"metric": RC.PRED_METRIC[target]}


def _series(store, device_id: str, metric: str, since: datetime, source_type: str):
    rows = store.query(device_id=device_id, metric_name=metric, since=since, limit=20000, entity_type="device")
    return [(B._utc(r["observed_at"]), float(r["value"])) for r in rows
            if r.get("value") is not None and r.get("source_type") == source_type]


def _source_type() -> tuple[str, str]:
    """(telemetry source_type, dataset_source label). Demo installs train on DEMO data only, live on REAL only."""
    return ("DEMO", "DEMO") if get_settings().mode == "demo" else ("REAL", "LIVE")


def dataset(db: Session, target: str, *, source: str, store=None, now: datetime | None = None) -> tuple[list, dict, dict]:
    """Labelled samples + raw series per entity (for the statistical baseline) + summary."""
    rule = _rule(db, target)
    p = rule["params"]
    thr, horizon = float(p["threshold"]), int(p["horizon_hours"])
    now = now or clock_now()
    rows, series = [], {}
    if source == "SYNTHETIC":
        for i in range(24):
            pts = B.synthetic_series(1000 + i, days=60, end=now, threshold=thr)
            ent = f"synthetic-{i:02d}"
            series[ent] = pts
            rows += B.samples(pts, threshold=thr, horizon=horizon, entity=ent)
        return rows, series, {"entities": 24, "generator": "breach_model.synthetic_series seeds 1000-1023",
                              "threshold": thr, "horizon_hours": horizon, "metric": rule["metric"]}
    if store is None:
        from ..infrastructure.runtime import telemetry_store
        store = telemetry_store()
    if store is None:
        return [], {}, {"reason": "telemetry store not configured"}
    src_type, _ = _source_type()
    since = now - timedelta(days=int(settings(db)["history_days"]))
    devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type == src_type)))
    for d in devices:
        if rule["metric"].startswith("util_") and d.family != "access_point":
            continue
        pts = _series(store, d.id, rule["metric"], since, src_type)
        if pts:
            series[d.id] = pts
            rows += B.samples(pts, threshold=thr, horizon=horizon, entity=d.id, end=now - timedelta(hours=horizon))
    return rows, series, {"entities": len(series), "devices_considered": len(devices), "threshold": thr,
                          "horizon_hours": horizon, "metric": rule["metric"], "history_days": settings(db)["history_days"]}


def _embargo(parts: dict, horizon: int) -> dict:
    """Drop validation/holdout samples whose label window overlaps the previous split (no label leakage)."""
    def last(rows):
        return max(B._utc(r["t"]) for r in rows) if rows else None
    t_train = last(parts["train"])
    val = [r for r in parts["validation"] if t_train is None or B._utc(r["t"]) >= t_train + timedelta(hours=horizon)]
    t_val = last(val) or t_train
    hold = [r for r in parts["holdout"] if t_val is None or B._utc(r["t"]) >= t_val + timedelta(hours=horizon)]
    return {"train": parts["train"], "validation": val, "holdout": hold}


def validation_status(source: str, holdout: dict, beats_statistical: bool | None) -> tuple[bool, str]:
    if source == "SYNTHETIC":
        return False, "Trained on SYNTHETIC data: pipeline evidence only, NOT validated for production predictions."
    if source != "LIVE":
        return False, "Trained on DEMO data: not validated for production predictions."
    c = VALIDATION_CRITERIA
    fails = [why for ok, why in (
        (holdout.get("n", 0) >= c["holdout_n"], f"holdout n < {c['holdout_n']}"),
        (holdout.get("positives", 0) >= c["holdout_positives"], f"holdout breaches < {c['holdout_positives']}"),
        ((holdout.get("brier_skill") or -1) >= c["brier_skill"], f"Brier skill < {c['brier_skill']}"),
        ((holdout.get("auc") or 0) >= c["auc"], f"AUC < {c['auc']}"),
        (beats_statistical is not False, "does not beat the statistical engine on the same windows"),
    ) if not ok]
    if fails:
        return False, "Not production-validated: " + "; ".join(fails)
    return True, "Production-validated on live telemetry holdout (criteria " + \
        ", ".join(f"{k}>={v}" for k, v in c.items()) + "; beats the statistical engine)."


def train(db: Session, target: str, *, actor: str, trigger: str = "MANUAL", synthetic: bool = False,
          store=None, now: datetime | None = None) -> MlTrainingRun:
    now = now or clock_now()
    source = "SYNTHETIC" if synthetic else _source_type()[1]
    run = MlTrainingRun(target=target, trigger=trigger, dataset_source=source, status="FAILED", summary={},
                        requested_by=actor, started_at=now)
    db.add(run)
    db.flush()
    try:
        rule = _rule(db, target)
        rows, series, summ = dataset(db, target, source=source, store=store, now=now)
        horizon, thr = int(rule["params"]["horizon_hours"]), float(rule["params"]["threshold"])
        pos = sum(r["y"] for r in rows)
        run.summary = summ | {"samples": len(rows), "positives": pos}
        if len(rows) < MIN_SAMPLES or pos < MIN_POSITIVES:
            run.status = "INSUFFICIENT_DATA"
            run.summary["reason"] = (f"needs >= {MIN_SAMPLES} labelled windows and >= {MIN_POSITIVES} breaches; "
                                     f"have {len(rows)} and {pos}")
            return run
        parts = _embargo(L.temporal_split(rows, train=0.6, validation=0.2), horizon)
        if len(parts["validation"]) < 20 or len(parts["holdout"]) < 20 or not any(r["y"] for r in parts["train"]):
            run.status = "INSUFFICIENT_DATA"
            run.summary["reason"] = "not enough windows after the temporal split and horizon embargo"
            return run
        params, tried = B.select(parts["train"], parts["validation"])
        base_rate = sum(r["y"] for r in parts["train"]) / len(parts["train"])
        hold = parts["holdout"]
        hold_ps = [B.predict(params, r["x"]) for r in hold]
        alert = float(rule["params"].get("alert_probability", B.DECISION_THRESHOLD))
        m_hold = B.metrics(hold_ps, [r["y"] for r in hold], alert=alert, base_rate=base_rate)
        m_val = B.metrics([B.predict(params, r["x"]) for r in parts["validation"]],
                          [r["y"] for r in parts["validation"]], alert=alert, base_rate=base_rate)
        clim = sum((base_rate - float(r["y"])) ** 2 for r in hold) / len(hold)
        # the statistical engine on the same holdout windows (deterministic subsample if large)
        step = max(1, len(hold) // STAT_BASELINE_SAMPLES)
        stat_pairs = []
        for r in hold[::step]:
            f = PR.forecast(series[r["entity"]], cutoff=B._utc(r["t"]), threshold=thr, horizon_hours=horizon,
                            min_days=int(rule["params"]["min_days"]))
            if f.get("probability") is not None:
                stat_pairs.append((f["probability"], r["y"], B.predict(params, r["x"])))
        stat_brier = (round(sum((p - float(y)) ** 2 for p, y, _ in stat_pairs) / len(stat_pairs), 4)
                      if stat_pairs else None)
        ml_same = (round(sum((q - float(y)) ** 2 for _, y, q in stat_pairs) / len(stat_pairs), 4)
                   if stat_pairs else None)
        validated, note = validation_status(source, m_hold, None if ml_same is None else ml_same <= stat_brier)
        version = 1 + max([m.version or 0 for m in db.scalars(select(MlModel).where(MlModel.target == target))] or [0])
        model = MlModel(
            name=f"{target.lower()}-v{version}", kind="TRAINED_ML", algorithm=B.ALGORITHM, status="CANDIDATE",
            dataset_sha256=L.dataset_fingerprint([{"id": r["id"], "y": r["y"]} for r in rows]),
            split={k: len(v) for k, v in parts.items()},
            metrics={"validation": m_val, "holdout": m_hold, "holdout_baseline_brier": round(clim, 4),
                     "statistical_holdout_brier": stat_brier, "ml_on_statistical_windows_brier": ml_same,
                     "statistical_windows": len(stat_pairs), "selection": tried},
            parameters=params | {"target": target, "metric": rule["metric"], "threshold": thr, "horizon_hours": horizon,
                                 "alert_probability": alert},
            trained_by=actor, trained_at=now, target=target, version=version, dataset_source=source,
            production_validated=validated, validation_note=note,
            training_profile=B.training_profile([r["x"] for r in parts["train"]]), health="NEW")
        db.add(model)
        db.flush()
        run.status, run.model_id = "SUCCEEDED", model.id
        run.summary |= {"model": model.name, "production_validated": validated, "holdout_brier": m_hold.get("brier"),
                        "statistical_holdout_brier": stat_brier, "holdout_auc": m_hold.get("auc")}
        return run
    except Exception as exc:  # recorded on the run; never silently dropped
        log.exception("ML training failed for %s", target)
        run.status = "FAILED"
        run.summary = (run.summary or {}) | {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return run
    finally:
        run.finished_at = clock_now()
        db.flush()


# ------------------------------------------------------------------------------------------- inference
def active(db: Session, target: str) -> MlModel | None:
    return db.scalar(select(MlModel).where(MlModel.target == target, MlModel.status == "PROMOTED")
                     .order_by(MlModel.decided_at.desc()).limit(1))


def mode_of(db: Session, m: MlModel | None) -> tuple[str, str]:
    if m is None:
        return "STATISTICAL_FALLBACK", "No promoted model for this target; rules + statistical engine in use."
    if not settings(db)["inference_enabled"]:
        return "STATISTICAL_FALLBACK", "ML inference disabled by an administrator."
    if m.health in ("DRIFT", "DEGRADED"):
        return "STATISTICAL_FALLBACK", f"Model {m.name} is {m.health}; automatic fallback to the statistical engine."
    if not m.production_validated:
        return "SHADOW", f"Model {m.name} is not production-validated ({m.dataset_source}); recorded and scored only."
    return "PRIMARY", f"Model {m.name} (production-validated) drives the probability."


def infer(db: Session, device: InmDevice, target: str, points, *, now: datetime, horizon: int,
          threshold: float, metric: str) -> dict | None:
    m = active(db, target)
    mode, why = mode_of(db, m)
    if m is None or not settings(db)["inference_enabled"]:
        return None
    params = m.parameters
    if float(params.get("threshold", threshold)) != float(threshold) or int(params.get("horizon_hours", horizon)) != horizon:
        return {"mode": "STATISTICAL_FALLBACK", "model": m.name,
                "reason": "Rule threshold/horizon changed since training; retrain required."}
    if m.dataset_source in ("SYNTHETIC", "DEMO") and device.source_type != "DEMO":
        return None                                      # never score real devices with non-live models
    x = B.extract(B.hourly(points), now, threshold, horizon)
    if x is None:
        return {"mode": mode, "model": m.name, "reason": "insufficient recent history for the model features"}
    p = B.predict(params, x)
    conf = B.confidence(params, x, p)
    used = mode == "PRIMARY"
    db.add(MlInference(model_id=m.id, target=target, device_id=device.id, metric=metric, threshold=threshold,
                       issued_at=now, window_end=now + timedelta(hours=horizon), probability=round(p, 4),
                       confidence=conf["level"], used_for_prediction=used))
    return {"mode": mode, "model": m.name, "model_id": m.id, "probability": round(p, 4), "confidence": conf,
            "used_for_prediction": used, "reason": why}


# ------------------------------------------------------------------------------------ monitoring/scoring
def score(db: Session, *, store=None, now: datetime | None = None) -> dict:
    now = now or clock_now()
    if store is None:
        from ..infrastructure.runtime import telemetry_store
        store = telemetry_store()
    if store is None:
        return {"scored": 0, "reason": "telemetry store not configured"}
    n = 0
    for inf in db.scalars(select(MlInference).where(MlInference.outcome.is_(None), MlInference.window_end <= now)
                          .limit(5000)):
        d = db.get(InmDevice, inf.device_id)
        pts = _series(store, inf.device_id, inf.metric, _utc(inf.issued_at), d.source_type) if d else []
        peak = PR.observed_peak(pts, _utc(inf.issued_at), _utc(inf.window_end))
        inf.observed_peak = peak
        inf.outcome = "UNOBSERVED" if peak is None else "BREACH" if peak >= inf.threshold else "NO_BREACH"
        inf.evaluated_at = now
        n += 1
    db.flush()
    return {"scored": n}


def performance(db: Session, m: MlModel) -> dict:
    rows = list(db.scalars(select(MlInference).where(MlInference.model_id == m.id,
                                                     MlInference.outcome.in_(("BREACH", "NO_BREACH")))))
    if not rows:
        return {"scored": 0, "state": "NO_OUTCOMES_YET"}
    base = ((m.metrics or {}).get("holdout") or {}).get("base_rate")
    met = B.metrics([r.probability for r in rows], [r.outcome == "BREACH" for r in rows],
                    alert=float((m.parameters or {}).get("alert_probability", 0.5)), base_rate=base)
    state = ("DEGRADED" if len(rows) >= DEGRADED_MIN_SCORED and (met.get("brier_skill") or 0) < 0
             else "OK" if len(rows) >= DEGRADED_MIN_SCORED else "ACCUMULATING")
    return {"scored": len(rows), "state": state, "live": met,
            "holdout": (m.metrics or {}).get("holdout"), "min_scored_for_verdict": DEGRADED_MIN_SCORED}


def check_drift(db: Session, m: MlModel, *, store=None, now: datetime | None = None, days: int = 7) -> MlDriftReport:
    now = now or clock_now()
    params = m.parameters
    xs = []
    if m.dataset_source == "SYNTHETIC":
        for i in range(6):     # synthetic models are monitored against fresh synthetic draws (pipeline evidence)
            pts = B.synthetic_series(5000 + i, days=days + 3, end=now, threshold=float(params["threshold"]))
            xs += [r["x"] for r in B.samples(pts, threshold=float(params["threshold"]),
                                             horizon=int(params["horizon_hours"]), entity="drift")]
    else:
        if store is None:
            from ..infrastructure.runtime import telemetry_store
            store = telemetry_store()
        src = "DEMO" if m.dataset_source == "DEMO" else "REAL"
        if store is not None:
            for d in db.scalars(select(InmDevice).where(InmDevice.source_type == src)):
                pts = _series(store, d.id, params["metric"], now - timedelta(days=days + 2), src)
                h = B.hourly(pts)
                t = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
                while t <= now:
                    x = B.extract(h, t, float(params["threshold"]), int(params["horizon_hours"]))
                    if x is not None:
                        xs.append(x)
                    t += timedelta(hours=6)
    res = B.drift(m.training_profile or {}, xs)
    rep = MlDriftReport(model_id=m.id, checked_at=now, state=res["state"], max_psi=res.get("max_psi"),
                        samples=res["samples"], features=res["features"])
    db.add(rep)
    _set_health(db, m, drift_state=res["state"])
    db.flush()
    return rep


def _set_health(db: Session, m: MlModel, *, drift_state: str | None = None) -> None:
    perf = performance(db, m)
    detail = dict(m.health_detail or {})
    if drift_state:
        detail["drift"] = drift_state
    detail["performance"] = perf["state"]
    new = ("DEGRADED" if perf["state"] == "DEGRADED" else "DRIFT" if detail.get("drift") == "DRIFT"
           else "WATCH" if detail.get("drift") == "WATCH" else "OK")
    if new != m.health and m.status == "PROMOTED" and new in ("DEGRADED", "DRIFT"):
        try:
            from ..infrastructure.notifications import enqueue_event
            enqueue_event(db, kind="ml_model_health", severity="major", ref=m.id, event=new,
                          message=f"Model {m.name} is {new}; predictions fell back to the statistical engine.")
        except Exception:  # noqa: BLE001  notification failure must not block the health change
            log.exception("could not notify model health change")
    m.health, m.health_detail = new, detail


def monitor(db: Session, *, store=None, now: datetime | None = None) -> dict:
    now = now or clock_now()
    out = {"score": score(db, store=store, now=now), "models": []}
    for m in db.scalars(select(MlModel).where(MlModel.status == "PROMOTED", MlModel.target.is_not(None))):
        rep = check_drift(db, m, store=store, now=now)
        out["models"].append({"model": m.name, "drift": rep.state, "health": m.health})
    return out


def scheduled_retrain(db: Session, *, store=None, now: datetime | None = None) -> dict:
    """Retrain each predictive target when its last successful run is older than the interval, or its promoted
    model drifted (if enabled). Produces CANDIDATES only; promotion stays an independent human decision."""
    from ..infrastructure import rule_store as RC
    now = now or clock_now()
    cfg = settings(db)
    out = []
    for target, rule in RC.effective(db, "PREDICTIVE").items():
        if not rule["enabled"]:
            continue
        last = db.scalar(select(MlTrainingRun).where(MlTrainingRun.target == target,
                                                     MlTrainingRun.dataset_source != "SYNTHETIC")
                         .order_by(MlTrainingRun.started_at.desc()).limit(1))
        act = active(db, target)
        due = last is None or _utc(last.started_at) <= now - timedelta(days=int(cfg["retrain_interval_days"]))
        drifted = bool(act and act.health == "DRIFT" and cfg["retrain_on_drift"])
        if due or drifted:
            run = train(db, target, actor="ml-scheduler", trigger="DRIFT" if drifted and not due else "SCHEDULED",
                        store=store, now=now)
            out.append({"target": target, "status": run.status, "model_id": run.model_id})
    return {"runs": out}


def status(db: Session) -> list[dict]:
    from ..infrastructure import rule_store as RC
    rows = []
    for target in RC.effective(db, "PREDICTIVE"):
        m = active(db, target)
        mode, why = mode_of(db, m)
        last_run = db.scalar(select(MlTrainingRun).where(MlTrainingRun.target == target)
                             .order_by(MlTrainingRun.started_at.desc()).limit(1))
        drift = (db.scalar(select(MlDriftReport).where(MlDriftReport.model_id == m.id)
                           .order_by(MlDriftReport.checked_at.desc()).limit(1)) if m else None)
        rows.append({"target": target, "mode": mode, "reason": why,
                     "model": None if m is None else {
                         "id": m.id, "name": m.name, "version": m.version, "dataset_source": m.dataset_source,
                         "production_validated": m.production_validated, "validation_note": m.validation_note,
                         "health": m.health, "holdout": (m.metrics or {}).get("holdout")},
                     "last_training": None if last_run is None else {
                         "id": last_run.id, "status": last_run.status, "trigger": last_run.trigger,
                         "dataset_source": last_run.dataset_source, "at": last_run.started_at.isoformat()},
                     "last_drift": None if drift is None else {"state": drift.state, "max_psi": drift.max_psi,
                                                               "at": drift.checked_at.isoformat()}})
    return rows


def compare_versions(db: Session, target: str, *, store=None, now: datetime | None = None) -> dict:
    """Evaluate every version of a target on the SAME observations: the most recent 20 % of windows of a freshly
    built dataset (live telemetry, or the synthetic generator for synthetic-only targets), plus the statistical
    engine and the climatological base rate on those windows. Register P8-04 / INTEG-P8-04."""
    now = now or clock_now()
    models = list(db.scalars(select(MlModel).where(MlModel.target == target).order_by(MlModel.version)))
    if not models:
        return {"target": target, "windows": 0, "versions": [], "reason": "no trained versions"}
    source = "SYNTHETIC" if all(m.dataset_source == "SYNTHETIC" for m in models) else _source_type()[1]
    rows, series, summ = dataset(db, target, source=source, store=store, now=now)
    rows = sorted(rows, key=lambda r: (r["t"], r["id"]))
    window = rows[int(len(rows) * 0.8):]
    if len(window) < 20:
        return {"target": target, "windows": len(window), "versions": [], "dataset_source": source,
                "reason": "not enough recent labelled windows to compare on the same observations"}
    ys = [r["y"] for r in window]
    rate = sum(r["y"] for r in rows[: int(len(rows) * 0.8)]) / max(1, int(len(rows) * 0.8))
    out = []
    for m in models:
        if int(m.parameters.get("horizon_hours", 0)) != int(summ.get("horizon_hours", -1)):
            out.append({"model": m.name, "version": m.version, "status": m.status, "comparable": False,
                        "reason": "trained for a different horizon"})
            continue
        ps = [B.predict(m.parameters, r["x"]) for r in window]
        met = B.metrics(ps, ys, alert=float(m.parameters.get("alert_probability", 0.5)), base_rate=rate)
        out.append({"model": m.name, "version": m.version, "status": m.status, "dataset_source": m.dataset_source,
                    "production_validated": m.production_validated, "comparable": True,
                    "brier": met["brier"], "brier_skill": met["brier_skill"], "auc": met["auc"],
                    "precision": met["precision"], "recall": met["recall"]})
    stat = []
    rule = _rule(db, target)
    for r in window[:: max(1, len(window) // STAT_BASELINE_SAMPLES)]:
        f = PR.forecast(series[r["entity"]], cutoff=B._utc(r["t"]), threshold=float(rule["params"]["threshold"]),
                        horizon_hours=int(rule["params"]["horizon_hours"]), min_days=int(rule["params"]["min_days"]))
        if f.get("probability") is not None:
            stat.append((f["probability"], r["y"]))
    best = min((v for v in out if v.get("comparable")), key=lambda v: v["brier"], default=None)
    return {"target": target, "dataset_source": source, "windows": len(window), "positives": sum(ys),
            "window_from": window[0]["t"], "window_to": window[-1]["t"], "versions": out,
            "climatology_brier": round(sum((rate - float(y)) ** 2 for y in ys) / len(ys), 4),
            "statistical_brier": round(sum((p - float(y)) ** 2 for p, y in stat) / len(stat), 4) if stat else None,
            "best_version": best["model"] if best else None}
