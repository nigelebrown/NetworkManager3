"""AI rule catalogue: every deterministic rule the platform evaluates, with inputs, condition, precedence,
output, suppression and versioned parameters.

Changing a rule is a two-person action: an operator/admin proposes new parameters, a *different* approver
or admin approves them, and a new version row is written. Engines read the effective parameters at run
time, so findings always link back to the exact rule version that produced them.

Pure definitions and validation only; persistence lives in ``backend.infrastructure.rule_store``.
"""
from __future__ import annotations

# Parameter schema: name -> (type, min, max). Values outside the range are rejected at proposal time.
BUILTIN: list[dict] = [
    # ---- site rules (isolated AI service, pseudonymised site aggregates) ----
    {"rule_id": "ap_offline", "engine": "SITE_RULES", "title": "Access points offline",
     "description": "Offline APs remove coverage and push clients onto neighbours.",
     "inputs": ["aps_down", "aps_total"], "condition": "aps_down >= min_down", "risk": "high", "precedence": 10,
     "output": "Site advisory: check PoE/uplink and last-seen before dispatch", "params": {"min_down": 1},
     "schema": {"min_down": ("int", 1, 1000)}, "suppression": 60},
    {"rule_id": "high_channel_utilization", "engine": "SITE_RULES", "title": "High channel utilisation",
     "description": "Sustained utilisation degrades experience. RF changes stay at 20/40 MHz.",
     "inputs": ["max_util_percent"], "condition": "max_util_percent >= threshold_percent", "risk": "medium",
     "precedence": 30, "output": "Site advisory: review channel plan and client density",
     "params": {"threshold_percent": 70}, "schema": {"threshold_percent": ("float", 30, 100)}, "suppression": 60},
    {"rule_id": "poor_experience", "engine": "SITE_RULES", "title": "Poor site experience",
     "description": "Mean client experience score is in the poor band.", "inputs": ["exp_score"],
     "condition": "exp_score < poor_below", "risk": "medium", "precedence": 40,
     "output": "Site advisory: open Experience to see the measured drivers", "params": {"poor_below": 60},
     "schema": {"poor_below": ("float", 1, 100)}, "suppression": 60},
    {"rule_id": "many_poor_clients", "engine": "SITE_RULES", "title": "Many clients in the poor band",
     "description": "A large share of connected clients score poor.", "inputs": ["clients_poor", "clients_total"],
     "condition": "clients_poor / clients_total >= fraction", "risk": "low", "precedence": 50,
     "output": "Site advisory: correlate with AP placement and band steering", "params": {"fraction": 0.25},
     "schema": {"fraction": ("float", 0.01, 1.0)}, "suppression": 60},
    {"rule_id": "critical_alerts_open", "engine": "SITE_RULES", "title": "Critical alerts unacknowledged",
     "description": "Critical alerts are open and not explained by offline APs.", "inputs": ["alerts_critical", "aps_down"],
     "condition": "alerts_critical >= min_alerts AND aps_down == 0 (ap_offline takes precedence)", "risk": "medium",
     "precedence": 20, "output": "Site advisory: acknowledge or resolve open critical alerts",
     "params": {"min_alerts": 1}, "schema": {"min_alerts": ("int", 1, 1000)}, "suppression": 60},
    # ---- device insights (recorded telemetry) ----
    {"rule_id": "PHASE4_NOT_MONITORED", "engine": "DEVICE_INSIGHTS", "title": "Device not monitored",
     "description": "No REAL telemetry recorded in the lookback window; no health can be inferred.",
     "inputs": ["telemetry rows (any metric)"], "condition": "no REAL telemetry within lookback_hours",
     "risk": "low", "precedence": 1, "output": "Device finding: create or repair a collection plan",
     "params": {"lookback_hours": 24}, "schema": {"lookback_hours": ("int", 1, 720)}, "suppression": 720},
    {"rule_id": "PHASE4_TELEMETRY_STALE", "engine": "DEVICE_INSIGHTS", "title": "Telemetry stale",
     "description": "Telemetry exists but the newest trusted REST observation is older than the freshness limit.",
     "inputs": ["latest trusted observation time"], "condition": "now - latest_trusted > stale_minutes",
     "risk": "medium", "precedence": 2, "output": "Device finding: check collection runs and reachability",
     "params": {"stale_minutes": 30}, "schema": {"stale_minutes": ("int", 5, 1440)}, "suppression": 120},
    {"rule_id": "PHASE4_COLLECTION_FAILING", "engine": "DEVICE_INSIGHTS", "title": "Collection failing",
     "description": "Scheduled read-only collection keeps failing for this device.", "inputs": ["collection_runs.state"],
     "condition": "last N runs all FAILED (N = consecutive_failures)", "risk": "medium", "precedence": 3,
     "output": "Device finding: review credentials / reachability", "params": {"consecutive_failures": 3},
     "schema": {"consecutive_failures": ("int", 1, 50)}, "suppression": 120},
    {"rule_id": "PHASE4_CPU_PERCENT_SUSTAINED", "engine": "DEVICE_INSIGHTS", "title": "Sustained high CPU",
     "description": "Two independent recent REST observations at or above the threshold.", "inputs": ["cpu_percent"],
     "condition": "2 distinct-event samples within window_minutes >= threshold", "risk": "medium", "precedence": 10,
     "output": "Device finding: check CPU load and traffic", "params": {"threshold": 90.0, "window_minutes": 30},
     "schema": {"threshold": ("float", 10, 100), "window_minutes": ("int", 5, 240)}, "suppression": 30},
    {"rule_id": "PHASE4_MEMORY_PERCENT_SUSTAINED", "engine": "DEVICE_INSIGHTS", "title": "Sustained high memory",
     "description": "Two independent recent REST observations at or above the threshold.", "inputs": ["memory_percent"],
     "condition": "2 distinct-event samples within window_minutes >= threshold", "risk": "medium", "precedence": 11,
     "output": "Device finding: inspect memory utilisation", "params": {"threshold": 92.0, "window_minutes": 30},
     "schema": {"threshold": ("float", 10, 100), "window_minutes": ("int", 5, 240)}, "suppression": 30},
    {"rule_id": "PHASE4_UTIL_5G_PERCENT_SUSTAINED", "engine": "DEVICE_INSIGHTS", "title": "Sustained 5 GHz utilisation",
     "description": "Two independent recent REST observations at or above the threshold (APs only).",
     "inputs": ["util_5g_percent"], "condition": "2 distinct-event samples within window_minutes >= threshold",
     "risk": "medium", "precedence": 12, "output": "Device finding: review channel occupancy; no automatic RF change",
     "params": {"threshold": 85.0, "window_minutes": 30},
     "schema": {"threshold": ("float", 10, 100), "window_minutes": ("int", 5, 240)}, "suppression": 30},
    {"rule_id": "PHASE4_UTIL_2G_PERCENT_SUSTAINED", "engine": "DEVICE_INSIGHTS", "title": "Sustained 2.4 GHz utilisation",
     "description": "Two independent recent REST observations at or above the threshold (APs only).",
     "inputs": ["util_2g_percent"], "condition": "2 distinct-event samples within window_minutes >= threshold",
     "risk": "medium", "precedence": 13, "output": "Device finding: inspect channel occupancy",
     "params": {"threshold": 85.0, "window_minutes": 30},
     "schema": {"threshold": ("float", 10, 100), "window_minutes": ("int", 5, 240)}, "suppression": 30},
    # ---- predictive (statistical baseline; validated by backtest before it is called operational) ----
    {"rule_id": "PRED_CPU_BREACH", "engine": "PREDICTIVE", "title": "CPU breach risk (next window)",
     "description": "Hour-of-week baseline + recent deviation + trend; probability of exceeding the threshold.",
     "inputs": ["cpu_percent history ≥ min_days"], "condition": "P(peak >= threshold within horizon) >= alert_probability",
     "risk": "medium", "precedence": 100, "output": "Prediction with probability, confidence and validation state",
     "params": {"threshold": 90.0, "horizon_hours": 24, "min_days": 7, "alert_probability": 0.6},
     "schema": {"threshold": ("float", 10, 100), "horizon_hours": ("int", 1, 168), "min_days": ("int", 3, 60),
                "alert_probability": ("float", 0.05, 0.99)}, "suppression": 360},
    {"rule_id": "PRED_MEMORY_BREACH", "engine": "PREDICTIVE", "title": "Memory breach risk (next window)",
     "description": "Hour-of-week baseline + recent deviation + trend.", "inputs": ["memory_percent history ≥ min_days"],
     "condition": "P(peak >= threshold within horizon) >= alert_probability", "risk": "medium", "precedence": 101,
     "output": "Prediction with probability, confidence and validation state",
     "params": {"threshold": 92.0, "horizon_hours": 24, "min_days": 7, "alert_probability": 0.6},
     "schema": {"threshold": ("float", 10, 100), "horizon_hours": ("int", 1, 168), "min_days": ("int", 3, 60),
                "alert_probability": ("float", 0.05, 0.99)}, "suppression": 360},
    {"rule_id": "PRED_UTIL_5G_BREACH", "engine": "PREDICTIVE", "title": "5 GHz congestion risk (next window)",
     "description": "Hour-of-week baseline + recent deviation + trend.", "inputs": ["util_5g_percent history ≥ min_days"],
     "condition": "P(peak >= threshold within horizon) >= alert_probability", "risk": "low", "precedence": 102,
     "output": "Prediction with probability, confidence and validation state",
     "params": {"threshold": 85.0, "horizon_hours": 24, "min_days": 7, "alert_probability": 0.6},
     "schema": {"threshold": ("float", 10, 100), "horizon_hours": ("int", 1, 168), "min_days": ("int", 3, 60),
                "alert_probability": ("float", 0.05, 0.99)}, "suppression": 360},
]
SCHEMA = {r["rule_id"]: r["schema"] for r in BUILTIN}
PRED_METRIC = {"PRED_CPU_BREACH": "cpu_percent", "PRED_MEMORY_BREACH": "memory_percent",
               "PRED_UTIL_5G_BREACH": "util_5g_percent"}


def validate_params(rule_id: str, params: dict) -> dict:
    schema = SCHEMA.get(rule_id)
    if schema is None:
        raise ValueError("unknown rule")
    unknown = set(params) - set(schema)
    if unknown:
        raise ValueError(f"unknown parameter(s): {sorted(unknown)}")
    out = {}
    for k, v in params.items():
        typ, lo, hi = schema[k]
        try:
            val = int(v) if typ == "int" else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number") from None
        if typ == "int" and float(v) != int(v):
            raise ValueError(f"{k} must be a whole number")
        if not lo <= val <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
        out[k] = val
    return out
