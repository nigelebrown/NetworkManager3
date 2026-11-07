"""Check whether a site's REST API is on, and enable it (over SSH) when it is not."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..database.models import Site
from ..security.crypto import decrypt_secret
from . import ssh
from .authentication import client_for_site
from .errors import ArubaAuthError, ArubaConnectionError, ArubaError, ArubaRestDisabledError

log = logging.getLogger("sims.aruba.onboarding")
REST_WAIT_SECONDS = 45  # how long to wait for REST to come up after `commit apply`
SSH_PORT = 22


def confirm_phrase(site: Site) -> str:
    return f"ENABLE REST API {site.name}"


def check_rest_api(site: Site, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Try a REST login. rest_enabled: True / False / None (could not tell, e.g. unreachable or TLS failure)."""
    try:
        with client_for_site(site, transport) as c:
            c.login()
        return {"rest_enabled": True, "detail": "REST API is enabled and the login works."}
    except ArubaRestDisabledError as exc:
        return {"rest_enabled": False, "detail": str(exc)}
    except ArubaAuthError as exc:  # JSON answer, so REST is on, but the credentials were refused
        return {"rest_enabled": True, "detail": f"REST API is enabled but the login was refused: {exc}"}
    except ArubaConnectionError as exc:
        return {"rest_enabled": None, "detail": f"Could not reach the controller: {exc}"}


def scan_ssh(site: Site) -> dict[str, Any]:
    try:
        return ssh.scan_host_key(site.vc_host, SSH_PORT)
    except ArubaError as exc:
        return {"error": str(exc)}


def enable_rest_api(site: Site, expected_fingerprint: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """SSH in, run the fixed enable sequence, then wait until REST answers. Never raises for device-side failures."""
    try:
        steps = ssh.enable_rest_api(site.vc_host, site.api_username, decrypt_secret(site.api_password_enc),
                                    expected_fingerprint, SSH_PORT)
    except ArubaError as exc:
        return {"ok": False, "rest_enabled": False, "message": str(exc), "steps": []}
    deadline = time.monotonic() + REST_WAIT_SECONDS
    state = check_rest_api(site, transport)
    while state["rest_enabled"] is not True and time.monotonic() < deadline:
        time.sleep(3)
        state = check_rest_api(site, transport)
    if state["rest_enabled"] is True:
        return {"ok": True, "rest_enabled": True, "steps": steps,
                "message": "REST API enabled and answering. You can now test the connection and poll the site."}
    return {"ok": False, "rest_enabled": False, "steps": steps,
            "message": "The commands were accepted but REST did not answer within "
                       f"{REST_WAIT_SECONDS}s. Wait a minute and use Check again; if it stays off, check the AP "
                       "(REST may need to be enabled from its own CLI/UI)."}
