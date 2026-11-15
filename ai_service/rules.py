"""Deterministic, explainable rule engine (Recommendation v1). This is NOT machine learning and says so.

Input is a pseudonymised per-site metrics context built by the API. The engine never sees credentials, client
MAC/IP/hostnames, or device addresses, and it returns advice only: no commands, no device writes.
"""
from __future__ import annotations

RULESET_VERSION = "rules-1.0.0"


def _rec(rule_id, risk, title, explanation, evidence, site):
    return {"rule_id": rule_id, "rule_version": RULESET_VERSION, "engine": "RULE", "risk": risk, "title": title,
            "explanation": explanation, "evidence": evidence, "legacy_site_id": site.get("site_id"),
            "approval_state": "ADVISORY", "trained_model": False}


DEFAULTS = {"ap_offline": {"min_down": 1}, "high_channel_utilization": {"threshold_percent": 70},
            "poor_experience": {"poor_below": 60}, "many_poor_clients": {"fraction": 0.25},
            "critical_alerts_open": {"min_alerts": 1}}


def _cfg(context: dict, rule_id: str) -> tuple[bool, dict]:
    """Effective parameters supplied by the platform's reviewed rule catalogue (numbers only)."""
    c = (context.get("rule_config") or {}).get(rule_id) or {}
    params = dict(DEFAULTS[rule_id])
    for k, v in (c.get("params") or {}).items():
        if k in params and isinstance(v, (int, float)) and not isinstance(v, bool):
            params[k] = v
    return c.get("enabled", True) is not False, params


def analyze(context: dict) -> list[dict]:
    recs: list[dict] = []
    on = {r: _cfg(context, r) for r in DEFAULTS}
    for site in context.get("sites", []):
        name = site.get("label", "site")
        down, total = site.get("aps_down", 0), site.get("aps_total", 0)
        if on["ap_offline"][0] and down >= on["ap_offline"][1]["min_down"]:
            recs.append(_rec("ap_offline", "high", f"{down} of {total} access points offline at {name}",
                             "Offline APs remove coverage and push clients onto neighbours. Check PoE/uplink on the "
                             "serving switch port and the AP's last-seen time before dispatching a technician.",
                             {"aps_down": down, "aps_total": total}, site))
        util = site.get("max_util_percent")
        thr = on["high_channel_utilization"][1]["threshold_percent"]
        if on["high_channel_utilization"][0] and util is not None and util >= thr:
            recs.append(_rec("high_channel_utilization", "medium", f"Channel utilisation {util:.0f}% at {name}",
                             f"Sustained utilisation at or above {thr:.0f}% degrades experience. Review channel plan and client "
                             "density. Any future RF change must stay at 20 or 40 MHz width (80 MHz is blocked).",
                             {"max_util_percent": util, "threshold_percent": thr}, site))
        exp = site.get("exp_score")
        poor_below = on["poor_experience"][1]["poor_below"]
        if on["poor_experience"][0] and exp is not None and exp < poor_below:
            recs.append(_rec("poor_experience", "medium", f"Experience score {exp:.0f}/100 at {name}",
                             "The mean client experience is in the poor band. Open Experience → lowest-scoring clients "
                             "to see which measured inputs (SNR, speed, stability) drive it.",
                             {"exp_score": exp, "poor_below": poor_below}, site))
        poor = site.get("clients_poor", 0)
        clients = site.get("clients_total", 0)
        if on["many_poor_clients"][0] and clients and poor / clients >= on["many_poor_clients"][1]["fraction"]:
            recs.append(_rec("many_poor_clients", "low", f"{poor} of {clients} clients in the poor band at {name}",
                             "A quarter or more of clients score poor. Correlate with AP placement and band steering.",
                             {"clients_poor": poor, "clients_total": clients}, site))
        if on["critical_alerts_open"][0] and site.get("alerts_critical", 0) >= on["critical_alerts_open"][1]["min_alerts"] \
                and not (on["ap_offline"][0] and down >= on["ap_offline"][1]["min_down"]):
            recs.append(_rec("critical_alerts_open", "medium", f"{site['alerts_critical']} critical alert(s) open at {name}",
                             "Critical alerts are unacknowledged. Acknowledge or resolve them so on-call has a clean view.",
                             {"alerts_critical": site["alerts_critical"]}, site))
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order[r["risk"]])
    return recs
