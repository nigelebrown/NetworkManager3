"""WP-02 scheduled READ-ONLY collection runner (role 02 worker; also callable from the API "run now").

* One CollectionRun row per (device, metric_family, scheduled_window) — the unique dedupe key makes
  duplicate polls impossible across worker replicas and restarts.
* Failures are recorded with a category, retried with bounded exponential backoff + jitter, and
  sent to DLQ when permanent or exhausted. A failure never writes a zero reading.
* The collector only performs read probes. There is no write path in this module.
"""
from __future__ import annotations

import ipaddress
import logging
import uuid
from datetime import datetime, timedelta, timezone

from typing import Callable

from . import outbox
from ..domain.events import EventEnvelope, new_event

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..database.models_phases import CollectionPlan, CollectionRun
from ..domain import collection as C
from ..domain.model import DataQuality, SourceType, TelemetryPoint

log = logging.getLogger("inm.collection")
NS = uuid.UUID("0b8b8a53-1c7e-4b1e-9d64-6f2f5f0e7c21")

METRIC_FAMILY_OF = {
    "cpu_percent": "system", "memory_percent": "system", "uptime_seconds": "system", "temp_max_c": "system",
    "fans_failed": "system", "psu_failed": "system",
    "util_5g_percent": "radios", "util_2g_percent": "radios", "noise_floor_dbm": "radios",
    "retry_percent": "rf_quality",
    "clients": "clients", "client_count": "clients",
    "ports_up": "interfaces", "ports_total": "interfaces", "port_errors_total": "interfaces",
    "port_errors_delta": "interfaces", "link_resets_delta": "interfaces", "max_port_util_percent": "interfaces",
    "port_rx_bps": "interfaces", "port_tx_bps": "interfaces", "port_util_percent": "interfaces",
    "poe_power_w": "poe", "poe_ports_powered": "poe", "lldp_neighbors": "lldp",
    # per-interface series (entity_type=port): one value per port per poll
    "port_link_up": "interfaces", "port_rx_errors": "interfaces", "port_tx_errors": "interfaces",
    "port_errors_delta": "interfaces", "port_link_resets_delta": "interfaces",
    "port_poe_w": "poe",
}
# Older names used by earlier releases; translated so nothing is silently mis-filed.
ALIASES = {"interfaces_up": "ports_up", "interfaces_total": "ports_total"}
UNITS = {"cpu_percent": "%", "memory_percent": "%", "util_5g_percent": "%", "util_2g_percent": "%", "retry_percent": "%",
         "uptime_seconds": "s", "clients": "count", "client_count": "count", "noise_floor_dbm": "dBm",
         "ports_up": "count", "ports_total": "count", "port_errors_total": "count", "port_errors_delta": "count",
         "link_resets_delta": "count", "max_port_util_percent": "%", "port_rx_bps": "bps", "port_tx_bps": "bps",
         "port_util_percent": "%", "poe_power_w": "W", "poe_ports_powered": "count", "lldp_neighbors": "count",
         "temp_max_c": "C", "fans_failed": "count", "psu_failed": "count",
         "port_link_up": "bool", "port_rx_errors": "count", "port_tx_errors": "count", "port_poe_w": "W",
         "port_link_resets_delta": "count"}


class CollectionFailure(Exception):
    def __init__(self, category: str, message: str = ""):
        super().__init__(message or category)
        self.category = category if category in C.FAILURE_CATEGORIES else "PARSE_ERROR"


Collector = Callable[[InmDevice, CollectionPlan, Session], dict]


