"""Plan -> confirm -> backup -> apply -> verify -> (rollback plan) workflow for config changes.

Safety properties (each is enforced here or in `ArubaClient.config_post`):
  * disabled unless SIMS_ALLOW_CONFIG_CHANGES=true;
  * simulated (demo) sites are read-only;
  * a fresh pre-change backup is taken as part of planning (planning fails if it cannot be);
  * applying needs the plan id plus the typed phrase "APPLY <site name>", within 15 minutes;
  * every plan / apply / failure is audit-logged (never with secrets);
  * after applying, the config is read back and compared with what was requested;
  * a rollback is itself a plan that goes through the same confirmation.
Passphrases are Fernet-encrypted in `config_changes.payload_enc` and are never stored in plain text,
logged, shown in a plan, or recoverable from backups, so a rollback cannot restore a previous passphrase.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..aruba import parsers
from ..aruba.authentication import client_for_site
from ..aruba.configuration import (
    HOSTNAME_RE, PSK_MODES, SsidChange, build_hostname_payload, build_ssid_payload,
    config_changes_enabled, fetch_running_config, mask_payload, parse_running_config, redact, sha256,
    verify_ssid,
)
from ..aruba.errors import ArubaError, ArubaUnsupportedError
from ..database.models import AccessPoint, ConfigBackup, ConfigChange, Site, utcnow
from ..monitoring.collector import poll_site
from ..monitoring.demo import demo_running_config
from ..security.crypto import decrypt_secret, encrypt_secret
from ..security.deps import audit

PLAN_TTL = timedelta(minutes=15)


class PlanError(ValueError):
    """The requested change is invalid or unsafe; message is shown to the admin."""


def confirm_phrase(site: Site) -> str:
    return f"APPLY {site.name}"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---- backups -----------------------------------------------------------------------------
def store_backup(db: Session, site: Site, text: str, kind: str, username: str | None) -> ConfigBackup:
    text = redact(text)
    b = ConfigBackup(site_id=site.id, kind=kind, taken_by=username, sha256=sha256(text), text=text,
                     parsed=parse_running_config(text), simulated=site.simulated)
    db.add(b)
    db.flush()
    return b


def pull_config(db: Session, site: Site, username: str | None, kind: str = "pull",
                transport: httpx.BaseTransport | None = None) -> ConfigBackup:
    """READ-ONLY: fetch the cluster's running config and store it as a backup."""
    if site.simulated:
        return store_backup(db, site, demo_running_config(site.name), kind, username)
    with client_for_site(site, transport) as c:
        text = fetch_running_config(c)
    return store_backup(db, site, text, kind, username)


def latest_backup(db: Session, site_id: int) -> ConfigBackup | None:
    return db.scalar(select(ConfigBackup).where(ConfigBackup.site_id == site_id)
                     .order_by(ConfigBackup.id.desc()).limit(1))


# ---- planning ----------------------------------------------------------------------------
def _require_writable(site: Site) -> None:
    if not config_changes_enabled():
        raise PlanError("Configuration changes are disabled. Set SIMS_ALLOW_CONFIG_CHANGES=true in .env and restart.")
    if site.simulated:
        raise PlanError("Simulated (demo) sites are read-only.")


def _save_plan(db, site, user, kind, summary, payload, plan, backup, rollback_of=None) -> ConfigChange:
    ch = ConfigChange(
        site_id=site.id, kind=kind, summary=summary[:300], payload_enc=encrypt_secret(json.dumps(payload)),
        plan=plan, backup_id=backup.id if backup else None, created_by=user, expires_at=utcnow() + PLAN_TTL, rollback_of=rollback_of,
    )
    db.add(ch)
    db.flush()
    return ch


