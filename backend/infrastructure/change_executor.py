"""Phase 6 — certified change execution engine and Phase 6/10 certification service.

The ONLY code path that writes device configuration. Order of operations for an approved plan:

  1. compare-and-set APPROVED -> EXECUTING (no double execution)
  2. execution gate: write lock, emergency stop, approval validity, per-operation certification for this
     model + firmware train, authenticated executor, device lease
  3. connect, verify device identity (model/firmware) still matches the approved plan
  4. full pre-change backup -> encrypted vault (PRE_CHANGE) -> decrypt-and-hash verification
     (failure here = ABORTED, zero writes)
  5. device checkpoint
  6. for each typed operation: apply -> readback -> compare (mismatch = failure)
  7. any failure: rollback to checkpoint -> full readback compared with the pre-change backup
  8. success: save, post-change backup (POST_CHANGE), APPLICATION attribution, audit
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models_inm import InmDevice, JobLease
from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
from ..database.models_phases import (ChangeAttribution, ChangeExecution, ChangePlan, ConfigArtifact,
                                      DeviceAccess, DeviceCertification, ExecutionDecision)
from ..domain import change_control as CC
from ..domain import vault as V
from ..domain.adapters import resolve
from .phase_services import LocalObjectStore, control, vault_master_key
from .writers import Credentials, WriterError

_EMULATORS: dict[str, object] = {}   # DEMO device id -> emulator (process-local, labelled EMULATED)


# ---- scope normalisation ------------------------------------------------------------------------
def model_family(model: str | None) -> str:
    m = (model or "").upper()
    for pat, fam in ((r"(CX\s*)?\b6100", "CX 6100"), (r"(CX\s*)?\b6200", "CX 6200"), (r"(CX\s*)?\b6300", "CX 6300"),
                     (r"AP-?505", "AP-505"), (r"AP-?535", "AP-535"), (r"AP-?515", "AP-515")):
        if re.search(pat, m):
            return fam
    return m.strip()[:48] or "UNKNOWN"


def firmware_train(fw: str | None) -> str:
    nums = re.findall(r"\d+", fw or "")
    return ".".join(nums[:2]) if len(nums) >= 2 else "UNKNOWN"


def environment(device: InmDevice) -> str:
    return "EMULATED" if device.source_type == "DEMO" else "HARDWARE"


def certification_map(db: Session, device: InmDevice) -> dict[str, str]:
    """operation -> status for this device's adapter/model family/firmware train and environment."""
    spec = resolve(device.vendor, device.family, device.model)
    rows = db.scalars(select(DeviceCertification).where(
        DeviceCertification.adapter_key == spec.key,
        DeviceCertification.model_family == model_family(device.model),
        DeviceCertification.firmware_train == firmware_train(device.firmware),
        DeviceCertification.environment == environment(device)))
    return {r.operation: r.status for r in rows}


# ---- writer factory -------------------------------------------------------------------------------
def writer_for(db: Session, device: InmDevice):
    """Return (writer, environment). REAL devices need a bound credential, an allowlisted address and,
    for Instant SSH changes, a pinned host key. DEMO devices use a labelled emulator."""
    if device.source_type == "DEMO":
        from .writers.emulator import CxEmulator, InstantEmulator
        emu = _EMULATORS.get(device.id)
        if emu is None:
            emu = CxEmulator(model=device.model or "CX 6200F", firmware=device.firmware or "10.14.1020") \
                if device.family == "switch" else InstantEmulator()
            _EMULATORS[device.id] = emu
        return emu.writer(), "EMULATED"
    import ipaddress

    from fastapi import HTTPException

    from ..api.quick_devices import _permit_probe
    from ..security.crypto import decrypt_secret
    binding = db.get(OnboardingDeviceCredential, device.id)
    profile = db.get(OnboardingCredential, binding.credential_id) if binding else None
    if profile is None or not getattr(profile, "enabled", True):
        raise WriterError("PRECONDITION", "No enabled credential profile is bound to this device.")
    if not device.mgmt_addresses:
        raise WriterError("PRECONDITION", "No management address recorded.")
    access = db.get(DeviceAccess, device.id)
    host = (access.vc_host if access and access.vc_host else None) or str(device.mgmt_addresses[0])
    for addr in {host, str(device.mgmt_addresses[0])}:
        try:
            _permit_probe(ipaddress.IPv4Address(addr))
        except (HTTPException, ValueError):
            raise WriterError("TARGET_NOT_ALLOWED", f"{addr} is not in the approved management ranges.") from None
    creds = Credentials(host=host, username=profile.username, password=decrypt_secret(profile.password_enc),
                        ssh_fingerprint=access.ssh_fingerprint if access else None,
                        extra={"ap_ip": str(device.mgmt_addresses[0])})
    verify = get_settings().tls_verify_arg
    if device.family == "switch":
        from .writers.aruba_cx import ArubaCxWriter
        return ArubaCxWriter(creds, verify=verify), "HARDWARE"
    from .writers.aruba_instant import ArubaInstantWriter
    return ArubaInstantWriter(creds, verify=verify), "HARDWARE"


