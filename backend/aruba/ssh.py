"""Enable the Aruba Instant REST API over SSH.

The REST API is OFF by default and cannot switch itself on, so this is the one place SIMS uses SSH. It runs a
FIXED command list (no user-supplied text ever reaches the AP's shell, so there is nothing to inject into).

Safety:
  * disabled unless SIMS_ALLOW_CONFIG_CHANGES=true;
  * the SSH host key is checked against a fingerprint the administrator confirmed BEFORE the password is sent;
  * the password goes only through SSH authentication, never into the shell, logs or return values.

STATUS: the command sequence (`configure terminal`, `allow-rest-api`, `end`, `commit apply`) follows Aruba's
Instant 8.10 REST API Guide. The SSH session handling is PENDING verification on a real AP-505.
Rollback (manual, on the AP CLI):  configure terminal / no allow-rest-api / end / commit apply
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import socket
import time
from typing import Any

import paramiko

from ..config import get_settings
from .errors import ArubaAuthError, ArubaConnectionError, ArubaSshError, ArubaUnsupportedError

log = logging.getLogger("sims.aruba.ssh")

ENABLE_REST_COMMANDS = ("configure terminal", "allow-rest-api", "end", "commit apply")
ROLLBACK_TEXT = "On the AP CLI: configure terminal / no allow-rest-api / end / commit apply"
_ERRORS = ("% parse error", "parse error", "invalid input", "command not found", "unknown command",
           "% invalid", "incomplete command", "permission denied", "not authorized")
LEGACY_SSH_HELP = (
    "SIMS tried modern SSH algorithms and then the legacy ssh-rsa / SHA-1 set, and the AP accepted neither, "
    "so SIMS cannot enable the REST API for you on this AP. Do it by hand from a terminal: "
    "ssh -o HostKeyAlgorithms=+ssh-rsa admin@{host}   "
    "(add -o PubkeyAcceptedAlgorithms=+ssh-rsa only if you log in with a key), then enter: "
    "configure terminal / allow-rest-api / end / commit apply. Then use Check again."
)
_PROMPT = re.compile(r"[#>]\s?$")


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style SHA256 fingerprint (what PuTTY / `ssh` display on first connect)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


# Algorithms only older Instant SSH servers still need. Never offered on the first attempt.
_LEGACY_ALGS = {
    "keys": ["ssh-rsa"],
    "kex": ["diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1", "diffie-hellman-group-exchange-sha1"],
}


def _open_transport(host: str, port: int, timeout: float):  # patched in tests
    """Connect with modern algorithms only; if the AP offers nothing we accept, retry once allowing the legacy
    ssh-rsa / SHA-1 set (the equivalent of `ssh -o HostKeyAlgorithms=+ssh-rsa`). The returned transport has
    `sims_legacy` set when the fallback was used. The pinned host-key fingerprint is still enforced by callers."""
    for legacy in (False, True):
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise ArubaConnectionError(f"SSH connection to {host}:{port} failed: {exc.strerror or type(exc).__name__}") from None
        t = paramiko.Transport(sock, disabled_algorithms=None if legacy else _LEGACY_ALGS)
        t.banner_timeout = timeout
        try:
            t.start_client(timeout=timeout)
        except paramiko.ssh_exception.IncompatiblePeer:
            t.close()
            if legacy:
                raise ArubaSshError(LEGACY_SSH_HELP.format(host=host)) from None
            continue
        except (paramiko.SSHException, EOFError, OSError) as exc:
            t.close()
            raise ArubaSshError(f"SSH negotiation with {host} failed: {type(exc).__name__}") from None
        t.sims_legacy = legacy
        if legacy:
            log.warning("SSH to %s needed legacy algorithms (ssh-rsa / SHA-1); host key is still pinned.", host)
        return t
    raise ArubaSshError(LEGACY_SSH_HELP.format(host=host))  # unreachable; keeps type checkers happy


def scan_host_key(host: str, port: int = 22, timeout: float = 8.0) -> dict[str, str]:
    """Read the AP's SSH host key WITHOUT authenticating (no credentials are sent)."""
    t = _open_transport(host, port, timeout)
    try:
        key = t.get_remote_server_key()
        return {"fingerprint": fingerprint(key), "key_type": key.get_name(),
                "legacy_algorithms": bool(getattr(t, "sims_legacy", False))}
    finally:
        t.close()


def _read_until_prompt(chan: Any, timeout: float) -> str:
    buf, end = "", time.monotonic() + timeout
    while time.monotonic() < end:
        if chan.recv_ready():
            buf += chan.recv(4096).decode("utf-8", "replace")
            last = buf.rstrip("\r\n").split("\n")[-1] if buf.strip() else ""
            if _PROMPT.search(last.rstrip()) or _PROMPT.search(last):
                return buf
        else:
            time.sleep(0.05)
    raise ArubaSshError("Timed out waiting for the AP's CLI prompt.")


