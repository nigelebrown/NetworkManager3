"""NOC health scoring from measured telemetry (pure). Unmeasured -> None, never a default 'good'."""
from __future__ import annotations

PENALTIES = [
    # metric, threshold, penalty, reason
    ("util_5g_percent", 80, 25, "5 GHz channel congested"),
    ("util_5g_percent", 60, 10, "5 GHz channel busy"),
    ("util_2g_percent", 80, 15, "2.4 GHz channel congested"),
    ("retry_percent", 15, 20, "High retransmissions"),
    ("retry_percent", 10, 10, "Elevated retransmissions"),
    ("cpu_percent", 85, 15, "High CPU"),
    ("memory_percent", 90, 10, "High memory"),
    ("clients", 50, 10, "Client density high"),
]


def latest_by_metric(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: str(x.get("observed_at")), reverse=True):
        if r.get("value") is None or r["metric_name"] in ("port_rx_bps", "port_tx_bps", "port_util_percent"):
            continue
        out.setdefault(r["metric_name"], r)
    return out


def health(family: str, latest: dict[str, dict], freshness: str, status: str | None = None) -> dict:
    if status == "down":
        return {"score": 0, "band": "POOR", "reasons": ["Device is down / unreachable"]}
    if not latest:
        return {"score": None, "band": "NO_DATA", "reasons": ["No measured telemetry"]}
    score, reasons, used = 100, [], set()
    for metric, thr, pen, why in PENALTIES:
        if metric in used or metric not in latest:
            continue
        v = latest[metric]["value"]
        if v is not None and v > thr:
            score -= pen
            reasons.append(f"{why} ({v:g})")
            used.add(metric)
    nf = latest.get("noise_floor_dbm")
    if nf and nf["value"] is not None and nf["value"] > -85:
        score -= 10
        reasons.append(f"High noise floor ({nf['value']:g} dBm)")
    if freshness in ("STALE", "GAP"):
        reasons.append(f"Telemetry {freshness.lower()}")
    score = max(0, score)
    band = "GOOD" if score >= 80 else "FAIR" if score >= 60 else "POOR"
    return {"score": score, "band": band, "reasons": reasons}