# ---- vault helper --------------------------------------------------------------------------------
def seal_artifact(db: Session, device: InmDevice, content: bytes, *, trigger: str, actor: str,
                  source: str) -> ConfigArtifact:
    sealed = V.seal(content, master_key=vault_master_key(), identity={
        "device_id": device.id, "serial": device.serial_number, "vendor": device.vendor, "model": device.model,
        "firmware": device.firmware, "capture_source": source, "fidelity": V.FIDELITY_FULL})
    path = LocalObjectStore().put(sealed.ciphertext, sealed.ciphertext_sha256)
    a = ConfigArtifact(device_id=device.id, serial=device.serial_number, vendor=device.vendor, model=device.model,
                       firmware=device.firmware, fidelity=V.FIDELITY_FULL, capture_source=source, trigger=trigger,
                       object_path=path, wrapped_key=sealed.wrapped_key, key_id=sealed.key_id,
                       plaintext_sha256=sealed.plaintext_sha256, ciphertext_sha256=sealed.ciphertext_sha256,
                       manifest=sealed.manifest, signature=sealed.signature, size=len(content), created_by=actor)
    db.add(a)
    db.flush()
    # Verify what was stored can be decrypted to the same bytes before trusting it as a restore point.
    back = V.open_sealed(LocalObjectStore().get(path), wrapped_key=a.wrapped_key, manifest=a.manifest,
                         signature=a.signature, master_key=vault_master_key())
    if back != content:
        raise WriterError("BACKUP_UNVERIFIED", "Vault round-trip mismatch.")
    return a


def _norm(cfg: bytes, family: str) -> str:
    """Comparable form of a full configuration (used to prove rollback restored the pre-change state)."""
    if family == "switch":
        try:
            return hashlib.sha256(json.dumps(json.loads(cfg), sort_keys=True).encode()).hexdigest()
        except ValueError:
            pass
    # Text configs (Instant): compare the set of effective lines; CLI edits may reorder lines in a block.
    return hashlib.sha256(b"\n".join(sorted(x.strip() for x in cfg.splitlines() if x.strip()))).hexdigest()


def _artifact_bytes(db: Session, artifact_id: str, device: InmDevice) -> bytes:
    a = db.get(ConfigArtifact, artifact_id)
    if a is None or a.fidelity != V.FIDELITY_FULL:
        raise WriterError("PRECONDITION", "Restore artifact missing or not full-fidelity.")
    compat = V.restore_compatibility(artifact=a.manifest, target={"vendor": device.vendor, "model": device.model,
                                                                  "firmware": device.firmware,
                                                                  "compatible_models": [a.model or ""] if
                                                                  model_family(a.model) == model_family(device.model) else []})
    if not compat["compatible"]:
        raise WriterError("INCOMPATIBLE", "; ".join(compat["reasons"]))
    try:
        return V.open_sealed(LocalObjectStore().get(a.object_path), wrapped_key=a.wrapped_key, manifest=a.manifest,
                             signature=a.signature, master_key=vault_master_key())
    except V.VaultIntegrityError as exc:
        raise WriterError("INTEGRITY", str(exc)) from None


# ---- lease ---------------------------------------------------------------------------------------
def _lease(db: Session, device_id: str, holder: str, ttl: int = 900) -> bool:
    now = datetime.now(timezone.utc)
    job = f"device-write:{device_id}"
    row = db.get(JobLease, job)
    if row is None:
        db.add(JobLease(job=job, holder=holder, expires_at=now + timedelta(seconds=ttl)))
        db.flush()
        return True
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if exp > now and row.holder != holder:
        return False
    row.holder, row.expires_at = holder, now + timedelta(seconds=ttl)
    return True


