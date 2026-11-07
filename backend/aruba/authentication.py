"""Building authenticated clients from stored Site records (credentials decrypted in memory only)."""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..database.models import Site
from ..security.crypto import decrypt_secret
from .client import ArubaClient, ArubaTarget
from .errors import ArubaError


def client_for_site(site: Site, transport: httpx.BaseTransport | None = None) -> ArubaClient:
    if not site.vc_host or not site.api_username or not site.api_password_enc:
        raise ArubaError("Site has no Virtual Controller address or credentials configured.")
    s = get_settings()
    target = ArubaTarget(
        host=site.vc_host,
        port=site.vc_port,
        username=site.api_username,
        password=decrypt_secret(site.api_password_enc),
    )
    return ArubaClient(
        target, timeout=s.aruba_timeout_seconds, verify=s.tls_verify_arg, transport=transport
    )


def test_connection(site: Site, transport: httpx.BaseTransport | None = None) -> dict:
    """Login + `show aps` + `show version`. Returns a summary; raises ArubaError on failure."""
    from .monitoring import collect_snapshot
    with client_for_site(site, transport) as c:
        c.login()
        snap = collect_snapshot(c)
    return {
        "ok": True,
        "aps_found": len(snap.aps),
        "clients_found": len(snap.clients),
        "firmware": snap.firmware,
        "warnings": snap.warnings,
    }
