"""AOS-CX switch firmware lifecycle (rc17): verified image → plan → independent approval → stage → reboot → verify,
with recovery to the previous image. Records live in app settings (no schema change).

Authenticity: an image is accepted only when its SHA-256 equals the vendor-published SHA-256 the administrator
copies from the HPE Networking Support Portal. The switch additionally validates the image signature on upload
(an invalid image is rejected by the device). The application does not hold HPE's signing key, so it does not
claim cryptographic signature verification itself.

Safety: stage writes only the NON-booted slot and never reboots. Boot needs its own acknowledgement inside an
approved plan, verifies the running version AND the switch serial afterwards, and is never re-sent automatically.
Recovery boots the recorded previous image. Hardware execution follows the platform write posture, emergency stop
and the device lease, exactly like configuration changes.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..security.deps import audit
from .images import image_dir

IMG, PLAN = "cx-fw-image:", "cx-fw-plan:"
NAME = re.compile(r"^([A-Z]{2})_(\d{2})_(\d{2})_(\d{4})\.swi$")
APPROVAL_TTL = timedelta(hours=24)
CHUNK = 1024 * 1024


class FirmwareError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def version_of(name: str) -> tuple[str, tuple[int, int, int]]:
    m = NAME.match(name)
    if not m:
        raise FirmwareError("Use the unmodified AOS-CX image file name, e.g. ML_10_14_1020.swi.", 422)
    return m.group(1), (int(m.group(2)), int(m.group(3)), int(m.group(4)))


def _train(firmware: str | None) -> str | None:
    m = re.match(r"^([A-Z]{2})\.", firmware or "")
    return m.group(1) if m else None


def _ver(firmware: str | None) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", firmware or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _dir() -> Path:
    d = image_dir() / "cx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_image(db: Session, filename: str, stream: BinaryIO, vendor_sha256: str, user: str, request=None) -> dict:
    train, ver = version_of(filename)
    vendor_sha256 = (vendor_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", vendor_sha256):
        raise FirmwareError("Enter the vendor-published SHA-256 (64 hex characters) from the HPE support portal.", 422)
    limit = get_settings().max_firmware_mb * CHUNK
    tmp = _dir() / f".upload-{uuid.uuid4().hex}"
    digest, size = hashlib.sha256(), 0
    try:
        with tmp.open("wb") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise FirmwareError("Image exceeds the configured firmware size limit.", 413)
                digest.update(chunk)
                out.write(chunk)
        if size < 1024:
            raise FirmwareError("File is too small to be a switch image.", 422)
        if not hmac.compare_digest(digest.hexdigest(), vendor_sha256):
            audit(db, "cx_firmware_image_rejected", username=user, target=filename, success=False, request=request,
                  detail="SHA-256 differs from the vendor-published value; file discarded")
            db.commit()
            raise FirmwareError("SHA-256 does not match the vendor-published value. The file was discarded.", 422)
        final = _dir() / f"{digest.hexdigest()}.swi"
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    iid = str(uuid.uuid4())
    raw = NAME.match(filename).groups()
    rec = {"filename": filename, "train": train, "version": ".".join(raw), "sha256": digest.hexdigest(),
           "size": size, "authenticity": "VENDOR_HASH_MATCH", "uploaded_by": user, "uploaded_at": _now().isoformat()}
    db.add(AppSetting(key=IMG + iid, value=rec, updated_by=user))
    audit(db, "cx_firmware_image_stored", username=user, target=iid, request=request,
          detail=f"{filename} sha256={rec['sha256'][:16]}… matches vendor-published hash")
    db.flush()
    return {"id": iid, **rec}


def images(db: Session) -> list[dict]:
    return sorted(({"id": r.key.removeprefix(IMG), **r.value} for r in db.scalars(select(AppSetting).where(
        AppSetting.key.startswith(IMG)))), key=lambda x: x["uploaded_at"], reverse=True)


def _image_path(img: dict) -> Path:
    path = _dir() / f"{img['sha256']}.swi"
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                size += len(chunk)
                digest.update(chunk)
    except OSError:
        raise FirmwareError("The approved image file is missing; upload it again and re-plan.") from None
    if size != img["size"] or not hmac.compare_digest(digest.hexdigest(), img["sha256"]):
        raise FirmwareError("Image integrity check failed; upload a verified image and re-plan.")
    return path


def _get(db: Session, key: str, what: str) -> AppSetting:
    row = db.get(AppSetting, key)
    if row is None:
        raise FirmwareError(f"{what} not found.", 404)
    return row


def plan(db: Session, device: InmDevice, image_id: str, user: str, *, allow_downgrade: bool = False, request=None) -> dict:
    if device.family != "switch":
        raise FirmwareError("Switch firmware plans apply to CX switches only.", 422)
    img = _get(db, IMG + image_id, "Image").value
    train = _train(device.firmware)
    if train and train != img["train"]:
        raise FirmwareError(f"Image train {img['train']} does not match the switch's {train} firmware.", 422)
    cur, new = _ver(device.firmware), _ver(img["version"])
    if cur and new and new < cur and not allow_downgrade:
        raise FirmwareError("This is a downgrade; set allow_downgrade after reviewing release notes.", 422)
    if cur and new and new == cur:
        raise FirmwareError("The switch already runs this version.", 422)
    pid = str(uuid.uuid4())
    rec = {"device_id": device.id, "serial": device.serial_number, "image_id": image_id, "target_version": img["version"],
           "previous_version": device.firmware, "allow_downgrade": allow_downgrade, "status": "PENDING_APPROVAL",
           "requested_by": user, "requested_at": _now().isoformat(), "approved_by": None, "approved_at": None,
           "history": [{"at": _now().isoformat(), "step": "PLANNED", "by": user}]}
    db.add(AppSetting(key=PLAN + pid, value=rec, updated_by=user))
    audit(db, "cx_firmware_plan_created", username=user, target=device.id, request=request,
          detail=f"plan={pid} {device.firmware} -> {img['version']}; independent approval required")
    db.flush()
    return {"id": pid, **rec}


def plans(db: Session, device_id: str | None = None) -> list[dict]:
    out = [{"id": r.key.removeprefix(PLAN), **r.value} for r in db.scalars(select(AppSetting).where(
        AppSetting.key.startswith(PLAN)))]
    return sorted((p for p in out if device_id in (None, p["device_id"])), key=lambda p: p["requested_at"], reverse=True)


def _save(row: AppSetting, status: str, step: str, user: str, **extra) -> dict:
    v = dict(row.value)
    v.update(extra)
    v["status"] = status
    v["history"] = [*v["history"], {"at": _now().isoformat(), "step": step, "by": user, **{k: extra[k] for k in extra if k.endswith("_evidence")}}]
    row.value = v
    return {"id": row.key.removeprefix(PLAN), **v}


def approve(db: Session, plan_id: str, user: str, request=None) -> dict:
    row = _get(db, PLAN + plan_id, "Plan")
    if row.value["status"] != "PENDING_APPROVAL":
        raise FirmwareError(f"Plan is {row.value['status']}.")
    if row.value["requested_by"].casefold() == user.casefold():
        audit(db, "cx_firmware_self_approval_denied", username=user, target=plan_id, success=False, request=request)
        db.commit()
        raise FirmwareError("An independent approver is required (no self-approval).", 403)
    out = _save(row, "APPROVED", "APPROVED", user, approved_by=user, approved_at=_now().isoformat())
    audit(db, "cx_firmware_plan_approved", username=user, target=plan_id, request=request,
          detail=f"requester={row.value['requested_by']}")
    return out


def _gates(db: Session, row: AppSetting, device: InmDevice, env: str, *, fresh_approval: bool = True) -> None:
    from ..infrastructure.change_executor import control
    v = row.value
    if not v.get("approved_at"):
        raise FirmwareError("Plan was never approved.")
    if fresh_approval and datetime.fromisoformat(v["approved_at"]) + APPROVAL_TTL <= _now():
        raise FirmwareError("Approval is older than 24 hours; re-approve the plan.")
    if device.serial_number != v["serial"]:
        raise FirmwareError("Device serial changed since planning; re-plan.")
    if control(db).emergency_stop:
        raise FirmwareError("Emergency stop is active.")
    s = get_settings()
    if env == "HARDWARE" and (s.device_write_lock or not s.allow_config_changes):
        raise FirmwareError("Hardware writes are locked on this platform (write lock / config changes disabled).")


def _device(db: Session, row: AppSetting) -> InmDevice:
    d = db.get(InmDevice, row.value["device_id"])
    if d is None:
        raise FirmwareError("Device no longer exists.", 404)
    return d


def _with_writer(db: Session, row: AppSetting, holder: str, fn, *, fresh_approval: bool = True):
    from ..infrastructure.change_executor import _lease, _release, writer_for
    from ..infrastructure.writers import WriterError
    device = _device(db, row)
    writer, env = writer_for(db, device)
    _gates(db, row, device, env, fresh_approval=fresh_approval)
    if not _lease(db, device.id, holder):
        raise FirmwareError("Another change, certification or recovery owns this device.")
    try:
        writer.open()
        try:
            return fn(writer, device, env)
        finally:
            writer.close()
    except WriterError as exc:
        raise FirmwareError(f"{exc.category}: {exc}") from None
    finally:
        _release(db, device.id, holder)


def stage(db: Session, plan_id: str, user: str, request=None) -> dict:
    row = _get(db, PLAN + plan_id, "Plan")
    if row.value["status"] not in ("APPROVED", "STAGE_FAILED"):
        raise FirmwareError(f"Plan is {row.value['status']}; stage needs an approved plan.")
    img = _get(db, IMG + row.value["image_id"], "Image").value
    path = _image_path(img)

    def go(writer, device, env):
        before = writer.firmware_status()
        slot = "secondary" if before.get("booted_image") != "secondary" else "primary"
        after = writer.firmware_upload(path, image=slot, filename=img["filename"])
        got = after.get(f"{slot}_version")
        ok = got == img["version"]
        return ok, {"slot": slot, "before": before, "after": after, "environment": env}
    ok, ev = _with_writer(db, row, f"cx-fw:{plan_id}", go)
    out = _save(row, "STAGED" if ok else "STAGE_FAILED", "STAGE", user, stage_evidence=ev, staged_slot=ev["slot"])
    audit(db, "cx_firmware_staged" if ok else "cx_firmware_stage_failed", username=user, target=row.value["device_id"],
          success=ok, request=request, detail=f"plan={plan_id} slot={ev['slot']} readback={ev['after'].get(ev['slot'] + '_version')}")
    return out


def _wait_back(writer, deadline_s: float, poll_s: float):
    """After a reboot: reconnect until /firmware answers or the deadline passes. Read-only; never re-sends boot."""
    import time
    from ..infrastructure.writers import WriterError
    end = time.monotonic() + deadline_s
    last = None
    while True:
        try:
            writer.reconnect()
            return writer.firmware_status(), writer.identity()
        except WriterError as exc:
            last = exc
        if time.monotonic() >= end:
            raise FirmwareError(f"Switch did not answer within {int(deadline_s)} s after reboot ({last}).", 504)
        time.sleep(poll_s)


def _reboot_wait() -> tuple[float, float]:
    import os
    return float(os.environ.get("INM_CX_REBOOT_WAIT_SECONDS", "900")), float(os.environ.get("INM_CX_REBOOT_POLL_SECONDS", "15"))


def boot(db: Session, plan_id: str, user: str, *, acknowledge_reboot: bool, request=None) -> dict:
    row = _get(db, PLAN + plan_id, "Plan")
    if row.value["status"] != "STAGED":
        raise FirmwareError(f"Plan is {row.value['status']}; boot needs a staged image.")
    if not acknowledge_reboot:
        raise FirmwareError("Rebooting interrupts every port on the switch; acknowledge_reboot=true is required.", 422)
    slot = row.value["staged_slot"]
    # Record that a reboot is being sent BEFORE sending it: a crash or timeout afterwards leaves RECOVERY_REQUIRED
    # evidence instead of a plan that could be booted again blindly.
    _save(row, "BOOT_SENT", "BOOT_SENT", user)
    db.commit()

    def go(writer, device, env):
        writer.firmware_boot(slot)              # sent exactly once; never replayed
        try:
            status, ident = _wait_back(writer, *_reboot_wait())
        except FirmwareError as exc:
            return False, {"status": {}, "serial": None, "environment": env, "error": str(exc)}
        ok = status.get("current_version") == row.value["target_version"] and ident.get("serial_number") == device.serial_number
        if ok:
            device.firmware = status["current_version"]
        return ok, {"status": status, "serial": ident.get("serial_number"), "environment": env}
    ok, ev = _with_writer(db, row, f"cx-fw:{plan_id}", go)
    out = _save(row, "BOOT_VERIFIED" if ok else "RECOVERY_REQUIRED", "BOOT", user, boot_evidence=ev)
    audit(db, "cx_firmware_boot_verified" if ok else "cx_firmware_boot_unverified", username=user,
          target=row.value["device_id"], success=ok, request=request,
          detail=f"plan={plan_id} running={ev['status'].get('current_version')} target={row.value['target_version']}")
    return out


def recover(db: Session, plan_id: str, user: str, request=None) -> dict:
    """Boot the slot holding the recorded previous version; verify it. Requires the plan's approval to be fresh."""
    row = _get(db, PLAN + plan_id, "Plan")
    if row.value["status"] not in ("RECOVERY_REQUIRED", "BOOT_VERIFIED", "BOOT_SENT", "RECOVERY_FAILED"):
        raise FirmwareError(f"Plan is {row.value['status']}; nothing to recover.")
    previous = row.value["previous_version"]

    def go(writer, device, env):
        st = writer.firmware_status()
        slot = next((s for s in ("primary", "secondary") if st.get(f"{s}_version") == previous), None)
        if slot is None:
            return False, {"reason": "No slot holds the previous version; recover from the console.", "status": st}
        if st.get("current_version") == previous:
            return True, {"status": st, "note": "Already running the previous version"}
        writer.firmware_boot(slot)
        try:
            st, _ = _wait_back(writer, *_reboot_wait())
        except FirmwareError as exc:
            return False, {"slot": slot, "error": str(exc)}
        ok = st.get("current_version") == previous
        if ok:
            device.firmware = previous
        return ok, {"status": st, "slot": slot}
    # Recovery returns to the recorded previous image; it does not need a fresh approval (posture, emergency stop and
    # the device lease still apply).
    ok, ev = _with_writer(db, row, f"cx-fw:{plan_id}", go, fresh_approval=False)
    out = _save(row, "RECOVERED" if ok else "RECOVERY_FAILED", "RECOVER", user, recover_evidence=ev)
    audit(db, "cx_firmware_recovered" if ok else "cx_firmware_recovery_failed", username=user,
          target=row.value["device_id"], success=ok, request=request, detail=f"plan={plan_id} previous={previous}")
    return out
