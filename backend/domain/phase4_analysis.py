"""Phase 4 recorded-telemetry quality and RULE-only recommendations.

Never polls devices, predicts failure, claims ML, or executes configuration.
A finding needs TWO recent independently collected REST observations. Manually
typed metrics can be displayed but are never used as automation evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

THRESHOLDS = {
    "cpu_percent": (90.0, "Sustained high CPU", "Check device CPU load and traffic."),
    "memory_percent": (92.0, "Sustained high memory", "Inspect memory utilisation."),
    "util_5g_percent": (85.0, "Sustained 5 GHz utilization",
                        "Review channel occupancy and client demand; do not auto-change RF."),
    "util_2g_percent": (85.0, "Sustained 2.4 GHz utilization",
                         "Inspect channel occupancy and interfering traffic."),
}


def time_of(row: dict) -> datetime:
    ts = row.get("observed_at")
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if not isinstance(ts, datetime):
        raise ValueError("Telemetry timestamp is missing.")
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


RULE_FOR_METRIC = {m: "PHASE4_" + m.upper() + "_SUSTAINED" for m in THRESHOLDS}


def summarize(device, rows: list[dict], *, now: datetime | None = None, rules: dict | None = None,
              plan=None) -> dict:
    """Use provenance and freshness; never equate missing data with device down.

    ``rules`` is the effective rule catalogue (rule_id -> {enabled, params, version}); built-in thresholds are
    used when it is absent. ``plan`` is the device's collection plan (for the collection-failing rule).
    Precedence: NOT_MONITORED > TELEMETRY_STALE / COLLECTION_FAILING > metric thresholds."""
    now = now or datetime.now(timezone.utc)
    rules = rules or {}

    def rule(rid, default_params):
        r = rules.get(rid)
        if r is None:
            return True, default_params, "1.0"
        return r["enabled"], {**default_params, **r["params"]}, str(r["version"])
    valid = [r for r in rows if r.get("source_type") == "REAL"
             and r.get("value") is not None and r.get("metric_name")]
    valid.sort(key=time_of, reverse=True)
    trusted = [r for r in valid if r.get("collection_method") == "ARUBA_REST_READ"
               and r.get("quality") == "OBSERVED" and
               timedelta(0) <= now - time_of(r) <= timedelta(minutes=30)]
    recent_any = valid[0] if valid else None
    recent_trusted = trusted[0] if trusted else None
    status = ("NO_TELEMETRY" if not valid
              else "REST_VERIFIED_RECENT" if recent_trusted
              else "MANUAL_OR_STALE")
    metrics = {}
    for row in valid:
        metrics.setdefault(row["metric_name"], {
            "value": row["value"], "observed_at": time_of(row).isoformat(),
            "collection_method": row["collection_method"],
        })
    findings = []
    on, p, ver = rule("PHASE4_NOT_MONITORED", {"lookback_hours": 24})
    lookback = now - timedelta(hours=p["lookback_hours"])
    recent_valid = [r for r in valid if time_of(r) >= lookback]
    if on and not recent_valid:
        findings.append({"rule_id": "PHASE4_NOT_MONITORED", "rule_version": ver, "metric": None,
                         "title": "Device not monitored", "risk": "low",
                         "explanation": f"No REAL telemetry in the last {p['lookback_hours']} h. Health is unknown, "
                                        "not healthy. Create or repair the collection plan.",
                         "threshold": p["lookback_hours"], "samples": [],
                         "collection_method": None, "last_observation": time_of(recent_any).isoformat() if recent_any else None})
    blocking = bool(findings)
    if not blocking:
        on, p, ver = rule("PHASE4_TELEMETRY_STALE", {"stale_minutes": 30})
        newest = max((time_of(r) for r in valid if r.get("collection_method") == "ARUBA_REST_READ"), default=None)
        if on and newest is not None and now - newest > timedelta(minutes=p["stale_minutes"]):
            findings.append({"rule_id": "PHASE4_TELEMETRY_STALE", "rule_version": ver, "metric": None,
                             "title": "Telemetry stale", "risk": "medium",
                             "explanation": f"Newest REST observation is {int((now - newest).total_seconds() // 60)} min old "
                                            f"(limit {p['stale_minutes']} min). Threshold rules are not evaluated on stale data.",
                             "threshold": p["stale_minutes"], "samples": [], "collection_method": "ARUBA_REST_READ",
                             "last_observation": newest.isoformat()})
        on, p, ver = rule("PHASE4_COLLECTION_FAILING", {"consecutive_failures": 3})
        fails = getattr(plan, "consecutive_failures", 0) or 0
        if on and plan is not None and fails >= p["consecutive_failures"]:
            findings.append({"rule_id": "PHASE4_COLLECTION_FAILING", "rule_version": ver, "metric": None,
                             "title": "Collection failing", "risk": "medium",
                             "explanation": f"{fails} consecutive collection failures "
                                            f"({getattr(plan, 'last_failure_category', None) or 'unknown cause'}).",
                             "threshold": p["consecutive_failures"], "samples": [], "collection_method": None,
                             "last_observation": None})
    for metric, (threshold, title, action) in ({} if blocking else THRESHOLDS).items():
        if metric.startswith("util_") and device.family != "access_point":
            continue
        on, p, ver = rule(RULE_FOR_METRIC[metric], {"threshold": threshold, "window_minutes": 30})
        if not on:
            continue
        threshold = p["threshold"]
        window = timedelta(minutes=p["window_minutes"])
        samples = [r for r in valid if r["metric_name"] == metric and isinstance(r["value"], (float, int))
                   and r.get("collection_method") == "ARUBA_REST_READ" and r.get("quality") == "OBSERVED"
                   and timedelta(0) <= now - time_of(r) <= window]
        if len(samples) < 2:
            continue
        # Separate samples must come from different event identities.
        selected = []
        events = set()
        for r in samples:
            if r["source_event_id"] in events:
                continue
            events.add(r["source_event_id"])
            selected.append(r)
            if len(selected) == 2:
                break
        if len(selected) != 2 or any(r["value"] < threshold for r in selected):
            continue
        findings.append({
            "rule_id": RULE_FOR_METRIC[metric], "rule_version": ver,
            "metric": metric, "title": title, "risk": "medium", "last_observation": time_of(selected[0]).isoformat(),
            "explanation": action + " Requires human review; not ML or automatic remediation.",
            "threshold": threshold,
            "samples": [{"value": r["value"],
                         "observed_at": time_of(r).isoformat(),
                         "source_event_id": r["source_event_id"]}
                        for r in selected],
            "collection_method": "ARUBA_REST_READ",
        })
    return {
        "device_id": device.id, "hostname": device.hostname,
        "family": device.family, "model": device.model,
        "source_type": "REAL", "evidence_status": status,
        "latest_observation": time_of(recent_any).isoformat() if recent_any else None,
        "trusted_observation": time_of(recent_trusted).isoformat() if recent_trusted else None,
        "metrics": metrics, "findings": findings,
        "device_writes": "LOCKED",
        "prediction_model": None,
    }
