"""WP-07 / GAP-10 — compatible device replacement planning (pure). No restore endpoint exists
until lab certification; the plan stops at 'LAB_VALIDATION_REQUIRED'."""
from __future__ import annotations

from .vault import restore_compatibility

IDENTITY_FIELDS = ("serial", "mgmt_ip", "hostname", "certificates", "uplink_ports", "poe_budget_w")

STEPS = ("SELECT_FAILED_DEVICE", "SELECT_KNOWN_GOOD_VERSION", "VALIDATE_REPLACEMENT_MODEL",
         "MAP_IDENTITY_FIELDS", "INDEPENDENT_APPROVAL", "LAB_RESTORE_AND_READBACK",
         "STAGED_PRODUCTION_RESTORE", "POSTFLIGHT_CHECKS", "CLOSE_WITH_RECEIPT")


def build_plan(*, old_device: dict, artifact: dict | None, new_device: dict,
               identity_map: dict) -> dict:
    blockers: list[str] = []
    if not artifact:
        blockers.append("No full-fidelity vault artifact for the failed device.")
        compat = {"compatible": False, "reasons": ["No artifact."]}
    else:
        if artifact.get("device_id") != old_device.get("id"):
            blockers.append("Artifact does not belong to the failed device.")
        compat = restore_compatibility(artifact=artifact, target=new_device)
        blockers.extend(compat["reasons"])
    if (new_device.get("serial") or "").casefold() == (old_device.get("serial") or "").casefold():
        blockers.append("Replacement serial equals the failed device serial.")
    if new_device.get("family") != old_device.get("family"):
        blockers.append("Device family differs.")
    unknown = [k for k in identity_map if k not in IDENTITY_FIELDS]
    if unknown:
        blockers.append("Unknown identity fields: " + ", ".join(sorted(unknown)))
    if "serial" not in identity_map:
        identity_map = dict(identity_map, serial={"old": old_device.get("serial"),
                                                  "new": new_device.get("serial")})
    state = "BLOCKED" if blockers else "LAB_VALIDATION_REQUIRED"
    return {"state": state, "blockers": blockers, "compatibility": compat,
            "identity_map": identity_map, "steps": list(STEPS),
            "current_step": "VALIDATE_REPLACEMENT_MODEL" if blockers else "LAB_RESTORE_AND_READBACK",
            "device_writes": "LOCKED",
            "optimization_on_successor": "RECOMMEND_ONLY",
            "note": "Planning only. Production restore requires lab certification and the write gate."}
