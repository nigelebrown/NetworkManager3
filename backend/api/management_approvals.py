"""Admin-confirmed, exact-IP management access approvals.

The API NEVER edits host firewalls, Docker state, or /etc/inm. A restricted
systemd host reconciler reads requests from the management DB and applies the
egress policy. A root-owned read-only status file proves host application.
Only registered REAL devices with an assigned credential may be approved.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import AppSetting, User
from ..database.models_inm import InmDevice
from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
from ..database.session import get_db
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api/v1/management-access", tags=["management-access"])
APPROVAL_KEY = "inm.approved_device_ips"
APPLIED_FILE = Path("/run/inm-approvals/applied.json")
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(s) for s in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))
MAX_IPS = 1000


def valid_exact_ip(raw: str) -> str:
    """Reject public, special-use, network ranges, and noncanonical IPs."""
    try:
        addr = ipaddress.IPv4Address(raw)
    except (ValueError, ipaddress.AddressValueError):
        raise ValueError("Only exact RFC1918 management IPv4 addresses are eligible") from None
    if str(addr) != raw or not any(addr in network for network in PRIVATE_NETWORKS):
        raise ValueError("Only exact RFC1918 management IPv4 addresses are eligible")
    return str(addr)


def approved_ips(value: dict | None) -> list[str]:
    """Validate the entire admin approval set before it is ever used."""
    if not isinstance(value, dict):
        return []
    raw = value.get("ips", [])
    if not isinstance(raw, list) or len(raw) > MAX_IPS or any(not isinstance(i, str) for i in raw):
        raise ValueError("Malformed management IP approval set")
    result = sorted(set(valid_exact_ip(ip) for ip in raw), key=lambda x: int(ipaddress.IPv4Address(x)))
    return result


def host_applied_ips() -> set[str]:
    """Read ONLY host-applied state; never substitute a requested DB approval."""
    try:
        content = APPLIED_FILE.read_text(encoding="utf-8")
        if len(content) > 20000:
            return set()
        data = json.loads(content)
        return set(approved_ips(data))
    except (OSError, ValueError, json.JSONDecodeError):
        return set()


def approval_status(db: Session, ip: str) -> str:
    if ip in host_applied_ips():
        return "APPLIED"
    row = db.get(AppSetting, APPROVAL_KEY)
    try:
        return "PENDING" if ip in approved_ips(row.value if row else None) else "NOT_APPROVED"
    except ValueError:
        return "ERROR"


def _registered_target(db: Session, device_id: str, profile_id: str, ip: str) -> InmDevice:
    device = db.get(InmDevice, device_id)
    binding = db.get(OnboardingDeviceCredential, device_id)
    profile = db.get(OnboardingCredential, profile_id)
    if (device is None or device.source_type != "REAL" or
        not binding or binding.credential_id != profile_id or
        profile is None or not profile.enabled or
        profile.device_family != device.family or
        ip not in (device.mgmt_addresses or [])):
        raise HTTPException(404, "No registered real device with this assigned credential and management IP")
    # A repeated address must be resolved before authorising management traffic.
    devices = db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL")).all()
    if any(other.id != device.id and ip in (other.mgmt_addresses or []) for other in devices):
        raise HTTPException(409, "Duplicate management IP in inventory; resolve before approval")
    return device


def _safety_gate() -> None:
    s = get_settings()
    if not (s.mode == "live" and s.inm_profile == "lab-readonly" and
            s.device_write_lock and not s.allow_config_changes and not s.device_writes_permitted):
        raise HTTPException(423, "IP approval is permitted only in locked live/lab-readonly mode")


class ApproveIP(BaseModel):
    device_id: str = Field(min_length=36, max_length=36)
    profile_id: str = Field(min_length=36, max_length=36)
    management_ip: str = Field(min_length=7, max_length=15)
    confirmation: str = Field(min_length=1, max_length=48)


@router.post("/approve-ip")
def approve_device_ip(body: ApproveIP, request: Request, db: Session = Depends(get_db),
                      admin: User = Depends(require_role("admin"))):
    _safety_gate()
    try:
        ip = valid_exact_ip(body.management_ip)
    except ValueError:
        raise HTTPException(422, "Only exact RFC1918 management IPv4 addresses can be approved") from None
    if body.confirmation != f"APPROVE IP {ip}":
        raise HTTPException(422, f"Confirm exactly APPROVE IP {ip}")
    _registered_target(db, body.device_id, body.profile_id, ip)
    row = db.scalar(select(AppSetting).where(AppSetting.key == APPROVAL_KEY).with_for_update())
    if row is None:
        row = AppSetting(key=APPROVAL_KEY, value={"ips": []}, updated_by=admin.username)
        db.add(row)
        db.flush()
    try:
        current = approved_ips(row.value)
    except ValueError:
        raise HTTPException(503, "Existing management approval set is invalid") from None
    if ip not in current and len(current) >= MAX_IPS:
        raise HTTPException(409, "Maximum 1000 approved exact IPs reached")
    if ip not in current:
        row.value = {"ips": sorted([*current, ip], key=lambda x: int(ipaddress.IPv4Address(x)))}
        row.updated_by = admin.username
    audit(db, "management_ip_approval_requested", username=admin.username,
          target=f"device:{body.device_id}",
          detail=f"approved_exact_ip={ip}/32 profile={body.profile_id}; host enforcement pending",
          request=request)
    db.commit()
    return {"management_ip": ip, "prefix": 32, "state": approval_status(db, ip),
            "device_id": body.device_id, "write_lock": "ENGAGED"}


@router.get("/approval-status/{device_id}")
def device_ip_approval_status(device_id: str, profile_id: str, management_ip: str,
                              db: Session = Depends(get_db),
                              admin: User = Depends(require_role("admin"))):
    try:
        ip = valid_exact_ip(management_ip)
    except ValueError:
        raise HTTPException(422, "Invalid management IP") from None
    _registered_target(db, device_id, profile_id, ip)
    return {"management_ip": ip, "state": approval_status(db, ip),
            "prefix": 32, "write_lock": "ENGAGED"}
