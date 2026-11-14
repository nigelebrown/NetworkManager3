"""Fail-closed production release acceptance: read-only, independent and evidence-backed.

This evaluator does NOT enable writes, certify devices, probe the network or
assert that the underlying evidence is genuine. It verifies signed attestations
and tamper-detecting SHA-256 checksums and refuses GO without the external
physical, disaster-recovery and security evidence defined by the release scope.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .adapters import INSTANT_TYPED_OPS, CX_TYPED_OPS

PHASES = tuple(str(i) for i in range(1, 12))
MODELS = ("AP-505", "AP-535", "CX 6100", "CX 6200")
DEVICE_CHECKS = ("discovery", "auth", "read_config", "telemetry", "device_logs",
                 "backup", "write", "readback", "rollback", "restore_drill")
DEPLOYMENT_GATES = ("ha_dr_restore", "penetration_test", "capacity_load",
                    "upgrade_rollback", "credential_rotation", "audit_integrity")
# These gaps affect the advertised first-party network-management scope.
PRIMARY_GAPS = ("enterprise_wlan", "dfs_roaming", "cx_port_authentication",
                "vsf_provisioning", "advanced_cx_policies", "firmware_orchestration",
                "client_roaming_analytics", "physical_device_diagnostics",
                "transceiver_monitoring", "continuous_audit_ingestion",
                "ztp_high_risk_changes")
MAX_ARTIFACT_BYTES = 25_000_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_AGE = timedelta(days=30)


def signed_payload(manifest: dict[str, Any]) -> bytes:
    """All release claims are covered by both reviewers' Ed25519 signatures."""
    return json.dumps({key: value for key, value in manifest.items() if key != "signatures"},
                      ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _recent(timestamp: Any, now: datetime) -> bool:
    if not isinstance(timestamp, str):
        return False
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return (parsed.tzinfo is not None and
                timedelta(0) <= now - parsed.astimezone(timezone.utc) <= MAX_AGE)
    except ValueError:
        return False


def _check_references(name: str, obj: Any, artifacts: dict, errors: list[str]) -> None:
    if not isinstance(obj, dict) or obj.get("status") != "PASS":
        errors.append(f"{name}: missing or non-PASS acceptance")
        return
    refs = obj.get("evidence")
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in artifacts for x in refs):
        errors.append(f"{name}: evidence must reference SHA-256-checked artifacts")


