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


def summarize(device, rows: list[dict], *, now: datetime | None = None) -> dict:
    """Use provenance and freshness; never equate missing data with device down."""
    now = now or datetime.now(timezone.utc)
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
    for metric, (threshold, title, action) in THRESHOLDS.items():
        if metric.startswith("util_") and device.family != "access_point":
            continue
        samples = [r for r in trusted if r["metric_name"] == metric
                   and isinstance(r["value"], (float, int))]
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
            "rule_id": "PHASE4_" + metric.upper() + "_SUSTAINED",
            "metric": metric, "title": title, "risk": "medium",
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
