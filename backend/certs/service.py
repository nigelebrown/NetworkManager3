"""Plan / apply an upload of the WebUI + REST server certificate (CLI `uiserver cert`), with the same typed-confirmation
workflow as other configuration changes.

Flow at apply time: open a one-shot TFTP server holding the file in memory -> SSH to the VC (pinned host key) and run
the copy command -> wait for the AP to fetch the file -> check that port 4343 now presents the uploaded certificate.
The private key and passphrase live only Fernet-encrypted in the change row and are wiped as soon as it is applied.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import Request
from sqlalchemy.orm import Session

from ..aruba import onboarding, ssh
from ..aruba.errors import ArubaError
from ..config import get_settings
from ..configmgmt import service as cfg
from ..database.models import ConfigChange, Site, utcnow
from ..firmware import images
from ..security.crypto import decrypt_secret, encrypt_secret
from ..security.deps import audit
from . import bundle
from .tftp import TftpOnce

VERIFY_SECONDS = 90
FETCH_WAIT_SECONDS = 30  # how long to wait for the AP to download the file after the command is accepted


def _tftp_ip() -> str:
    """The IPv4 address the AP should fetch the file from: the host of SIMS_PUBLIC_BASE_URL."""
    try:
        host = urlparse(images.public_base()).hostname or ""
        socket.inet_aton(host)
    except (images.ImageError, OSError):
        raise cfg.PlanError("Set SIMS_PUBLIC_BASE_URL to http://<this computer's LAN IPv4>:8000 in .env and restart; "
                            "the AP downloads the certificate from that address.") from None
    return host


def plan_cert(db: Session, site: Site, user: str, pem: bytes, passphrase: str, *, request: Request | None = None) -> ConfigChange:
    cfg._require_writable(site)
    if not site.vc_host:
        raise cfg.PlanError("This site has no Virtual Controller address.")
    try:
        info = bundle.inspect_bundle(pem, passphrase, host=site.vc_host)
    except bundle.BundleError as exc:
        raise cfg.PlanError(str(exc)) from None
    tftp_ip = _tftp_ip()
    scan = onboarding.scan_ssh(site)
    if not scan.get("fingerprint"):
        raise cfg.PlanError(scan.get("error") or "Could not read the AP's SSH host key (is SSH, TCP 22, reachable from SIMS?).")
    name = "cert-" + hashlib.sha256(pem).hexdigest()[:10] + ".pem"
    payload = {"pem": base64.b64encode(pem).decode(), "passphrase": passphrase, "tftp_ip": tftp_ip, "filename": name,
               "ssh_fingerprint": scan["fingerprint"], "sha256": info["sha256"]}
    ch = cfg._save_plan(db, site, user, "cert_upload", f"Upload WebUI/REST certificate {info['subject_cn'] or '(no CN)'}",
                        payload, {}, None)
    ch.plan = {
        "kind": "cert_upload", "confirm_phrase": cfg.confirm_phrase(site), "target": "WebUI / REST server certificate (CLI: uiserver cert)", "certificate": info,
        "ssh_host_key": scan["fingerprint"], "tftp_from": f"{tftp_ip}:{get_settings().tftp_port} (UDP)",
        "warnings": info["warnings"] + [
            "The file crosses the network over TFTP (not encrypted). The private key inside is passphrase-protected, and the "
            "server runs for seconds, accepts only this AP's address and only this file.",
            "The passphrase is typed into the AP command line and may appear in the AP's own command history.",
            "The AP's web server may restart; REST sessions and the Web UI drop briefly. If the new certificate is not signed "
            "by a CA trusted on this computer, set SIMS_ARUBA_CA_BUNDLE or SIMS will fail TLS verification on the next poll.",
            "SIMS needs permission to listen on UDP port 69 (Linux: grant it with setcap; Windows: allow it in the firewall).",
        ],
        "rollback": "SIMS cannot undo this. Keep the previous certificate and upload it the same way to go back.",
    }
    audit(db, "cert_plan", username=user, target=f"site:{site.name}", detail=f"#{ch.id} {info['subject_cn']} sha256 {info['sha256'][:12]}"[:240],
          request=request)
    return ch


def _peer_sha256(host: str, port: int, timeout: float = 5.0) -> str | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # reading what is presented, not trusting it
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(binary_form=True)
        return hashlib.sha256(der).hexdigest() if der else None
    except (OSError, ssl.SSLError):
        return None


def _wipe(change: ConfigChange) -> None:
    change.payload_enc = encrypt_secret("{}")


def apply_cert(db: Session, change: ConfigChange, site: Site, user: str, *, request: Request | None) -> dict[str, Any]:
    p = json.loads(decrypt_secret(change.payload_enc))
    s = get_settings()
    outcome: dict[str, Any]
    server = None
    try:
        try:
            server = TftpOnce(p["filename"], base64.b64decode(p["pem"]), site.vc_host, s.tftp_bind, s.tftp_port).start()
        except OSError as exc:
            raise ArubaError(f"SIMS {exc}. On Linux grant the permission with: sudo setcap 'cap_net_bind_service=+ep' "
                             f"$(readlink -f .venv/bin/python3). On Windows allow UDP 69 in the firewall.") from None
        ssh.copy_uiserver_cert(site.vc_host, site.api_username, decrypt_secret(site.api_password_enc), p["ssh_fingerprint"],
                               tftp_ip=p["tftp_ip"], filename=p["filename"], passphrase=p["passphrase"])
        if not server.wait(FETCH_WAIT_SECONDS):
            raise ArubaError("The AP accepted the command but never downloaded the file from SIMS (check that UDP 69 reaches "
                             "this computer and SIMS_PUBLIC_BASE_URL is this computer's address as the AP sees it).")
        deadline, seen = time.monotonic() + VERIFY_SECONDS, None
        while time.monotonic() < deadline:
            seen = _peer_sha256(site.vc_host, site.vc_port)
            if seen == p["sha256"]:
                break
            time.sleep(5)
        verified = seen == p["sha256"]
        change.status, change.applied_at = ("applied" if verified else "applied_unverified"), utcnow()
        change.result = ("Certificate uploaded and the AP now presents it." if verified else
                         "Certificate uploaded, but the AP is not presenting it yet (it may need a restart of its web server or a "
                         "reboot). Check again later.")
        outcome = {"ok": True, "status": change.status, "message": change.result, "problems": [] if verified else [change.result]}
    except ArubaError as exc:
        change.status, change.result = "failed", str(exc)[:500]
        outcome = {"ok": False, "status": "failed", "message": str(exc), "problems": []}
    finally:
        if server:
            server.stop()
        _wipe(change)  # key + passphrase gone from the database whatever happened
    audit(db, "cert_apply", username=user, target=f"site:{site.name}", detail=f"#{change.id} -> {change.status}"[:240],
          request=request, success=change.status == "applied")
    return outcome
