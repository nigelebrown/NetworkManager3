"""Low-level Aruba Instant REST client (read-only).

Endpoints used (Aruba Instant 8.x REST API guide):
    POST /rest/login      body {"user": ..., "passwd": ...}      -> {"Status": "Success", "sid": "..."}
    GET  /rest/show-cmd   ?iap_ip_addr=<vc>&cmd=<show ...>&sid=  -> JSON containing the command output
    POST /rest/logout     body {"sid": ...}

STATUS: login + show-cmd are documented for 8.x. The exact JSON *shape* of each show-cmd
response varies by command, so parsing lives in `parsers.py` and is deliberately tolerant.
Use `python -m backend.verify_live` against your AP to capture real responses.

Safety:
  * Only commands matching READ_ONLY_COMMANDS can be sent (no config commands in Phase 1).
  * Commands are passed as URL parameters (httpx encodes them); never interpolated into a shell.
  * Passwords and session ids are never logged.
  * TLS verification follows settings (on by default).
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import (
    ArubaAuthError, ArubaCommandError, ArubaConnectionError, ArubaRestDisabledError, ArubaUnsupportedError,
)

log = logging.getLogger("sims.aruba")

# Allowlist: a command must start with one of these AND contain only safe characters.
READ_ONLY_COMMANDS = (
    "show aps",
    "show clients",
    "show version",
    "show summary",
    "show ap-env",
    "show ap bss-table",
    "show ap arm rf-summary",
    "show ap arm history",
    "show ap monitor ap-list",
    "show radio-stats",
    "show arm rf-summary",
    "show ap debug radio-stats",
    "show ap debug client-table",
    "show ap debug system-status",
    "show log system",
    "show alert",
    "show ap association",
    "show running-config",
)
# Config/action endpoints this app may POST to (Aruba Instant 8.10 REST API Guide).
CONFIG_ENDPOINTS = ("ssid", "hostname", "os-upgrade")
_SAFE_CMD = re.compile(r"^[a-z0-9][a-z0-9 _\-\.:]{0,120}$", re.IGNORECASE)


def validate_command(cmd: str) -> str:
    # Reject control characters (newlines, tabs, NUL...) BEFORE normalizing whitespace, so an
    # embedded newline can never be collapsed into a harmless-looking space.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cmd):
        raise ArubaUnsupportedError("Command contains control characters.")
    cmd = " ".join(cmd.strip().split(" "))
    cmd = re.sub(r" {2,}", " ", cmd)
    if not _SAFE_CMD.match(cmd):
        raise ArubaUnsupportedError("Command contains characters that are not permitted.")
    lowered = cmd.lower()
    if not any(lowered == c or lowered.startswith(c + " ") for c in READ_ONLY_COMMANDS):
        raise ArubaUnsupportedError(
            f"Command '{cmd}' is not on the read-only allowlist (Phase 1 is read-only)."
        )
    return cmd


@dataclass
class ArubaTarget:
    host: str
    port: int = 4343
    username: str = ""
    password: str = ""

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"


class ArubaClient:
    """One authenticated session to one Instant Virtual Controller."""

    def __init__(
        self,
        target: ArubaTarget,
        *,
        timeout: float = 10.0,
        verify: bool | str = True,
        transport: httpx.BaseTransport | None = None,
    ):
        self.target = target
        self._sid: str | None = None
        if verify is False:
            log.warning(
                "TLS certificate verification is DISABLED for %s (SIMS_ARUBA_VERIFY_TLS=false). "
                "Use only for lab/development.", target.host,
            )
        self._http = httpx.Client(
            base_url=target.base_url,
            timeout=httpx.Timeout(timeout),
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> "ArubaClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._sid:
                self.logout()
        finally:
            self._http.close()

    # -- auth ------------------------------------------------------------------
    @property
    def logged_in(self) -> bool:
        return self._sid is not None

    def login(self) -> None:
        try:
            resp = self._http.post(
                "/rest/login",
                json={"user": self.target.username, "passwd": self.target.password},
            )
        except httpx.HTTPError as exc:
            raise ArubaConnectionError(self._describe(exc)) from None
        try:
            data = resp.json()
        except ValueError:
            raise ArubaRestDisabledError(
                f"The controller did not answer like a REST API (HTTP {resp.status_code}, not JSON). The REST API is "
                "off by default on Aruba Instant; enable it from Sites -> REST API."
            ) from None
        if resp.status_code == 404:
            raise ArubaRestDisabledError(
                "The controller returned 404 for /rest/login. The REST API is probably disabled (the default); "
                "enable it from Sites -> REST API."
            )
        sid = data.get("sid") if isinstance(data, dict) else None
        status = str(data.get("Status", "")).lower() if isinstance(data, dict) else ""
        if resp.status_code != 200 or not sid or status not in ("success", ""):
            msg = data.get("Message") if isinstance(data, dict) else None
            raise ArubaAuthError(f"Login rejected (HTTP {resp.status_code}). {msg or ''}".strip())
        self._sid = str(sid)
        log.info("Logged in to Aruba VC %s", self.target.host)

    def logout(self) -> None:
        sid, self._sid = self._sid, None
        if not sid:
            return
        try:
            self._http.post("/rest/logout", json={"sid": sid})
        except httpx.HTTPError:
            pass  # best effort

    # -- configuration writes (guarded) --------------------------------------------------
    def config_post(self, endpoint: str, body: dict[str, Any]) -> str:
        """POST a configuration/action call. Returns the device's message on success.

        Guards (all enforced here so no caller can bypass them):
          * SIMS_ALLOW_CONFIG_CHANGES must be true;
          * only endpoints in CONFIG_ENDPOINTS are reachable;
          * never retried automatically (a retry could apply a change twice).
        Per the Aruba Instant 8.10 REST API Guide, config calls are HTTP POST to the *master* AP.
        """
        from ..config import get_settings
        if not get_settings().device_writes_permitted:
            raise ArubaUnsupportedError("Configuration changes are disabled (SIMS_ALLOW_CONFIG_CHANGES=false).")
        if endpoint not in CONFIG_ENDPOINTS:
            raise ArubaUnsupportedError(f"Endpoint '{endpoint}' is not permitted.")
        if not self._sid:
            self.login()
        try:
            resp = self._http.post(f"/rest/{endpoint}", params={"sid": self._sid}, json=body)
        except httpx.HTTPError as exc:
            raise ArubaConnectionError(self._describe(exc)) from None
        try:
            data = resp.json()
        except ValueError:
            raise ArubaCommandError(f"'{endpoint}': non-JSON response (HTTP {resp.status_code}).") from None
        if not isinstance(data, dict):
            raise ArubaCommandError(f"'{endpoint}': unexpected response type.")
        message = str(data.get("message") or data.get("Message") or data.get("Status") or "")[:200]
        code = data.get("Status-code", data.get("Status"))
        ok = resp.status_code == 200 and (str(code) in ("0", "Success", "success") or
                                          message.lower() == "success")
        if not ok:
            raise ArubaCommandError(f"'{endpoint}' rejected by the device (code {code}): {message}")
        return message or "Success"

    # -- commands ------------------------------------------------------------------
    def show(self, cmd: str, *, iap_ip: str | None = None, _retry: bool = True) -> dict[str, Any]:
        """Run a read-only `show` command and return the raw JSON dict.

        `iap_ip` runs the command on another AP of the cluster (REST `iap_ip_addr`); default is the AP we
        are connected to. It must be a literal IP address, never free text.
        """
        cmd = validate_command(cmd)
        if iap_ip is not None:
            try:
                iap_ip = str(ipaddress.ip_address(iap_ip.strip()))
            except ValueError:
                raise ArubaUnsupportedError("iap_ip must be an IP address.") from None
        if not self._sid:
            self.login()
        try:
            resp = self._http.get(
                "/rest/show-cmd",
                params={"iap_ip_addr": iap_ip or self.target.host, "cmd": cmd, "sid": self._sid},
            )
        except httpx.HTTPError as exc:
            raise ArubaConnectionError(self._describe(exc)) from None

        if resp.status_code in (401, 403) and _retry:
            self._sid = None
            return self.show(cmd, iap_ip=iap_ip, _retry=False)
        try:
            data = resp.json()
        except ValueError:
            raise ArubaCommandError(
                f"'{cmd}': non-JSON response (HTTP {resp.status_code})."
            ) from None
        if not isinstance(data, dict):
            raise ArubaCommandError(f"'{cmd}': unexpected response type {type(data).__name__}.")

        status = str(data.get("Status", "")).lower()
        if status and status != "success":
            msg = str(data.get("Message") or data.get("Status") or "unknown error")
            # An expired sid is reported in the body on some firmware: retry once.
            if _retry and ("sid" in msg.lower() or "session" in msg.lower() or "auth" in msg.lower()):
                self._sid = None
                return self.show(cmd, iap_ip=iap_ip, _retry=False)
            raise ArubaCommandError(f"'{cmd}' failed: {msg}")
        return data

    @staticmethod
    def _describe(exc: httpx.HTTPError) -> str:
        """Human-readable, secret-free description of a transport error."""
        text = str(exc)
        if isinstance(exc, httpx.ConnectTimeout | httpx.ReadTimeout | httpx.PoolTimeout):
            return "Timed out contacting the Virtual Controller."
        if isinstance(exc, httpx.ConnectError):
            if "CERTIFICATE_VERIFY_FAILED" in text or "certificate" in text.lower():
                return (
                    "TLS certificate verification failed. The AP uses a self-signed certificate: "
                    "either provide SIMS_ARUBA_CA_BUNDLE or (lab only) set SIMS_ARUBA_VERIFY_TLS=false."
                )
            return "Connection failed (host unreachable, refused, or port blocked)."
        return f"HTTP error talking to the Virtual Controller: {type(exc).__name__}."
