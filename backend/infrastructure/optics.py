"""Optical transceiver monitoring for AOS-CX (rc18).

Source: the read-only, device-advertised AnyCLI command ``show interface transceiver detail`` (never a config path).
The parser accepts the documented single-channel (SFP/SFP+/SFP28) layout and multi-channel (QSFP+/QSFP28) per-lane
sections, plus the manufacturer threshold table (High Alarm / High Warn / Low Warn / Low Alarm). Format details are
protocol assumption A-CX-15 (validate on hardware).

Units are normalised: power in dBm (converted from mW when only mW is given; 0 mW is LOSS OF SIGNAL, not -inf and
not "healthy"), temperature in degC, supply voltage in V, laser bias in mA. A value that is absent, "n/a" or
unparseable is NOT_MEASURED; a port without DOM support is UNSUPPORTED. Neither is ever reported as OK.

State per metric: OK / WARNING / CRITICAL / NOT_MEASURED, using the module's own thresholds; when the module provides
none, conservative platform defaults are used and the evaluation is labelled THRESHOLD_SOURCE=PLATFORM_DEFAULT.
Alerts open on WARNING/CRITICAL and resolve only on a measured return inside the warning band (hysteresis by design).
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

METRICS = ("temperature_c", "voltage_v", "bias_ma", "tx_power_dbm", "rx_power_dbm")
LABELS = {"temperature": "temperature_c", "voltage": "voltage_v", "tx bias": "bias_ma", "bias current": "bias_ma",
          "bias": "bias_ma", "rx power": "rx_power_dbm", "tx power": "tx_power_dbm"}
# Conservative defaults (SFF-8472 typical module ranges) used ONLY when the module reports no thresholds.
DEFAULT_THRESHOLDS = {
    "temperature_c": {"high_alarm": 75.0, "high_warn": 70.0, "low_warn": 0.0, "low_alarm": -5.0},
    "voltage_v": {"high_alarm": 3.63, "high_warn": 3.47, "low_warn": 3.13, "low_alarm": 2.97},
    "bias_ma": {"high_alarm": 15.0, "high_warn": 12.0, "low_warn": 2.0, "low_alarm": 1.0},
    "tx_power_dbm": {"high_alarm": 3.0, "high_warn": 1.0, "low_warn": -7.0, "low_alarm": -9.0},
    "rx_power_dbm": {"high_alarm": 3.0, "high_warn": 1.0, "low_warn": -14.0, "low_alarm": -18.0},
}
NUM = r"-?[0-9]+(?:\.[0-9]+)?"


def mw_to_dbm(mw: float | None) -> float | None:
    if mw is None or not math.isfinite(mw) or mw <= 0:
        return None
    return round(10 * math.log10(mw), 2)


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    m = re.search(NUM, text)
    if not m or re.search(r"\bn/?a\b", text, re.I):
        return None
    v = float(m.group(0))
    return v if math.isfinite(v) else None


def _power(text: str) -> tuple[float | None, float | None, bool]:
    """'0.52mW, -2.84dBm' / '-2.84 dBm' / '0.0000mW' -> (dbm, mw, loss_of_signal)."""
    dbm = re.search(rf"({NUM})\s*dBm", text, re.I)
    mw = re.search(rf"({NUM})\s*mW", text, re.I)
    mwv = float(mw.group(1)) if mw else None
    if mwv is not None and mwv <= 0:
        return None, 0.0, True
    if dbm:
        return float(dbm.group(1)), mwv, False
    return (mw_to_dbm(mwv), mwv, False) if mwv is not None else (None, None, False)


def _thresholds(lines: list[str]) -> dict:
    out: dict = {}
    for ln in lines:
        m = re.match(rf"^\s*(Temperature|Voltage|Bias(?: Current)?|Tx Bias|Rx Power|Tx Power)\s*(?:\(?\w+\)?)?\s+"
                     rf"({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s*$", ln, re.I)
        if m:
            key = LABELS[m.group(1).lower()]
            ha, hw, lw, la = (float(m.group(i)) for i in range(2, 6))
            out[key] = {"high_alarm": ha, "high_warn": hw, "low_warn": lw, "low_alarm": la}
    return out


def parse_transceiver_detail(text: str) -> dict:
    """-> {"state", "ports": {port: {identity..., "lanes": [{metric: value}], "thresholds", "dom_supported"}}}."""
    if not isinstance(text, str) or len(text) > 2_000_000:
        return {"state": "UNRECOGNIZED", "ports": {}, "reason": "invalid or oversized response"}
    sections = re.split(r"(?m)^Transceiver in (\d+/\d+/\d+)\s*$", text)
    if len(sections) < 3:
        return {"state": "UNRECOGNIZED", "ports": {}, "reason": "no 'Transceiver in <port>' sections"}
    ports = {}
    for port, body in zip(sections[1::2], sections[2::2]):
        lines = body.splitlines()
        kv = {}
        for ln in lines:
            m = re.match(r"^\s{2,}([A-Za-z][A-Za-z .]+?)\s*:\s*(.+?)\s*$", ln)
            if m:
                kv.setdefault(m.group(1).strip().lower(), m.group(2))
        ident = {"type": kv.get("type"), "vendor_name": kv.get("vendor name"),
                 "part_number": kv.get("vendor part number") or kv.get("product number"),
                 "serial_number": kv.get("vendor serial num") or kv.get("vendor serial number"),
                 "wavelength_nm": _num(kv.get("wavelength")), "connector": kv.get("connector type")}
        dom = (kv.get("diagnostic support") or "").strip().lower()
        dom_supported = dom not in ("none", "no", "not supported", "")
        lane_texts = re.split(r"(?mi)^\s*(?:Lane|Channel)\s+(\d+)\s*:?\s*$", body)
        chunks = [("1", lane_texts[0])] if len(lane_texts) == 1 else list(zip(lane_texts[1::2], lane_texts[2::2]))
        lanes = []
        for lane_no, chunk in chunks:
            vals: dict = {"lane": int(lane_no)}
            for ln in chunk.splitlines():
                m = re.match(r"^\s*(Temperature|Voltage|Tx Bias|Bias Current|Rx Power|Tx Power)\s*:\s*(.+?)\s*$", ln, re.I)
                if not m:
                    continue
                key = LABELS[m.group(1).lower()]
                if key in ("rx_power_dbm", "tx_power_dbm"):
                    dbm, mw, los = _power(m.group(2))
                    vals[key] = dbm
                    vals[key.replace("_dbm", "_mw")] = mw
                    if los:
                        vals[key.replace("_power_dbm", "_los")] = True
                else:
                    vals[key] = _num(m.group(2))
            if len(vals) > 1:
                lanes.append(vals)
        if len(chunks) > 1 and lanes:                 # module-level temperature/voltage may sit above the lanes
            head = {}
            for ln in lane_texts[0].splitlines():
                m = re.match(r"^\s*(Temperature|Voltage)\s*:\s*(.+?)\s*$", ln, re.I)
                if m:
                    head[LABELS[m.group(1).lower()]] = _num(m.group(2))
            for ln in lanes:
                for k, v in head.items():
                    ln.setdefault(k, v)
        ports[port] = {**ident, "dom_supported": dom_supported and bool(lanes), "lanes": lanes,
                       "lane_count": len(lanes), "thresholds": _thresholds(lines)}
    return {"state": "COLLECTED", "ports": ports, "source": "CX_REST_ANYCLI_TRANSCEIVER_DETAIL"}


def evaluate(port: dict) -> dict:
    """Per-lane/per-metric state with the module's thresholds (or labelled platform defaults)."""
    if not port.get("dom_supported"):
        return {"state": "UNSUPPORTED", "lanes": [], "threshold_source": None,
                "reason": "transceiver reports no digital diagnostics"}
    th_src = "MODULE" if port.get("thresholds") else "PLATFORM_DEFAULT"
    lanes_out, worst = [], "OK"
    rank = {"OK": 0, "NOT_MEASURED": 1, "WARNING": 2, "CRITICAL": 3}
    for lane in port["lanes"]:
        res = {}
        for metric in METRICS:
            v = lane.get(metric)
            t = (port.get("thresholds") or {}).get(metric) or DEFAULT_THRESHOLDS[metric]
            if metric == "rx_power_dbm" and lane.get("rx_los"):
                res[metric] = {"value": None, "state": "CRITICAL", "reason": "loss of signal (0 mW)"}
            elif v is None:
                res[metric] = {"value": None, "state": "NOT_MEASURED"}
            elif v >= t["high_alarm"] or v <= t["low_alarm"]:
                res[metric] = {"value": v, "state": "CRITICAL", "threshold": t}
            elif v >= t["high_warn"] or v <= t["low_warn"]:
                res[metric] = {"value": v, "state": "WARNING", "threshold": t}
            else:
                res[metric] = {"value": v, "state": "OK", "threshold": t}
            if rank[res[metric]["state"]] > rank[worst]:
                worst = res[metric]["state"]
        lanes_out.append({"lane": lane["lane"], "metrics": res})
    return {"state": worst, "lanes": lanes_out, "threshold_source": th_src}