def _release(db: Session, device_id: str) -> None:
    row = db.get(JobLease, f"device-write:{device_id}")
    if row is not None:
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)


# ---- execution -----------------------------------------------------------------------------------
def execute_plan(db: Session, plan: ChangePlan, *, actor: str, writer=None, env: str | None = None) -> dict:
    device = db.get(InmDevice, plan.device_id)
    now = datetime.now(timezone.utc)
    env = env or environment(device)
    steps: list[dict] = []

    def step(name, ok, **kw):
        steps.append({"step": name, "ok": bool(ok), "at": datetime.now(timezone.utc).isoformat(), **kw})

    certs = certification_map(db, device)
    caps = [{"op": o["op"], "capability": certs.get(o["op"], "UNCERTIFIED")} for o in plan.operations]
    latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    ok_appr, why = CC.approval_valid(approved_at=plan.approved_at, now=now, approved_config_hash=plan.expected_config_hash,
                                     current_config_hash=latest.plaintext_sha256 if latest else None,
                                     approved_firmware=plan.firmware_at_request, current_firmware=device.firmware)
    lock = get_settings().device_write_lock or not get_settings().allow_config_changes
    holder = f"{actor}:{plan.id}"
    leased = _lease(db, device.id, holder)
    gate = CC.execution_gate(write_lock=lock if env == "HARDWARE" else False,
                             emergency_stop=control(db).emergency_stop, plan_state=plan.state,
                             approval_ok=ok_appr, capabilities=caps, pre_backup_verified=True,
                             device_lease_held=leased, executor_authenticated=bool(actor))
    dec = ExecutionDecision(kind="CHANGE_PLAN", plan_id=plan.id, device_id=device.id, proposed_diff_hash=plan.plan_hash,
                            global_lock_state="LOCKED" if lock else "UNLOCKED", decision=gate["decision"],
                            guards=gate["guards"] + [{"guard": f"environment:{env}", "pass": True}],
                            denial_reason=(", ".join(gate["denial_reasons"]) + (f" ({why})" if why else "")) or None,
                            evaluated_by=actor)
    db.add(dec)
    if gate["decision"] != "ALLOW":
        if leased:
            _release(db, device.id)
        db.flush()
        return {"decision": "DENY", "decision_id": dec.id, "denial_reason": dec.denial_reason, "guards": dec.guards,
                "device_writes": 0, "adapter_called": False, "environment": env}
    # single execution: APPROVED -> EXECUTING compare-and-set
    res = db.execute(update(ChangePlan).where(ChangePlan.id == plan.id, ChangePlan.state == "APPROVED")
                     .values(state="EXECUTING").execution_options(synchronize_session=False))
    if res.rowcount != 1:
        _release(db, device.id)
        return {"decision": "DENY", "denial_reason": "Plan is already executing or executed.", "device_writes": 0,
                "adapter_called": False, "environment": env}
    db.flush()
    ex = ChangeExecution(plan_id=plan.id, device_id=device.id, environment=env, outcome="ABORTED", steps=[],
                         executed_by=actor)
    db.add(ex)
    writes = 0
    outcome = "ABORTED"
    try:
        if writer is None:
            writer, env2 = writer_for(db, device)
        writer.open()
        step("connect", True)
        if hasattr(writer, "identity") and env == "HARDWARE":
            ident = writer.identity()
            same = model_family(ident.get("model")) == model_family(device.model) and \
                firmware_train(ident.get("firmware")) == firmware_train(device.firmware)
            step("identity_check", same, observed=ident)
            if not same:
                raise WriterError("IDENTITY_MISMATCH", "Device model/firmware differs from the approved plan.")
        takeover = all(o["op"] == "switch.central.disable" for o in plan.operations)
        if hasattr(writer, "management_authority") and not takeover:
            auth = writer.management_authority()
            step("management_authority", auth["authority"] == "LOCAL", authority=auth["authority"], detail=auth["detail"])
            if auth["authority"] != "LOCAL":
                raise WriterError("MANAGED_BY_" + auth["authority"].split("_NOT")[0].replace("CENTRAL_CONFIGURED", "ARUBA_CENTRAL"),
                                  auth["detail"] + " Use the approved takeover plan before configuring this device here.")
        pre = writer.backup()
        art = seal_artifact(db, device, pre, trigger="PRE_CHANGE", actor=actor,
                            source="EMULATOR" if env == "EMULATED" else "DEVICE_READ")
        ex.pre_artifact_id = art.id
        step("pre_change_backup_verified", True, artifact=art.id, sha256=art.plaintext_sha256)
        cp = writer.checkpoint()
        ex.checkpoint = cp[:80]
        step("checkpoint", True, checkpoint=cp[:80])
        try:
            for op in plan.operations:
                if op["op"] == "config.restore":
                    content = _artifact_bytes(db, op["artifact_id"], device)
                    writer.restore(content)
                    writes += 1
                    verified = _norm(writer.backup(), device.family) == _norm(content, device.family)
                    sent, observed = [{"restore_artifact": op["artifact_id"]}], {"matches_artifact": verified}
                    step("apply_readback", verified, op=op, sent=sent, readback=observed)
                    if not verified:
                        raise WriterError("READBACK_MISMATCH", "Restored configuration differs from the artifact.")
                    continue
                sent = writer.apply(op)
                writes += 1
                verified, observed = writer.readback(op)
                step("apply_readback", verified, op=op, sent=sent, readback=observed)
                if not verified:
                    raise WriterError("READBACK_MISMATCH", f"{op['op']}: device reports {observed}")
        except WriterError as exc:
            step("failure", False, category=exc.category, message=str(exc)[:300])
            try:
                writer.rollback(cp)
                after = writer.backup()
                restored = _norm(after, device.family) == _norm(pre, device.family)
                step("rollback_readback", restored, matches_pre_change=restored)
                outcome = "ROLLED_BACK" if restored else "ROLLBACK_FAILED"
            except WriterError as rexc:
                step("rollback", False, category=rexc.category, message=str(rexc)[:300])
                outcome = "ROLLBACK_FAILED"
            raise
        writer.save()
        step("save", True)
        post = writer.backup()
        part = seal_artifact(db, device, post, trigger="POST_CHANGE", actor=actor,
                             source="EMULATOR" if env == "EMULATED" else "DEVICE_READ")
        ex.post_artifact_id = part.id
        step("post_change_backup", True, artifact=part.id, sha256=part.plaintext_sha256)
        db.add(ChangeAttribution(device_id=device.id, change_plan_id=plan.id, change_at=datetime.now(timezone.utc),
                                 level="VERIFIED", actor=plan.requester, change_type="APPLICATION",
                                 evidence={"plan": plan.id, "approver": plan.approver, "execution": ex.id,
                                           "executed_by": actor, "environment": env},
                                 candidates=[plan.requester], created_by="change-executor"))
        outcome = "APPLIED_VERIFIED"
    except WriterError as exc:
        if not any(s["step"] == "failure" for s in steps):
            step("failure", False, category=exc.category, message=str(exc)[:300])
    finally:
        try:
            if writer is not None:
                writer.close()
        except Exception:
            pass
        _release(db, device.id)
    ex.steps, ex.outcome, ex.finished_at = steps, outcome, datetime.now(timezone.utc)
    plan.state = {"APPLIED_VERIFIED": "APPLIED_VERIFIED", "ROLLED_BACK": "FAILED_ROLLED_BACK",
                  "ROLLBACK_FAILED": "ROLLBACK_FAILED", "ABORTED": "APPROVED"}[outcome]
    db.flush()
    return {"decision": "ALLOW", "decision_id": dec.id, "execution_id": ex.id, "outcome": outcome,
            "steps": steps, "device_writes": writes, "adapter_called": True, "environment": env}


