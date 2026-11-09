"""Quick read-only intake for actual access points and switches.

A manually entered REAL observation is not claimed to be API-verified. Optional
one-off Aruba REST reads require an explicit, narrow device CIDR allowlist.
The existing Phase 1 device-write lock remains mandatory.
"""
from __future__ import annotations

import ipaddress
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..aruba.errors import ArubaError
from ..database.models import User
from ..database.models_inm import DeviceCapability, InmDevice, InmSite
from ..database.session import get_db
from ..domain.model import DataQuality, SourceType, TelemetryPoint
from ..infrastructure.runtime import telemetry_store
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/v1/quick-devices", tags=["quick-devices"])

METRIC_UNITS = {
    "access_point": {"clients": "count", "cpu_percent": "%", "memory_percent": "%",
                     "util_5g_percent": "%", "util_2g_percent": "%", "uptime_seconds": "seconds"},
    "switch": {"ports_up": "count", "ports_total": "count", "poe_power_w": "W",
               "cpu_percent": "%", "memory_percent": "%", "uptime_seconds": "seconds"},
}
PERCENT_METRICS = {"cpu_percent", "memory_percent", "util_5g_percent", "util_2g_percent"}
COUNT_METRICS = {"clients", "ports_up", "ports_total", "uptime_seconds"}


class QuickDevice(BaseModel):
    family: Literal["access_point", "switch"]
    site_name: str = Field(min_length=1, max_length=100)
    hostname: str = Field(min_length=1, max_length=100)
    serial_number: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    firmware: str | None = Field(default=None, max_length=64)
    management_ip: ipaddress.IPv4Address | None = None
    status: Literal["up", "down", "unknown"] = "unknown"
    metrics: dict[str, float] = Field(default_factory=dict, max_length=12)

    @field_validator("site_name", "hostname", "model", "serial_number")
    @classmethod
    def strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v or any(ord(ch) < 32 for ch in v):
            raise ValueError("Provide printable, nonempty device details")
        return v

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, v: dict[str, float], info):
        kind = info.data.get("family")
        for name, number in v.items():
            if name not in METRIC_UNITS.get(kind, {}):
                raise ValueError(f"Unsupported metric for {kind}: {name}")
            if not (-1e6 < number < 1e9):
                raise ValueError(f"Metric out of range: {name}")
            if name in PERCENT_METRICS and not (0 <= number <= 100):
                raise ValueError(f"Percentage outside 0-100: {name}")
            if name in COUNT_METRICS and (number < 0 or int(number) != number):
                raise ValueError(f"Count must be a nonnegative integer: {name}")
        if v.get("ports_up", 0) > v.get("ports_total", float("inf")):
            raise ValueError("ports_up cannot exceed ports_total")
        return v


def record_observation(db: Session, body: QuickDevice, actor: str, request: Request,
                       *, evidence: str = "MANUAL", notes: str = "") -> dict:
    """Idempotent device upsert. All real inventory isolated from demo fixture identities."""
    now = datetime.now(timezone.utc)
    site = db.scalar(select(InmSite).where(InmSite.name == body.site_name, InmSite.source_type == "REAL"))
    if site is None:
        site = InmSite(name=body.site_name, source_type="REAL",
                       description="Imported real device inventory; no device writes")
        db.add(site)
        db.flush()

    dev = db.scalar(select(InmDevice).where(InmDevice.source_type == "REAL",
                                           InmDevice.vendor == "Aruba",
                                           func.lower(InmDevice.serial_number) == body.serial_number.lower()))
    if dev is not None and dev.family != body.family:
        raise HTTPException(409, "This serial number is already registered as a different device family")
    if dev is None:
        dev = InmDevice(source_type="REAL", environment="lab", vendor="Aruba",
                        family=body.family, serial_number=body.serial_number, adapter="manual_snapshot")
        db.add(dev)
    dev.hostname, dev.model, dev.firmware = body.hostname, body.model, body.firmware
    dev.mgmt_addresses = [str(body.management_ip)] if body.management_ip else []
    dev.site_id, dev.status = site.id, body.status
    dev.adapter = "aruba_readonly_probe" if evidence == "PROBED" else "manual_snapshot"
    dev.last_observed_at = now
    db.flush()
    caps = list(db.scalars(select(DeviceCapability).where(DeviceCapability.device_id == dev.id)))
    if not caps:
        db.add(DeviceCapability(device_id=dev.id, operation="inventory.read",
                                status="READ_ONLY" if evidence == "PROBED" else "UNVERIFIED",
                                evidence=f"{evidence}: {actor}; no configuration writes"))
    audit(db, "device_quick_import", username=actor, target=f"device:{dev.id}",
          detail=f"{body.family} {body.model} site={body.site_name} source={evidence} {notes[:60]}",
          request=request)
    db.flush()
    store = telemetry_store()
    if body.metrics and store is not None:
        event = str(uuid.uuid4())
        points = [
            TelemetryPoint(device_id=dev.id, site_id=site.id, metric_name=k,
                           observed_at=now, value=float(v), unit=METRIC_UNITS[body.family][k],
                           collection_method="ARUBA_REST_READ" if evidence == "PROBED" else "MANUAL_ENTRY",
                           quality=DataQuality.OBSERVED if evidence == "PROBED" else DataQuality.INFERRED,
                           source_type=SourceType.REAL, source_event_id=event,
                           tags={"intake": evidence, "operator": actor})
            for k, v in body.metrics.items()
        ]
        store.write_points(points)
    return {"id": dev.id, "source_type": "REAL", "evidence": evidence,
            "site": site.name, "hostname": dev.hostname, "family": dev.family,
            "metrics_recorded": list(body.metrics), "device_writes": "LOCKED"}


