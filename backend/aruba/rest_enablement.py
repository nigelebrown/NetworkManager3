"""Explicit, authenticated SSH bootstrap for Aruba REST API during onboarding.

A non-REST device cannot enable REST using REST itself. Never auto-change a device from a
network scan: an administrator must verify the SSH SHA256 host key, confirm the IP, and
have the device write gate explicitly unlocked. HTTPS verification policy applies only
to outbound Aruba management clients; SSH identity verification is ALWAYS enforced.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import ssh
from .errors import ArubaAuthError, ArubaSshError, ArubaUnsupportedError
from ..config import get_settings

CX_ALLOWED_VRFS = frozenset({"default", "mgmt"})
CX_ROLLBACK = {"default": "no https-server vrf default", "mgmt": "no https-server vrf mgmt"}


def cx_commands(vrf: str = "default") -> tuple[str, ...]:
    """Only documented, fixed AOS-CX commands. 6100 supports default VRF only."""
    if vrf not in CX_ALLOWED_VRFS:
        raise ArubaUnsupportedError("Only the default and mgmt VRFs are supported for CX REST bootstrap.")
    return ("configure terminal", f"https-server vrf {vrf}", "end", "write memory")


def enable_cx_rest_api(host: str, username: str, password: str, expected_fingerprint: str,
                       *, vrf: str = "default", port: int = 22, timeout: float = 20.0) -> list[dict]:
    """Enable the HTTPS REST server on the selected VRF; no arbitrary CLI input accepted."""
    commands = cx_commands(vrf)
    if not get_settings().device_writes_permitted:
        raise ArubaUnsupportedError("Device configuration writes are locked. REST bootstrap needs an explicit unlock.")
    transport = ssh._open_transport(host, port, timeout)
    try:
        actual = ssh.fingerprint(transport.get_remote_server_key())
        if actual != expected_fingerprint:
            raise ArubaSshError("SSH host key mismatch. Refusing to send credentials or enable REST.")
        try:
            transport.auth_password(username, password)
        except ssh.paramiko.AuthenticationException:
            raise ArubaAuthError("SSH login was rejected. Check the saved credential profile.") from None
        except (ssh.paramiko.SSHException, EOFError, OSError):
            raise ArubaSshError("SSH login failed before any changes were made.") from None
        channel = transport.open_session()
        channel.get_pty()
        channel.invoke_shell()
        ssh._read_until_prompt(channel, timeout)
        result = []
        for command in commands:
            channel.send(command + "\n")
            response = ssh._read_until_prompt(channel, timeout)
            # Aruba CLI replies echo commands; strip the first line before checking errors.
            lines = [line.strip() for line in response.replace("\r", "").split("\n")[1:] if line.strip()]
            problem = next((line for line in lines if any(word in line.lower() for word in ssh._ERRORS)
                            or line.lower().startswith(("error:", "failed to enable"))), None)
            if problem:
                raise ArubaSshError(f"Switch rejected '{command}': {problem[:140]}")
            result.append({"command": command, "ok": True})
        return result
    finally:
        transport.close()
