"""Roaming and RF-interference analytics (rc17). Pure functions over MEASURED inputs only.

Roaming: client connect/disconnect/roam events are INFERRED from successive polls (ClientEvent); a roam that
starts and ends between two polls is invisible, so every result carries the poll-resolution caveat.
Interference: per-radio correlation of measured noise floor with measured retry rate and channel utilisation.
Missing samples are skipped, never zero-filled; fewer than MIN_PAIRS paired samples → INSUFFICIENT_DATA.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import median

PING_PONG_WINDOW = timedelta(minutes=10)
EXCESSIVE_ROAMS_PER_HOUR = 6
STICKY_RSSI_DBM = -75
STICKY_MIN_SECONDS = 600
NOISE_HIGH_DBM = -82.0
RETRY_HIGH_PERCENT = 15.0
MIN_PAIRS = 6


def roaming(events: list[dict], *, hours: float) -> dict:
    """events: {"mac","ts","kind","ap_from","ap_to"} ordered or not. Returns pairs, ping-pong, excessive roamers."""
    roams = sorted((e for e in events if e["kind"] == "roam" and e.get("ap_from") and e.get("ap_to")),
                   key=lambda e: e["ts"])
    pairs = Counter((e["ap_from"], e["ap_to"]) for e in roams)
    by_client: dict[str, list[dict]] = defaultdict(list)
    for e in roams:
        by_client[e["mac"]].append(e)
    ping_pong: Counter = Counter()
    pp_clients: dict[str, int] = {}
    for mac, seq in by_client.items():
        n = 0
        for a, b in zip(seq, seq[1:]):
            if a["ap_to"] == b["ap_from"] and b["ap_to"] == a["ap_from"] and b["ts"] - a["ts"] <= PING_PONG_WINDOW:
                ping_pong[tuple(sorted((a["ap_from"], a["ap_to"])))] += 1
                n += 1
        if n:
            pp_clients[mac] = n
    rate = {mac: len(seq) / hours for mac, seq in by_client.items()} if hours > 0 else {}
    excessive = sorted(([mac, round(r, 2)] for mac, r in rate.items() if r >= EXCESSIVE_ROAMS_PER_HOUR),
                       key=lambda x: -x[1])
    return {"roams": len(roams), "clients_roaming": len(by_client),
            "top_pairs": [{"from": f, "to": t, "count": c} for (f, t), c in pairs.most_common(15)],
            "ping_pong_pairs": [{"aps": list(k), "count": c} for k, c in ping_pong.most_common(15)],
            "ping_pong_clients": len(pp_clients),
            "excessive_roamers": [{"mac": m, "roams_per_hour": r} for m, r in excessive[:25]],
            "thresholds": {"ping_pong_window_minutes": int(PING_PONG_WINDOW.total_seconds() // 60),
                           "excessive_roams_per_hour": EXCESSIVE_ROAMS_PER_HOUR}}


def sticky_candidates(clients: list[dict]) -> list[dict]:
    """Connected clients that stay on a weak signal for a long time (only clients with a measured RSSI)."""
    out = []
    for c in clients:
        r, secs = c.get("rssi_dbm"), c.get("connected_seconds")
        if c.get("is_connected") and isinstance(r, (int, float)) and isinstance(secs, (int, float)) \
                and r <= STICKY_RSSI_DBM and secs >= STICKY_MIN_SECONDS:
            out.append({"mac": c["mac"], "ap": c.get("ap_name"), "rssi_dbm": r, "connected_seconds": int(secs),
                        "band": c.get("band")})
    return sorted(out, key=lambda x: x["rssi_dbm"])


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < MIN_PAIRS:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5, 3)


def interference(series: dict[str, list[tuple[datetime, float]]], *, bucket_s: int = 300) -> dict:
    """series: metric -> [(t, v)] for ONE device (noise_floor_dbm, retry_percent, util_5g_percent ...).
    Samples are aligned into buckets; only buckets where both metrics were measured are paired."""
    def buckets(points):
        b: dict[int, list[float]] = defaultdict(list)
        for t, v in points:
            if isinstance(v, (int, float)):
                b[int(t.timestamp() // bucket_s)].append(float(v))
        return {k: sum(v) / len(v) for k, v in b.items()}
    noise = buckets(series.get("noise_floor_dbm", []))
    retry = buckets(series.get("retry_percent", []))
    util = buckets(series.get("util_5g_percent", []) or series.get("util_2g_percent", []))
    if not noise:
        return {"state": "INSUFFICIENT_DATA", "reason": "no measured noise floor", "samples": 0}
    keys = sorted(set(noise) & set(retry))
    corr_retry = _pearson([noise[k] for k in keys], [retry[k] for k in keys])
    ukeys = sorted(set(noise) & set(util))
    corr_util = _pearson([noise[k] for k in ukeys], [util[k] for k in ukeys])
    high = [k for k in noise if noise[k] >= NOISE_HIGH_DBM]
    high_with_retry = [k for k in high if k in retry and retry[k] >= RETRY_HIGH_PERCENT]
    share = round(len(high) / len(noise), 3)
    if len(keys) < MIN_PAIRS:
        state = "INSUFFICIENT_DATA"
    elif share >= 0.25 and (corr_retry or 0) >= 0.5 and high_with_retry:
        state = "INTERFERENCE_SUSPECTED"
    elif share >= 0.25:
        state = "NOISY"
    else:
        state = "CLEAR"
    return {"state": state, "samples": len(noise), "paired_samples": len(keys),
            "median_noise_dbm": round(median(noise.values()), 1), "high_noise_share": share,
            "noise_retry_correlation": corr_retry, "noise_utilisation_correlation": corr_util,
            "high_noise_with_high_retry_buckets": len(high_with_retry),
            "thresholds": {"noise_high_dbm": NOISE_HIGH_DBM, "retry_high_percent": RETRY_HIGH_PERCENT,
                           "min_pairs": MIN_PAIRS, "bucket_seconds": bucket_s}}
