"""Process-driven Aruba AP / CX onboarding with reusable encrypted credentials.

Steps: select/create credential -> authorize target -> read-only discovery ->
review found equipment -> register -> capture a redacted baseline -> save drafts.
No device configuration write endpoint exists. REST probes are opt-in and use
only explicit RFC1918 allowlisted /24-/32 management targets.
"""
from __future__ import annotations

from ..infrastructure.posture import read_only, write_posture

import ipaddress
import json
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..aruba.errors import ArubaError
from ..aruba.client import ArubaClient, ArubaTarget
from ..aruba.configuration import fetch_running_config, parse_running_config
from ..aruba.monitoring import collect_snapshot
from ..config import get_settings
from ..database.models import User
from ..database.models_inm import InmDevice
from ..domain.phase2_acceptance import build_acceptance
from ..database.models_onboarding import (
    OnboardingCredential, OnboardingDiscovery, OnboardingDeviceCredential,
    OnboardingConfigSnapshot, OnboardingConfigDraft,
)
from ..database.session import get_db
from ..security.crypto import encrypt_secret, decrypt_secret
from ..security.deps import audit, require_role
from .quick_devices import QuickDevice, QuickProbe, _permit_probe, _probe_ap, _probe_cx, record_observation

router = APIRouter(prefix="/api/v1/onboarding", tags=["guided-onboarding"])

FAMILY = Literal["access_point", "switch"]
PROTOCOL = {"access_point": "aruba_instant_rest", "switch": "aruba_cx_rest"}


def _locked():
    """Read-only/draft module: no device-write path, so it stays usable whether or not the
    operator has enabled manual config changes (device_writes_permitted)."""
    return None


def _profile(db: Session, profile_id: str, family: str | None = None) -> OnboardingCredential:
    obj = db.get(OnboardingCredential, profile_id)
    if obj is None:
        raise HTTPException(404, "Credential profile not found")
    if not obj.enabled:
        raise HTTPException(409, "This credential profile is disabled")
    if family and family != obj.device_family:
        raise HTTPException(422, "Select a credential profile matching the device family")
    return obj


def _public_profile(p: OnboardingCredential) -> dict:
    # Password or encrypted cipher NEVER appears in JSON responses.
    return {
        "id": p.id, "name": p.name, "device_family": p.device_family,
        "protocol": p.protocol, "username": p.username,
        "description": p.description, "enabled": p.enabled,
        "has_password": bool(p.password_enc), "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "last_test_status": p.last_test_status,
        "last_tested_at": p.last_tested_at.isoformat() if p.last_tested_at else None,
    }


class CredentialInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    device_family: FAMILY
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name", "username")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Provide a nonempty, printable value")
        return value


class CredentialRotate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class TargetCheck(BaseModel):
    management_ip: ipaddress.IPv4Address


@router.get("/credentials")
def list_credentials(db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    return [_public_profile(p) for p in db.scalars(
        select(OnboardingCredential).order_by(OnboardingCredential.device_family, OnboardingCredential.name)
    )]


@router.post("/credentials", status_code=201)
def add_credential(body: CredentialInput, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_role("admin"))):
    _locked()
    if db.scalar(select(OnboardingCredential).where(func.lower(OnboardingCredential.name) == body.name.lower())):
        raise HTTPException(409, "A credential profile with that name already exists")
    profile = OnboardingCredential(
        name=body.name, device_family=body.device_family,
        protocol=PROTOCOL[body.device_family], username=body.username,
        password_enc=encrypt_secret(body.password), description=body.description,
        created_by=admin.username,
    )
    db.add(profile)
    db.flush()
    audit(db, "onboarding_credential_created", username=admin.username, target=profile.id,
          detail=f"{profile.name} {profile.protocol}; password encrypted and never returned",
          request=request)
    return _public_profile(profile)