def _check_artifacts(artifacts: Any, root: Path, errors: list[str]) -> dict[str, str]:
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("evidence: no artifacts declared")
        return {}
    result = {}
    directory = root.resolve()
    for name, digest in artifacts.items():
        if (not isinstance(name, str) or not isinstance(digest, str) or not HASH_RE.fullmatch(digest) or
            len(name) > 180 or name.startswith("/") or
            any(c in (".", "..", "") for c in name.replace("\\", "/").split("/"))):
            errors.append("evidence: invalid relative path or SHA-256")
            continue
        try:
            target = directory.joinpath(*name.split("/"))
            # Reject symlinks and require all evidence below an explicitly
            # provided root; GitHub-side fixture files never suffice by default.
            if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(directory):
                raise ValueError("missing file, symlink, or outside evidence root")
            if target.stat().st_size > MAX_ARTIFACT_BYTES or target.stat().st_size == 0:
                raise ValueError("empty or oversized evidence")
            hasher = hashlib.sha256()
            with target.open("rb") as file:
                for chunk in iter(lambda: file.read(128 * 1024), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                raise ValueError("SHA-256 mismatch")
            result[name] = digest
        except (OSError, ValueError) as exc:
            errors.append(f"evidence:{name}: {exc}")
    return result


def _signatures(manifest: dict, keyring: Any, errors: list[str]) -> None:
    if not isinstance(keyring, dict) or not isinstance(keyring.get("reviewers"), dict):
        errors.append("signoff: trusted Ed25519 reviewer keyring not supplied")
        return
    signoffs = manifest.get("signatures")
    if not isinstance(signoffs, list):
        errors.append("signoff: missing independent signatures")
        return
    payload = signed_payload(manifest)
    roles: set[str] = set()
    used: set[str] = set()
    prepared_by = manifest.get("prepared_by")
    for signer in signoffs:
        if not isinstance(signer, dict) or not isinstance(signer.get("reviewer"), str):
            errors.append("signoff: malformed signer record")
            continue
        name = signer["reviewer"]
        info = keyring["reviewers"].get(name)
        if name in used or name == prepared_by or not isinstance(info, dict):
            errors.append(f"signoff:{name}: duplicate, prepared-by, or untrusted reviewer")
            continue
        used.add(name)
        role = info.get("role")
        if role not in ("security", "operations"):
            errors.append(f"signoff:{name}: incorrect trusted reviewer role")
            continue
        try:
            pk = base64.b64decode(info["ed25519_public_key"], validate=True)
            sig = base64.b64decode(signer["signature"], validate=True)
            if len(pk) != 32 or len(sig) != 64:
                raise ValueError("invalid Ed25519 key/signature length")
            Ed25519PublicKey.from_public_bytes(pk).verify(sig, payload)
        except Exception as exc:  # untrusted signature/key format; never leak key material
            errors.append(f"signoff:{name}: invalid signature ({type(exc).__name__})")
            continue
        roles.add(role)
    if roles != {"operations", "security"}:
        errors.append("signoff: independent operations and security signatures are required")


def evaluate(manifest: Any, *, evidence_root: Path, trusted_keyring: Any,
             expected_commit: str, now: datetime | None = None) -> dict:
    """Return a complete, deterministic GO/NO_GO verdict. NO_GO has zero side effects."""
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return {"decision": "NO_GO", "failures": ["manifest: expected a JSON object"], "checked_at": now.isoformat()}
    if manifest.get("schema_version") != 1:
        errors.append("manifest: unsupported schema_version (expected 1)")
    sha = manifest.get("release_commit")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        errors.append("manifest: release commit must be a full lowercase SHA-1")
    if sha != expected_commit:
        errors.append("manifest: release commit differs from deployed candidate")
    if manifest.get("deployment_profile") != "live":
        errors.append("deployment: only live mode is eligible")
    if not manifest.get("prepared_by"):
        errors.append("manifest: prepared_by required")
    if not _recent(manifest.get("created_at"), now):
        errors.append("manifest: timestamp missing, future, or older than 30 days")

    ci = manifest.get("ci")
    if (not isinstance(ci, dict) or ci.get("conclusion") != "success" or
        ci.get("commit") != sha or not isinstance(ci.get("run_url"), str) or
        not ci["run_url"].startswith("https://github.com/") or
        not _recent(ci.get("completed_at"), now)):
        errors.append("CI: successful recent run for exact candidate SHA required")

    artifacts = _check_artifacts(manifest.get("artifacts"), Path(evidence_root), errors)
    phases = manifest.get("phases")
    for key in PHASES:
        _check_references(f"phase-{key}", phases.get(key) if isinstance(phases, dict) else None, artifacts, errors)
    for key in DEPLOYMENT_GATES:
        gates = manifest.get("deployment_gates")
        _check_references(f"deployment:{key}", gates.get(key) if isinstance(gates, dict) else None,
                          artifacts, errors)
    gaps = manifest.get("gap_closure")
    for key in PRIMARY_GAPS:
        _check_references(f"gap:{key}", gaps.get(key) if isinstance(gaps, dict) else None, artifacts, errors)
    models = manifest.get("device_certifications")
    if not isinstance(models, list):
        models = []
        errors.append("hardware: device_certifications must be a list")
    counts: dict[str, int] = {}
    for device in models:
        if not isinstance(device, dict):
            errors.append("hardware: malformed model record")
            continue
        model = device.get("model")
        if not isinstance(model, str) or model not in MODELS:
            errors.append(f"hardware: invalid or unsupported model {str(model)[:60]}")
            continue
        counts[model] = counts.get(model, 0) + 1
        if (device.get("environment") != "PHYSICAL" or
            not isinstance(device.get("firmware"), str) or not device["firmware"].strip() or
            device["firmware"].strip().casefold() in ("unknown", "emulated", "demo") or
            not isinstance(device.get("serial_number"), str) or not device["serial_number"].strip() or
            device["serial_number"].upper().startswith(("EMU", "DEMO"))):
            errors.append(f"hardware:{model}: physical serial/firmware/environment not evidenced")
        for check in DEVICE_CHECKS:
            checks = device.get("checks")
            _check_references(f"hardware:{model}:{check}",
                              checks.get(check) if isinstance(checks, dict) else None, artifacts, errors)
        ops = device.get("certified_operations")
        expected_ops = INSTANT_TYPED_OPS if model.startswith("AP-") else CX_TYPED_OPS
        if not isinstance(ops, dict) or not ops:
            errors.append(f"hardware:{model}: no per-operation certification evidence")
        else:
            for name in expected_ops:
                _check_references(f"hardware:{model}:{name}", ops.get(name), artifacts, errors)
            for name, item in ops.items():
                if name not in expected_ops:
                    errors.append(f"hardware:{model}: unrecognized certification operation {name}")
    for model in MODELS:
        if counts.get(model, 0) != 1:
            errors.append(f"hardware:{model}: exactly one physical model/firmware record required")

    _signatures(manifest, trusted_keyring, errors)
    return {"decision": "GO" if not errors else "NO_GO",
            "checked_at": now.isoformat(), "release_commit": sha,
            "phases_required": len(PHASES), "models_required": list(MODELS),
            "verified_artifacts": len(artifacts), "failures": errors}
