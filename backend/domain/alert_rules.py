"""AP/CX alert rules catalogue (pure). Thresholds are overridable globally or per site.

Each rule evaluates MEASURED values only; a missing metric never raises or clears an alert.
"""
from __future__ import annotations

from dataclasses import dataclass


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
        if not isinstance(v, (int, float)):
            continue
        thr = float(o.get("threshold", r.threshold))
        hit = v > thr if r.op == "gt" else v >= thr if r.op == "ge" else v == thr
        if hit:
            fired.append({"rule_id": r.id, "severity": o.get("severity", r.severity), "value": float(v),
                          "threshold": thr, "message": f"{r.title}: {v:g} (threshold {thr:g})"})
    return fired


def port_rates(prev: list[dict] | None, cur: list[dict], seconds: float) -> dict:
    """Per-port bps and utilisation from two counter snapshots; counter resets yield None, never negative."""
    prev_by = {p["name"]: p for p in (prev or [])}
    out, err_delta, resets = {}, 0.0, 0.0
    for p in cur:
        q = prev_by.get(p["name"])
        if not q or seconds <= 0:
            continue
        rate = {}
        for k in ("rx_bytes", "tx_bytes"):
            a, b = q.get(k), p.get(k)
            rate[k.replace("_bytes", "_bps")] = (b - a) * 8 / seconds if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a else None
        e0 = (q.get("rx_errors") or 0) + (q.get("tx_errors") or 0)
        e1 = (p.get("rx_errors") or 0) + (p.get("tx_errors") or 0)
        if e1 >= e0:
            err_delta += e1 - e0
        if isinstance(p.get("link_resets"), (int, float)) and isinstance(q.get("link_resets"), (int, float)) \
                and p["link_resets"] >= q["link_resets"]:
            resets += p["link_resets"] - q["link_resets"]
        speed = p.get("speed_bps")
        peak = max((x for x in rate.values() if x is not None), default=None)
        rate["util_percent"] = round(100 * peak / speed, 1) if peak is not None and speed else None
        out[p["name"]] = rate
    utils = [r["util_percent"] for r in out.values() if r.get("util_percent") is not None]
    return {"ports": out, "port_errors_delta": err_delta if out else None,
            "link_resets_delta": resets if out else None, "max_port_util_percent": max(utils) if utils else None}