@router.post("", status_code=201)
def create_device(body: QuickDevice, request: Request, db: Session = Depends(get_db),
                  admin: User = Depends(require_role("admin"))):
    if not get_settings().device_write_lock:
        raise HTTPException(403, "Quick intake requires the global device-write safety lock")
    return record_observation(db, body, admin.username, request)


class BulkQuickDeviceInput(BaseModel):
    """Review all entries before saving. Bulk onboarding is MANUAL only."""
    devices: list[QuickDevice] = Field(min_length=1, max_length=100)


@router.post("/bulk", status_code=201)
def create_device_batch(body: BulkQuickDeviceInput, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_role("admin"))):
    """Import up to 100 AP/CX devices in one management-DB transaction.

    No hardware calls. Bulk telemetry is excluded until cross-database transaction
    reconciliation is available; add observations individually after import.
    """
    if not get_settings().device_write_lock:
        raise HTTPException(403, "Bulk intake requires the global device-write safety lock.")
    serials: set[str] = set()
    for row in body.devices:
        key = row.serial_number.strip().casefold()
        if key in serials:
            raise HTTPException(422, f"Duplicate serial number in batch: {row.serial_number}")
        serials.add(key)
        if row.metrics:
            raise HTTPException(422, "Bulk device import accepts inventory only; record metrics individually.")
    # Detect all cross-family collisions BEFORE writing any inventory rows.
    existing = list(db.scalars(select(InmDevice).where(
        InmDevice.source_type == "REAL", InmDevice.vendor == "Aruba",
        func.lower(InmDevice.serial_number).in_(serials))))
    indexed = {device.serial_number.casefold(): device for device in existing}
    for row in body.devices:
        old = indexed.get(row.serial_number.casefold())
        if old is not None and old.family != row.family:
            raise HTTPException(409, f"Serial {row.serial_number} already exists as {old.family}")
    results = []
    for row in body.devices:
        already = row.serial_number.casefold() in indexed
        result = record_observation(db, row, admin.username, request, evidence="MANUAL")
        results.append({"id": result["id"], "hostname": result["hostname"],
                        "serial_number": row.serial_number,
                        "action": "UPDATED" if already else "ADDED"})
    return {"count": len(results), "added": sum(x["action"] == "ADDED" for x in results),
            "updated": sum(x["action"] == "UPDATED" for x in results),
            "source_type": "REAL", "collection_method": "MANUAL",
            "device_writes": "LOCKED", "results": results}


class QuickProbe(BaseModel):
    family: Literal["access_point", "switch"]
    site_name: str = Field(min_length=1, max_length=100)
    management_ip: ipaddress.IPv4Address
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    # Required only as a fallback when the device API does not expose identity.
    hostname: str = Field(min_length=1, max_length=100)
    serial_number: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)


def _permit_probe(ip: ipaddress.IPv4Address) -> None:
    cidrs = os.environ.get("INM_READONLY_DEVICE_CIDRS", "").strip()
    if not cidrs:
        raise HTTPException(403, "Live polling is disabled. Add approved target CIDRs to INM_READONLY_DEVICE_CIDRS; manual intake works without network access.")
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise HTTPException(422, "Only explicitly allowed, private management IPv4 addresses can be probed.")
    try:
        allowed = [ipaddress.ip_network(x.strip(), strict=False) for x in cidrs.split(",") if x.strip()]
        if not allowed or len(allowed) > 20 or any(
            not isinstance(net, ipaddress.IPv4Network) or
            not net.is_private or net.prefixlen < 24
            for net in allowed
        ):
            raise ValueError("Oversized or nonprivate management allowlist")
    except ValueError:
        raise HTTPException(503, "Server target allowlist is invalid; use up to 20 private IPv4 /24–/32 ranges.")
    if not any(ip in net for net in allowed):
        raise HTTPException(403, "This device IP is not in the server administrator's approved management ranges.")


