"""WP-08 / GAP-11, GAP-12 — explainable learning loop (pure).

Kinds: RULE_BASED, STATISTICAL, TRAINED_ML. A model is never called trained unless it was fit
on a labelled dataset with temporal train/validation/holdout separation and then promoted by
an independent approver. An LLM explanation is not a trained optimizer.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

KINDS = ("RULE_BASED", "STATISTICAL", "TRAINED_ML")
DECISIONS = ("ACCEPTED", "REJECTED", "DEFERRED")
MIN_SAMPLES_PER_WINDOW = 3
MIN_LABELS_TO_TRAIN = 20


def _ts(x) -> datetime:
    if isinstance(x, str):
        x = datetime.fromisoformat(x.replace("Z", "+00:00"))
    return x if x.tzinfo else x.replace(tzinfo=timezone.utc)


def evaluate_outcome(*, metric: str, higher_is_better: bool, before: list[dict],
                     after: list[dict], action_applied: bool,
                     confounders: list[str] | None = None) -> dict:
    """Compare before/after windows. Insufficient data -> INSUFFICIENT_EVIDENCE (never success)."""
    def series(rows):
        return [r["value"] for r in rows if r.get("metric_name") == metric
                and r.get("value") is not None and r.get("quality") == "OBSERVED"]
    b, a = series(before), series(after)
    confounders = list(confounders or [])
    if not action_applied:
        confounders.append("NO_ACTION_APPLIED")
    if len(b) < MIN_SAMPLES_PER_WINDOW or len(a) < MIN_SAMPLES_PER_WINDOW:
        return {"status": "INSUFFICIENT_EVIDENCE", "metric": metric, "before_n": len(b),
                "after_n": len(a), "success": None, "quality_delta": None,
                "confounders": confounders}
    mb, ma = sum(b) / len(b), sum(a) / len(a)
    sb = math.sqrt(sum((x - mb) ** 2 for x in b) / max(1, len(b) - 1))
    delta = (ma - mb) if higher_is_better else (mb - ma)
    # Require a change beyond one before-window standard deviation to call it an effect.
    threshold = max(sb, 1e-9)
    status = "IMPROVED" if delta > threshold else "DEGRADED" if delta < -threshold else "NO_CHANGE"
    return {"status": status, "metric": metric, "before_n": len(b), "after_n": len(a),
            "before_mean": round(mb, 3), "after_mean": round(ma, 3),
            "quality_delta": round(delta, 3),
            "success": (status == "IMPROVED") if action_applied and not confounders else None,
            "confounders": confounders}


def recommendation_quality(rec: dict) -> dict:
    """Disclose input quality on every recommendation. Missing coverage -> INSUFFICIENT_EVIDENCE."""
    ev = rec.get("evidence") or {}
    samples = ev.get("samples") or []
    kind = rec.get("kind") or ("TRAINED_ML" if rec.get("model_id") else "RULE_BASED")
    if kind not in KINDS:
        kind = "RULE_BASED"
    observed = [s for s in samples if s.get("value") is not None]
    status = "OK" if len(observed) >= 2 else "INSUFFICIENT_EVIDENCE"
    return {"kind": kind, "samples": len(samples), "observed_samples": len(observed),
            "evidence_status": status}


def temporal_split(rows: list[dict], *, train: float = 0.6, validation: float = 0.2) -> dict:
    """Strict time-ordered split. Raises if any validation/holdout row precedes a training row."""
    if not rows:
        raise ValueError("Empty dataset.")
    ordered = sorted(rows, key=lambda r: (_ts(r["t"]), str(r.get("id", ""))))
    n = len(ordered)
    i, j = int(n * train), int(n * (train + validation))
    parts = {"train": ordered[:i], "validation": ordered[i:j], "holdout": ordered[j:]}
    check_no_leakage(parts)
    return parts


def check_no_leakage(parts: dict) -> None:
    def bounds(rows):
        return (min(_ts(r["t"]) for r in rows), max(_ts(r["t"]) for r in rows)) if rows else None
    tr, va, ho = bounds(parts["train"]), bounds(parts["validation"]), bounds(parts["holdout"])
    if tr and va and va[0] < tr[1]:
        raise ValueError("Leakage: validation rows precede training rows.")
    if va and ho and ho[0] < va[1]:
        raise ValueError("Leakage: holdout rows precede validation rows.")
    if tr and ho and ho[0] < tr[1]:
        raise ValueError("Leakage: holdout rows precede training rows.")


def dataset_fingerprint(rows: list[dict]) -> str:
    body = json.dumps(sorted(rows, key=lambda r: str(r.get("id"))), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def train_rule_success_model(rows: list[dict]) -> dict:
    """A small, transparent learned model: per-rule success probability with Laplace smoothing,
    fit on TRAIN only, evaluated (Brier score, accuracy) on HOLDOUT. Rows need: id, t, rule_id,
    label (bool). Fewer than MIN_LABELS_TO_TRAIN labels -> refuse to train."""
    labelled = [r for r in rows if isinstance(r.get("label"), bool)]
    if len(labelled) < MIN_LABELS_TO_TRAIN:
        return {"status": "NOT_TRAINED", "reason": f"Need >= {MIN_LABELS_TO_TRAIN} labelled outcomes; "
                f"have {len(labelled)}.", "kind": "RULE_BASED"}
    parts = temporal_split(labelled)
    counts: dict[str, list[int]] = {}
    for r in parts["train"]:
        c = counts.setdefault(r["rule_id"], [0, 0])
        c[0] += int(r["label"]); c[1] += 1
    prior = (sum(c[0] for c in counts.values()) + 1) / (sum(c[1] for c in counts.values()) + 2)

    def predict(rule_id):
        s, n = counts.get(rule_id, [0, 0])
        return (s + prior * 2) / (n + 2)

    def score(rows):
        if not rows:
            return {"n": 0, "brier": None, "accuracy": None}
        brier = sum((predict(r["rule_id"]) - float(r["label"])) ** 2 for r in rows) / len(rows)
        acc = sum((predict(r["rule_id"]) >= 0.5) == r["label"] for r in rows) / len(rows)
        return {"n": len(rows), "brier": round(brier, 4), "accuracy": round(acc, 4)}
    # Baseline: constant prior from train.
    base = (sum((prior - float(r["label"])) ** 2 for r in parts["holdout"]) / len(parts["holdout"])
            if parts["holdout"] else None)
    return {"status": "CANDIDATE", "kind": "TRAINED_ML", "algorithm": "laplace_rule_success_v1",
            "hyperparameters": {"prior_strength": 2},
            "parameters": {k: round(predict(k), 4) for k in sorted(counts)},
            "metrics": {"validation": score(parts["validation"]), "holdout": score(parts["holdout"]),
                        "holdout_baseline_brier": round(base, 4) if base is not None else None},
            "split": {k: len(v) for k, v in parts.items()},
            "dataset_sha256": dataset_fingerprint(labelled)}


def check_promotion(*, trainer: str, approver: str, metrics: dict) -> None:
    if trainer.casefold() == approver.casefold():
        raise ValueError("Model promotion requires an independent approver.")
    h = metrics.get("holdout", {})
    base = metrics.get("holdout_baseline_brier")
    if not h.get("n") or h.get("brier") is None or base is None:
        raise ValueError("No holdout evaluation; cannot promote.")
    if h["brier"] > base:
        raise ValueError("Model does not beat the baseline on holdout; cannot promote.")
