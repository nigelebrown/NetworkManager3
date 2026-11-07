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
from ..database.models import ConfigChange, FirmwareImage, Site, utcnow
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
        if images.image_path(i.filename) is None:
            raise cfg.PlanError(f"The stored file for {i.filename} is missing on disk. Delete and upload it again.")
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
        "images": [{"ap_class": i.ap_class, "filename": i.filename, "version": i.version, "size_bytes": i.size_bytes,
                    "sha256": i.sha256[:16], "url": _masked(urls[i.ap_class])} for i in imgs],
        "warnings": warnings,
        "rollback": "SIMS cannot undo a staged image. To abandon it, do not reboot; to go back, stage the previous image.",
    }
    audit(db, "firmware_plan", username=user, target=f"site:{site.name}", detail=f"#{ch.id} {ch.summary}"[:240], request=request)
    return ch


def apply_stage(db: Session, change: ConfigChange, site: Site, user: str, *, request: Request | None,
                transport: httpx.BaseTransport | None) -> dict[str, Any]:
    payload = json.loads(decrypt_secret(change.payload_enc))
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
