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
    "ssh -o HostKeyAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1 admin@{host}   "
    "(add -o PubkeyAcceptedAlgorithms=+ssh-rsa only for public-key login), then enter: "
    "configure terminal / allow-rest-api / end / commit apply. Then use Check again."
)
_PROMPT = re.compile(r"[#>]\s?$")


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style SHA256 fingerprint (what PuTTY / `ssh` display on first connect)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


# rc18: Paramiko 5 negotiates modern algorithms only (ed25519, ecdsa, rsa-sha2-256/512; curve25519, ecdh, group14/16
# with SHA-2). There is no silent fallback: a device that offers only ssh-rsa / SHA-1 is refused unless an
# administrator approved that exact device for legacy SSH AND INM_SSH_LEGACY_COMPAT=true (see ssh_legacy.py).
_LEGACY_ALGS = {"keys": ["ssh-rsa"], "kex": ["diffie-hellman-group14-sha1"]}   # documentation / legacy client only
LEGACY_REFUSED_HELP = (
    "{host} offers only legacy SSH algorithms (ssh-rsa / SHA-1), which this platform does not negotiate by default "
    "({why}). Prefer upgrading the device firmware. If the device must be used as is, an administrator can approve it "
    "for legacy SSH (Platform settings → SSH legacy compatibility) and enable INM_SSH_LEGACY_COMPAT on the server; "
    "every legacy session is audited."
)