def plan_ssid(db: Session, site: Site, user: str, ch: SsidChange, *, request: Request | None = None,
              transport: httpx.BaseTransport | None = None, rollback_of: int | None = None) -> ConfigChange:
    _require_writable(site)
    backup = pull_config(db, site, user, "pre-change", transport)  # raises ArubaError if unreachable
    existing = next((s for s in (backup.parsed or {}).get("ssids", []) if s["profile"] == ch.profile), None)
    creating = existing is None
    warnings: list[str] = []

    if creating:
        missing = [n for n, v in (("ESSID", ch.essid), ("security mode", ch.opmode), ("VLAN", ch.vlan)) if v is None]
        if missing:
            raise PlanError(f"Creating a new SSID profile requires: {', '.join(missing)}.")
        if ch.opmode in PSK_MODES and not ch.passphrase:
            raise PlanError("A passphrase (8-63 characters) is required for a WPA2/WPA3 personal SSID.")
    else:
        if ch.opmode in PSK_MODES and existing["opmode"] not in PSK_MODES and not ch.passphrase:
            raise PlanError("Switching to a WPA2/WPA3 personal mode requires a passphrase.")
        if ch.opmode and ch.opmode != existing["opmode"]:
            warnings.append("Changing the security mode disconnects clients that cannot use the new mode.")
        if ch.vlan is not None and ch.vlan != existing["vlan"]:
            warnings.append("Changing the VLAN re-addresses every client on this SSID. Confirm the VLAN is trunked "
                            "to the AP uplink and has DHCP.")
        if ch.essid and ch.essid != existing["essid"]:
            warnings.append("Changing the ESSID disconnects clients until they reconnect to the new name.")
    if ch.enabled is False:
        warnings.append("Disabling this SSID disconnects its clients.")
    if not creating and not any(v is not None for v in (ch.essid, ch.vlan, ch.opmode, ch.passphrase, ch.rf_band, ch.enabled)):
        raise PlanError("No changes requested.")
    warnings.append("PENDING VERIFICATION: how /rest/ssid treats fields you leave out is not defined in Aruba's guide. "
                    "SIMS reads the config back after applying and reports any mismatch.")

    body = build_ssid_payload(ch, creating=creating)
    if creating:
        rollback = (f"New profile. Rollback = a plan that disables '{ch.profile}'. To remove it entirely run on the AP CLI: "
                    f"`no wlan ssid-profile {ch.profile}`. (Deleting through REST is undocumented, so SIMS does not do it.)")
    else:
        rollback = ("Rollback = a plan restoring the previous ESSID, VLAN, RF band and enabled state. Security mode and passphrase "
                    "are NOT restored automatically (passphrases are never stored).")
    plan: dict[str, Any] = {
        "target": {"site": site.name, "controller": f"{site.vc_host}:{site.vc_port}"},
        "creating": creating, "before": existing,
        "after": {k: v for k, v in ch.model_dump().items() if v is not None and k != "passphrase"}
                 | ({"passphrase": "(set)"} if ch.passphrase else {}),
        "request": {"method": "POST", "path": "/rest/ssid", "body": mask_payload(body)},
        "rollback": rollback, "warnings": warnings, "backup_id": backup.id,
        "verification": "Config is read back after applying and compared with the request.",
        "confirm_phrase": confirm_phrase(site),
    }
    summary = f"{'Create' if creating else 'Modify'} SSID profile '{ch.profile}' on {site.name}"
    change = _save_plan(db, site, user, "ssid_upsert", summary,
                        {"ssid": ch.model_dump(), "creating": creating}, plan, backup, rollback_of)
    audit(db, "config_plan", username=user, target=f"site:{site.name}", detail=f"#{change.id} {summary}", request=request)
    return change


