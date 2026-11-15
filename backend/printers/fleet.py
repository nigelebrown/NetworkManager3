"""Supplies, reliability, health, benchmarking and recommendation rules (PRN-041–060, FR-07, FR-08).

Pure functions over plain dicts so every rule is unit-testable. Each output carries its evidence,
assumptions, sample size and a confidence/sufficiency state. Nothing here writes to a printer, places
an order or claims a trained model.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

RULE_VERSION = "printer-rules-2.0.0"
REPLACEMENT_JUMP_POINTS = 30.0


def _utc(t):
    if t is None:
        return None
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ---- supplies -------------------------------------------------------------------------------------
def detect_replacement(before: float | None, after: float | None) -> dict | None:
    """A level rise ≥30 points suggests a new supply. Clear cases (≤20% → ≥90%) are auto-evidenced;
    everything else needs operator confirmation (partial refills, re-seated cartridges, sensor noise)."""
    if before is None or after is None or after - before < REPLACEMENT_JUMP_POINTS:
        return None
    clear = before <= 20 and after >= 90
    return {"event_type": "REPLACEMENT_CONFIRMED" if clear else "REPLACEMENT_SUSPECTED",
            "status": "CONFIRMED" if clear else "OPEN",
            "confirmation": "AUTO_EVIDENCE" if clear else "OPERATOR_REQUIRED",
            "level_before": before, "level_after": after}


def forecast_with_uncertainty(points: list[tuple[datetime, float]], *, now: datetime | None = None) -> dict:
    """Least-squares days-to-empty with a ~95% interval from the slope standard error.
    Uses only the segment after the most recent rise (refill/replacement)."""
    now = _utc(now) or datetime.now(timezone.utc)
    pts = sorted(((_utc(t), float(v)) for t, v in points if v is not None), key=lambda p: p[0])
    seg: list = []
    for p in pts:
        if seg and p[1] > seg[-1][1] + 5:
            seg = []
        seg.append(p)
    if len(seg) < 4 or (seg[-1][0] - seg[0][0]).total_seconds() < 86400:
        return {"status": "INSUFFICIENT_EVIDENCE", "points": len(seg), "days_to_empty": None,
                "reason": "needs ≥4 device-reported levels spanning ≥24 h since the last replacement"}
    t0 = seg[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in seg]
    ys = [y for _, y in seg]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if not sxx:
        return {"status": "INSUFFICIENT_EVIDENCE", "points": n, "days_to_empty": None, "reason": "no time spread"}
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in resid) / max(1, n - 2) / sxx)
    if slope >= 0:
        return {"status": "NO_DECLINE_OBSERVED", "points": n, "days_to_empty": None}
    x_now = (now - t0).total_seconds() / 86400
    level_now = max(0.0, intercept + slope * x_now)
    days = level_now / -slope
    fast, slow = slope - 1.96 * se, slope + 1.96 * se
    lower = round(level_now / -fast, 1) if fast < 0 else None
    upper = round(level_now / -slow, 1) if slow < 0 else None
    ss_tot = sum((y - my) ** 2 for y in ys) or 1e-9
    r2 = 1 - sum(r * r for r in resid) / ss_tot
    conf = "HIGH" if r2 >= 0.9 and n >= 8 else "MEDIUM" if r2 >= 0.7 else "LOW"
    return {"status": "FORECAST", "kind": "STATISTICAL", "points": n, "slope_percent_per_day": round(slope, 3),
            "r_squared": round(r2, 3), "level_now_estimate": round(level_now, 1), "days_to_empty": round(days, 1),
            "days_to_empty_low": lower, "days_to_empty_high": upper, "confidence": conf,
            "expected_empty_date": (now + timedelta(days=days)).date().isoformat(),
            "caveat": "Statistical trend of device-reported level; workload changes alter the forecast."}


def measured_yields(events: list[dict]) -> list[dict]:
    """Yield between consecutive CONFIRMED replacements of the same supply, from the device meter."""
    by_supply: dict[str, list[dict]] = {}
    for e in events:
        if e.get("status") == "CONFIRMED" and e.get("meter_value") is not None:
            by_supply.setdefault(f"{e['supply_index']}|{e['colour']}", []).append(e)
    out = []
    for key, evs in by_supply.items():
        evs.sort(key=lambda e: _utc(e["observed_at"]))
        for a, b in zip(evs, evs[1:]):
            y = b["meter_value"] - a["meter_value"]
            if y <= 0:
                continue
            cost = a.get("cost_amount")
            out.append({"supply": key, "supply_type": a.get("supply_type"), "colour": a.get("colour"),
                        "installed_at": _utc(a["observed_at"]).isoformat(), "removed_at": _utc(b["observed_at"]).isoformat(),
                        "measured_yield": y, "cost_amount": cost, "currency": a.get("currency", "JMD"),
                        "cost_per_1000": round(float(cost) * 1000 / y, 2) if cost is not None else None,
                        "basis": "device meter delta between confirmed replacements (all impressions, not per-colour)"})
    return out


# ---- reliability ------------------------------------------------------------------------------------
def operating_hours(start: datetime, end: datetime, window: dict | None = None) -> float:
    """Hours inside the operating window (default 24×7). window = {days:[0..6], start:'08:00', end:'17:00'}."""
    start, end = _utc(start), _utc(end)
    if end <= start:
        return 0.0
    if not window:
        return (end - start).total_seconds() / 3600
    days = set(window.get("days", range(7)))
    ws = time.fromisoformat(window.get("start", "00:00"))
    we = time.fromisoformat(window.get("end", "23:59"))
    total = 0.0
    d = start.date()
    while d <= end.date():
        if d.weekday() in days:
            lo = datetime.combine(d, ws, tzinfo=timezone.utc)
            hi = datetime.combine(d, we, tzinfo=timezone.utc)
            ov = (min(hi, end) - max(lo, start)).total_seconds()
            total += max(0.0, ov) / 3600
        d += timedelta(days=1)
    return total


QUALIFYING_CAUSES = {"MECHANICAL"}


def reliability(records: list[dict], *, period_start: datetime, period_end: datetime, impressions: float | None,
                devices: int = 1, window: dict | None = None, qualifying_types: set[str] | None = None) -> dict:
    """MTBF / MTTR / incidents per 100k impressions. WAN, power and user causes are excluded unless the
    record is explicitly categorised MECHANICAL with evidence."""
    hours = operating_hours(period_start, period_end, window) * max(devices, 1)
    q = [r for r in records if r.get("qualifying_failure") and r.get("cause_category") in QUALIFYING_CAUSES
         and (not qualifying_types or (r.get("incident_type") or "") in qualifying_types)
         and period_start <= _utc(r.get("reported_at") or r["observed_at"]) <= period_end]
    excluded = [r for r in records if r.get("cause_category") in ("WAN", "POWER", "USER")]
    repairs = [(_utc(r["restored_at"]) - _utc(r["reported_at"])).total_seconds() / 3600
               for r in q if r.get("reported_at") and r.get("restored_at") and _utc(r["restored_at"]) > _utc(r["reported_at"])]
    out = {"operating_hours": round(hours, 1), "qualifying_failures": len(q), "excluded_non_mechanical": len(excluded),
           "maintenance_events": len(records), "window": window or "24x7",
           "mtbf_hours": None, "mttr_hours": None, "incidents_per_100k_impressions": None, "status": "INSUFFICIENT_EVIDENCE"}
    if hours <= 0:
        return out | {"reason": "no operating time in period"}
    out["mtbf_hours"] = round(hours / len(q), 1) if q else None
    out["mtbf_note"] = None if q else f"no qualifying failure in {round(hours)} operating hours (MTBF > observed time)"
    out["mttr_hours"] = round(statistics.mean(repairs), 2) if repairs else None
    if impressions:
        out["incidents_per_100k_impressions"] = round(len(q) * 100000 / impressions, 3)
    out["status"] = "CALCULATED" if hours >= 24 * 30 else "LIMITED_OBSERVATION"
    return out


# ---- health -----------------------------------------------------------------------------------------
def health_score(*, availability: float | None, snmp_success: float | None, active_faults: list[dict],
                 supplies: list[dict], repeated_faults: int, last_success_at: datetime | None,
                 now: datetime | None = None, stale_after_hours: float = 2.0) -> dict:
    """Evidence-based 0–100 score. Returns None with INSUFFICIENT_DATA when the printer was never read."""
    now = _utc(now) or datetime.now(timezone.utc)
    if last_success_at is None:
        return {"score": None, "status": "INSUFFICIENT_DATA", "components": [],
                "reason": "no successful poll recorded; health is unknown, not healthy"}
    comps = []

    def comp(name, weight, value, evidence):
        comps.append({"component": name, "weight": weight, "score": None if value is None else round(value, 1),
                      "evidence": evidence})

    comp("availability", 30, None if availability is None else availability * 100,
         "reachable share of observed time (7 days)" if availability is not None else "no reachability history")
    comp("collection", 10, None if snmp_success is None else snmp_success * 100, "successful polls / attempted polls")
    crit = [f for f in active_faults if f.get("severity") == "critical"]
    warn = [f for f in active_faults if f.get("severity") == "warning"]
    comp("faults", 30, max(0.0, 100 - 60 * len(crit) - 20 * len(warn)),
         f"{len(crit)} critical, {len(warn)} warning active")
    known = [s for s in supplies if s.get("status") not in (None, "UNKNOWN")]
    bad = [s for s in known if s["status"] in ("EMPTY", "FULL")]
    low = [s for s in known if s["status"] in ("LOW", "NEARLY_FULL")]
    comp("supplies", 15, None if not known else max(0.0, 100 - 50 * len(bad) - 15 * len(low)),
         f"{len(bad)} empty/full, {len(low)} low of {len(known)} known" if known else "no usable supply levels")
    comp("fault_recurrence", 15, max(0.0, 100 - 20 * repeated_faults), f"{repeated_faults} recurring fault codes in 7 days")
    usable = [c for c in comps if c["score"] is not None]
    wsum = sum(c["weight"] for c in usable)
    score = round(sum(c["score"] * c["weight"] for c in usable) / wsum, 1) if wsum else None
    stale = (now - _utc(last_success_at)).total_seconds() > stale_after_hours * 3600
    coverage = round(wsum / 100, 2)
    status = ("STALE" if stale else "INSUFFICIENT_DATA" if coverage < 0.5 or score is None else
              "CRITICAL" if score < 50 else "DEGRADED" if score < 80 else "HEALTHY")
    return {"score": score, "status": status, "evidence_coverage": coverage, "components": comps,
            "stale": stale, "last_success_at": _utc(last_success_at).isoformat()}


# ---- benchmarking ----------------------------------------------------------------------------------
def workload_band(monthly_volume: float | None) -> str:
    if monthly_volume is None:
        return "UNKNOWN"
    return "LOW" if monthly_volume < 1000 else "MEDIUM" if monthly_volume < 5000 else "HIGH"


def _median(vals: Iterable[float | None]):
    v = [x for x in vals if x is not None]
    return round(statistics.median(v), 2) if v else None


def benchmark(devices: list[dict], *, min_devices: int = 3, min_months: int = 3) -> list[dict]:
    """devices: [{model, manufacturer, segment:{colour,multifunction,technology}, monthly_volumes:[...],
    recommended_monthly, yields:[...], cost_per_1000:[...], incidents, mtbf_hours, mttr_hours, impressions,
    maintenance_cost, consumable_cost, cost_complete}] — one row per in-service device."""
    cohorts: dict[tuple, list[dict]] = {}
    for d in devices:
        seg = d.get("segment") or {}
        key = (d["manufacturer"], d["model"], seg.get("colour"), seg.get("multifunction"), seg.get("technology"))
        cohorts.setdefault(key, []).append(d)
    out = []
    for (manu, model, colour, mfp, tech), rows in sorted(cohorts.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        months = [m for r in rows for m in r.get("monthly_volumes", []) if m is not None]
        per_device_avg = [statistics.mean(r["monthly_volumes"]) for r in rows if r.get("monthly_volumes")]
        impressions = sum(r.get("impressions") or 0 for r in rows)
        incidents = sum(r.get("incidents") or 0 for r in rows)
        rec_util = [statistics.mean(r["monthly_volumes"]) * 100 / r["recommended_monthly"] for r in rows
                    if r.get("monthly_volumes") and r.get("recommended_monthly")]
        complete = rows and all(r.get("cost_complete") for r in rows)
        tco = None
        if complete and impressions:
            tco = round(sum((r.get("maintenance_cost") or 0) + (r.get("consumable_cost") or 0) for r in rows)
                        * 1000 / impressions, 2)
        sufficient = len(rows) >= min_devices and len(months) >= min_devices * min_months
        sample = {"devices": len(rows), "device_months": len(months)}
        completeness = round(sum(1 for r in rows if r.get("monthly_volumes")) / len(rows), 2)
        out.append({
            "manufacturer": manu, "model": model,
            "segment": {"colour": colour, "multifunction": mfp, "technology": tech},
            "in_service": len(rows), "sample": sample, "completeness": completeness,
            "total_monthly_volume": round(sum(per_device_avg), 1) if per_device_avg else None,
            "median_monthly_volume_per_device": _median(per_device_avg),
            "median_recommended_utilization_percent": _median(rec_util),
            "median_measured_yield": _median(y for r in rows for y in r.get("yields", [])),
            "median_cost_per_1000": _median(c for r in rows for c in r.get("cost_per_1000", [])),
            "incidents": incidents,
            "incidents_per_100k_impressions": round(incidents * 100000 / impressions, 3) if impressions else None,
            "median_mtbf_hours": _median(r.get("mtbf_hours") for r in rows),
            "median_mttr_hours": _median(r.get("mttr_hours") for r in rows),
            "tco_per_1000": tco, "tco_status": "COMPLETE" if tco is not None else "INCOMPLETE_COST_DATA",
            "workload_band": workload_band(_median(per_device_avg)),
            "evidence": "SUFFICIENT" if sufficient else "INSUFFICIENT_EVIDENCE",
            "confidence": ("HIGH" if sufficient and completeness >= 0.9 and len(rows) >= 2 * min_devices else
                           "MEDIUM" if sufficient else "INSUFFICIENT"),
            "suitability": None if not sufficient else
            f"Evaluated for {workload_band(_median(per_device_avg)).lower()} workloads only",
        })
    return out


# ---- recommendations ---------------------------------------------------------------------------------
def _rec(code, title, *, printer_id=None, model=None, reasons, evidence, assumptions, confidence, period):
    return {"code": code, "title": title, "printer_id": printer_id, "model": model, "reason_codes": reasons,
            "evidence": evidence, "assumptions": assumptions, "confidence": confidence, "rule_version": RULE_VERSION,
            "period_start": period[0], "period_end": period[1],
            "dedupe_key": f"{code}:{printer_id or model}"}


def recommend(printers: list[dict], cohorts: list[dict], *, period: tuple[datetime, datetime]) -> list[dict]:
    """printers: [{id, name, site, model, duty:{status,...}, colour_share, colour_capability, forecasts:[...],
    recurring_faults:{code:count}}]. Advisory only — every recommendation requires human review."""
    recs: list[dict] = []
    by_site: dict[Any, list[dict]] = {}
    for p in printers:
        by_site.setdefault(p.get("site") or p.get("location") or "UNASSIGNED", []).append(p)
        duty = p.get("duty") or {}
        if duty.get("status") == "SUSTAINED_HIGH":
            recs.append(_rec("UNDERSIZED", f"{p['name']} is undersized for its measured workload",
                             printer_id=p["id"], reasons=["SUSTAINED_HIGH_UTILIZATION", "ABOVE_RECOMMENDED_VOLUME"],
                             evidence={"recent_months": duty.get("recent"), "mean_recommended_percent": duty.get("mean_recommended_percent")},
                             assumptions=["Manufacturer recommended monthly volume is verified for this model",
                                          "Monthly volumes are validated counter deltas (≥60% coverage)"],
                             confidence="HIGH" if duty.get("months_evaluated", 0) >= 6 else "MEDIUM", period=period))
        share = p.get("colour_share")
        if share is not None and p.get("colour_capability") == "SUPPORTED" and share < 0.05 and p.get("colour_months", 0) >= 3:
            recs.append(_rec("MONO_REPLACEMENT", f"{p['name']} prints almost only mono ({share * 100:.1f}% colour)",
                             printer_id=p["id"], reasons=["LOW_COLOUR_SHARE"],
                             evidence={"colour_share": share, "months": p.get("colour_months")},
                             assumptions=["Mono and colour counters are SUPPORTED (standard or verified vendor OIDs)"],
                             confidence="MEDIUM", period=period))
        for f in p.get("forecasts", []):
            if f.get("status") == "FORECAST" and f.get("days_to_empty") is not None and f["days_to_empty"] <= 14:
                recs.append(_rec("SUPPLY_REPLENISH", f"{p['name']}: {f['supply']} expected empty in ~{f['days_to_empty']} days",
                                 printer_id=p["id"], reasons=["FORECAST_EMPTY_WITHIN_14_DAYS"],
                                 evidence=f, assumptions=["Recent workload continues"],
                                 confidence=f.get("confidence", "LOW"), period=period) | {"dedupe_key": f"SUPPLY_REPLENISH:{p['id']}:{f['supply']}"})
        for code, count in (p.get("recurring_faults") or {}).items():
            if count >= 3:
                recs.append(_rec("RECURRING_FAULT", f"{p['name']}: {code.replace('_', ' ').lower()} occurred {count}× in 30 days",
                                 printer_id=p["id"], reasons=["REPEATED_FAULT"], evidence={"fault_code": code, "count_30d": count},
                                 assumptions=["Each occurrence is a distinct fault period from device status"],
                                 confidence="HIGH", period=period) | {"dedupe_key": f"RECURRING_FAULT:{p['id']}:{code}"})
    for site, members in by_site.items():
        low = [p for p in members if (p.get("duty") or {}).get("status") == "SUSTAINED_LOW"]
        high = [p for p in members if (p.get("duty") or {}).get("status") == "SUSTAINED_HIGH"]
        if len(low) >= 2:
            recs.append(_rec("CONSOLIDATION", f"Consolidate {len(low)} under-used printers at {site}",
                             reasons=["MULTIPLE_SUSTAINED_LOW_SAME_LOCATION"],
                             evidence={"printers": [p["id"] for p in low], "site": str(site)},
                             assumptions=["Printers at the same location serve overlapping users",
                                          "Network/physical access allows consolidation"],
                             confidence="MEDIUM", period=period) | {"dedupe_key": f"CONSOLIDATION:{site}"})
        if low and high:
            recs.append(_rec("RELOCATION", f"Swap a low-use and a high-use printer at {site}",
                             reasons=["LOW_AND_HIGH_UTILIZATION_SAME_LOCATION"],
                             evidence={"low": [p["id"] for p in low], "high": [p["id"] for p in high]},
                             assumptions=["Device capabilities (colour/MFP) satisfy both workloads"],
                             confidence="LOW", period=period) | {"dedupe_key": f"RELOCATION:{site}"})
    segments: dict[tuple, list[dict]] = {}
    for c in cohorts:
        if c["evidence"] == "SUFFICIENT":
            s = c["segment"]
            segments.setdefault((s.get("colour"), s.get("multifunction"), s.get("technology"), c["workload_band"]), []).append(c)
    for c in cohorts:
        model = f"{c['manufacturer']} {c['model']}"
        if c["in_service"] == 1:
            recs.append(_rec("STANDARDIZATION_RISK", f"{model} is a single-unit model in the fleet", model=model,
                             reasons=["ORPHAN_MODEL"], evidence={"in_service": 1},
                             assumptions=["Single-unit models carry spare/supply/skills overhead"],
                             confidence="MEDIUM", period=period))
        if c["evidence"] != "SUFFICIENT":
            continue
        s = c["segment"]
        peers = segments.get((s.get("colour"), s.get("multifunction"), s.get("technology"), c["workload_band"]), [])
        if len(peers) < 2:
            continue
        med_cost = _median(p["median_cost_per_1000"] for p in peers)
        med_inc = _median(p["incidents_per_100k_impressions"] for p in peers)
        cost, inc = c["median_cost_per_1000"], c["incidents_per_100k_impressions"]
        if cost is not None and med_cost and cost > 1.25 * med_cost:
            recs.append(_rec("SUPPLY_OR_MODEL_ALTERNATIVE", f"{model} costs {cost} per 1,000 vs segment median {med_cost}",
                             model=model, reasons=["COST_ABOVE_SEGMENT_MEDIAN"],
                             evidence={"cost_per_1000": cost, "segment_median": med_cost, "peers": len(peers)},
                             assumptions=["Verified purchase costs", "Comparable workload band"],
                             confidence=c["confidence"], period=period))
        if cost is not None and med_cost and cost <= med_cost and (inc is None or med_inc is None or inc <= med_inc):
            recs.append(_rec("STANDARDIZATION_CANDIDATE", f"{model} is a low-cost, reliable standard candidate",
                             model=model, reasons=["COST_AT_OR_BELOW_MEDIAN", "INCIDENTS_AT_OR_BELOW_MEDIAN"],
                             evidence={"cost_per_1000": cost, "segment_median_cost": med_cost,
                                       "incidents_per_100k": inc, "segment_median_incidents": med_inc},
                             assumptions=["Evidence sufficient for this segment and workload band only"],
                             confidence=c["confidence"], period=period))
    return recs