def aruba_read_collector(device: InmDevice, plan: CollectionPlan, db: Session) -> dict:
    """Real read-only probe using the device's bound encrypted credential profile."""
    import httpx
    from fastapi import HTTPException

    from ..api.quick_devices import QuickProbe, _permit_probe, _probe_ap
    from ..aruba.errors import ArubaError
    from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
    from ..security.crypto import decrypt_secret

    binding = db.get(OnboardingDeviceCredential, device.id)
    profile = db.get(OnboardingCredential, binding.credential_id) if binding else None
    if profile is None or not getattr(profile, "enabled", True):
        raise CollectionFailure("AUTH_REJECTED", "No enabled credential profile bound to device.")
    addrs = device.mgmt_addresses or []
    if not addrs:
        raise CollectionFailure("TARGET_NOT_ALLOWED", "No management address recorded.")
    try:
        ip = ipaddress.IPv4Address(str(addrs[0]))
        _permit_probe(ip)
    except HTTPException as exc:
        raise CollectionFailure("TARGET_NOT_ALLOWED", str(exc.detail)) from None
    except ValueError:
        raise CollectionFailure("TARGET_NOT_ALLOWED", "Invalid management address.") from None
    probe = QuickProbe(family=device.family, site_name="Collection", management_ip=ip,
                       username=profile.username, password=decrypt_secret(profile.password_enc),
                       hostname=device.hostname or str(ip), serial_number=device.serial_number,
                       model=device.model or "Unknown")
    if device.family == "switch":
        from .change_executor import writer_for
        from .writers import WriterError
        try:
            w, _env = writer_for(db, device)
            w.open()
            try:
                t = w.telemetry()
            finally:
                w.close()
        except WriterError as exc:
            raise CollectionFailure({"AUTH_REJECTED": "AUTH_REJECTED", "REST_DISABLED": "REST_DISABLED",
                                     "TARGET_NOT_ALLOWED": "TARGET_NOT_ALLOWED", "PRECONDITION": "AUTH_REJECTED",
                                     "RATE_LIMITED": "RATE_LIMITED", "UNSUPPORTED_API": "UNSUPPORTED_API",
                                     "CONNECTION_LOST": "CONNECTION_LOST", "PARSE_ERROR": "PARSE_ERROR",
                                     "DEVICE_REJECTED": "PARSE_ERROR"}
                                    .get(exc.category, "UNREACHABLE"), str(exc)) from None
        if (t["identity"].get("serial_number") or device.serial_number).casefold() != device.serial_number.casefold():
            raise CollectionFailure("PARSE_ERROR", "Switch answered with a different serial; refusing to record.")
        return {"metrics": t["metrics"], "firmware": t["identity"].get("firmware"), "ports": t["ports"],
                "hardware": t["hardware"]}
    try:
        obs = _probe_ap(probe)
    except httpx.ConnectTimeout:
        raise CollectionFailure("UNREACHABLE") from None
    except httpx.ConnectError as exc:
        raise CollectionFailure("TLS_ERROR" if "SSL" in str(exc) or "certificate" in str(exc) else "UNREACHABLE") from None
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise CollectionFailure("AUTH_REJECTED" if code in (401, 403) else "RATE_LIMITED" if code == 429
                                else "REST_DISABLED" if code == 404 else "PARSE_ERROR") from None
    except ArubaError as exc:
        raise CollectionFailure("AUTH_REJECTED" if "auth" in str(exc).lower() or "login" in str(exc).lower()
                                else "PARSE_ERROR") from None
    except (httpx.HTTPError, OSError):
        raise CollectionFailure("UNREACHABLE") from None
    if (obs.get("serial_number") or "").casefold() != (device.serial_number or "").casefold():
        raise CollectionFailure("PARSE_ERROR", "Device answered with a different serial; refusing to record.")
    return {"metrics": obs.get("metrics") or {}, "firmware": obs.get("firmware")}


def demo_collector(device: InmDevice, plan: CollectionPlan, db: Session) -> dict:
    """DEMO devices are never contacted: switches read the labelled protocol emulator through the SAME
    CX telemetry code path as hardware; APs get synthetic readings."""
    if device.family == "switch":
        from .change_executor import writer_for
        w, _env = writer_for(db, device)
        w.open()
        try:
            t = w.telemetry()
        finally:
            w.close()
        return {"metrics": t["metrics"], "firmware": None, "demo": True, "ports": t["ports"], "hardware": t["hardware"]}
    import random
    r = random.Random(f"{device.id}:{int(datetime.now(timezone.utc).timestamp() // 300)}")
    m = {"cpu_percent": round(r.uniform(10, 60), 1), "memory_percent": round(r.uniform(40, 70), 1)}
    if device.family == "access_point":
        m.update(clients=float(r.randint(0, 30)), util_5g_percent=round(r.uniform(10, 90), 1),
                 util_2g_percent=round(r.uniform(20, 80), 1))
    return {"metrics": m, "firmware": None, "demo": True}