def _open_transport(host: str, port: int, timeout: float):  # patched in tests
    """Modern algorithms only; never downgrades silently. The pinned host-key fingerprint is enforced by callers."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ArubaConnectionError(f"SSH connection to {host}:{port} failed: {exc.strerror or type(exc).__name__}") from None
    t = paramiko.Transport(sock)
    t.banner_timeout = timeout
    try:
        t.start_client(timeout=timeout)
    except paramiko.ssh_exception.IncompatiblePeer:
        t.close()
        from . import ssh_legacy
        ok, why = ssh_legacy.allowed(host)
        if not ok:
            ssh_legacy._audit("ssh_legacy_refused", host, why, False)
            raise ArubaSshError(LEGACY_REFUSED_HELP.format(host=host, why=why)) from None
        ssh_legacy._audit("ssh_legacy_used", host, why, True)
        log.warning("SSH to %s uses APPROVED legacy algorithms (ssh-rsa / SHA-1); host key still pinned.", host)
        return ssh_legacy.LegacyTransport(host, port, timeout)
    except (paramiko.SSHException, EOFError, OSError) as exc:
        t.close()
        raise ArubaSshError(f"SSH negotiation with {host} failed: {type(exc).__name__}") from None
    t.sims_legacy = False
    return t


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
                    timeout: float = 20.0, *, approved_ap_rest_only: bool = False) -> list[dict[str, Any]]:
    """Run ONLY the fixed Instant REST bootstrap sequence, after pinned SSH identity.

    approved_ap_rest_only is used solely by the administrator-verified registered
    AP-505/535 onboarding endpoint. It permits THIS sequence in a locked live
    profile, never arbitrary device configuration writes.
    """
    policy = get_settings()
    safe_locked_bootstrap = bool(
        approved_ap_rest_only and policy.mode == "live" and policy.inm_profile == "lab-readonly"
        and policy.device_write_lock and not policy.allow_config_changes
        and not policy.device_writes_permitted
    )
    if not policy.device_writes_permitted and not safe_locked_bootstrap:
        raise ArubaUnsupportedError("Configuration changes are disabled (write lock engaged); AP REST-only bootstrap requires an explicit authorized device approval.")
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


# ---- Phase 6: allowlisted configuration command sessions ---------------------------------------
# Only commands rendered by backend.infrastructure.writers.aruba_instant reach this function, and each
# line is re-validated here against a fixed grammar, so no free text can reach the AP shell.
_ALLOWED_CONFIG_LINES = [re.compile(p) for p in (
    r"^configure terminal$", r"^end$", r"^commit apply$", r"^exit$",
    r"^arm$", r"^(no )?client-match$", r"^band-steering-mode (prefer-5ghz|force-5ghz|balance-bands|disable)$",
    r"^air-time-fairness-mode (default-access|fair-access|preferred-access)$",
    r"^(min|max)-tx-power \d{1,2}$", r"^wide-bands (none|5ghz|2.4ghz|all)$", r"^(no )?80mhz-support$",
    r"^no (band-steering-mode|air-time-fairness-mode|wide-bands|min-tx-power|max-tx-power)$",
    r"^(no )?wlan (ssid-profile|auth-server) [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    r"^essid [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$", r"^type employee$", r"^enable$",
    r"^vlan ([0-9]{1,4}|[A-Za-z][A-Za-z0-9_.-]{0,31})$", r"^no vlan$", r"^opmode (wpa2-aes|wpa3-aes-ccm-128)$",
    r"^auth-server [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    r"^ip (?:[0-9]{1,3}\.){3}[0-9]{1,3}$",
    r"^(port|acctport) [0-9]{1,5}$", r"^key [A-Za-z0-9!@#%^*_+=.,:\-]{1,128}$",
    r"^(a|g)-channel \d{1,3} -?\d{1,2}(?:\.\d)?$",
    r"^(no )?(dot11k|dot11v|dot11r|radius-accounting)$",
    r"^radius-interim-accounting-interval (?:[0-9]|[1-5][0-9]|60)(?: (?:[0-9]|[1-5][0-9]))?$",
    r"^no radius-interim-accounting-interval$",
    r"^a-channels [0-9]{1,3}\+?(?:,[0-9]{1,3}\+?){0,47}$",
    r"^captive-portal external profile [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    r"^mac-authentication$", r"^set-role-(pre-auth|mac-auth) [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    # rc17 typed WLAN security objects (roles, external captive portal, MAC + 802.1X)
    r"^(no )?wlan (access-rule|external-captive-portal) [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    r"^rule (?:any any|(?:[0-9]{1,3}\.){3}[0-9]{1,3} (?:[0-9]{1,3}\.){3}[0-9]{1,3}) match "
    r"(?:any any any|(?:tcp|udp) [0-9]{1,5} [0-9]{1,5}) (?:permit|deny)$",
    r"^server [A-Za-z0-9][A-Za-z0-9.-]{0,252}$", r'^url "/[A-Za-z0-9._~!$&()*+,;=:@%/?-]{0,254}"$', r"^https$",
    r"^redirect-url https://[A-Za-z0-9._~!$&()*+,;=:@%/?-]{1,247}$",
    r"^no (mac-authentication|l2-auth-failthrough|set-role-mac-auth)$", r"^l2-auth-failthrough$",
    # rc18 RADIUS server attributes and SSID server assignment
    r"^timeout [0-9]{1,2}$", r"^retry-count [0-5]$", r"^no auth-server [A-Za-z0-9][A-Za-z0-9_.-]{0,31}$",
    r"^(no )?server-load-balancing$",
)]


def run_config_lines(host: str, username: str, password: str, expected_fingerprint: str, lines: list[str], *,
                     port: int = 22, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Run an allowlisted command sequence with host-key pinning. Raises on the first rejected line."""
    if not get_settings().device_writes_permitted:
        raise ArubaUnsupportedError("Configuration changes are disabled (device writes not permitted).")
    lines = [ln.strip() for ln in lines]
    for ln in lines:
        if not any(p.fullmatch(ln) for p in _ALLOWED_CONFIG_LINES):
            raise ArubaUnsupportedError(f"Command not in the allowlisted grammar: {ln[:60]!r}")
    t = _open_transport(host, port, timeout)
    try:
        if fingerprint(t.get_remote_server_key()) != expected_fingerprint:
            raise ArubaSshError("SSH host key does not match the pinned fingerprint. Nothing was sent.")
        try:
            t.auth_password(username, password)
        except paramiko.AuthenticationException:
            raise ArubaAuthError("SSH login rejected.") from None
        chan = t.open_session()
        chan.get_pty()
        chan.invoke_shell()
        _read_until_prompt(chan, timeout)
        results = []
        for cmd in lines:
            chan.send(cmd + "\n")
            out = _read_until_prompt(chan, timeout)
            reply = [ln for ln in out.replace("\r", "").split("\n")[1:] if ln.strip()]
            bad = next((ln for ln in reply if any(m in ln.lower() for m in _ERRORS)), None)
            display = "key ********" if cmd.startswith("key ") else cmd
            results.append({"command": display, "ok": bad is None})
            if bad:
                raise ArubaSshError(f"The AP rejected '{display}' (configuration command failed).")
        return results
    finally:
        t.close()


# ------------------------------------------------------------------ read-only CLI collection (REST disabled)
_MORE = re.compile(r"--\s*more\s*--|<--- more --->|press any key", re.I)


