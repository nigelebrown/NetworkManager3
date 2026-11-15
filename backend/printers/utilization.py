"""Counter intervals and utilization analytics (PRN-031–040). Pure functions, no I/O.

Rules:
* A delta exists only between two cumulative readings of the SAME meter on the SAME physical device.
* Decreases are classified (ROLLOVER with a computable delta, otherwise RESET); a serial change is a
  REPLACEMENT; rates above a plausible engine maximum are IMPLAUSIBLE. None of those invent volume.
* Sampled counts are attributed to polling intervals proportionally by time. Exact job timestamps are
  only available from job accounting, which this module does not claim.
* Measured zero (covered time, delta 0) is distinct from missing (no covered time → ``None``).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..domain.printer_intelligence import classify_counter

DEFAULT_MAX_PAGES_PER_MINUTE = 150      # above any office engine; rates beyond this are IMPLAUSIBLE
DEFAULT_GAP_HOURS = 26                  # interval longer than this: total known, timing uncertain
GRANULARITIES = ("hour", "day", "week", "month", "year")
KNOWN_DELTA = ("VALID", "ROLLOVER", "GAP")


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def derive_interval(prev_value: int, prev_at: datetime, cur_value: int, cur_at: datetime, *,
                    serial_changed: bool = False, max_ppm: float = DEFAULT_MAX_PAGES_PER_MINUTE,
                    gap_hours: float = DEFAULT_GAP_HOURS, width_bits: int = 32) -> dict:
    prev_at, cur_at = _utc(prev_at), _utc(cur_at)
    seconds = (cur_at - prev_at).total_seconds()
    base = {"start_at": prev_at, "end_at": cur_at, "start_value": prev_value, "end_value": cur_value}
    if seconds <= 0:
        return base | {"delta": None, "quality": "INVALID", "reason": "non-increasing timestamps"}
    if serial_changed:
        return base | {"delta": None, "quality": "REPLACEMENT",
                       "reason": "device serial changed between readings; counters are not comparable"}
    c = classify_counter(prev_value, cur_value, width_bits=width_bits)
    if c["kind"] == "RESET_OR_REPLACEMENT":
        return base | {"delta": None, "quality": "RESET", "reason": "counter decreased without a rollover signature"}
    delta = c["delta"]
    quality = "ROLLOVER" if c["kind"] == "ROLLOVER" else "VALID"
    if delta / (seconds / 60.0) > max_ppm:
        return base | {"delta": None, "quality": "IMPLAUSIBLE",
                       "reason": f"{delta} in {seconds / 60:.1f} min exceeds {max_ppm} pages/min"}
    if seconds > gap_hours * 3600 and quality == "VALID":
        return base | {"delta": delta, "quality": "GAP",
                       "reason": f"{seconds / 3600:.1f} h between readings; total known, timing spread evenly"}
    return base | {"delta": delta, "quality": quality,
                   "reason": "32-bit counter wrap" if quality == "ROLLOVER" else None}


def derive_intervals(readings: Iterable[dict], **kw) -> list[dict]:
    """readings: [{value, observed_at, serial?}] for ONE meter kind, any order."""
    pts = sorted(readings, key=lambda r: _utc(r["observed_at"]))
    out = []
    for a, b in zip(pts, pts[1:]):
        out.append(derive_interval(a["value"], a["observed_at"], b["value"], b["observed_at"],
                                   serial_changed=bool(a.get("serial") and b.get("serial") and a["serial"] != b["serial"]),
                                   **kw))
    return out


# ---- buckets ----------------------------------------------------------------------------------------
def bucket_start(t: datetime, granularity: str) -> datetime:
    t = _utc(t)
    if granularity == "hour":
        return t.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "week":
        d = t.replace(hour=0, minute=0, second=0, microsecond=0)
        return d - timedelta(days=d.weekday())
    if granularity == "month":
        return t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "year":
        return t.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"granularity must be one of {GRANULARITIES}")


def next_bucket(t: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return t + timedelta(hours=1)
    if granularity == "day":
        return t + timedelta(days=1)
    if granularity == "week":
        return t + timedelta(days=7)
    if granularity == "month":
        return t.replace(year=t.year + (t.month == 12), month=1 if t.month == 12 else t.month + 1)
    if granularity == "year":
        return t.replace(year=t.year + 1)
    raise ValueError(granularity)


def aggregate(intervals: list[dict], start: datetime, end: datetime, granularity: str) -> list[dict]:
    """Attribute interval deltas to calendar buckets by time overlap. Returns one row per bucket."""
    start, end = _utc(start), _utc(end)
    buckets = []
    b = bucket_start(start, granularity)
    while b < end:
        nb = next_bucket(b, granularity)
        buckets.append({"bucket_start": b, "bucket_end": nb, "_vol": 0.0, "_cov": 0.0, "_gap": False, "_bad": 0})
        b = nb
    for iv in intervals:
        s, e = _utc(iv["start_at"]), _utc(iv["end_at"])
        dur = (e - s).total_seconds()
        if dur <= 0:
            continue
        for bk in buckets:
            lo, hi = max(s, bk["bucket_start"], start), min(e, bk["bucket_end"], end)
            ov = (hi - lo).total_seconds()
            if ov <= 0:
                continue
            if iv["quality"] in KNOWN_DELTA and iv.get("delta") is not None:
                bk["_vol"] += iv["delta"] * ov / dur
                bk["_cov"] += ov
                bk["_gap"] |= iv["quality"] == "GAP"
            else:
                bk["_bad"] += 1
    out = []
    for bk in buckets:
        span = (min(bk["bucket_end"], end) - max(bk["bucket_start"], start)).total_seconds() or 1
        full = (bk["bucket_end"] - bk["bucket_start"]).total_seconds()
        cov = min(1.0, bk["_cov"] / span)
        full_cov = min(1.0, bk["_cov"] / full)
        if cov == 0:
            vol, conf = None, "INSUFFICIENT"
        else:
            vol = round(bk["_vol"], 2)
            conf = ("HIGH" if cov >= 0.9 and not bk["_gap"] else "MEDIUM" if cov >= 0.6 else "LOW")
        out.append({"bucket_start": bk["bucket_start"].isoformat(), "bucket_end": bk["bucket_end"].isoformat(),
                    "volume": vol, "coverage": round(cov, 3), "full_bucket_coverage": round(full_cov, 3),
                    "measured_zero": vol == 0.0,
                    # Volume scaled to the whole calendar bucket from the observed rate (labelled estimate).
                    "estimated_full_volume": round(vol / full_cov, 1) if vol is not None and full_cov > 0 else None,
                    "confidence": conf, "includes_gap_spread": bk["_gap"], "excluded_intervals": bk["_bad"]})
    return out


def period_summary(buckets: list[dict]) -> dict:
    covered = [b for b in buckets if b["volume"] is not None]
    total = round(sum(b["volume"] for b in covered), 2) if covered else None
    cov = round(sum(b["coverage"] for b in buckets) / len(buckets), 3) if buckets else 0.0
    return {"total": total, "buckets": len(buckets), "covered_buckets": len(covered),
            "missing_buckets": len(buckets) - len(covered), "mean_coverage": cov,
            "average_per_covered_bucket": round(total / len(covered), 2) if covered else None,
            "confidence": "INSUFFICIENT" if not covered else "HIGH" if cov >= 0.9 else "MEDIUM" if cov >= 0.6 else "LOW"}


# ---- workload analytics -----------------------------------------------------------------------------
def robust_anomalies(daily: list[dict], z_threshold: float = 3.5) -> list[dict]:
    """Median/MAD robust z-score over covered days. Needs ≥14 covered days; otherwise nothing is claimed."""
    # Only complete days: a partially observed (or still running) day is not a dip.
    pts = [(d["bucket_start"], d["volume"]) for d in daily
           if d["volume"] is not None and d.get("full_bucket_coverage", d["coverage"]) >= 0.9]
    if len(pts) < 14:
        return []
    vols = [v for _, v in pts]
    med = statistics.median(vols)
    # MAD-based scale; fall back to mean absolute deviation, then to 5% of the median, so a very steady
    # workload cannot produce an unbounded score from a near-zero spread.
    scale = statistics.median([abs(v - med) for v in vols]) / 0.6745
    if scale <= 0:
        scale = 1.253314 * statistics.mean([abs(v - med) for v in vols])
    scale = max(scale, 0.05 * abs(med), 1.0)
    out = []
    for day, v in pts:
        z = (v - med) / scale
        if abs(z) >= z_threshold:
            out.append({"day": day, "volume": v, "median": med, "robust_z": round(z, 2),
                        "kind": "PEAK" if z > 0 else "DIP"})
    return out


def duty_cycle_assessment(monthly: list[dict], recommended_monthly: int | None, max_duty: int | None, *,
                          low_fraction: float = 0.2, high_fraction: float = 1.0, sustain_months: int = 3) -> dict:
    """Compare validated monthly volumes with VERIFIED manufacturer figures. Never invents figures."""
    usable = [m for m in monthly if m["volume"] is not None and m.get("full_bucket_coverage", m["coverage"]) >= 0.6]
    if not recommended_monthly:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": "no verified recommended monthly volume for this model",
                "months_evaluated": len(usable)}
    if len(usable) < sustain_months:
        return {"status": "INSUFFICIENT_EVIDENCE", "reason": f"needs ≥{sustain_months} months with ≥60% coverage",
                "months_evaluated": len(usable)}
    util = [{"month": m["bucket_start"][:7], "volume": m["estimated_full_volume"],
             "recommended_percent": round(100 * m["estimated_full_volume"] / recommended_monthly, 1),
             "max_duty_percent": round(100 * m["estimated_full_volume"] / max_duty, 1) if max_duty else None,
             "coverage": m["coverage"]} for m in usable]
    recent = util[-sustain_months:]
    low = all(u["recommended_percent"] < low_fraction * 100 for u in recent)
    high = all(u["recommended_percent"] > high_fraction * 100 for u in recent)
    over_max = any((u["max_duty_percent"] or 0) > 100 for u in recent)
    status = "SUSTAINED_HIGH" if high or over_max else "SUSTAINED_LOW" if low else "WITHIN_RANGE"
    return {"status": status, "months_evaluated": len(usable), "recent": recent, "monthly": util,
            "thresholds": {"low_below_percent": low_fraction * 100, "high_above_percent": high_fraction * 100,
                           "sustain_months": sustain_months},
            "mean_recommended_percent": round(sum(u["recommended_percent"] for u in recent) / len(recent), 1)}
