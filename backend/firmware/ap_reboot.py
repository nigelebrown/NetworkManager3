"""AP (Instant) cluster reboot orchestration after a staged firmware change (rc17).

Sequence (each step audited):
  1. A staged firmware change (status applied) with a complete pre-upgrade member inventory.
  2. Independent reboot approval (approver/admin who is not the requester), valid 24 h, bound to the change id
     and the expected member serials.
  3. Reboot: gates (emergency stop, HARDWARE write posture, pinned SSH host key, allow-listed VC address) →
     journal state REBOOT_SENT and COMMIT → send ONE fixed command (`reload all`) over the pinned SSH session.
     A change whose journal already says REBOOT_SENT is never sent again (no blind replay after a timeout):
     the operator verifies, or creates a separately approved recovery plan.
  4. Wait (bounded) and verify every planned member's running version (upgrade.verify_members) → REBOOT_VERIFIED
     or REBOOT_UNVERIFIED with the evidence. Rollback is the existing approved recovery plan; nothing automatic.

Protocol assumption A-IAP-12 (hardware validation required): the Instant CLI command `reload all` reboots every
cluster member and the VC returns to service with the staged image.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..configmgmt import service as cfg
from ..database.models import ConfigChange, Site
from ..security.deps import audit

APPROVAL_TTL = timedelta(hours=24)
COMMAND = "reload all"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plan(change: ConfigChange) -> dict:
    if change.kind != "firmware_stage":
        raise cfg.PlanError("Only a staged firmware change can be rebooted.")
    return dict(change.plan or {})


def approve(db: Session, change: ConfigChange, approver: str, *, request=None) -> dict:
    plan = _plan(change)
    if change.status not in ("applied", "applied_unverified"):
        raise cfg.PlanError("Stage the firmware first; nothing to activate.")
    if (change.created_by or "").casefold() == approver.casefold():
        audit(db, "ap_reboot_self_approval_denied", username=approver, target=f"change:{change.id}", success=False,
              request=request)
        raise cfg.PlanError("An independent approver must authorise the cluster reboot.")
    members = sorted(str(m.get("serial")) for m in plan.get("expected_members") or [])
    if not members:
        raise cfg.PlanError("A complete pre-upgrade member inventory is required before a reboot can be approved.")
    plan["reboot_approval"] = {"by": approver, "at": _now().isoformat(), "members": members}
    change.plan = plan
    audit(db, "ap_reboot_approved", username=approver, target=f"change:{change.id}",
          detail=f"{len(members)} members; target {plan.get('target')}", request=request)
    return plan["reboot_approval"]


def _gates(db: Session, change: ConfigChange, site: Site, actor: str) -> tuple[str, str, str]:
    from ipaddress import IPv4Address

    from ..api.quick_devices import _permit_probe
    from ..infrastructure.ap_autodiscovery import pinned_fingerprint
    from ..infrastructure.posture import write_posture
    from ..security.crypto import decrypt_secret
    plan = _plan(change)
    appr = plan.get("reboot_approval")
    if not appr:
        raise cfg.PlanError("The cluster reboot has not been approved.")
    if datetime.fromisoformat(appr["at"]) + APPROVAL_TTL < _now():
        raise cfg.PlanError("The reboot approval expired (24 h); approve again.")
    current = sorted(str(m.get("serial")) for m in plan.get("expected_members") or [])
    if current != appr["members"]:
        raise cfg.PlanError("The member inventory changed after approval; approve again.")
    from ..infrastructure.change_executor import control
    if control(db).emergency_stop:
        raise cfg.PlanError("Emergency stop is active; nothing was sent.")
    if write_posture().get("hardware_writes") != "ENABLED":
        raise cfg.PlanError("Hardware writes are locked on this server; nothing was sent.")
    if site.simulated or not site.enabled or not site.vc_host or not site.api_username or not site.api_password_enc:
        raise cfg.PlanError("A live enabled cluster with controller credentials is required.")
    _permit_probe(IPv4Address(site.vc_host))
    fp = pinned_fingerprint(db, site.vc_host)
    if not fp:
        raise cfg.PlanError("Approve the controller's SSH host key first; nothing was sent.")
    return fp, site.api_username, decrypt_secret(site.api_password_enc)


def reboot(db: Session, change: ConfigChange, site: Site, actor: str, *, request=None, runner=None,
           verifier=None, sleep=time.sleep) -> dict:
    plan = _plan(change)
    state = (plan.get("reboot") or {}).get("state")
    if state in ("REBOOT_SENT", "REBOOT_VERIFIED", "REBOOT_UNVERIFIED"):
        raise cfg.PlanError(f"Reboot already {state}; it is never sent twice. Verify members or plan a recovery.")
    try:
        fp, user, password = _gates(db, change, site, actor)
    except Exception as exc:  # noqa: BLE001 — every refusal is audited with its reason
        audit(db, "ap_reboot_refused", username=actor, target=f"change:{change.id}", detail=str(exc)[:200],
              success=False, request=request)
        db.commit()
        if isinstance(exc, cfg.PlanError):
            raise
        raise cfg.PlanError(str(exc)[:200]) from None
    plan["reboot"] = {"state": "REBOOT_SENT", "sent_at": _now().isoformat(), "by": actor, "command": COMMAND}
    change.plan = plan
    audit(db, "ap_reboot_sent", username=actor, target=f"change:{change.id}", detail=f"VC {site.vc_host}: {COMMAND}",
          request=request)
    db.commit()                                         # journal BEFORE contacting the device
    from ..aruba.ssh import run_exec_command
    send = runner or run_exec_command
    try:
        send(site.vc_host, user, password, fp, COMMAND, timeout=20.0)
    except Exception as exc:  # noqa: BLE001 — a dropped session is expected while the cluster reloads
        plan = dict(change.plan)
        plan["reboot"] = plan["reboot"] | {"send_error": type(exc).__name__}
        change.plan = plan
    wait = float(os.environ.get("INM_IAP_REBOOT_WAIT_SECONDS", "600"))
    poll = float(os.environ.get("INM_IAP_REBOOT_POLL_SECONDS", "30"))
    from . import upgrade
    verify = verifier or (lambda: upgrade.verify_members(db, change, site, actor, request=request))
    deadline = time.monotonic() + wait
    result = None
    while True:
        sleep(min(poll, max(0.0, deadline - time.monotonic())))
        try:
            result = verify()
        except cfg.PlanError as exc:
            result = {"state": "UNVERIFIED", "failure": str(exc)[:200]}
        if result.get("state") == "VERIFIED_RUNNING_TARGET" or time.monotonic() >= deadline:
            break
    plan = dict(change.plan)
    final = "REBOOT_VERIFIED" if result.get("state") == "VERIFIED_RUNNING_TARGET" else "REBOOT_UNVERIFIED"
    plan["reboot"] = plan["reboot"] | {"state": final, "finished_at": _now().isoformat(),
                                       "verification": result.get("state")}
    change.plan = plan
    if final == "REBOOT_VERIFIED":
        change.status = "applied"
    audit(db, "ap_reboot_" + ("verified" if final == "REBOOT_VERIFIED" else "unverified"), username=actor,
          target=f"change:{change.id}", detail=f"verification={result.get('state')} missing={result.get('missing_serials')}",
          request=request, success=final == "REBOOT_VERIFIED")
    db.commit()
    return {"state": final, "verification": result,
            "next": None if final == "REBOOT_VERIFIED" else
            "Members did not all report the target version. Investigate, then create an approved recovery plan."}