class SshShowClient:
    """Read-only Instant CLI over SSH with the same `.show()` contract as ArubaClient.

    Used when a cluster's REST API is disabled and cannot (or may not) be enabled. Every command passes the SAME
    read-only allowlist as REST (`client.validate_command`), the host key must match a pinned fingerprint BEFORE the
    password is offered, and no configuration mode is ever entered. Per-member targeting (`iap_ip`) is not
    available over the VC CLI, so targeted commands raise ArubaCommandError and are recorded as unavailable.
    """

    transport_name = "ssh_cli"

    def __init__(self, host: str, username: str, password: str, expected_fingerprint: str, *, port: int = 22,
                 timeout: float = 20.0):
        self.host, self.username, self._password = host, username, password
        self.expected_fingerprint, self.port, self.timeout = expected_fingerprint, port, timeout
        self._t = None
        self._chan = None

    def __enter__(self) -> "SshShowClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def logged_in(self) -> bool:
        return self._chan is not None

    def login(self) -> None:
        if self._chan is not None:
            return
        if not self.expected_fingerprint:
            raise ArubaSshError("No approved SSH host-key fingerprint for this controller; approve it first.")
        t = _open_transport(self.host, self.port, self.timeout)
        try:
            actual = fingerprint(t.get_remote_server_key())
            if actual != self.expected_fingerprint:
                raise ArubaSshError("SSH host key does not match the approved fingerprint. Nothing was sent.")
            try:
                t.auth_password(self.username, self._password)
            except paramiko.AuthenticationException:
                raise ArubaAuthError("SSH login rejected (check the username/password).") from None
            except (paramiko.SSHException, EOFError, OSError) as exc:
                raise ArubaSshError(f"SSH login failed: {type(exc).__name__}") from None
            chan = t.open_session()
            chan.get_pty(width=512, height=1000)
            chan.invoke_shell()
            _read_until_prompt(chan, self.timeout)
        except Exception:
            t.close()
            raise
        self._t, self._chan = t, chan

    def show(self, cmd: str, *, iap_ip: str | None = None, _retry: bool = True) -> dict[str, Any]:
        from .client import validate_command
        from .errors import ArubaCommandError
        cmd = validate_command(cmd)
        if iap_ip:
            raise ArubaCommandError("Per-AP targeting is not available over the controller CLI (REST disabled).")
        self.login()
        chan = self._chan
        chan.send(cmd + "\n")
        buf, end = "", time.monotonic() + self.timeout
        while time.monotonic() < end:
            if chan.recv_ready():
                buf += chan.recv(65536).decode("utf-8", "replace")
                tail = buf.rstrip("\r\n").split("\n")[-1] if buf.strip() else ""
                if _MORE.search(tail):
                    buf = _MORE.sub("", buf)
                    chan.send(" ")
                    continue
                if _PROMPT.search(tail.rstrip()) and "\n" in buf:
                    break
            else:
                time.sleep(0.05)
        else:
            raise ArubaCommandError(f"Timed out reading '{cmd}' over SSH.")
        buf = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|[\x08]+", "", buf)
        lines = buf.replace("\r", "").split("\n")[1:-1]  # drop echoed command and trailing prompt
        text = "\n".join(lines)
        if any(m in text.lower() for m in _ERRORS):
            raise ArubaCommandError(f"The controller rejected '{cmd}'.")
        return {"Command output": text, "_transport": "ssh_cli"}

    def logout(self) -> None:
        self.close()

    def close(self) -> None:
        if self._t is not None:
            try:
                self._t.close()
            finally:
                self._t, self._chan = None, None


def run_exec_command(host: str, username: str, password: str, expected_fingerprint: str, command: str, *,
                     port: int = 22, timeout: float = 60.0, max_bytes: int = 32768) -> str:
    """Run ONE fixed operational command (ping/traceroute) after verifying the pinned host key. The command string is
    built by the caller from a fixed template with validated arguments; never pass user text. Output is bounded."""
    if not expected_fingerprint:
        raise ArubaSshError("No approved SSH host-key fingerprint for this device; approve it first.")
    t = _open_transport(host, port, min(timeout, 20.0))
    try:
        if fingerprint(t.get_remote_server_key()) != expected_fingerprint:
            raise ArubaSshError("SSH host key does not match the approved fingerprint. Nothing was sent.")
        try:
            t.auth_password(username, password)
        except paramiko.AuthenticationException:
            raise ArubaAuthError("SSH login rejected (check the username/password).") from None
        chan = t.open_session()
        chan.get_pty(width=512, height=1000)
        chan.invoke_shell()
        _read_until_prompt(chan, 20.0)
        chan.send(command + "\n")
        out = _read_until_prompt(chan, timeout)
        try:
            chan.send("exit\n")
        except (OSError, EOFError) as _exc:
            log.debug("best-effort sending exit to an SSH channel: %s", type(_exc).__name__)
        return out[:max_bytes]
    finally:
        t.close()