@router.post("/credentials/{profile_id}/rotate")
def rotate_credential(profile_id: str, body: CredentialRotate, request: Request,
                      db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    profile = _profile(db, profile_id)
    if body.username is not None:
        profile.username = body.username.strip()
    profile.password_enc = encrypt_secret(body.password)
    profile.updated_at = datetime.now(timezone.utc)
    profile.last_test_status = None
    audit(db, "onboarding_credential_rotated", username=admin.username, target=profile.id,
          detail=f"{profile.name}: new encrypted secret applies to all bound devices",
          request=request)
    return _public_profile(profile)


@router.post("/credentials/{profile_id}/disable")
def disable_credential(profile_id: str, request: Request, db: Session = Depends(get_db),
                       admin: User = Depends(require_role("admin"))):
    _locked()
    profile = _profile(db, profile_id)
    profile.enabled = False
    profile.updated_at = datetime.now(timezone.utc)
    audit(db, "onboarding_credential_disabled", username=admin.username, target=profile.id,
          detail=f"{profile.name} disabled; devices remain in inventory", request=request)
    return _public_profile(profile)


def _probe(profile: OnboardingCredential, ip: ipaddress.IPv4Address) -> dict:
    # Password decrypted only within this server-side call. Never log it.
    probe = QuickProbe(
        family=profile.device_family, site_name="Discovery",
        management_ip=ip, username=profile.username,
        password=decrypt_secret(profile.password_enc),
        hostname=str(ip), serial_number=f"UNKNOWN-{ip}",
        model="Unknown",
    )
    return _probe_ap(probe) if profile.device_family == "access_point" else _probe_cx(probe)


@router.post("/credentials/{profile_id}/test")
def test_credential(profile_id: str, body: TargetCheck, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    profile = _profile(db, profile_id)
    _permit_probe(body.management_ip)
    try:
        observation = _probe(profile, body.management_ip)
    except (ArubaError, httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        profile.last_test_status = "FAILED"
        profile.last_tested_at = datetime.now(timezone.utc)
        audit(db, "onboarding_credential_test_failed", username=admin.username, target=profile.id,
              detail=f"{profile.protocol} target={body.management_ip} result={type(exc).__name__}",
              request=request, success=False)
        db.commit()
        raise HTTPException(502, "Read-only credential test failed. Confirm authorization, TLS, REST enablement and connectivity.")
    profile.last_test_status = "SUCCESS"
    profile.last_tested_at = datetime.now(timezone.utc)
    audit(db, "onboarding_credential_test", username=admin.username, target=profile.id,
          detail=f"{profile.protocol} target={body.management_ip} success; no device configuration writes",
          request=request)
    return {"status": "SUCCESS", "profile_id": profile.id, "target": str(body.management_ip),
            "hostname": observation.get("hostname"), "model": observation.get("model"),
            **read_only()}


class DiscoveryRequest(BaseModel):
    credential_id: str
    scope: str = Field(min_length=1, max_length=48)
    site_name: str = Field(min_length=1, max_length=100)
    rate_per_s: int = Field(default=16, ge=1, le=64)
    workflow_id: str | None = None


def _job_out(job, **extra) -> dict:
    from ..infrastructure import discovery_jobs as DJ
    return {"id": job.id, "status": job.status, "results": job.results or [], "targets": len(job.targets or []),
            "site_name": job.site_name, "credential_id": job.credential_id, "scope": job.scope,
            "workflow_id": job.workflow_id, "progress": DJ.progress(job) if job.targets else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None, **read_only(), **extra}


def _targets(scope: str) -> list[ipaddress.IPv4Address]:
    try:
        net = ipaddress.ip_network(scope.strip(), strict=False)
    except ValueError:
        raise HTTPException(422, "Enter an IPv4 address or an authorized CIDR.")
    if not isinstance(net, ipaddress.IPv4Network):
        raise HTTPException(422, "Only IPv4 management targets are supported")
    if net.num_addresses > 256:
        raise HTTPException(422, "Scan at most one /24 (256 addresses) per job. Larger plans must be divided into approved /24 batches.")
    ips = list(net.hosts()) if net.prefixlen < 31 else list(net)
    for ip in ips:
        _permit_probe(ip)  # check ALL destinations before initiating traffic
    return ips


@router.post("/discover", status_code=201)
def discover_devices(body: DiscoveryRequest, request: Request,
                     db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    profile = _profile(db, body.credential_id)
    addresses = _targets(body.scope)
    if len(addresses) > 8:
        # Durable job (E2E-07): the row holds the work list and cursor; a worker (or the standalone scheduler)
        # leases it, survives API/worker restarts and honours pause/cancel. No in-process threads.
        from ..infrastructure import discovery_jobs as DJ
        job, created = DJ.create(db, credential_id=profile.id, family=profile.device_family, scope=body.scope,
                                 site_name=body.site_name.strip(), targets=[str(a) for a in addresses],
                                 created_by=admin.username, rate_per_s=body.rate_per_s,
                                 workflow_id=body.workflow_id)
        if created:
            audit(db, "onboarding_discovery_queued", username=admin.username, target=job.id,
                  detail=f"profile={profile.id} scope={body.scope} targets={len(addresses)} "
                         f"rate={job.rate_per_s}/s (durable job, read-only)", request=request)
        db.commit()
        return _job_out(job, duplicate_of_active=not created)
    found: list[dict] = []
    for address in addresses:
        try:
            observed = _probe(profile, address)
            serial = str(observed.get("serial_number") or "").strip()
            # Missing device serials are not guessed. They cannot be registered
            # until device identity is verified, even if a REST endpoint answered.
            if serial.startswith("UNKNOWN-"):
                serial = ""
            existing = db.scalar(select(InmDevice).where(
                InmDevice.source_type == "REAL", InmDevice.vendor == "Aruba",
                func.lower(InmDevice.serial_number) == serial.lower())) if serial else None
            found.append({
                "management_ip": str(address), "status": "FOUND", "family": profile.device_family,
                "hostname": str(observed.get("hostname") or address)[:100],
                "model": str(observed.get("model") or "Unknown")[:64],
                "serial_number": serial[:64],
                "firmware": str(observed.get("firmware") or "")[:64] or None,
                "existing_device_id": existing.id if existing else None,
                "enrollable": bool(serial),
                "result_note": "API identity read; existing device" if existing else "API identity read",
            })
        except (ArubaError, httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
            found.append({"management_ip": str(address), "status": "FAILED",
                          "result_note": f"Read-only REST probe failed ({type(exc).__name__}).",
                          "enrollable": False})
    job = OnboardingDiscovery(credential_id=profile.id, site_name=body.site_name.strip(),
                             scope=body.scope, device_family=profile.device_family,
                             results=found, created_by=admin.username, status="SCANNED")
    db.add(job)
    db.flush()
    audit(db, "onboarding_discovery_finished", username=admin.username, target=job.id,
          detail=f"profile={profile.id}, targets={len(addresses)}, found={sum(x['status']=='FOUND' for x in found)}, writes locked",
          request=request)
    return {"id": job.id, "status": job.status, "results": job.results,
            "site_name": job.site_name, "credential_id": job.credential_id,
            **read_only()}


def _tcp_open(ip: str, port: int, timeout: float = 1.0) -> bool:
    import socket
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _identify(profile_id: str, ip: str) -> dict:
    """Read-only identity probe of one reachable address (own DB session; credential decrypted per call)."""
    from ..database.session import session_scope
    with session_scope() as db:
        profile = db.get(OnboardingCredential, profile_id)
        port = 4343 if profile.device_family == "access_point" else 443
        if not _tcp_open(ip, port):
            return {"management_ip": ip, "status": "NO_RESPONSE", "enrollable": False,
                    "result_note": f"TCP {port} closed or filtered"}
        try:
            observed = _probe(profile, ipaddress.IPv4Address(ip))
        except (ArubaError, httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
            return {"management_ip": ip, "status": "FAILED", "enrollable": False,
                    "result_note": f"Read-only REST probe failed ({type(exc).__name__})."}
        serial = str(observed.get("serial_number") or "").strip()
        if serial.startswith("UNKNOWN-"):
            serial = ""
        existing = db.scalar(select(InmDevice).where(
            InmDevice.source_type == "REAL", InmDevice.vendor == "Aruba",
            func.lower(InmDevice.serial_number) == serial.lower())) if serial else None
        return {"management_ip": ip, "status": "FOUND", "family": profile.device_family,
                "hostname": str(observed.get("hostname") or ip)[:100], "model": str(observed.get("model") or "Unknown")[:64],
                "serial_number": serial[:64], "firmware": str(observed.get("firmware") or "")[:64] or None,
                "existing_device_id": existing.id if existing else None, "enrollable": bool(serial),
                "result_note": "API identity read; existing device" if existing else "API identity read"}


@router.get("/discoveries/{job_id}")
def get_discovery(job_id: str, db: Session = Depends(get_db),
                  admin: User = Depends(require_role("admin"))):
    job = db.get(OnboardingDiscovery, job_id)
    if job is None:
        raise HTTPException(404, "Discovery not found")
    return _job_out(job)


@router.post("/discoveries/{job_id}/control/{action}")
def control_discovery(job_id: str, action: str, request: Request, db: Session = Depends(get_db),
                      admin: User = Depends(require_role("admin"))):
    """pause | resume | cancel | retry a durable discovery job."""
    from ..infrastructure import discovery_jobs as DJ
    job = db.get(OnboardingDiscovery, job_id)
    if job is None:
        raise HTTPException(404, "Discovery not found")
    if not job.targets:
        raise HTTPException(409, "Small synchronous discoveries cannot be controlled; run a new discovery.")
    try:
        DJ.set_state(db, job, action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, f"onboarding_discovery_{action}", username=admin.username, target=job.id,
          detail=f"status={job.status}", request=request)
    return _job_out(job)


class RegisterDiscovered(BaseModel):
    indices: list[int] = Field(min_length=1, max_length=64)


@router.post("/discoveries/{job_id}/register", status_code=201)
def register_discovered(job_id: str, body: RegisterDiscovered, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    job = db.get(OnboardingDiscovery, job_id)
    if not job:
        raise HTTPException(404, "Discovery not found")
    _profile(db, job.credential_id, job.device_family)
    if len(body.indices) != len(set(body.indices)):
        raise HTTPException(422, "Duplicate selection indices")
    selected = []
    for i in body.indices:
        if i < 0 or i >= len(job.results):
            raise HTTPException(422, "Invalid discovered-device selection")
        row = job.results[i]
        if row.get("status") != "FOUND" or not row.get("enrollable") or not row.get("serial_number"):
            raise HTTPException(422, "A selected device has no verified identity")
        selected.append(row)
    if len({row["serial_number"].casefold() for row in selected}) != len(selected):
        raise HTTPException(422, "Duplicate serial numbers among selected devices")
    registered = []
    for row in selected:
        payload = QuickDevice(family=job.device_family, site_name=job.site_name,
                              hostname=row["hostname"], serial_number=row["serial_number"],
                              model=row["model"], firmware=row.get("firmware"),
                              management_ip=row["management_ip"], status="up",
                              metrics={})
        result = record_observation(db, payload, admin.username, request, evidence="PROBED",
                                    notes="guided read-only discovery")
        binding = db.get(OnboardingDeviceCredential, result["id"])
        if binding is None:
            db.add(OnboardingDeviceCredential(device_id=result["id"],
                                              credential_id=job.credential_id, bound_by=admin.username))
        else:
            binding.credential_id = job.credential_id
            binding.bound_by = admin.username
        registered.append({"device_id": result["id"], "hostname": result["hostname"],
                           "serial_number": row["serial_number"], "config_status": "NOT_CAPTURED"})
    job.status = "REGISTERED"
    audit(db, "onboarding_registered", username=admin.username, target=job.id,
          detail=f"registered={len(registered)}, shared_credential={job.credential_id}; device writes locked",
          request=request)
    return {"registered": registered, **read_only(), "next_step": "CAPTURE_BASELINE"}


def _cx_baseline(host: str, username: str, password: str) -> dict:
    """Read-only, secret-free CX baseline through the shared CxSession (E2E-04/05/09/16)."""
    from ..infrastructure.writers import WriterError
    from ..infrastructure.writers.aruba_cx import CxSession
    try:
        with CxSession(host, username, password, timeout=8.0, verify=get_settings().tls_verify_for(host)) as s:
            vdata = s.request("GET", "/system/vlans", params={"depth": 1}).json()
            pdata = s.request("GET", "/system/interfaces", params={"depth": 1}).json()
    except WriterError as exc:
        raise RuntimeError(f"{exc.category}: {exc}") from None
    # Whitelist only configuration-relevant, non-secret fields; no raw device responses, credentials,
    # ACLs or interface descriptions.
    vlans = []
    for name, item in (vdata.items() if isinstance(vdata, dict) else []):
        if isinstance(item, dict):
            vid = item.get("id")
            if isinstance(vid, int) and 1 <= vid <= 4094:
                vlans.append({"id": vid, "name": str(item.get("name") or "")[:64]})
        elif str(name).isdigit() and 1 <= int(name) <= 4094:
            vlans.append({"id": int(name), "name": ""})
    interfaces = []
    for name, item in (pdata.items() if isinstance(pdata, dict) else []):
        if len(interfaces) >= 128:
            break
        if isinstance(item, dict) and len(str(name)) <= 40:
            interfaces.append({"name": str(name),
                               "admin_state": str(item.get("admin") or item.get("admin_state") or "unknown")[:24],
                               "oper_state": str(item.get("link_state") or item.get("oper_state") or "unknown")[:24]})
    return {"vlans": vlans[:256], "interfaces": interfaces, "ssids": []}


class CaptureInput(BaseModel):
    credential_id: str | None = None


@router.post("/devices/{device_id}/capture", status_code=201)
def capture_baseline(device_id: str, body: CaptureInput, request: Request,
                     db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    device = db.get(InmDevice, device_id)
    if not device or device.source_type != "REAL" or device.vendor != "Aruba":
        raise HTTPException(404, "Real Aruba device not found")
    binding = db.get(OnboardingDeviceCredential, device_id)
    profile_id = body.credential_id or (binding.credential_id if binding else None)
    if not profile_id:
        raise HTTPException(422, "Select an existing credential profile to read this device")
    profile = _profile(db, profile_id, device.family)
    if not device.mgmt_addresses:
        raise HTTPException(422, "Device has no management IP")
    try:
        ip = ipaddress.IPv4Address(device.mgmt_addresses[0])
    except ipaddress.AddressValueError:
        raise HTTPException(422, "Management address must be a valid IPv4 address")
    _permit_probe(ip)
    try:
        password = decrypt_secret(profile.password_enc)
        if device.family == "access_point":
            with ArubaClient(ArubaTarget(str(ip), 4343, profile.username, password),
                             timeout=min(8, get_settings().aruba_timeout_seconds),
                             verify=get_settings().tls_verify_for(str(ip))) as client:
                client.login()
                text = fetch_running_config(client)  # redacted by vendor parser
                config = parse_running_config(text)
            warning = "Read-only, parsed and redacted Instant configuration. Not a restorable full backup."
        else:
            config = _cx_baseline(str(ip), profile.username, password)
            warning = "Partial CX REST baseline (VLANs and interface states); not a full backup."
    except (ArubaError, httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        audit(db, "onboarding_capture_failed", username=admin.username, target=device.id,
              detail=f"read-only configuration capture failed ({type(exc).__name__})",
              success=False, request=request)
        db.commit()
        raise HTTPException(502, "Configuration read failed. Verify REST, TLS, profile and device reachability.")
    snapshot = OnboardingConfigSnapshot(device_id=device.id, collected_by=admin.username,
                                        source=profile.protocol, completeness="PARTIAL",
                                        config_json=config, warning=warning)
    db.add(snapshot)
    if binding is None:
        db.add(OnboardingDeviceCredential(device_id=device.id, credential_id=profile.id,
                                          bound_by=admin.username))
    db.flush()
    audit(db, "onboarding_baseline_captured", username=admin.username, target=device.id,
          detail=f"snapshot={snapshot.id} source={profile.protocol} only read-only data; secrets excluded",
          request=request)
    return {"snapshot_id": snapshot.id, "device_id": device.id, "source": snapshot.source,
            "completeness": snapshot.completeness, "config": snapshot.config_json,
            "warning": warning, **read_only()}


@router.get("/devices/{device_id}/config")
def device_config(device_id: str, db: Session = Depends(get_db),
                  admin: User = Depends(require_role("admin"))):
    device = db.get(InmDevice, device_id)
    if not device or device.source_type != "REAL":
        raise HTTPException(404, "Real device not found")
    binding = db.get(OnboardingDeviceCredential, device_id)
    snap = db.scalar(select(OnboardingConfigSnapshot).where(
        OnboardingConfigSnapshot.device_id == device_id
    ).order_by(OnboardingConfigSnapshot.captured_at.desc(), OnboardingConfigSnapshot.id.desc()))
    drafts = list(db.scalars(select(OnboardingConfigDraft).where(
        OnboardingConfigDraft.device_id == device_id
    ).order_by(OnboardingConfigDraft.created_at.desc()).limit(20)))
    return {
        "device_id": device.id, "credential_id": binding.credential_id if binding else None,
        "baseline": {"id": snap.id, "captured_at": snap.captured_at.isoformat(),
                     "completeness": snap.completeness, "config": snap.config_json,
                     "warning": snap.warning} if snap else None,
        "drafts": [{"id": d.id, "state": d.state, "created_at": d.created_at.isoformat(),
                    "proposed": d.proposed_json} for d in drafts],
        "hardware_writes": write_posture()["hardware_writes"],
    }


def _device_acceptance(db: Session, device: InmDevice) -> dict:
    binding = db.get(OnboardingDeviceCredential, device.id)
    credential = db.get(OnboardingCredential, binding.credential_id) if binding else None
    snapshot = db.scalar(select(OnboardingConfigSnapshot).where(
        OnboardingConfigSnapshot.device_id == device.id
    ).order_by(OnboardingConfigSnapshot.captured_at.desc(),
               OnboardingConfigSnapshot.id.desc()))
    drafts = list(db.scalars(select(OnboardingConfigDraft).where(
        OnboardingConfigDraft.device_id == device.id
    ).order_by(OnboardingConfigDraft.created_at.desc()).limit(100)))
    from ..infrastructure.posture import certification_summary
    return build_acceptance(device, credential, binding, snapshot, drafts,
                            writes_locked=get_settings().device_write_lock,
                            hardware_certified=certification_summary(db, device)["hardware_certified"])


@router.get("/devices/{device_id}/acceptance")
def device_acceptance_report(device_id: str, db: Session = Depends(get_db),
                             admin: User = Depends(require_role("admin"))):
    """Auditable no-network-I/O evidence report. NOT hardware certification."""
    device = db.get(InmDevice, device_id)
    if not device or device.source_type != "REAL" or device.vendor != "Aruba":
        raise HTTPException(404, "Real Aruba device not found")
    return _device_acceptance(db, device)


@router.get("/acceptance")
def fleet_acceptance_report(db: Session = Depends(get_db),
                            admin: User = Depends(require_role("admin"))):
    """Pilot acceptance summary, limited to 250 devices to bound database work."""
    devices = list(db.scalars(select(InmDevice).where(
        InmDevice.source_type == "REAL", InmDevice.vendor == "Aruba"
    ).order_by(InmDevice.last_observed_at.desc()).limit(250)))
    reports = [_device_acceptance(db, d) for d in devices]
    return {
        "device_count": len(reports),
        "limit": 250,
        "hardware_certified": sum(1 for r in reports if r["hardware_certified"]),
        **read_only(),
        "stages": {stage: sum(r["stage"] == stage for r in reports)
                   for stage in ("MANUAL_REGISTERED", "REST_DISCOVERED",
                                 "BASELINE_CAPTURED", "DRAFTED")},
        "devices": [{k: r[k] for k in ("device_id", "family", "reported_model",
                   "recognized_pilot_model", "inventory_evidence", "stage",
                   "hardware_certified")} for r in reports],
    }


class DraftInput(BaseModel):
    proposed: dict = Field(max_length=3)


def _safe_draft(doc: dict) -> dict:
    if set(doc) - {"vlans", "ssids", "interfaces"}:
        raise HTTPException(422, "Draft only supports VLANs, SSIDs and interface state fields")
    if len(json.dumps(doc)) > 30000:
        raise HTTPException(422, "Draft exceeds 30 KB")
    if "vlans" in doc:
        if not isinstance(doc["vlans"], list) or len(doc["vlans"]) > 256:
            raise HTTPException(422, "Provide up to 256 VLANs")
        for item in doc["vlans"]:
            if isinstance(item, int):
                vid = item
            elif isinstance(item, dict) and set(item) <= {"id", "name"}:
                vid = item.get("id")
            else:
                raise HTTPException(422, "Invalid VLAN draft entry")
            if type(vid) is not int or not 1 <= vid <= 4094:
                raise HTTPException(422, "VLAN IDs must be 1-4094")
    if "ssids" in doc:
        if not isinstance(doc["ssids"], list) or len(doc["ssids"]) > 32:
            raise HTTPException(422, "Provide up to 32 SSID drafts")
        for item in doc["ssids"]:
            if not isinstance(item, dict) or set(item) - {"profile", "essid", "vlan", "rf_band", "channel_width_mhz", "enabled"}:
                raise HTTPException(422, "Unsupported SSID field; secrets cannot be drafted here")
            if item.get("channel_width_mhz") is not None and item["channel_width_mhz"] not in (20, 40):
                raise HTTPException(422, "Only 20 MHz and 40 MHz wireless channel widths are permitted")
            if "vlan" in item and item["vlan"] is not None and (type(item["vlan"]) is not int or not 1 <= item["vlan"] <= 4094):
                raise HTTPException(422, "SSID VLAN must be 1-4094")
    if "interfaces" in doc:
        if not isinstance(doc["interfaces"], list) or len(doc["interfaces"]) > 128:
            raise HTTPException(422, "Too many interface draft entries")
        for item in doc["interfaces"]:
            if not isinstance(item, dict) or set(item) - {"name", "admin_state", "access_vlan", "trunk_vlans"}:
                raise HTTPException(422, "Unsupported interface field")
            if "access_vlan" in item and item["access_vlan"] is not None and (type(item["access_vlan"]) is not int or not 1 <= item["access_vlan"] <= 4094):
                raise HTTPException(422, "Invalid interface access VLAN")
    return doc


@router.post("/devices/{device_id}/drafts", status_code=201)
def create_draft(device_id: str, body: DraftInput, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _locked()
    device = db.get(InmDevice, device_id)
    if not device or device.source_type != "REAL":
        raise HTTPException(404, "Real device not found")
    doc = _safe_draft(body.proposed)
    baseline = db.scalar(select(OnboardingConfigSnapshot).where(
        OnboardingConfigSnapshot.device_id == device.id
    ).order_by(OnboardingConfigSnapshot.captured_at.desc()))
    if baseline is None:
        raise HTTPException(409, "Capture a read-only device configuration baseline before creating a draft")
    draft = OnboardingConfigDraft(device_id=device.id, baseline_id=baseline.id,
                                  proposed_json=doc, created_by=admin.username, state="DRAFT")
    db.add(draft)
    db.flush()
    audit(db, "onboarding_config_draft_saved", username=admin.username, target=device.id,
          detail=f"draft={draft.id}, baseline={baseline.id}; not deployed", request=request)
    return {"id": draft.id, "state": "DRAFT", "hardware_writes": write_posture()["hardware_writes"], "proposed": doc}
