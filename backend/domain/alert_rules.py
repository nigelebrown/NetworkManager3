"""AP/CX alert rules catalogue (pure). Thresholds are overridable globally or per site.

Each rule evaluates MEASURED values only; a missing metric never raises or clears an alert.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AlertRule:
    id: str
    family: str          # access_point | switch | any
    metric: str
    op: str              # gt | ge | eq
    threshold: float
    severity: str        # critical | major | minor | info
    title: str


CATALOGUE: tuple[AlertRule, ...] = (
    AlertRule("DEVICE_UNREACHABLE", "any", "consecutive_failures", "ge", 3, "critical", "Device not answering polls"),
    AlertRule("TELEMETRY_STALE", "any", "stale_minutes", "ge", 30, "minor", "Telemetry stale"),
    AlertRule("PROBE_UNREACHABLE", "any", "probe_failures", "ge", 3, "critical", "Management service not answering active probe"),
    AlertRule("CLOCK_DRIFT", "any", "clock_offset_abs_s", "gt", 120, "minor", "Device clock differs from server time"),
    AlertRule("CPU_HIGH", "any", "cpu_percent", "ge", 90, "major", "CPU high"),
    AlertRule("MEMORY_HIGH", "any", "memory_percent", "ge", 92, "major", "Memory high"),
    AlertRule("AP_5G_UTIL_HIGH", "access_point", "util_5g_percent", "ge", 85, "major", "5 GHz channel congested"),
    AlertRule("AP_2G_UTIL_HIGH", "access_point", "util_2g_percent", "ge", 85, "minor", "2.4 GHz channel congested"),
    AlertRule("AP_RETRY_HIGH", "access_point", "retry_percent", "ge", 20, "minor", "High retransmissions"),
    AlertRule("AP_NOISE_HIGH", "access_point", "noise_floor_dbm", "ge", -82, "minor", "High noise floor"),
    AlertRule("AP_CLIENTS_HIGH", "access_point", "clients", "ge", 60, "minor", "Client density high"),
    AlertRule("SW_PORT_ERRORS", "switch", "port_errors_delta", "ge", 100, "major", "Interface errors increasing"),
    AlertRule("SW_PORT_FLAPPING", "switch", "link_resets_delta", "ge", 5, "major", "Port flapping"),
    AlertRule("SW_POE_BUDGET", "switch", "poe_budget_percent", "ge", 90, "major", "PoE budget nearly exhausted"),
    AlertRule("SW_TEMP_HIGH", "switch", "temp_max_c", "ge", 65, "major", "Switch temperature high"),
    AlertRule("SW_FAN_FAILED", "switch", "fans_failed", "ge", 1, "critical", "Fan failure"),
    AlertRule("SW_PSU_FAILED", "switch", "psu_failed", "ge", 1, "critical", "Power supply failure"),
    AlertRule("CONFIG_DRIFT", "any", "template_drift_count", "ge", 1, "major", "Installed configuration differs from template"),
    AlertRule("CONFIG_COMPLIANCE_CRITICAL", "any", "critical_rule_failures", "ge", 1, "major", "Critical best-practice failures"),
    AlertRule("SW_UPLINK_UTIL_HIGH", "switch", "max_port_util_percent", "ge", 85, "major", "Port utilisation high"),
)

# Per-interface rules: evaluated for EACH port separately (alert entity = port), so an operator sees which port.
PORT_CATALOGUE: tuple[AlertRule, ...] = (
    AlertRule("PORT_UTIL_HIGH", "port", "port_util_percent", "ge", 90, "major", "Interface utilisation high"),
    AlertRule("PORT_ERRORS", "port", "port_errors_delta", "ge", 10, "major", "Interface errors increasing"),
    AlertRule("PORT_FLAPPING", "port", "port_link_resets_delta", "ge", 3, "major", "Interface flapping"),
)


# Hysteresis avoids opening and resolving alerts on successive polls near a threshold.
_ABSOLUTE_BAND = {"cpu_percent": 5, "memory_percent": 5, "util_5g_percent": 5, "util_2g_percent": 5,
                  "retry_percent": 5, "poe_budget_percent": 5, "max_port_util_percent": 5, "port_util_percent": 5,
                  "noise_floor_dbm": 3, "temp_max_c": 3}
_RELATIVE_BAND = {"port_errors_delta": 0.5, "link_resets_delta": 0.5, "port_link_resets_delta": 0.5, "clients": 0.1}


def clear_threshold(rule: AlertRule, threshold: float, override: dict | None = None) -> float:
    if override and isinstance(override.get("clear_threshold"), (int, float)):
        return min(float(override["clear_threshold"]), threshold)
    if rule.metric in _ABSOLUTE_BAND:
        return threshold - _ABSOLUTE_BAND[rule.metric]
    if rule.metric in _RELATIVE_BAND:
        return threshold - abs(threshold) * _RELATIVE_BAND[rule.metric]
    return threshold


def cleared(rule: AlertRule, value: float, threshold: float, override: dict | None = None) -> bool:
    if rule.op == "eq":
        return value != threshold
    clear = clear_threshold(rule, threshold, override)
    return value < clear if (rule.op == "ge" or clear < threshold) else value <= clear


def rule_by_id(rule_id: str) -> AlertRule | None:
    return next((r for r in (*CATALOGUE, *PORT_CATALOGUE) if r.id == rule_id), None)


def evaluate(family: str, values: dict, overrides: dict[str, dict] | None = None) -> list[dict]:
    """values: metric -> number (absent = unmeasured). overrides: rule_id -> {threshold, severity, enabled}."""
    overrides = overrides or {}
    fired = []
    for r in CATALOGUE:
        if r.family not in ("any", family):
            continue
        o = overrides.get(r.id, {})
        if o.get("enabled") is False:
            continue
        v = values.get(r.metric)
        if not measured_number(v):
            continue
        thr = float(o.get("threshold", r.threshold))
        hit = v > thr if r.op == "gt" else v >= thr if r.op == "ge" else v == thr
        if hit:
            fired.append({"rule_id": r.id, "severity": o.get("severity", r.severity), "value": float(v),
                          "threshold": thr, "message": f"{r.title}: {v:g} (threshold {thr:g})"})
    return fired


MIN_RATE_INTERVAL_S = 5.0


def measured_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def port_rates(prev: list[dict] | None, cur: list[dict], seconds: float) -> dict:
    """Derive rates without inventing zero counters or implausible utilization."""
    prev_by = {p["name"]: p for p in (prev or [])}
    out, err_delta, resets = {}, 0.0, 0.0
    err_seen = reset_seen = False
    if not measured_number(seconds) or seconds < MIN_RATE_INTERVAL_S:
        return {"ports": {}, "port_errors_delta": None, "link_resets_delta": None,
                "max_port_util_percent": None, "skipped": "INTERVAL_TOO_SHORT_OR_CLOCK_SKEW"}
    for p in cur:
        q = prev_by.get(p["name"])
        if not q:
            continue
        rate: dict = {}
        notes: list[str] = []
        for k in ("rx_bytes", "tx_bytes"):
            a, b = q.get(k), p.get(k)
            key = k.replace("_bytes", "_bps")
            if measured_number(a) and measured_number(b) and 0 <= a <= b:
                rate[key] = (b - a) * 8 / seconds
            else:
                rate[key] = None
                if measured_number(a) and measured_number(b):
                    notes.append("COUNTER_RESET")
        errors = [(q.get(k), p.get(k)) for k in ("rx_errors", "tx_errors")]
        if all(measured_number(a) and measured_number(b) and 0 <= a <= b for a, b in errors):
            err_delta += sum(b - a for a, b in errors)
            err_seen = True
        a, b = q.get("link_resets"), p.get("link_resets")
        if measured_number(a) and measured_number(b) and 0 <= a <= b:
            resets += b - a
            reset_seen = True
        speed, old_speed = p.get("speed_bps"), q.get("speed_bps")
        peak = max((x for x in (rate.get("rx_bps"), rate.get("tx_bps")) if x is not None), default=None)
        util = None
        if peak is not None and measured_number(speed) and speed > 0:
            if measured_number(old_speed) and old_speed != speed:
                notes.append("SPEED_CHANGED")
            elif peak > speed:
                notes.append("UTILISATION_INCONSISTENT")
            else:
                util = round(100 * peak / speed, 1)
        rate["util_percent"] = util
        if notes:
            rate["notes"] = notes
        out[p["name"]] = rate
    utils = [r["util_percent"] for r in out.values() if r.get("util_percent") is not None]
    return {"ports": out, "port_errors_delta": err_delta if err_seen else None,
            "link_resets_delta": resets if reset_seen else None, "max_port_util_percent": max(utils) if utils else None}
