"""Predictive health: statistical breach-risk forecast with honest validation (pure functions).

Model ``baseline-trend-v1`` (named and versioned; no ML library, no training data leaves the platform):
  forecast(t) = hour-of-week median baseline(t) + recent deviation · decay^h + bounded trend · h
  P(breach)   = 1 − Φ((threshold − forecast_peak) / σ_residual)  over the prediction window.

It is only *operational* when its own walk-forward backtest on the same fleet shows skill (Brier score better
than the climatological base rate) on enough evaluated windows; otherwise every prediction is labelled
EXPERIMENTAL. Insufficient history yields INSUFFICIENT_DATA — never a "healthy" verdict.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

MODEL_VERSION = "baseline-trend-v1"
MIN_POINTS = 48
VALIDATION_MIN_WINDOWS = 30


def _utc(t) -> datetime:
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def hourly(points: list[tuple[datetime, float]]) -> dict[datetime, float]:
    """Mean per UTC hour (resampling irregular samples)."""
    acc: dict[datetime, list[float]] = {}
    for t, v in points:
        acc.setdefault(_utc(t).replace(minute=0, second=0, microsecond=0), []).append(float(v))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def forecast(points: list[tuple[datetime, float]], *, cutoff: datetime, threshold: float, horizon_hours: int = 24,
             min_days: int = 7) -> dict:
    """Predict breach risk for (cutoff, cutoff + horizon]. Only data strictly before ``cutoff`` is used."""
    cutoff = _utc(cutoff)
    hist = hourly([(t, v) for t, v in points if _utc(t) < cutoff and v is not None])
    if not hist:
        return {"state": "INSUFFICIENT_DATA", "reason": "no history before cutoff", "probability": None}
    span_days = (max(hist) - min(hist)).total_seconds() / 86400
    if len(hist) < MIN_POINTS or span_days < min_days:
        return {"state": "INSUFFICIENT_DATA", "probability": None,
                "reason": f"needs ≥{min_days} days and ≥{MIN_POINTS} hourly points (have {span_days:.1f} d, {len(hist)} h)"}
    how: dict[int, list[float]] = {}
    for t, v in hist.items():
        how.setdefault(t.weekday() * 24 + t.hour, []).append(v)
    overall = statistics.median(hist.values())
    base = {k: statistics.median(v) for k, v in how.items()}

    def baseline(t: datetime) -> float:
        return base.get(t.weekday() * 24 + t.hour, overall)

    resid = [v - baseline(t) for t, v in hist.items()]
    sigma = max(statistics.pstdev(resid) if len(resid) > 1 else 0.0, 0.02 * max(abs(overall), 1.0), 0.5)
    recent = sorted((t for t in hist if t >= cutoff - timedelta(hours=3)))
    deviation = statistics.mean(hist[t] - baseline(t) for t in recent) if recent else 0.0
    last24 = sorted(t for t in hist if t >= cutoff - timedelta(hours=24))
    slope = 0.0
    if len(last24) >= 6:
        xs = [(t - last24[0]).total_seconds() / 3600 for t in last24]
        ys = [hist[t] for t in last24]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
        slope = max(-sigma / 4, min(sigma / 4, slope))       # bounded: trends are not extrapolated freely
    peak, peak_at = -math.inf, None
    for h in range(1, horizon_hours + 1):
        t = cutoff + timedelta(hours=h)
        f = baseline(t) + deviation * (0.85 ** h) + slope * min(h, 12)
        if f > peak:
            peak, peak_at = f, t
    # Peak over many hours: widen σ slightly for the max of correlated hourly values.
    p = 1 - _phi((threshold - peak) / (sigma * 1.15))
    coverage = len(hist) / max(1, span_days * 24)
    conf = "HIGH" if span_days >= 14 and coverage >= 0.8 else "MEDIUM" if coverage >= 0.5 else "LOW"
    return {"state": "AT_RISK" if p >= 0.5 else "NO_RISK_EXPECTED", "probability": round(p, 4),
            "predicted_peak": round(peak, 2), "predicted_peak_at": peak_at.isoformat() if peak_at else None,
            "confidence": conf,
            "features": {"baseline_median": round(overall, 2), "sigma": round(sigma, 3), "recent_deviation": round(deviation, 3),
                         "trend_per_hour": round(slope, 4), "history_days": round(span_days, 1), "hourly_points": len(hist),
                         "coverage": round(coverage, 3)}}


def observed_peak(points: list[tuple[datetime, float]], start: datetime, end: datetime) -> float | None:
    vals = [float(v) for t, v in points if start < _utc(t) <= end and v is not None]
    return max(vals) if vals else None


def backtest(points: list[tuple[datetime, float]], *, threshold: float, horizon_hours: int = 24, min_days: int = 7,
             step_hours: int = 24, now: datetime | None = None) -> list[dict]:
    """Walk-forward evaluation: cutoffs every ``step_hours`` where a full future window was observed."""
    if not points:
        return []
    now = _utc(now) if now else max(_utc(t) for t, _ in points)
    first = min(_utc(t) for t, _ in points)
    out = []
    cutoff = (first + timedelta(days=min_days)).replace(minute=0, second=0, microsecond=0)
    while cutoff + timedelta(hours=horizon_hours) <= now:
        f = forecast(points, cutoff=cutoff, threshold=threshold, horizon_hours=horizon_hours, min_days=min_days)
        peak = observed_peak(points, cutoff, cutoff + timedelta(hours=horizon_hours))
        if f["probability"] is not None and peak is not None:
            out.append({"cutoff": cutoff.isoformat(), "p": f["probability"], "breach": peak >= threshold, "peak": peak})
        cutoff += timedelta(hours=step_hours)
    return out


def evaluate(windows: list[dict], *, alert_probability: float = 0.6) -> dict:
    """Brier skill vs base rate, calibration bins, precision/recall/false alerts at the alert threshold."""
    n = len(windows)
    if n == 0:
        return {"windows": 0, "state": "INSUFFICIENT_HISTORY", "reason": "no evaluable windows"}
    base = sum(w["breach"] for w in windows) / n
    brier = sum((w["p"] - w["breach"]) ** 2 for w in windows) / n
    ref = sum((base - w["breach"]) ** 2 for w in windows) / n
    skill = None if ref == 0 else round(1 - brier / ref, 4)
    tp = sum(1 for w in windows if w["p"] >= alert_probability and w["breach"])
    fp = sum(1 for w in windows if w["p"] >= alert_probability and not w["breach"])
    fn = sum(1 for w in windows if w["p"] < alert_probability and w["breach"])
    tn = n - tp - fp - fn
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        sel = [w for w in windows if lo <= w["p"] < hi or (hi >= 1.0 and w["p"] == 1.0)]
        if sel:
            bins.append({"range": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "mean_predicted": round(sum(w["p"] for w in sel) / len(sel), 3),
                         "observed_rate": round(sum(w["breach"] for w in sel) / len(sel), 3)})
    calib = max((abs(b["mean_predicted"] - b["observed_rate"]) for b in bins if b["n"] >= 5), default=None)
    validated = n >= VALIDATION_MIN_WINDOWS and skill is not None and skill > 0 and (calib is None or calib <= 0.25)
    state = "VALIDATED" if validated else "EXPERIMENTAL" if n >= 5 else "INSUFFICIENT_HISTORY"
    reason = None if validated else (
        f"only {n} evaluated windows (need {VALIDATION_MIN_WINDOWS})" if n < VALIDATION_MIN_WINDOWS else
        "no skill over the base rate" if not skill or skill <= 0 else "poorly calibrated")
    if ref == 0:
        reason = f"outcome never varied in history (base rate {base:.0%}); skill cannot be assessed"
        state = "EXPERIMENTAL" if n >= 5 else "INSUFFICIENT_HISTORY"
    return {"windows": n, "base_rate": round(base, 4), "brier": round(brier, 4), "brier_reference": round(ref, 4),
            "brier_skill": skill, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "false_alert_rate": round(fp / (fp + tn), 3) if fp + tn else None,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "calibration": bins,
            "max_calibration_gap": calib, "alert_probability": alert_probability, "state": state, "reason": reason}


def drift(points: list[tuple[datetime, float]], *, now: datetime, recent_days: int = 3) -> dict:
    """Shift of the recent level vs the preceding reference period, in reference standard deviations."""
    now = _utc(now)
    h = hourly(points)
    rec = [v for t, v in h.items() if t >= now - timedelta(days=recent_days)]
    ref = [v for t, v in h.items() if now - timedelta(days=recent_days + 14) <= t < now - timedelta(days=recent_days)]
    if len(rec) < 12 or len(ref) < 48:
        return {"state": "INSUFFICIENT_DATA"}
    sd = statistics.pstdev(ref) or 1e-9
    z = (statistics.mean(rec) - statistics.mean(ref)) / sd
    return {"state": "DRIFT" if abs(z) >= 2 else "STABLE", "z": round(z, 2), "recent_mean": round(statistics.mean(rec), 2),
            "reference_mean": round(statistics.mean(ref), 2)}