def enable_rest_api(host: str, username: str, password: str, expected_fingerprint: str, port: int = 22,
                    timeout: float = 20.0) -> list[dict[str, Any]]:
    """Run the fixed enable sequence. Returns [{'command', 'ok'}...]. Raises ArubaError subclasses on failure."""
    if not get_settings().device_writes_permitted:
        raise ArubaUnsupportedError("Configuration changes are disabled (SIMS_ALLOW_CONFIG_CHANGES=false).")
    t = _open_transport(host, port, timeout)
    try:
        actual = fingerprint(t.get_remote_server_key())
        if actual != expected_fingerprint:
            # Abort BEFORE authenticating so the password is never offered to an unverified host.
            raise ArubaSshError("The AP's SSH host key does not match the fingerprint you confirmed. Nothing was sent. "
                                "Re-run the check and verify the new fingerprint.")
        try:
            t.auth_password(username, password)
        except paramiko.AuthenticationException:
            raise ArubaAuthError("SSH login rejected (check the username/password).") from None
        except (paramiko.SSHException, EOFError, OSError) as exc:
            raise ArubaSshError(f"SSH login failed: {type(exc).__name__}") from None
        chan = t.open_session()
        chan.get_pty()
        chan.invoke_shell()
        _read_until_prompt(chan, timeout)  # banner / first prompt
        results = []
        for cmd in ENABLE_REST_COMMANDS:
            chan.send(cmd + "\n")
            out = _read_until_prompt(chan, timeout)
            reply = [ln for ln in out.replace("\r", "").split("\n")[1:] if ln.strip()]  # drop the echoed command
            bad = next((ln for ln in reply if any(m in ln.lower() for m in _ERRORS)), None)
            if bad:
                results.append({"command": cmd, "ok": False})
                raise ArubaSshError(f"The AP rejected '{cmd}': {bad.strip()[:120]}")
            results.append({"command": cmd, "ok": True})
        log.info("REST API enable sequence completed on %s (legacy SSH algorithms: %s)", host,
                 bool(getattr(t, "sims_legacy", False)))
        return results
    finally:
        t.close()


_TFTP_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_COPY_FAIL = _ERRORS + ("failed", "error", "timed out", "timeout", "unable", "not found", "no such")


def copy_uiserver_cert(host: str, username: str, password: str, expected_fingerprint: str, *, tftp_ip: str,
                       filename: str, passphrase: str, on_sent=None, port: int = 22, timeout: float = 60.0) -> str:
    """Run `copy tftp <ip> <file> uiserver cert <passphrase> format pem` (Aruba CLI Bank, Instant 8.3+).

    The command is built from validated parts only. The passphrase is part of the command line (the CLI requires
    it), so it is scrubbed from everything this function returns, raises or logs. `on_sent` is called right after the
    command is submitted. Returns the AP's (scrubbed) reply text.
    """
    import ipaddress
    if not get_settings().device_writes_permitted:
        raise ArubaUnsupportedError("Configuration changes are disabled (SIMS_ALLOW_CONFIG_CHANGES=false).")
    try:
        ipaddress.IPv4Address(tftp_ip)
    except ValueError:
        raise ArubaUnsupportedError("The TFTP address must be an IPv4 address.") from None
    if not _TFTP_NAME.match(filename):
        raise ArubaUnsupportedError("Invalid certificate file name.")
    if not re.fullmatch(r"[A-Za-z0-9!@#%^*_+=.,:\-]{1,64}", passphrase):
        raise ArubaUnsupportedError("The passphrase contains characters that are not allowed on the AP command line.")
    cmd = f"copy tftp {tftp_ip} {filename} uiserver cert {passphrase} format pem"

    def scrub(text: str) -> str:
        return text.replace(passphrase, "***")

    t = _open_transport(host, port, timeout)
    try:
        if fingerprint(t.get_remote_server_key()) != expected_fingerprint:
            raise ArubaSshError("The AP's SSH host key does not match the fingerprint you confirmed. Nothing was sent. "
                                "Re-run the check and verify the new fingerprint.")
        try:
            t.auth_password(username, password)
        except paramiko.AuthenticationException:
            raise ArubaAuthError("SSH login rejected (check the username/password).") from None
        except (paramiko.SSHException, EOFError, OSError) as exc:
            raise ArubaSshError(f"SSH login failed: {type(exc).__name__}") from None
        chan = t.open_session()
        chan.get_pty()
        chan.invoke_shell()
        _read_until_prompt(chan, timeout)
        chan.send(cmd + "\n")
        if on_sent:
            on_sent()
        try:
            out = _read_until_prompt(chan, timeout)
        except ArubaSshError as exc:
            raise ArubaSshError(scrub(str(exc))) from None
        reply = [scrub(ln).strip() for ln in out.replace("\r", "").split("\n")[1:] if ln.strip()]
        bad = next((ln for ln in reply if any(m in ln.lower() for m in _COPY_FAIL)), None)
        if bad:
            raise ArubaSshError(f"The AP reported a problem with the certificate copy: {bad[:160]}")
        log.info("Certificate copy command submitted to %s", host)
        return "\n".join(reply)[:500]
    finally:
        t.close()
