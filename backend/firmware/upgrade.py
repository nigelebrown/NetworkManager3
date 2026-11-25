"""Stage a firmware upgrade on a cluster: the Virtual Controller downloads the image from SIMS and syncs it to
the other APs. SIMS requests `auto-reboot: no`, so nothing restarts until an admin reboots the cluster."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..aruba.authentication import client_for_site
from ..aruba.errors import ArubaError
from ..configmgmt import service as cfg
from ..database.models import AccessPoint, ConfigChange, FirmwareImage, Site, utcnow
from ..security.crypto import decrypt_secret
from ..security.deps import audit
from . import compliance, images


def _masked(url: str) -> str:
    return re.sub(r"/fw/(\d+)/[0-9a-f]{64}/", r"/fw/\1/<secret-token>/", url)


def plan_upgrade(db: Session, site: Site, user: str, image_ids: list[int], *, allow_downgrade: bool = False,
                 request: Request | None = None) -> ConfigChange:
    cfg._require_writable(site)
    imgs = [db.get(FirmwareImage, i) for i in dict.fromkeys(image_ids)]
    if not imgs or any(i is None for i in imgs):
        raise cfg.PlanError("Choose at least one stored firmware image.")
    classes = [i.ap_class for i in imgs]
    if len(set(classes)) != len(classes):
        raise cfg.PlanError("Choose at most one image per AP class.")
    if len({i.version for i in imgs}) != 1:
        raise cfg.PlanError("All chosen images must be the same version.")
    for i in imgs:
        try:
            images.verify_image(i.filename, i.sha256, i.size_bytes)
        except images.ImageError as exc:
            raise cfg.PlanError(str(exc)) from None
    try:
        images.public_base()
    except images.ImageError as exc:
        raise cfg.PlanError(str(exc)) from None

    running = compliance.site_version(db, site.id)
    r, t = compliance.parse_version(running), compliance.parse_version(imgs[0].version)
    warnings = ["Stage only: the image is downloaded and synced to the APs, but nothing reboots. The new version runs "
                "only after you reboot the cluster yourself.",
                "The Virtual Controller must be able to reach SIMS at the address below; SIMS cannot test that from here."]
    if r and t and t == r:
        raise cfg.PlanError(f"The cluster already runs {compliance.fmt(r)}.")
    if r and t and t < r:
        if not allow_downgrade:
            raise cfg.PlanError(f"{imgs[0].version} is older than the running {compliance.fmt(r)}. Tick 'allow downgrade' if that is intended.")
        warnings.append("DOWNGRADE: the chosen version is older than the one running now.")

    ch = cfg._save_plan(db, site, user, "firmware_stage", f"Stage firmware {imgs[0].version} ({', '.join(classes)})",
                        {}, {}, None)
    urls = {i.ap_class: images.download_url(ch.id, i.filename) for i in imgs}
    body = {"upgrade-info": {"auto-reboot": "no", **{f"{c}-url": u for c, u in urls.items()}}}
    ch.payload_enc = cfg.encrypt_secret(json.dumps({"body": body, "files": [i.filename for i in imgs]}))
    ch.plan = {
        "kind": "firmware_stage", "confirm_phrase": cfg.confirm_phrase(site), "running": compliance.fmt(r), "target": imgs[0].version, "auto_reboot": "no",
        "expected_members": [{"serial": ap.serial, "model": ap.model, "ip": ap.ip}
                             for ap in db.scalars(select(AccessPoint).where(AccessPoint.site_id == site.id))],
        "images": [{"ap_class": i.ap_class, "filename": i.filename, "version": i.version, "size_bytes": i.size_bytes,
                    "sha256": i.sha256, "url": _masked(urls[i.ap_class])} for i in imgs],
        "warnings": warnings,
        "rollback": "SIMS cannot undo a staged image. To abandon it, do not reboot; to go back, stage the previous image.",
    }
    audit(db, "firmware_plan", username=user, target=f"site:{site.name}", detail=f"#{ch.id} {ch.summary}"[:240], request=request)
    return ch


def apply_stage(db: Session, change: ConfigChange, site: Site, user: str, *, request: Request | None,
                transport: httpx.BaseTransport | None) -> dict[str, Any]:
    payload = json.loads(decrypt_secret(change.payload_enc))
    try:
        planned = (change.plan or {}).get("images") or []
        if not planned or {i.get("filename") for i in planned} != set(payload.get("files") or []):
            raise images.ImageError("Firmware plan is incomplete; create a new plan.")
        for item in planned:
            images.verify_image(item["filename"], item.get("sha256"), item.get("size_bytes"))
    except images.ImageError as exc:
        change.status, change.result = "failed", str(exc)
        audit(db, "firmware_integrity_refused", username=user, target=f"site:{site.name}",
              detail=f"#{change.id} image integrity validation failed", request=request, success=False)
        return {"ok": False, "status": "failed", "message": str(exc), "problems": []}
    try:
        with client_for_site(site, transport) as c:
            c.login()
            msg = c.config_post("os-upgrade", payload["body"])
    except ArubaError as exc:
        change.status, change.result = "failed", str(exc)[:500]
        audit(db, "firmware_apply", username=user, target=f"site:{site.name}", detail=f"#{change.id} FAILED: {exc}"[:240],
              request=request, success=False)
        return {"ok": False, "status": "failed", "message": str(exc), "problems": []}
    # The download links become valid now (applied_at starts their time window).
    change.status, change.applied_at = "applied", utcnow()
    change.result = (f"The Virtual Controller accepted the request ({msg}). It will download the image from SIMS and sync it "
                     "to the other APs. Nothing was rebooted. Compliance changes only after you reboot the cluster.")[:500]
    audit(db, "firmware_apply", username=user, target=f"site:{site.name}", detail=f"#{change.id} {change.summary} -> applied"[:240],
          request=request)
    return {"ok": True, "status": "applied", "message": change.result, "problems": []}


def verify_members(db: Session, change: ConfigChange, site: Site, actor: str, *, request=None, transport=None) -> dict:
    """Read-only verification of all planned members after an operator-controlled reboot.

    A controller-reported aggregate version is not accepted as member proof.
    Do not update inventory or certification based on failed or missing identity.
    """
    from ipaddress import IPv4Address
    from fastapi import HTTPException
    from ..api.quick_devices import _permit_probe
    from ..aruba import parsers
    if change.kind != "firmware_stage" or change.status not in ("applied", "applied_unverified"):
        raise cfg.PlanError("Only a staged firmware change can be verified.")
    if site.simulated or not site.enabled:
        raise cfg.PlanError("A live enabled cluster is required.")
    planned = change.plan or {}
    members = planned.get("expected_members") or []
    target = compliance.parse_version(planned.get("target"))
    expected = {str(x.get("serial") or "").strip().casefold(): x for x in members}
    if not members or "" in expected or len(expected) != len(members) or not target or len(members) > 128:
        raise cfg.PlanError("A bounded, complete pre-upgrade serial inventory is required for member verification.")
    observed = []
    seen = set()
    failure = None
    try:
        _permit_probe(IPv4Address(site.vc_host))
        with client_for_site(site, transport) as client:
            aps = parsers.normalize_aps(parsers.extract_rows(client.show("show aps"), "show aps"))
            if len(aps) > 128:
                raise ValueError("Membership exceeds verification limit")
            for ap in aps:
                serial = str(ap.get("serial") or "").strip().casefold()
                ip = ap.get("ip")
                row = {"serial": ap.get("serial"), "ip": ip, "model": ap.get("model"),
                       "version": None, "state": "IDENTITY_UNVERIFIED"}
                observed.append(row)
                if not serial or serial in seen or serial not in expected:
                    row["state"] = "UNEXPECTED_OR_DUPLICATE_MEMBER"
                    continue
                seen.add(serial)
                try:
                    _permit_probe(IPv4Address(ip))
                    summary = parsers.output_text(client.show("show summary", iap_ip=ip)) or ""
                    if len(summary) > 100000:
                        continue
                    actual = re.search(r"(?im)^Serial Number\s*:\s*(\S+)\s*$", summary)
                    actual_ip = re.search(r"(?im)^IP Address\s*:\s*(\S+)\s*$", summary)
                    if not actual or actual[1].casefold() != serial or not actual_ip or actual_ip[1] != ip:
                        continue
                    version = parsers.parse_version(client.show("show version", iap_ip=ip))
                    row["version"] = version
                    row["state"] = ("VERIFIED_TARGET" if compliance.parse_version(version) == target else
                                    "VERSION_MISMATCH" if compliance.parse_version(version) else "VERSION_UNKNOWN")
                except (ArubaError, ValueError, TypeError, HTTPException):
                    row["state"] = "READ_FAILED"
    except (ArubaError, ValueError, TypeError, HTTPException) as exc:
        failure = type(exc).__name__
    missing = [expected[s]["serial"] for s in expected.keys() - seen]
    ok = (not failure and not missing and len(observed) == len(expected)
          and all(row["state"] == "VERIFIED_TARGET" for row in observed))
    result = {"state": "VERIFIED_RUNNING_TARGET" if ok else "UNVERIFIED",
              "checked_at": utcnow().isoformat(), "target": planned.get("target"),
              "members": observed, "missing_serials": sorted(missing), "failure": failure,
              "note": "Running version verification only; device health, reboot completion and re-certification remain separate."}
    change.plan = {**planned, "member_verification": result}
    audit(db, "firmware_members_verified", username=actor, target=f"site:{site.name}",
          detail=f"change={change.id} state={result['state']} expected={len(expected)} observed={len(observed)}",
          request=request, success=ok)
    return result


def plan_recovery(db: Session, change: ConfigChange, site: Site, actor: str, image_ids: list[int], *, request=None):
    """Create a separately reviewable stage-only rollback to the recorded prior release."""
    if change.kind != "firmware_stage" or change.status not in ("applied", "applied_unverified"):
        raise cfg.PlanError("Only a previously staged firmware change has a recovery plan.")
    prior = (change.plan or {}).get("running")
    if not compliance.parse_version(prior):
        raise cfg.PlanError("The previous firmware version is unknown; use vendor-guided recovery.")
    expected_classes = {i.get("ap_class") for i in (change.plan or {}).get("images", [])}
    recovery_images = [db.get(FirmwareImage, i) for i in dict.fromkeys(image_ids)]
    if not recovery_images or any(i is None for i in recovery_images):
        raise cfg.PlanError("Upload the exact previous firmware images first.")
    if {i.ap_class for i in recovery_images} != expected_classes or any(
            compliance.parse_version(i.version) != compliance.parse_version(prior) for i in recovery_images):
        raise cfg.PlanError("Recovery images must match every original AP class and the recorded prior version.")
    plan = plan_upgrade(db, site, actor, image_ids, allow_downgrade=True, request=request)
    plan.rollback_of = change.id
    plan.plan = {**plan.plan, "recovery_of": change.id,
                 "recovery_note": "Separate review and confirmation required; manual reboot remains mandatory."}
    audit(db, "firmware_recovery_plan", username=actor, target=f"site:{site.name}",
          detail=f"change={change.id} recovery={plan.id}", request=request)
    return plan