# ---- certification ---------------------------------------------------------------------------------
TEST_OPS = {
    "switch": lambda t: [
        {"op": "switch.vlan.define", "value": t.get("test_vlan", 4090)},
        {"op": "switch.port.access_vlan", "port": t["test_port"], "value": t.get("test_vlan", 4090)},
        {"op": "switch.port.trunk_vlans", "port": t["test_port"], "value": [1, t.get("test_vlan", 4090)]},
        {"op": "switch.port.admin_state", "port": t["test_port"], "value": "down"},
        {"op": "switch.port.poe", "port": t["test_port"], "value": "disabled"},
        {"op": "switch.lldp", "port": t["test_port"], "value": "disabled"},
    ] + ([{"op": "switch.lag.members", "lag": "lag250", "value": t["lag_ports"]}] if t.get("lag_ports") else []),
    "access_point": lambda t: [
        {"op": "ap.client_match", "value": "disabled"}, {"op": "ap.band_steering", "value": "balance-bands"},
        {"op": "ap.airtime_fairness", "value": "fair-access"}, {"op": "ap.radio.width", "value": 20},
        {"op": "ap.radio.tx_power", "min_dbm": 10, "max_dbm": 17},
        {"op": "ap.radio.channel", "band": "5", "value": t.get("test_channel", 44), "power": 14},
    ] + ([{"op": "ap.ssid.vlan", "ssid": t["test_ssid"], "value": t.get("test_vlan", 100)}] if t.get("test_ssid") else []),
}