def _points(device: InmDevice, run_id: str, metrics: dict, families: set[str], now: datetime,
            interval: int) -> list[TelemetryPoint]:
    pts = []
    metrics = {ALIASES.get(k, k): v for k, v in metrics.items()}
    for name, value in sorted(metrics.items()):
        if METRIC_FAMILY_OF.get(name, "system") not in families or not isinstance(value, (int, float)):
            continue
        demo = device.source_type == "DEMO"
        pts.append(TelemetryPoint(device_id=device.id, metric_name=name, observed_at=now, value=float(value),
                                  unit=UNITS.get(name, "unit"),
                                  collection_method="DEMO_SYNTHETIC" if demo else "ARUBA_REST_READ",
                                  quality=DataQuality.OBSERVED, source_type=SourceType.DEMO if demo else SourceType.REAL,
                                  source_event_id=run_id, site_id=device.site_id, sample_period_s=interval,
                                  tags={"collector": "scheduled", "plan": "wp02"}))
    return pts


def _port_snapshot(db: Session, device: InmDevice, result: dict, now: datetime, port_points: list) -> dict:
    """Store the faceplate snapshot and derive rates from the previous counters (no negative deltas)."""
    from ..database.models_phases import DevicePortSnapshot
    from ..domain.alert_rules import port_rates
    snap = db.get(DevicePortSnapshot, device.id)
    extra: dict = {}
    if snap is not None:
        prev_t = snap.collected_at if snap.collected_at.tzinfo else snap.collected_at.replace(tzinfo=timezone.utc)
        rates = port_rates(snap.ports, result["ports"], (now - prev_t).total_seconds())
        for name, r in rates["ports"].items():
            for k, metric in (("rx_bps", "port_rx_bps"), ("tx_bps", "port_tx_bps"), ("util_percent", "port_util_percent")):
                if r.get(k) is not None:
                    port_points.append((metric, float(r[k]), name))
        prev_by = {q["name"]: q for q in snap.ports or []}
        for p in result["ports"]:
            p["rates"] = rates["ports"].get(p["name"])
            q = prev_by.get(p["name"])
            if q is None:
                continue
            cur_e = (p.get("rx_errors") or 0) + (p.get("tx_errors") or 0)
            old_e = (q.get("rx_errors") or 0) + (q.get("tx_errors") or 0)
            if cur_e >= old_e:                        # a counter reset yields no delta, never a negative one
                port_points.append(("port_errors_delta", float(cur_e - old_e), p["name"]))
            if isinstance(p.get("link_resets"), (int, float)) and isinstance(q.get("link_resets"), (int, float)) \
                    and p["link_resets"] >= q["link_resets"]:
                port_points.append(("port_link_resets_delta", float(p["link_resets"] - q["link_resets"]), p["name"]))
        for k in ("port_errors_delta", "link_resets_delta", "max_port_util_percent"):
            if rates[k] is not None:
                extra[k] = float(rates[k])
    else:
        snap = DevicePortSnapshot(device_id=device.id, ports=[], hardware={}, environment="")
        db.add(snap)
    for p in result["ports"]:          # per-interface state every poll (independent series per port, E2E-01/02)
        if p.get("link") in ("up", "down"):
            port_points.append(("port_link_up", 1.0 if p["link"] == "up" else 0.0, p["name"]))
        for k, metric in (("rx_errors", "port_rx_errors"), ("tx_errors", "port_tx_errors"), ("poe_w", "port_poe_w")):
            if isinstance(p.get(k), (int, float)):
                port_points.append((metric, float(p[k]), p["name"]))
    snap.collected_at, snap.ports, snap.hardware = now, result["ports"], result.get("hardware") or {}
    snap.environment = "EMULATED" if device.source_type == "DEMO" else "HARDWARE"
    return extra


def _event_id(run_id: str, kind: str) -> str:
    """Deterministic: re-collecting the same run never creates a second logical event."""
    return str(uuid.uuid5(NS, f"{run_id}:{kind}"))


