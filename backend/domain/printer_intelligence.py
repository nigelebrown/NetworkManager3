"""WP-12 / GAP-18 — printer model packs, counter transitions and statistical supply forecast (pure).

Estimates are labelled STATISTICAL and never presented as manufacturer-certified yield.
Trained maintenance models require labelled failures; none are claimed here.
"""
from __future__ import annotations

from datetime import datetime, timezone

COUNTER_32 = 2 ** 32

# Read-only Printer-MIB (RFC 3805) knowledge. Vendor-private OIDs only where publicly documented.
MIB_PACKS = {
    "rfc3805-generic": {
        "applies_to": "Any Printer-MIB compliant device",
        "oids": {"prtMarkerLifeCount": "1.3.6.1.2.1.43.10.2.1.4",
                 "prtMarkerCounterUnit": "1.3.6.1.2.1.43.10.2.1.3",
                 "prtMarkerSuppliesLevel": "1.3.6.1.2.1.43.11.1.1.9",
                 "prtMarkerSuppliesMaxCapacity": "1.3.6.1.2.1.43.11.1.1.8",
                 "prtMarkerSuppliesDescription": "1.3.6.1.2.1.43.11.1.1.6",
                 "hrDeviceStatus": "1.3.6.1.2.1.25.3.2.1.5",
                 "hrPrinterDetectedErrorState": "1.3.6.1.2.1.25.3.5.1.2"},
        "write_oids": [], "certification": "STANDARD_MIB_READ_ONLY"},
}


def classify_counter(previous: int | None, current: int | None, *, width_bits: int = 32) -> dict:
    """Distinguish a genuine wrap (previous near max) from a reset/replacement. Never fabricate."""
    if previous is None or current is None:
        return {"kind": "INSUFFICIENT_DATA", "delta": None}
    if current >= previous:
        return {"kind": "NORMAL", "delta": current - previous}
    maxv = 2 ** width_bits
    if previous > maxv * 0.95 and current < maxv * 0.05:
        return {"kind": "ROLLOVER", "delta": (maxv - previous) + current}
    return {"kind": "RESET_OR_REPLACEMENT", "delta": None}


def _ts(t) -> datetime:
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def supply_forecast(readings: list[dict], *, now: datetime | None = None) -> dict:
    """Least-squares days-to-empty on percent_remaining since the last refill/replacement.
    Requires >=3 observed (non-manual-estimate) points spanning >=24h and a declining slope."""
    now = now or datetime.now(timezone.utc)
    pts = sorted(((_ts(r["observed_at"]), float(r["percent_remaining"])) for r in readings
                  if r.get("percent_remaining") is not None and r.get("source") != "ESTIMATE"),
                 key=lambda x: x[0])
    # Keep only the segment after the most recent increase (refill or new cartridge).
    seg: list = []
    for p in pts:
        if seg and p[1] > seg[-1][1] + 5:
            seg = []
        seg.append(p)
    if len(seg) < 3 or (seg[-1][0] - seg[0][0]).total_seconds() < 86400:
        return {"status": "INSUFFICIENT_EVIDENCE", "kind": "STATISTICAL", "points": len(seg),
                "days_to_empty": None}
    t0 = seg[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in seg]
    ys = [y for _, y in seg]
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    if slope >= 0:
        return {"status": "NO_DECLINE_OBSERVED", "kind": "STATISTICAL", "points": n, "days_to_empty": None}
    intercept = my - slope * mx
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys) or 1e-9
    days_now = (now - t0).total_seconds() / 86400
    remaining = intercept + slope * days_now
    return {"status": "FORECAST", "kind": "STATISTICAL", "points": n,
            "slope_percent_per_day": round(slope, 3), "r_squared": round(1 - ss_res / ss_tot, 3),
            "days_to_empty": round(max(0.0, -remaining / slope), 1),
            "caveat": "Statistical trend of device-reported level; not manufacturer-certified yield."}
