"""Phase 2 pilot acceptance: evidence, not assumptions about Aruba equipment.

This module reads persisted inventory and sanitized baseline metadata only.
It performs no network I/O and cannot certify any physical device. No secrets.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def normalized_model(family: str, name: str | None) -> str | None:
    """Recognize the four approved pilot families, not arbitrary vendor models.

    Suffixes such as -RW/-US on AP names, and F/48G on CX names, are permitted.
    This classification is NOT a claim that firmware has passed acceptance.
    """
    text = (name or "").upper().strip()
    if family == "access_point":
        for code in ("505", "535"):
            if re.search(r"(?<!\d)AP[\s-]*" + code + r"(?!\d)", text):
                return "AP-" + code
    if family == "switch":
        for code in ("6100", "6200"):
            if re.search(r"(?<!\d)(?:CX|ARUBAOS-CX|ARUBA\s+CX)[\s-]*" + code + r"(?!\d)", text):
                return "CX " + code
    return None


def _field(key: str, available: bool, detail: str) -> dict:
    return {"field": key, "status": "OBSERVED" if available else "NOT_VERIFIED",
            "detail": detail}


def _rows(doc: dict, key: str) -> list:
    value = doc.get(key)
    return value if isinstance(value, list) else []


def build_acceptance(device, profile, binding, snapshot, drafts: list,
                     *, writes_locked: bool, hardware_certified: bool = False) -> dict:
    """Conservative readiness report from existing DB rows; no live probing."""
    family = device.family
    model = normalized_model(family, device.model)
    observed = device.adapter == "aruba_readonly_probe"
    raw = snapshot.config_json if snapshot and isinstance(snapshot.config_json, dict) else {}
    vlans, ssids, interfaces = (_rows(raw, name) for name in ("vlans", "ssids", "interfaces"))
    fields = []
    if family == "access_point":
        fields = [
            _field("ssid_profiles", bool(ssids), "SSID profiles read from Aruba Instant redacted running-config"),
            _field("ssid_vlan_mappings", any(isinstance(s, dict) and s.get("vlan") is not None for s in ssids),
                   "At least one SSID VLAN mapping found; empty or unsupported mappings remain unverified"),
            _field("vlans", bool(vlans), "VLAN IDs extracted from the reported SSID configuration"),
            _field("radio_channel_power", False,
                   "A radio configuration baseline is not collected by this onboarding adapter"),
            _field("channel_width", False,
                   "Actual 20/40 MHz radio channel widths were not read; draft editor is restricted to 20/40 MHz"),
        ]
    elif family == "switch":
        fields = [
            _field("vlans", bool(vlans), "CX REST VLAN entries returned by the device"),
            _field("interfaces", bool(interfaces), "CX REST interface entries returned by the device"),
            _field("interface_admin_state",
                   any(isinstance(p, dict) and p.get("admin_state") not in (None, "", "unknown")
                       for p in interfaces), "Interface administrative state reported by CX REST"),
            _field("interface_access_vlan",
                   any(isinstance(p, dict) and p.get("access_vlan") is not None for p in interfaces),
                   "No inferred access VLANs; not captured by current CX adapter unless explicitly returned"),
            _field("lacp_poe_lldp", False, "LACP, PoE and LLDP configuration are not captured by the pilot adapter"),
        ]
    else:
        fields = [_field("configuration", False, "Unsupported family for the read-only Aruba pilot")]

    missing = []
    if not model:
        missing.append("Confirm that the hardware is an approved AP-505/AP-535 or CX 6100/CX 6200 model.")
    if not observed:
        missing.append("Perform an explicitly authorized read-only REST discovery to verify device identity.")
    if not profile or not profile.enabled or not binding:
        missing.append("Assign an enabled reusable credential profile and test it on the authorized target.")
    if not snapshot:
        missing.append("Capture a read-only configuration baseline from the device.")
    missing.extend("Verify " + field["field"].replace("_", " ") + " against the physical device."
                   for field in fields if field["status"] != "OBSERVED")
    missing.append("Record model/firmware-specific physical acceptance; automated tests cannot certify hardware.")
    if not writes_locked:
        missing.append("Global device-write lock is OFF; do not use this pilot acceptance workflow.")
    stage = ("DRAFTED" if drafts else "BASELINE_CAPTURED" if snapshot
             else "REST_DISCOVERED" if observed else "MANUAL_REGISTERED")
    return {
        "device_id": device.id, "source_type": device.source_type,
        "family": family, "reported_model": device.model,
        "recognized_pilot_model": model,
        "reported_firmware": device.firmware,
        "inventory_evidence": "REST_DISCOVERY_RECORDED" if observed else "MANUAL_ENTRY",
        "last_observed_at": device.last_observed_at.isoformat() if device.last_observed_at else None,
        "credential": {
            "bound": bool(binding), "enabled": bool(profile and profile.enabled),
            "profile_id": profile.id if profile else None,
            "last_test_status": profile.last_test_status if profile else None,
        },
        "baseline": {
            "id": snapshot.id if snapshot else None,
            "captured_at": snapshot.captured_at.isoformat() if snapshot and snapshot.captured_at else None,
            "source": snapshot.source if snapshot else None,
            "completeness": snapshot.completeness if snapshot else "NOT_CAPTURED",
            "warning": snapshot.warning if snapshot else None,
        },
        "draft_count": len(drafts),
        "field_checks": fields,
        "stage": stage,
        "operator_pilot_eligible": bool(model and writes_locked),
        "hardware_certified": bool(hardware_certified),
        "device_writes": "LOCKED" if writes_locked else "UNSAFE",
        "remaining_actions": missing,
    }