def _telemetry_event(device: InmDevice, plan: CollectionPlan, runs: list, pts: list, metrics: dict, result: dict,
                     now: datetime, corr: str) -> EventEnvelope:
    from .telemetry.timescale import identity
    ev_id = _event_id(runs[0].id, "telemetry")
    points = []
    for p in pts:
        et, ei = identity(p)
        points.append({"device_id": p.device_id, "metric": p.metric_name, "value": p.value, "unit": p.unit,
                       "observed_at": p.observed_at.isoformat(), "collection_method": p.collection_method,
                       "quality": p.quality.value, "sample_period_s": p.sample_period_s, "tags": p.tags,
                       "entity_type": et, "entity_id": ei})
    alert_values = {k: v for k, v in metrics.items() if isinstance(v, (int, float))}
    alert_values["consecutive_failures"] = 0
    return new_event("device.telemetry.observed", source_service="collector", actor_id="scheduled-collector",
                     source_type="DEMO" if device.source_type == "DEMO" else "REAL", event_id=ev_id,
                     correlation_id=corr, occurred_at=now, site_id=device.site_id, device_id=device.id,
                     adapter_version=device.adapter,
                     payload={"collector": "scheduled", "plan_id": plan.id, "run_ids": [r.id for r in runs],
                              "families": sorted({r.metric_family for r in runs}), "points": points,
                              "expected_points": len(points), "alert_values": alert_values,
                              "firmware": result.get("firmware"),
                              "summary": f"{device.hostname or device.serial_number}: {len(points)} point(s)"})


def _failure_event(device: InmDevice, plan: CollectionPlan, runs: list, category: str, now: datetime, corr: str,
                   attempt: int) -> EventEnvelope:
    return new_event("device.poll.failed", source_service="collector", actor_id="scheduled-collector",
                     source_type="DEMO" if device.source_type == "DEMO" else "REAL",
                     event_id=_event_id(f"{runs[0].id}:{attempt}", "failure"), correlation_id=corr, occurred_at=now,
                     site_id=device.site_id, device_id=device.id, data_quality="UNAVAILABLE",
                     payload={"collector": "scheduled", "plan_id": plan.id, "run_ids": [r.id for r in runs],
                              "failure_category": category, "consecutive_failures": plan.consecutive_failures,
                              "summary": f"{device.hostname or device.serial_number}: poll failed ({category})"})


