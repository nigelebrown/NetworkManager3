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


def analyze(context: dict) -> list[dict]:
    recs: list[dict] = []
    for site in context.get("sites", []):
        name = site.get("label", "site")
        down, total = site.get("aps_down", 0), site.get("aps_total", 0)
        if down:
            recs.append(_rec("ap_offline", "high", f"{down} of {total} access points offline at {name}",
                             "Offline APs remove coverage and push clients onto neighbours. Check PoE/uplink on the "
                             "serving switch port and the AP's last-seen time before dispatching a technician.",
                             {"aps_down": down, "aps_total": total}, site))
        util = site.get("max_util_percent")
        if util is not None and util >= 70:
            recs.append(_rec("high_channel_utilization", "medium", f"Channel utilisation {util:.0f}% at {name}",
                             "Sustained utilisation above 70% degrades experience. Review channel plan and client "
                             "density. Any future RF change must stay at 20 or 40 MHz width (80 MHz is blocked).",
                             {"max_util_percent": util, "threshold_percent": 70}, site))
        exp = site.get("exp_score")
        if exp is not None and exp < 60:
            recs.append(_rec("poor_experience", "medium", f"Experience score {exp:.0f}/100 at {name}",
                             "The mean client experience is in the poor band. Open Experience → lowest-scoring clients "
                             "to see which measured inputs (SNR, speed, stability) drive it.",
                             {"exp_score": exp, "poor_below": 60}, site))
        poor = site.get("clients_poor", 0)
        clients = site.get("clients_total", 0)
        if clients and poor / clients >= 0.25:
            recs.append(_rec("many_poor_clients", "low", f"{poor} of {clients} clients in the poor band at {name}",
                             "A quarter or more of clients score poor. Correlate with AP placement and band steering.",
                             {"clients_poor": poor, "clients_total": clients}, site))
        if site.get("alerts_critical", 0) > 0 and not down:
            recs.append(_rec("critical_alerts_open", "medium", f"{site['alerts_critical']} critical alert(s) open at {name}",
                             "Critical alerts are unacknowledged. Acknowledge or resolve them so on-call has a clean view.",
                             {"alerts_critical": site["alerts_critical"]}, site))
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order[r["risk"]])
    return recs