def run_certification(db: Session, device: InmDevice, *, actor: str, test: dict, writer=None) -> dict:
    """Run every operation against a LAB device: backup, checkpoint, apply, readback, rollback, verify the
    full configuration equals the pre-test backup. Each passed op is recorded TESTED_PENDING_APPROVAL."""
    import uuid
    env = environment(device)
    if env == "HARDWARE" and not (get_settings().allow_config_changes and not get_settings().device_write_lock):
        raise WriterError("WRITES_DISABLED", "Certification writes to a lab device; enable device writes first.")
    if control(db).emergency_stop:
        raise WriterError("EMERGENCY_STOP", "Emergency stop is active.")
    if writer is None:
        writer, env = writer_for(db, device)
    spec = resolve(device.vendor, device.family, device.model)
    run_id = str(uuid.uuid4())
    results = []
    writer.open()
    try:
        ident = writer.identity() if hasattr(writer, "identity") else {"model": device.model, "firmware": device.firmware}
        base = writer.backup()
        seal_artifact(db, device, base, trigger="MANUAL", actor=actor, source="CERTIFICATION_BASELINE")
        results.append({"operation": "read.backup_full_config", "passed": bool(base), "evidence": {"bytes": len(base)}})
        for op in TEST_OPS[device.family](test):
            ev = {"op": op}
            try:
                cp = writer.checkpoint()
                ev["rendered"] = writer.render(op)
                if op["op"] in ("switch.port.access_vlan", "switch.port.trunk_vlans"):
                    # prerequisite inside the same checkpoint: the VLAN must exist on the switch
                    writer.apply({"op": "switch.vlan.define", "value": test.get("test_vlan", 4090)})
                writer.apply(op)
                ok, observed = writer.readback(op)
                ev["readback"] = observed
                writer.rollback(cp)
                restored = _norm(writer.backup(), device.family) == _norm(base, device.family)
                ev["rollback_restored"] = restored
                passed = ok and restored
            except WriterError as exc:
                ev["error"] = f"{exc.category}: {str(exc)[:200]}"
                passed = False
                try:
                    writer.rollback(cp)
                except Exception:
                    pass
            results.append({"operation": op["op"], "passed": passed, "evidence": ev})
        # full-configuration restore: write the captured baseline back and prove it reads back identically
        ev = {}
        try:
            writer.restore(base)
            ev["matches_baseline"] = _norm(writer.backup(), device.family) == _norm(base, device.family)
            results.append({"operation": "config.restore", "passed": ev["matches_baseline"], "evidence": ev})
        except WriterError as exc:
            results.append({"operation": "config.restore", "passed": False,
                            "unsupported": exc.category == "UNSUPPORTED",
                            "evidence": {"error": f"{exc.category}: {str(exc)[:200]}"}})
    finally:
        writer.close()
    for r in results:
        row = db.scalar(select(DeviceCertification).where(
            DeviceCertification.adapter_key == spec.key, DeviceCertification.model_family == model_family(device.model),
            DeviceCertification.firmware_train == firmware_train(device.firmware),
            DeviceCertification.operation == r["operation"], DeviceCertification.environment == env))
        if row is None:
            row = DeviceCertification(adapter_key=spec.key, model_family=model_family(device.model),
                                      firmware_train=firmware_train(device.firmware), operation=r["operation"],
                                      environment=env)
            db.add(row)
        # An operation the adapter deliberately does not implement is UNSUPPORTED, not a failed test.
        row.status = "TESTED_PENDING_APPROVAL" if r["passed"] else ("UNSUPPORTED" if r.get("unsupported") else "FAILED")
        row.run_id, row.device_id, row.evidence, row.tested_by = run_id, device.id, r["evidence"], actor
        row.tested_at, row.approved_by, row.approved_at = datetime.now(timezone.utc), None, None
    db.flush()
    return {"run_id": run_id, "environment": env, "identity": ident, "adapter": spec.key,
            "model_family": model_family(device.model), "firmware_train": firmware_train(device.firmware),
            "results": results}