def plan_rename(db: Session, site: Site, user: str, ap: AccessPoint, new_name: str, *, request: Request | None = None,
                transport: httpx.BaseTransport | None = None, rollback_of: int | None = None) -> ConfigChange:
    _require_writable(site)
    if ap.site_id != site.id:
        raise PlanError("That AP does not belong to this site.")
    if not HOSTNAME_RE.match(new_name):
        raise PlanError("AP name must be 1-32 characters: letters, digits and '-', starting with a letter or digit.")
    if not ap.ip:
        raise PlanError("The AP's IP address is not known yet; poll the site first.")
    if new_name == ap.name:
        raise PlanError("The AP already has that name.")
    backup = pull_config(db, site, user, "pre-change", transport)
    rollback = (f"Rollback = a plan renaming it back to '{ap.name}'." if HOSTNAME_RE.match(ap.name) else
                f"The previous name '{ap.name}' is the MAC-based default and is not a valid hostname, so it cannot be restored "
                "through SIMS; reset it from the AP's own UI if needed.")
    plan = {
        "target": {"site": site.name, "controller": f"{site.vc_host}:{site.vc_port}", "ap": ap.name, "ap_ip": ap.ip},
        "before": {"name": ap.name, "ap_id": ap.id},
        "after": {"name": new_name},
        "request": {"method": "POST", "path": "/rest/hostname", "body": build_hostname_payload(ap.ip, new_name)},
        "rollback": rollback,
        "warnings": ["PENDING VERIFICATION: /rest/hostname is documented but not yet tested on your AP-505."],
        "backup_id": backup.id, "verification": "`show aps` is read back and checked for the new name.",
        "confirm_phrase": confirm_phrase(site),
    }
    summary = f"Rename AP '{ap.name}' to '{new_name}' on {site.name}"
    change = _save_plan(db, site, user, "ap_rename", summary,
                        {"ap_id": ap.id, "ap_ip": ap.ip, "hostname": new_name}, plan, backup, rollback_of)
    audit(db, "config_plan", username=user, target=f"site:{site.name}", detail=f"#{change.id} {summary}", request=request)
    return change


def plan_rollback(db: Session, change: ConfigChange, user: str, *, request: Request | None = None,
                  transport: httpx.BaseTransport | None = None) -> ConfigChange:
    if change.status not in ("applied", "applied_unverified"):
        raise PlanError("Only an applied change can be rolled back.")
    site = db.get(Site, change.site_id)
    before = change.plan.get("before")
    if change.kind == "ssid_upsert":
        profile = json.loads(decrypt_secret(change.payload_enc))["ssid"]["profile"]
        if before:
            rb = SsidChange(profile=profile, essid=before.get("essid"),
                            vlan=before.get("vlan") if isinstance(before.get("vlan"), int) else None,
                            rf_band=before.get("rf_band") if before.get("rf_band") in ("2.4", "5.0", "all") else None,
                            enabled=before.get("enabled"))
        else:
            rb = SsidChange(profile=profile, enabled=False)
        return plan_ssid(db, site, user, rb, request=request, transport=transport, rollback_of=change.id)
    if change.kind == "ap_rename":
        ap = db.get(AccessPoint, before["ap_id"])
        if ap is None:
            raise PlanError("The AP no longer exists in SIMS.")
        return plan_rename(db, site, user, ap, before["name"], request=request, transport=transport, rollback_of=change.id)
    if change.kind in ("firmware_stage", "cert_upload"):
        raise PlanError("Firmware and certificate changes cannot be rolled back by SIMS. For firmware, upload and stage "
                        "the previous image; for a certificate, upload the previous one.")
    raise PlanError("Unknown change type.")