def trend(points: list[tuple[datetime, float]], low_warn: float, *, now: datetime | None = None,
          min_days: float = 3.0) -> dict:
    """Least-squares slope of Rx power (dB/day). DEGRADING when falling >= 0.05 dB/day and projected to reach the
    low-warning threshold within 30 days. Needs >= 6 samples spanning >= min_days."""
    pts = sorted((t, v) for t, v in points if v is not None)
    if len(pts) < 6 or (pts[-1][0] - pts[0][0]).total_seconds() < min_days * 86400:
        return {"state": "INSUFFICIENT_HISTORY", "samples": len(pts)}
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    latest = ys[-1]
    days = (latest - low_warn) / -slope if slope < 0 else None
    state = "DEGRADING" if slope <= -0.05 and days is not None and days <= 30 else "STABLE"
    return {"state": state, "slope_db_per_day": round(slope, 3), "latest_dbm": latest,
            "days_to_low_warning": round(days, 1) if days is not None else None, "samples": n}


# ---------------------------------------------------------------- persistence + alerts
def _entity(port: str, lane: int) -> str:
    return port if lane == 1 else f"{port}:{lane}"


def ingest(db, device, parsed: dict, *, now: datetime | None = None, store=None) -> dict:
    """Store current state (AppSetting optics:<device>), write telemetry points (entity_type transceiver), and
    open/resolve optical alerts. Returns counts."""
    from sqlalchemy import select

    from ..database.models import AppSetting
    from ..database.models_phases import InmAlert
    from ..domain.model import DataQuality, SourceType, TelemetryPoint
    now = now or datetime.now(timezone.utc)
    demo = device.source_type == "DEMO"
    points, summary, states = [], {"ports": 0, "warning": 0, "critical": 0, "unsupported": 0}, {}
    for port, data in (parsed.get("ports") or {}).items():
        ev = evaluate(data)
        states[port] = {k: data.get(k) for k in ("type", "vendor_name", "part_number", "serial_number", "wavelength_nm",
                                                  "lane_count", "dom_supported", "thresholds")} | {"evaluation": ev}
        summary["ports"] += 1
        summary["unsupported"] += ev["state"] == "UNSUPPORTED"
        for lane in ev["lanes"]:
            for metric, r in lane["metrics"].items():
                if r["value"] is None:
                    continue
                points.append(TelemetryPoint(device_id=device.id, metric_name=f"optic_{metric}", observed_at=now,
                                             value=float(r["value"]), unit=metric.rsplit("_", 1)[-1],
                                             collection_method="DEMO_SYNTHETIC" if demo else "CX_REST_ANYCLI",
                                             quality=DataQuality.OBSERVED,
                                             source_type=SourceType.DEMO if demo else SourceType.REAL,
                                             source_event_id=f"optics:{device.id}:{int(now.timestamp())}",
                                             site_id=device.site_id, entity_type="transceiver",
                                             entity_id=_entity(port, lane["lane"])))
        # alert lifecycle per port (worst lane/metric); resolve only on a measured healthy reading
        entity = f"optic:{port}"
        open_a = db.scalar(select(InmAlert).where(InmAlert.device_id == device.id, InmAlert.entity == entity,
                                                  InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))))
        bad = [(lane["lane"], m, r) for lane in ev["lanes"] for m, r in lane["metrics"].items()
               if r["state"] in ("WARNING", "CRITICAL")]
        if bad:
            sev = "critical" if any(r["state"] == "CRITICAL" for _, _, r in bad) else "major"
            summary["critical" if sev == "critical" else "warning"] += 1
            lane, metric, r = sorted(bad, key=lambda x: x[2]["state"] != "CRITICAL")[0]
            msg = (f"{device.hostname or device.serial_number} {port}"
                   f"{'' if lane == 1 else f' lane {lane}'}: optical {metric.replace('_', ' ')} "
                   f"{r.get('value') if r.get('value') is not None else r.get('reason')} ({r['state']})")[:300]
            if open_a is None:
                db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id="OPTIC_" + metric.upper(),
                                severity=sev, message=msg, value=r.get("value"), entity=entity,
                                threshold=(r.get("threshold") or {}).get("low_warn"), opened_at=now, last_seen_at=now,
                                source_type=device.source_type, suppressed=False))
            else:
                open_a.severity, open_a.message, open_a.last_seen_at, open_a.value = sev, msg, now, r.get("value")
        elif open_a is not None and ev["state"] == "OK":
            open_a.state, open_a.resolved_at = "RESOLVED", now                 # recovery notification via outbox
    row = db.get(AppSetting, "optics:" + device.id) or AppSetting(key="optics:" + device.id, value={})
    row.value = {"observed_at": now.isoformat(), "source": parsed.get("source"), "ports": states,
                 "state": parsed.get("state")}
    row.updated_by, row.updated_at = "optics-collector", now
    db.add(row)
    if store is not None and points:
        store.write_points(points)
    db.flush()
    return summary | {"points": len(points)}


def device_view(db, device_id: str, store=None, *, days: int = 30) -> dict:
    from ..database.models import AppSetting
    row = db.get(AppSetting, "optics:" + device_id)
    if row is None:
        return {"device_id": device_id, "state": "NO_DATA", "ports": {}}
    out = dict(row.value)
    if store is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        for port, p in out.get("ports", {}).items():
            rows = store.query(device_id=device_id, metric_name="optic_rx_power_dbm", since=since, limit=5000,
                               entity_type="transceiver", entity_id=port)
            pts = [(datetime.fromisoformat(r["observed_at"]), r["value"]) for r in rows]
            lw = ((p.get("thresholds") or {}).get("rx_power_dbm") or DEFAULT_THRESHOLDS["rx_power_dbm"])["low_warn"]
            p["rx_trend"] = trend(pts, lw)
    return {"device_id": device_id} | out