def run_due(db: Session, *, now: datetime | None = None, collector: Collector | None = None,
            store=None, worker: str = "worker", plan_ids: list[str] | None = None, manual: bool = False) -> dict:
    """Execute every due ACTIVE plan once. Returns a summary. Safe to call concurrently."""
    now = now or datetime.now(timezone.utc)
    collector = collector or aruba_read_collector
    q = select(CollectionPlan).where(CollectionPlan.state == "ACTIVE")
    if plan_ids:
        q = q.where(CollectionPlan.id.in_(plan_ids))
    summary = {"evaluated": 0, "succeeded": 0, "retry": 0, "dlq": 0, "skipped_duplicate": 0,
               "skipped_window": 0, "points_written": 0}
    queued: list[str] = []
    collected: list[str] = []
    monitored_ports: dict[str, set[str]] = {}
    for group in db.scalars(select(AppSetting).where(AppSetting.key.startswith("port-group:"))):
        for member in group.value.get("members", []):
            monitored_ports.setdefault(member["device_id"], set()).add(member["port"])
    for plan in list(db.scalars(q)):
        if plan_ids is None and not C.due(now, plan.next_due_at):
            continue
        if not C.in_window(now, plan.window_start_hour, plan.window_end_hour):
            summary["skipped_window"] += 1
            continue
        device = db.get(InmDevice, plan.device_id)
        if device is None:
            continue
        summary["evaluated"] += 1
        # an operator's explicit "poll now" is its own run; scheduled polls dedupe per interval window
        window = int(now.timestamp()) if manual else C.scheduled_window(now, plan.interval_seconds)
        runs: list[CollectionRun] = []
        nonce = uuid.uuid4().hex if manual else ""       # every explicit "poll now" is a distinct run
        for fam in plan.metric_families:
            key = C.dedupe_key(device.id, fam, window) if not manual else \
                C.dedupe_key(device.id, f"{fam}:manual:{nonce}", window)
            existing = db.scalar(select(CollectionRun).where(CollectionRun.dedupe_key == key))
            if existing is not None:
                retry_at = existing.retry_at
                if retry_at is not None and retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                if existing.state == "RETRY" and retry_at is not None and retry_at <= now:
                    existing.state, existing.attempt, existing.worker = "LEASED", existing.attempt + 1, worker
                    runs.append(existing)
                else:
                    summary["skipped_duplicate"] += 1
                continue
            run = CollectionRun(id=str(uuid.uuid5(NS, key)), plan_id=plan.id, device_id=device.id,
                                metric_family=fam, scheduled_window=window, dedupe_key=key, attempt=1,
                                state="LEASED", worker=worker, started_at=now)
            try:
                with db.begin_nested():
                    db.add(run)
                    db.flush()
            except IntegrityError:
                summary["skipped_duplicate"] += 1
                continue
            runs.append(run)
        if not runs:
            continue
        corr = runs[0].id            # one correlation id per poll: run -> event -> points -> log -> alerts -> UI
        try:
            result = (demo_collector if device.source_type == "DEMO" else collector)(device, plan, db)
        except CollectionFailure as exc:
            attempt = max(r.attempt for r in runs)
            action = C.next_action_after_failure(exc.category, attempt)
            delay = C.backoff_seconds(attempt)
            for r in runs:
                r.state = "DLQ" if action == "DLQ" else "RETRY"
                r.failure_category = exc.category
                r.finished_at = now
                r.retry_at = None if action == "DLQ" else now + timedelta(seconds=delay)
                r.correlation_id = corr
            plan.last_failure_at, plan.last_failure_category = now, exc.category
            plan.consecutive_failures += 1
            plan.next_due_at = (now + timedelta(seconds=delay)) if action == "RETRY" \
                else C.next_due_after(now, plan.interval_seconds)
            summary["dlq" if action == "DLQ" else "retry"] += 1
            ev = _failure_event(device, plan, runs, exc.category, now, corr, attempt)
            outbox.enqueue(db, ev)
            for r in runs:
                r.event_id = ev.event_id
            queued.append(ev.event_id)
            log.info("collection %s device=%s category=%s action=%s corr=%s", plan.id, device.id, exc.category,
                     action, corr)
            continue
        families = {r.metric_family for r in runs}
        metrics = dict(result.get("metrics") or {})
        port_points: list = []
        if result.get("ports") is not None:
            metrics.update(_port_snapshot(db, device, result, now, port_points))
        pts = _points(device, runs[0].id, metrics, families, now, plan.interval_seconds)
        selected_ports = monitored_ports.get(device.id)
        if selected_ports is not None:
            port_points = [(metric, value, port) for metric, value, port in port_points if port in selected_ports]
        if "interfaces" in families or "poe" in families:
            pts += [TelemetryPoint(device_id=device.id, metric_name=n, observed_at=now, value=v, unit=UNITS[n],
                                   collection_method=pts[0].collection_method if pts else
                                   ("DEMO_SYNTHETIC" if device.source_type == "DEMO" else "ARUBA_REST_READ"),
                                   quality=DataQuality.OBSERVED,
                                   source_type=SourceType.DEMO if device.source_type == "DEMO" else SourceType.REAL,
                                   source_event_id=runs[0].id, site_id=device.site_id,
                                   sample_period_s=plan.interval_seconds, tags={"port": port},
                                   entity_type="port", entity_id=port)
                    for n, v, port in port_points if METRIC_FAMILY_OF.get(n) in families]
        ev = _telemetry_event(device, plan, runs, pts, metrics, result, now, corr)
        outbox.enqueue(db, ev)
        for r in runs:
            # COLLECTED = read from the device, event durably queued; SUCCEEDED only after ingest verified storage
            r.state, r.finished_at, r.failure_category, r.retry_at = "COLLECTED", now, None, None
            r.event_id, r.correlation_id = ev.event_id, corr
            r.expected_points = sum(1 for p in pts if METRIC_FAMILY_OF.get(p.metric_name, "system") == r.metric_family)
        plan.next_due_at = C.next_due_after(now, plan.interval_seconds)
        queued.append(ev.event_id)
        collected.extend(r.id for r in runs)
    db.flush()
    if queued and outbox.mode() == "inprocess":
        outbox.relay(db, now=now, event_ids=queued)
    done = list(db.scalars(select(CollectionRun).where(CollectionRun.id.in_(collected)))) if collected else []
    summary["succeeded"] = len({r.plan_id for r in done if r.state == "SUCCEEDED"})
    summary["awaiting_persistence"] = len({r.plan_id for r in done if r.state == "COLLECTED"})
    summary["points_written"] = sum(r.points_written or 0 for r in done if r.state == "SUCCEEDED")
    summary["events_queued"] = len(queued)
    db.flush()
    return summary
