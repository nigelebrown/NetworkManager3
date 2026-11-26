"""Trained breach-risk model (rc18, directive 2.1) — pure functions, no ML library, no network.

Pipeline, shared by training, validation, inference and drift monitoring so they cannot diverge:

  extract(hourly, cutoff, threshold, horizon) -> feature vector computed ONLY from data strictly before cutoff
  label(hourly, cutoff, threshold, horizon)   -> did the observed hourly peak in (cutoff, cutoff+horizon] reach
                                                 the threshold? None when the window was not observed enough
  fit(...)                                    -> L2-regularised logistic regression (batch gradient descent on
                                                 standardised features; deterministic)
  select(...)                                 -> picks the L2 strength on the VALIDATION split; HOLDOUT is only
                                                 used for the final, reported metrics
  metrics(...)                                -> accuracy / precision / recall / F1 at the alert threshold, ROC
                                                 AUC, Brier, Brier skill vs base rate, log loss, calibration bins
  psi(...)                                    -> population stability index per feature (drift)

Features are expressed relative to the rule threshold so a model generalises across thresholds and devices.
A model trained on SYNTHETIC data is never production-validated; see service.validation_status().
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

ALGORITHM = "logreg_breach_v1"
FEATURES = ("last_ratio", "mean6_ratio", "max6_ratio", "std6_ratio", "mean24_ratio", "max24_ratio",
            "slope6_ratio", "near_frac24", "yday_window_max_ratio", "lastweek_window_max_ratio",
            "hour_sin", "hour_cos")
MIN_HOURS_48 = 24          # at least 24 hourly points in the last 48 h, otherwise no prediction
DECISION_THRESHOLD = 0.5


def _utc(t) -> datetime:
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def hourly(points) -> dict[datetime, float]:
    acc: dict[datetime, list[float]] = {}
    for t, v in points:
        if v is None:
            continue
        acc.setdefault(_utc(t).replace(minute=0, second=0, microsecond=0), []).append(float(v))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _window(h: dict, start: datetime, hours: int) -> list[float]:
    return [h[start + timedelta(hours=i)] for i in range(hours) if (start + timedelta(hours=i)) in h]


def extract(h: dict[datetime, float], cutoff: datetime, threshold: float, horizon: int) -> list[float] | None:
    """Feature vector at ``cutoff`` (hour-aligned). Uses only hours < cutoff."""
    cutoff = _utc(cutoff).replace(minute=0, second=0, microsecond=0)
    thr = max(float(threshold), 1e-6)
    last48 = _window(h, cutoff - timedelta(hours=48), 48)
    if len(last48) < MIN_HOURS_48:
        return None
    w6 = _window(h, cutoff - timedelta(hours=6), 6)
    w24 = _window(h, cutoff - timedelta(hours=24), 24)
    if len(w6) < 3 or len(w24) < 12:
        return None
    last = w6[-1]
    m6 = sum(w6) / len(w6)
    sd6 = math.sqrt(sum((x - m6) ** 2 for x in w6) / len(w6))
    xs = list(range(len(w6)))
    mx = sum(xs) / len(xs)
    sxx = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = sum((x - mx) * (y - m6) for x, y in zip(xs, w6)) / sxx
    yday = _window(h, cutoff - timedelta(hours=24), horizon)
    lastweek = _window(h, cutoff - timedelta(days=7), horizon)
    m24 = sum(w24) / len(w24)
    hour = cutoff.hour + cutoff.minute / 60
    return [last / thr, m6 / thr, max(w6) / thr, sd6 / thr, m24 / thr, max(w24) / thr, slope / thr,
            sum(1 for x in w24 if x >= 0.8 * thr) / len(w24),
            (max(yday) / thr) if yday else m24 / thr,           # missing seasonality -> neutral daily mean
            (max(lastweek) / thr) if lastweek else m24 / thr,
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24)]


def label(h: dict[datetime, float], cutoff: datetime, threshold: float, horizon: int) -> bool | None:
    cutoff = _utc(cutoff).replace(minute=0, second=0, microsecond=0)
    future = _window(h, cutoff, horizon)
    if len(future) < max(1, horizon // 2):
        return None                                     # not observed: never a "no breach" label
    return max(future) >= threshold


def samples(points, *, threshold: float, horizon: int, step_hours: int = 6, start: datetime | None = None,
            end: datetime | None = None, entity: str = "") -> list[dict]:
    """Walk-forward labelled samples for one series. ``end`` is the latest cutoff whose window is complete."""
    h = hourly(points)
    if not h:
        return []
    first, last = min(h), max(h)
    t = max(first + timedelta(hours=48), _utc(start) if start else first).replace(minute=0, second=0, microsecond=0)
    stop = min(last - timedelta(hours=horizon) + timedelta(hours=1), _utc(end) if end else last)
    out = []
    while t <= stop:
        x = extract(h, t, threshold, horizon)
        y = label(h, t, threshold, horizon) if x is not None else None
        if x is not None and y is not None:
            out.append({"id": f"{entity}@{t.isoformat()}", "t": t.isoformat(), "x": x, "y": y, "entity": entity})
        t += timedelta(hours=step_hours)
    return out


# ---------------------------------------------------------------------------------------------- model
def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def standardiser(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(rows)
    k = len(rows[0])
    mean = [sum(r[i] for r in rows) / n for i in range(k)]
    std = [math.sqrt(sum((r[i] - mean[i]) ** 2 for r in rows) / n) or 1.0 for i in range(k)]
    return mean, std


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            m[c][c] = 1e-12
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def fit(xs: list[list[float]], ys: list[bool], *, l2: float = 0.01, iterations: int = 25, tol: float = 1e-7) -> dict:
    """L2-regularised logistic regression by Newton/IRLS on standardised features (deterministic). The bias is
    not penalised; it starts at the base-rate log-odds, so a model that learns nothing predicts the base rate."""
    mean, std = standardiser(xs)
    z = [[1.0] + [(r[i] - mean[i]) / std[i] for i in range(len(r))] for r in xs]
    k, n = len(z[0]), len(z)
    pos = sum(ys)
    beta = [math.log((pos + 0.5) / (n - pos + 0.5))] + [0.0] * (k - 1)
    used = 0
    for used in range(1, iterations + 1):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for row, y in zip(z, ys):
            p = _sigmoid(sum(b * x for b, x in zip(beta, row)))
            w = max(p * (1 - p), 1e-9)
            e = p - float(y)
            for i in range(k):
                grad[i] += e * row[i]
                wi = w * row[i]
                hrow = hess[i]
                for j in range(i, k):
                    hrow[j] += wi * row[j]
        for i in range(k):
            for j in range(i):
                hess[i][j] = hess[j][i]
            if i:                                           # penalise weights, not the bias
                grad[i] += l2 * n * beta[i]
                hess[i][i] += l2 * n
        step = _solve(hess, grad)
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < tol:
            break
    return {"algorithm": ALGORITHM, "features": list(FEATURES), "mean": mean, "std": std, "weights": beta[1:],
            "bias": beta[0], "l2": l2, "iterations": used,
            "train_range": [[min(r[i] for r in xs), max(r[i] for r in xs)] for i in range(k - 1)]}


def predict(params: dict, x: list[float]) -> float:
    z = params["bias"] + sum(w * (xi - m) / s for w, xi, m, s in
                             zip(params["weights"], x, params["mean"], params["std"]))
    return _sigmoid(z)


def confidence(params: dict, x: list[float], p: float) -> dict:
    """Confidence = how decisive the probability is, reduced when inputs leave the training range."""
    ood = [params["features"][i] for i, (lo, hi) in enumerate(params["train_range"])
           if x[i] < lo - 0.1 * (hi - lo or 1) or x[i] > hi + 0.1 * (hi - lo or 1)]
    entropy = 0.0 if p in (0.0, 1.0) else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))
    score = max(0.0, (1 - entropy) * (0.5 if ood else 1.0))
    level = "HIGH" if score >= 0.6 and not ood else "MEDIUM" if score >= 0.25 else "LOW"
    return {"score": round(score, 3), "level": level, "out_of_range_features": ood}


# -------------------------------------------------------------------------------------------- metrics
def auc(ps: list[float], ys: list[bool]) -> float | None:
    pos = sum(ys)
    neg = len(ys) - pos
    if not pos or not neg:
        return None
    ranked = sorted(zip(ps, ys), key=lambda t: t[0])
    rank_sum, i = 0.0, 0
    while i < len(ranked):                                    # average ranks for ties
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        rank_sum += avg * sum(1 for k in range(i, j + 1) if ranked[k][1])
        i = j + 1
    return round((rank_sum - pos * (pos + 1) / 2) / (pos * neg), 4)


def metrics(ps: list[float], ys: list[bool], *, alert: float = DECISION_THRESHOLD, base_rate: float | None = None) -> dict:
    n = len(ys)
    if not n:
        return {"n": 0}
    rate = sum(ys) / n if base_rate is None else base_rate
    tp = sum(1 for p, y in zip(ps, ys) if p >= alert and y)
    fp = sum(1 for p, y in zip(ps, ys) if p >= alert and not y)
    fn = sum(1 for p, y in zip(ps, ys) if p < alert and y)
    tn = n - tp - fp - fn
    brier = sum((p - float(y)) ** 2 for p, y in zip(ps, ys)) / n
    ref = sum((rate - float(y)) ** 2 for y in ys) / n
    eps = 1e-12
    logloss = -sum(math.log(max(eps, p)) if y else math.log(max(eps, 1 - p)) for p, y in zip(ps, ys)) / n
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        sel = [(p, y) for p, y in zip(ps, ys) if lo <= p < lo + 0.2 or (lo == 0.8 and p == 1.0)]
        if sel:
            bins.append({"bin": f"{lo:.1f}-{lo + 0.2:.1f}", "n": len(sel),
                         "mean_p": round(sum(p for p, _ in sel) / len(sel), 3),
                         "observed": round(sum(y for _, y in sel) / len(sel), 3)})
    return {"n": n, "positives": sum(ys), "accuracy": round((tp + tn) / n, 4),
            "precision": round(prec, 4) if prec is not None else None,
            "recall": round(rec, 4) if rec is not None else None,
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec and rec else None,
            "auc": auc(ps, ys), "brier": round(brier, 4), "base_rate": round(rate, 4),
            "brier_skill": round(1 - brier / ref, 4) if ref > 0 else None, "log_loss": round(logloss, 4),
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "calibration": bins, "alert_threshold": alert}


def select(train: list[dict], validation: list[dict], *, grid=(0.001, 0.01, 0.1, 1.0)) -> tuple[dict, list[dict]]:
    """Model selection on the validation split only."""
    tried = []
    best = None
    for l2 in grid:
        params = fit([r["x"] for r in train], [r["y"] for r in train], l2=l2)
        m = metrics([predict(params, r["x"]) for r in validation], [r["y"] for r in validation],
                    base_rate=sum(r["y"] for r in train) / len(train))
        tried.append({"l2": l2, "validation_brier": m.get("brier"), "validation_auc": m.get("auc")})
        if best is None or (m.get("brier") is not None and m["brier"] < best[1]):
            best = (params, m["brier"])
    return best[0], tried


# ---------------------------------------------------------------------------------------------- drift
def bin_edges(values: list[float], bins: int = 10) -> list[float]:
    s = sorted(values)
    edges = sorted({s[min(len(s) - 1, int(len(s) * q / bins))] for q in range(1, bins)})
    return edges


def histogram(values: list[float], edges: list[float]) -> list[float]:
    counts = [0] * (len(edges) + 1)
    for v in values:
        i = 0
        while i < len(edges) and v > edges[i]:
            i += 1
        counts[i] += 1
    n = len(values) or 1
    return [c / n for c in counts]


def training_profile(xs: list[list[float]]) -> dict:
    prof = {}
    for i, name in enumerate(FEATURES):
        col = [r[i] for r in xs]
        edges = bin_edges(col)
        prof[name] = {"edges": edges, "expected": histogram(col, edges)}
    return prof


def psi(expected: list[float], actual: list[float]) -> float:
    eps = 1e-4
    return round(sum((a - e) * math.log((a + eps) / (e + eps)) for e, a in zip(expected, actual)), 4)


def drift(profile: dict, xs: list[list[float]], *, watch: float = 0.1, alarm: float = 0.25) -> dict:
    if len(xs) < 30:
        return {"state": "INSUFFICIENT_DATA", "samples": len(xs), "features": {}}
    per = {}
    for i, name in enumerate(FEATURES):
        p = profile.get(name)
        if p:
            per[name] = psi(p["expected"], histogram([r[i] for r in xs], p["edges"]))
    worst = max(per.values()) if per else 0.0
    return {"state": "DRIFT" if worst >= alarm else "WATCH" if worst >= watch else "STABLE", "max_psi": worst,
            "samples": len(xs), "features": per}


# ------------------------------------------------------------------------------------------ synthetic
def synthetic_series(seed: int, *, days: int = 60, end: datetime | None = None, threshold: float = 90.0):
    """Clearly synthetic labelled telemetry for pipeline testing where live data is lacking. Diurnal load +
    weekly pattern + AR(1) noise + occasional ramps that cross the threshold. Never written to telemetry."""
    rng = random.Random(seed)
    end = _utc(end or datetime(2026, 1, 1, tzinfo=timezone.utc)).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    base = rng.uniform(25, 45)
    amp = rng.uniform(8, 20)
    noise, ramp_left, ramp_peak = 0.0, 0, 0.0
    out = []
    t = start
    while t < end:
        noise = 0.7 * noise + rng.gauss(0, 3)
        diurnal = amp * max(0.0, math.sin(math.pi * (t.hour - 7) / 12)) if 7 <= t.hour <= 19 else 0.0
        weekly = -0.4 * amp if t.weekday() >= 5 else 0.0
        if ramp_left == 0 and rng.random() < 0.012:
            ramp_left, ramp_peak = rng.randint(6, 18), rng.uniform(threshold - 8, threshold + 12)
        v = base + diurnal + weekly + noise
        if ramp_left:
            v = max(v, ramp_peak - 3 * ramp_left + rng.gauss(0, 2))
            ramp_left -= 1
        out.append((t, max(0.0, min(100.0, v))))
        t += timedelta(hours=1)
    return out
