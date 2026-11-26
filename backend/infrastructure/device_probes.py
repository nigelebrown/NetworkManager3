"""Independent active reachability probe and device clock-offset measurement (rc17; INTEG-P3-05, P3-05).

* Independent of the collection path: a TCP connect to the device's management service (CX REST 443, Instant REST
  4343, otherwise SSH 22) measured from the worker. A probe result never fills a missing RF/interface counter —
  it is stored as its own metrics (probe_reachable, probe_rtt_ms) with collection_method ACTIVE_PROBE.
* Targets: only REGISTERED REAL devices, only their recorded management address, and only addresses inside the
  approved read-only CIDRs (the same allow-list as polling). No ranges, no discovery, bounded concurrency.
* Clock offset: the HTTP ``Date`` header of an unauthenticated HTTPS request to the device's management web
  server (no credentials are ever sent; TLS policy is the platform's normal per-address policy). Offset =
  device Date − request midpoint, resolution 1 s. |offset| > tolerance → CLOCK_DRIFT state, minor alert, and
  device-timestamped evidence (syslog) from that device is downgraded in change attribution.
* Emulation (tests / demo) uses ``EMULATED`` only when INM_PROBE_EMULATION=true; it is never a fallback.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..domain.model import DataQuality, SourceType, TelemetryPoint

log = logging.getLogger("inm.device_probes")
CLOCK_KEY = "device-clock:"
PROBE_KEY = "device-probe:"
EMULATED: dict[str, dict] = {}       # host -> {"reachable": bool, "rtt_ms": float, "device_time": datetime | None}


def tolerance_s() -> float:
    return float(os.environ.get("INM_CLOCK_DRIFT_TOLERANCE_SECONDS", "120"))


def _port(device: InmDevice) -> int:
    if device.vendor.lower() in ("aruba", "hpe aruba", "hpe"):
        return 443 if device.family == "switch" else 4343
    return 22


def _measure(host: str, port: int, timeout: float = 2.0) -> dict:
    if os.environ.get("INM_PROBE_EMULATION") == "true":
        e = EMULATED.get(host)
        if e is None:
            return {"reachable": False, "rtt_ms": None, "device_time": None, "error": "NO_EMULATED_TARGET",
                    "method": "EMULATED"}
        return {"reachable": e["reachable"], "rtt_ms": e.get("rtt_ms") if e["reachable"] else None,
                "device_time": e.get("device_time") if e["reachable"] else None,
                "error": None if e["reachable"] else "TIMEOUT", "method": "EMULATED"}
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            rtt = round((time.perf_counter() - t0) * 1000, 2)
    except (TimeoutError, socket.timeout):
        return {"reachable": False, "rtt_ms": None, "device_time": None, "error": "TIMEOUT", "method": "TCP_CONNECT"}
    except OSError as exc:
        return {"reachable": False, "rtt_ms": None, "device_time": None,
                "error": "REFUSED" if isinstance(exc, ConnectionRefusedError) else "UNREACHABLE", "method": "TCP_CONNECT"}
    device_time = None
    if port in (443, 4343):
        import httpx

        from ..config import get_settings
        try:
            sent = datetime.now(timezone.utc)
            r = httpx.get(f"https://{host}:{port}/", timeout=timeout, follow_redirects=False,
                          verify=get_settings().tls_verify_for(host))
            back = datetime.now(timezone.utc)
            if r.headers.get("date"):
                device_time = parsedate_to_datetime(r.headers["date"])
                midpoint = sent + (back - sent) / 2
                device_time = device_time if device_time.tzinfo else device_time.replace(tzinfo=timezone.utc)
                device_time = {"device": device_time, "midpoint": midpoint}
        except Exception as exc:  # noqa: BLE001 — clock is optional evidence; reachability already measured
            log.debug("clock read failed for %s: %s", host, type(exc).__name__)
    return {"reachable": True, "rtt_ms": rtt, "device_time": device_time, "error": None, "method": "TCP_CONNECT"}


def _offset(measured: dict, now: datetime) -> float | None:
    dt = measured.get("device_time")
    if dt is None:
        return None
    if isinstance(dt, dict):
        return round((dt["device"] - dt["midpoint"]).total_seconds(), 1)
    return round((dt - now).total_seconds(), 1)


def clock_state(db: Session, device_id: str) -> dict:
    row = db.get(AppSetting, CLOCK_KEY + device_id)
    return row.value if row else {"state": "UNKNOWN", "offset_s": None, "measured_at": None}


def _save(db: Session, key: str, value: dict) -> None:
    row = db.get(AppSetting, key) or AppSetting(key=key, value={})
    row.value, row.updated_by, row.updated_at = value, "device-probe", datetime.now(timezone.utc)
    db.add(row)


def run(db: Session, *, now: datetime | None = None, store=None, limit: int = 500) -> dict:
    """Probe every registered REAL device with an allowed management address. Returns counts."""
    from ..printers.snmp import permitted_target
    from .alert_engine import apply as apply_alerts
    now = now or datetime.now(timezone.utc)
    targets = []
    skipped = 0
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL", InmDevice.lifecycle != "retired")
                        .limit(limit)):
        host = next((a for a in (d.mgmt_addresses or []) if isinstance(a, str)), None)
        if host and permitted_target(host):
            targets.append((d, host, _port(d)))
        elif host:
            skipped += 1
    out = {"probed": 0, "reachable": 0, "unreachable": 0, "clock_drift": 0, "skipped_not_allowed": skipped}
    if not targets:
        return out
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as ex:
        results = list(ex.map(lambda t: _measure(t[1], t[2]), targets))
    points: list[TelemetryPoint] = []
    tol = tolerance_s()
    for (d, host, port), m in zip(targets, results):
        out["probed"] += 1
        prev = (db.get(AppSetting, PROBE_KEY + d.id).value if db.get(AppSetting, PROBE_KEY + d.id) else {})
        failures = 0 if m["reachable"] else int(prev.get("consecutive_failures", 0)) + 1
        _save(db, PROBE_KEY + d.id, {"reachable": m["reachable"], "rtt_ms": m["rtt_ms"], "error": m["error"],
                                     "port": port, "method": m["method"], "consecutive_failures": failures,
                                     "measured_at": now.isoformat()})
        out["reachable" if m["reachable"] else "unreachable"] += 1
        common = dict(device_id=d.id, observed_at=now, collection_method="ACTIVE_PROBE", quality=DataQuality.OBSERVED,
                      source_type=SourceType.REAL, source_event_id=f"probe:{d.id}:{int(now.timestamp())}",
                      site_id=d.site_id, tags={"probe": m["method"], "port": port})
        points.append(TelemetryPoint(metric_name="probe_reachable", value=1.0 if m["reachable"] else 0.0, unit="bool", **common))
        if m["rtt_ms"] is not None:
            points.append(TelemetryPoint(metric_name="probe_rtt_ms", value=m["rtt_ms"], unit="ms", **common))
        values = {"probe_failures": float(failures)}
        off = _offset(m, now)
        if off is not None:
            state = "DRIFT" if abs(off) > tol else "OK"
            out["clock_drift"] += state == "DRIFT"
            _save(db, CLOCK_KEY + d.id, {"state": state, "offset_s": off, "tolerance_s": tol,
                                         "measured_at": now.isoformat(), "method": "HTTP_DATE" if m["method"] != "EMULATED" else "EMULATED"})
            points.append(TelemetryPoint(metric_name="clock_offset_s", value=off, unit="s", **common))
            values["clock_offset_abs_s"] = abs(off)
        apply_alerts(db, d, values, now=now)
    if store is not None and points:
        store.write_points(points)
    db.flush()
    return out
