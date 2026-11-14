"""WP-10 / GAP-14 — vendor-neutral adapter contract and capability registry (pure).

Every adapter exposes the same operations. Anything not implemented returns a structured
UNSUPPORTED result; no adapter can bypass the central write gate (writes go through
change_control.execution_gate, which this module never calls around).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OPERATIONS = ("probe_identity", "get_capabilities", "collect", "read_config_summary",
              "read_audit_logs", "run_diagnostics", "backup_full_config", "validate_change", "apply_change",
              "readback", "rollback", "restore_full_config")
WRITE_OPERATIONS = {"apply_change", "rollback", "restore_full_config"}
# Software implementation state (what the code does). Hardware certification is a SEPARATE fact recorded per
# adapter + model family + firmware train + environment by the certification workflow (E2E-08/12).
SOFTWARE_STATES = ("IMPLEMENTED", "IMPLEMENTED_INVERSE", "GUIDED", "NOT_IMPLEMENTED", "SCAFFOLD",
                   "UNSUPPORTED_BY_DEVICE")
AVAILABLE = {"IMPLEMENTED", "IMPLEMENTED_INVERSE"}
CERT_STATES = ("UNCERTIFIED", "TESTED_PENDING_APPROVAL", "CERTIFIED_WRITE", "FAILED", "UNSUPPORTED", "REVOKED")

# Typed configuration operations each Aruba writer implements. A contract test asserts these equal the
# writers' own OPERATIONS tuples, so the registry can never again contradict the implementation.
CX_TYPED_OPS = ("switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans",
                "switch.port.admin_state", "switch.port.poe", "switch.lldp", "switch.lag.members",
                "switch.central.disable", "switch.port.description", "switch.port.stp_bpdu_guard",
                "switch.port.storm_control", "switch.port.qos_trust", "switch.system.ntp", "switch.system.dns",
                "switch.system.syslog", "config.restore")
INSTANT_TYPED_OPS = ("ap.ssid.vlan", "ap.ssid.create", "ap.ssid.enterprise", "ap.radio.channel", "ap.radio.width",
                     "ap.radio.tx_power", "ap.client_match", "ap.band_steering", "ap.airtime_fairness")


@dataclass(frozen=True)
class AdapterSpec:
    key: str
    vendor: str
    family: str
    models: tuple[str, ...]
    protocols: tuple[str, ...]
    firmware_min: str | None
    firmware_max: str | None
    operations: dict[str, str] = field(default_factory=dict)   # op -> SOFTWARE_STATES
    certification: str = "UNCERTIFIED"   # default for a model/firmware with no certification record
    notes: str = ""
    typed_operations: tuple[str, ...] = ()
    implemented: bool = True             # False = scaffold/placeholder adapter (E2E-19)


REGISTRY: tuple[AdapterSpec, ...] = (
    AdapterSpec("aruba_instant_8_10", "Aruba", "access_point", ("AP-505", "AP-515", "AP-535"),
                ("HTTPS:4343 REST (JSON-body login, sid session)", "SSH CLI with pinned host key"), "8.10.0.0",
                "8.12.99",
                {"probe_identity": "IMPLEMENTED", "get_capabilities": "IMPLEMENTED",
                 "collect": "IMPLEMENTED", "read_config_summary": "IMPLEMENTED",
                 "read_audit_logs": "IMPLEMENTED", "run_diagnostics": "IMPLEMENTED",
                 "backup_full_config": "IMPLEMENTED",
                 "validate_change": "IMPLEMENTED", "apply_change": "IMPLEMENTED",
                 "readback": "IMPLEMENTED", "rollback": "IMPLEMENTED_INVERSE",
                 "restore_full_config": "GUIDED"},
                "UNCERTIFIED",
                "Instant VC REST; cluster members are not addressed directly. Rollback re-applies recorded prior "
                "values (inverse operations) from the encrypted recovery journal. Full-configuration restore is a "
                "guided procedure: Instant exposes no complete automatic restore through its REST/CLI interfaces.",
                INSTANT_TYPED_OPS),
    AdapterSpec("aruba_cx_10_14", "Aruba", "switch", ("CX 6100", "CX 6200"),
                ("HTTPS REST v10.09-v10.15 (negotiated; form-body login, CSRF token, cookie session)",),
                "10.09.0000", "10.15.9999",
                {"probe_identity": "IMPLEMENTED", "get_capabilities": "IMPLEMENTED",
                 "collect": "IMPLEMENTED", "read_config_summary": "IMPLEMENTED",
                 "read_audit_logs": "IMPLEMENTED", "run_diagnostics": "IMPLEMENTED",
                 "backup_full_config": "IMPLEMENTED",
                 "validate_change": "IMPLEMENTED", "apply_change": "IMPLEMENTED",
                 "readback": "IMPLEMENTED", "rollback": "IMPLEMENTED", "restore_full_config": "IMPLEMENTED"},
                "UNCERTIFIED",
                "Checkpoint-based rollback and full running-config restore via /fullconfigs. API version negotiated "
                "per switch; firmware older than 10.09 is refused (no CSRF / different request shapes).",
                CX_TYPED_OPS),
    AdapterSpec("cisco_ios_xe_snmpv3", "Cisco", "switch", ("Catalyst 9200", "Catalyst 9300", "Catalyst 2960-X"),
                ("SNMPv3 (authPriv)", "NETCONF (only where firmware provides)"), None, None,
                {op: "SCAFFOLD" for op in OPERATIONS},
                "UNCERTIFIED", "PLACEHOLDER — not implemented. Devices can be inventoried manually only.",
                (), False),
    AdapterSpec("unifi_controller", "Ubiquiti", "access_point", ("U6-Lite", "U6-Pro", "U7-Pro"),
                ("UniFi Network controller API",), None, None,
                {op: "SCAFFOLD" for op in OPERATIONS},
                "UNCERTIFIED", "PLACEHOLDER — not implemented. Devices can be inventoried manually only.",
                (), False),
    AdapterSpec("generic_snmpv3", "Generic", "other", (), ("SNMPv3 (authPriv)",), None, None,
                {op: "SCAFFOLD" for op in OPERATIONS},
                "UNCERTIFIED", "PLACEHOLDER — not implemented. Manual inventory only.", (), False),
)


def _model_tokens(model: str) -> set[str]:
    """Model numbers in a reported model string: 'CX 6200F 48G' / 'JL724A 6200F 24G' / '6100 24G CL4' -> {'6200'} etc.;
    'AP-535-US' / 'AP-505' / '505' -> {'535'} / {'505'}. Letters glued to the number (6200F) are model variants."""
    return set(re.findall(r"(?<![0-9])([0-9]{3,4})(?=[A-Za-z]*(?:[^0-9A-Za-z]|$))", model or ""))


def resolve(vendor: str, family: str, model: str | None) -> AdapterSpec:
    v = (vendor or "").casefold()
    have = _model_tokens(model or "")
    for spec in REGISTRY:
        if spec.vendor.casefold() != v or spec.family != family:
            continue
        if not spec.models or have & {t for m in spec.models for t in _model_tokens(m)}:
            return spec
    return REGISTRY[-1]


def operation_result(spec: AdapterSpec, op: str, *, writes_locked: bool = True) -> dict:
    """Structured answer for an operation request. Writes are always routed to the central gate; the reason
    reflects the LIVE write posture, not a hardcoded release statement."""
    if op not in OPERATIONS:
        return {"op": op, "status": "UNSUPPORTED", "reason": "Unknown operation."}
    state = spec.operations.get(op, "NOT_IMPLEMENTED")
    if not spec.implemented:
        return {"op": op, "status": "UNSUPPORTED", "adapter_state": state,
                "reason": f"{spec.key} is a placeholder adapter; nothing is implemented."}
    if op in WRITE_OPERATIONS:
        if state not in AVAILABLE:
            return {"op": op, "status": "GUIDED" if state == "GUIDED" else "UNSUPPORTED", "adapter_state": state,
                    "reason": "Guided procedure — no automatic operation." if state == "GUIDED"
                    else f"{spec.key} does not implement {op}."}
        return {"op": op, "status": "DENIED_BY_CENTRAL_POLICY", "adapter_state": state,
                "reason": "Implemented. Writes run only through an approved change plan and the execution gate "
                          "(per-operation certification for this model/firmware"
                          + (", and the global write lock is ON)." if writes_locked else ").")}
    if state not in AVAILABLE:
        return {"op": op, "status": "UNSUPPORTED", "adapter_state": state,
                "reason": f"{spec.key} does not implement {op}."}
    return {"op": op, "status": "AVAILABLE_READ_ONLY", "adapter_state": state,
            "certification": spec.certification}


def public(spec: AdapterSpec) -> dict:
    return {"key": spec.key, "vendor": spec.vendor, "family": spec.family, "models": list(spec.models),
            "protocols": list(spec.protocols), "firmware_min": spec.firmware_min,
            "firmware_max": spec.firmware_max, "certification": spec.certification,
            "operations": {op: spec.operations.get(op, "NOT_IMPLEMENTED") for op in OPERATIONS},
            "typed_operations": list(spec.typed_operations), "implemented": spec.implemented,
            "notes": spec.notes}