# ---- applying ----------------------------------------------------------------------------
def apply_change(db: Session, change: ConfigChange, user: str, confirm_text: str, *, request: Request | None = None,
                 transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    site = db.get(Site, change.site_id)
    _require_writable(site)
    if change.status != "planned":
        raise PlanError(f"This change is already {change.status}; create a new plan.")
    if _aware(change.expires_at) < datetime.now(timezone.utc):
        change.status, change.result = "cancelled", "Plan expired before it was applied."
        if change.kind == "cert_upload":
            from ..certs.service import _wipe
            _wipe(change)
        raise PlanError("This plan expired (15 minutes). Create a new plan.")
    if confirm_text.strip() != confirm_phrase(site):
        raise PlanError(f"Type exactly: {confirm_phrase(site)}")
    from ..config import get_settings
    if get_settings().require_independent_approval:
        appr = (change.plan or {}).get("approval") or {}
        if not appr.get("approved_by"):
            raise PlanError("This change needs independent approval before it can be applied (two-person rule).")
        if appr["approved_by"].casefold() == (change.created_by or "").casefold():
            raise PlanError("The approver must be a different person from the requester.")
    from ..infrastructure.phase_services import control as _control
    if _control(db).emergency_stop:
        raise PlanError("Emergency stop is active; no device writes are allowed.")

    if change.kind == "firmware_stage":
        from ..firmware import upgrade
        return upgrade.apply_stage(db, change, site, user, request=request, transport=transport)
    if change.kind == "cert_upload":
        from ..certs import service as cert_service
        return cert_service.apply_cert(db, change, site, user, request=request)

    payload = json.loads(decrypt_secret(change.payload_enc))
    problems: list[str] = []
    try:
        with client_for_site(site, transport) as c:
            c.login()
            if change.kind == "ssid_upsert":
                ch = SsidChange(**payload["ssid"])
                c.config_post("ssid", build_ssid_payload(ch, creating=payload["creating"]))
            else:
                c.config_post("hostname", build_hostname_payload(payload["ap_ip"], payload["hostname"]))
            change.status, change.applied_at = "applied", utcnow()
            # Read back + post-change backup. Failure here downgrades to applied_unverified, never to "failed".
            try:
                text = fetch_running_config(c)
                post = store_backup(db, site, text, "post-change", user)
                if change.kind == "ssid_upsert":
                    problems = verify_ssid(post.parsed or {}, ch)
                else:
                    rows = parsers.normalize_aps(parsers.extract_rows(c.show("show aps"), "show aps"))
                    if not any(a["name"] == payload["hostname"] for a in rows):
                        problems = [f"AP name '{payload['hostname']}' not seen in `show aps` after the change."]
            except ArubaError as exc:
                problems = [f"Could not read the configuration back: {exc}"]
    except ArubaError as exc:
        if change.status != "applied":
            change.status, change.result = "failed", str(exc)[:500]
            audit(db, "config_apply", username=user, target=f"site:{site.name}", detail=f"#{change.id} FAILED: {exc}"[:240],
                  request=request, success=False)
            return {"ok": False, "status": "failed", "message": str(exc), "problems": []}
        problems = [str(exc)]

    change.status = "applied" if not problems else "applied_unverified"
    change.result = ("Applied and verified." if not problems else "Applied; verification: " + "; ".join(problems))[:500]
    audit(db, "config_apply", username=user, target=f"site:{site.name}",
          detail=f"#{change.id} {change.summary} -> {change.status}"[:240], request=request, success=not problems)
    poll_site(db, site, transport)  # refresh inventory so renamed APs show up immediately
    return {"ok": True, "status": change.status, "message": change.result, "problems": problems}


def approve_change(db: Session, change: ConfigChange, approver: str, approver_role: str, *,
                   request: Request | None = None) -> ConfigChange:
    """Independent approval for legacy SIMS change plans — same two-person rule as INM change plans."""
    if change.status != "planned":
        raise PlanError(f"This change is already {change.status}.")
    if approver_role not in ("approver", "admin"):
        raise PlanError("Approver role required.")
    if approver.casefold() == (change.created_by or "").casefold():
        raise PlanError("The requester cannot approve their own change.")
    plan = dict(change.plan or {})
    plan["approval"] = {"approved_by": approver, "approved_at": utcnow().isoformat()}
    change.plan = plan
    audit(db, "config_change_approved", username=approver, target=f"change:{change.id}",
          detail=f"{change.kind}; requester={change.created_by}", request=request)
    return change