def _probe_ap(body: QuickProbe) -> dict:
    from ..aruba.client import ArubaClient, ArubaTarget
    from ..aruba.monitoring import collect_snapshot
    s = get_settings()
    with ArubaClient(ArubaTarget(str(body.management_ip), 4343, body.username, body.password),
                     timeout=min(s.aruba_timeout_seconds, 8), verify=s.tls_verify_arg) as client:
        client.login()
        snap = collect_snapshot(client)
    ap = next((a for a in snap.aps if a.get("ip") == str(body.management_ip)), None)
    ap = ap or (snap.aps[0] if len(snap.aps) == 1 else None)
    if ap is None:
        raise ValueError("Instant VC returned multiple APs; enter this AP as a manual snapshot or target its own IP.")
    metrics = {"clients": float(ap.get("client_count") or 0)}
    for key in ("cpu_percent", "memory_percent", "util_5g_percent", "util_2g_percent", "uptime_seconds"):
        if isinstance(ap.get(key), (int, float)):
            metrics[key] = float(ap[key])
    return {"hostname": ap.get("name") or body.hostname,
            "serial_number": ap.get("serial") or body.serial_number,
            "model": ap.get("model") or body.model, "firmware": snap.firmware,
            "status": ap.get("status") if ap.get("status") in ("up", "down") else "unknown",
            "metrics": metrics, "notes": f"Instant VC returned {len(snap.aps)} AP(s)"}


def _probe_cx(body: QuickProbe) -> dict:
    # ArubaOS-CX OpenAPI 10.14: POST /rest/v10.14/login with query username/password;
    # GET /system, GET /system/interfaces, POST /logout. No config paths are used.
    # Base URL is the host only: request paths below already carry /rest/v10.14 (a base path here would
    # double it to /rest/v10.14/rest/v10.14/... and every real switch would answer 404).
    url = f"https://{body.management_ip}"
    with httpx.Client(base_url=url, timeout=8.0, verify=get_settings().tls_verify_arg,
                      follow_redirects=False, trust_env=False) as client:
        response = client.post("/rest/v10.14/login",
                               params={"username": body.username, "password": body.password})
        response.raise_for_status()
        try:
            sysr = client.get("/rest/v10.14/system", params={"depth": 1})
            sysr.raise_for_status()
            data = sysr.json()
            if not isinstance(data, dict):
                raise ValueError("Unexpected CX system response")
            iface = client.get("/rest/v10.14/system/interfaces", params={"depth": 1})
            iface.raise_for_status()
            ports = iface.json()
            if not isinstance(ports, dict):
                ports = {}
            physical = [x for name, x in ports.items() if
                        isinstance(x, dict) and str(name).startswith("1/")]
            selected = physical if physical else [x for x in ports.values() if isinstance(x, dict)]
            ports_up = sum(1 for x in selected if str(x.get("oper_state", "")).lower() == "up")
            result = {"hostname": data.get("hostname") or body.hostname,
                      "serial_number": data.get("serial_number") or body.serial_number,
                      "model": data.get("platform_name") or body.model,
                      "firmware": data.get("software_version"),
                      "status": "up",
                      "metrics": {"ports_up": float(ports_up),
                                  "ports_total": float(len(physical) if physical else len(ports))},
                      "notes": "CX REST read-only system/interfaces"}
        finally:
            try:
                client.post("/rest/v10.14/logout")
            except httpx.HTTPError:
                pass
    return result


@router.post("/probe", status_code=201)
def probe_device(body: QuickProbe, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_role("admin"))):
    if not get_settings().device_write_lock:
        raise HTTPException(403, "Device-write safety lock required.")
    _permit_probe(body.management_ip)
    try:
        observation = _probe_ap(body) if body.family == "access_point" else _probe_cx(body)
        out = QuickDevice(family=body.family, site_name=body.site_name,
                          hostname=str(observation["hostname"])[:100], serial_number=str(observation["serial_number"])[:64],
                          model=str(observation["model"])[:64], firmware=str(observation.get("firmware") or "")[:64] or None,
                          management_ip=body.management_ip, status=observation["status"],
                          metrics=observation["metrics"])
    except (ArubaError, httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        # Never leak the request URL: AOS-CX login uses query parameters for credentials.
        audit(db, "device_probe_failed", username=admin.username,
              target=f"{body.family}:{body.management_ip}",
              detail=f"{type(exc).__name__}; read-only probe failed", request=request, success=False)
        db.commit()  # retain the failure audit when returning HTTP 502
        raise HTTPException(502, f"Read-only device probe failed ({type(exc).__name__}). Confirm REST enablement, TLS trust and reachability.")
    return record_observation(db, out, admin.username, request,
                              evidence="PROBED", notes=observation.get("notes", ""))
