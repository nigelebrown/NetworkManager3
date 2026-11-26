"""Legacy SSH compatibility (ssh-rsa / SHA-1 host keys, diffie-hellman-group14-sha1) for EXPLICITLY APPROVED
devices only (rc18).

Paramiko 5 (required since rc18) implements modern algorithms only, so nothing in the platform can silently fall back
to SHA-1. A device that offers nothing modern is refused unless BOTH:
  * INM_SSH_LEGACY_COMPAT=true on this server (off by default), and
  * an administrator approved that exact IP for legacy SSH (reason + expiry <= 90 days; AppSetting
    `ssh-legacy-approved:<ip>`).
Every legacy use and every refusal is audited. The legacy path uses the system OpenSSH client with the host key
pinned (StrictHostKeyChecking=yes against a single-entry known_hosts built from the key whose fingerprint the
caller already verified) and password auth through a private, 0600 askpass file — never on the command line or in
the environment.

LegacyTransport implements only the subset of paramiko.Transport/Channel the callers in ssh.py use.
"""
from __future__ import annotations
import logging

import base64
import os
import re
import select
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

MAX_APPROVAL_DAYS = 90
KEY = "ssh-legacy-approved:"
LEGACY_OPTS = ["-o", "HostKeyAlgorithms=ssh-rsa", "-o", "KexAlgorithms=diffie-hellman-group14-sha1",
               "-o", "PubkeyAcceptedAlgorithms=ssh-rsa"]


def enabled() -> bool:
    return os.environ.get("INM_SSH_LEGACY_COMPAT", "false").lower() == "true"


def _audit(action: str, host: str, detail: str, success: bool) -> None:
    try:
        from ..database.session import session_scope
        from ..security.deps import audit
        with session_scope() as db:
            audit(db, action, username="ssh-policy", target=host, detail=detail[:240], success=success)
    except Exception as _exc:  # noqa: BLE001 — a missing DB (CLI tools) must not hide the refusal itself
        _log.warning("legacy-SSH audit record could not be written (no database): %s", type(_exc).__name__, exc_info=True)


def approval(host: str) -> dict | None:
    from ..database.models import AppSetting
    from ..database.session import session_scope
    with session_scope() as db:
        row = db.get(AppSetting, KEY + host)
        return dict(row.value) if row else None


def approve(db, host: str, *, actor: str, reason: str, days: int, request=None) -> dict:
    from ..database.models import AppSetting
    from ..security.deps import audit
    if not (1 <= days <= MAX_APPROVAL_DAYS) or len(reason.strip()) < 10:
        raise ValueError(f"Approval needs a reason (10+ characters) and 1–{MAX_APPROVAL_DAYS} days.")
    v = {"approved_by": actor, "reason": reason.strip()[:300],
         "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()}
    row = db.get(AppSetting, KEY + host) or AppSetting(key=KEY + host, value={})
    row.value, row.updated_by, row.updated_at = v, actor, datetime.now(timezone.utc)
    db.add(row)
    audit(db, "ssh_legacy_approved", username=actor, target=host, detail=f"{days} days: {reason[:120]}", request=request)
    return v


def revoke(db, host: str, *, actor: str, request=None) -> None:
    from ..database.models import AppSetting
    from ..security.deps import audit
    row = db.get(AppSetting, KEY + host)
    if row is not None:
        db.delete(row)
    audit(db, "ssh_legacy_revoked", username=actor, target=host, request=request)


def allowed(host: str) -> tuple[bool, str]:
    if not enabled():
        return False, "legacy SSH compatibility is disabled on this server (INM_SSH_LEGACY_COMPAT=false)"
    a = approval(host)
    if not a:
        return False, "this device is not approved for legacy SSH"
    if datetime.fromisoformat(a["expires_at"]) <= datetime.now(timezone.utc):
        return False, "the legacy SSH approval for this device has expired"
    if shutil.which("ssh") is None:
        return False, "the OpenSSH client is not installed on this server"
    return True, f"approved by {a['approved_by']} until {a['expires_at']}"


class _Key:
    def __init__(self, name: str, blob: bytes):
        self._name, self._blob = name, blob

    def get_name(self) -> str:
        return self._name

    def asbytes(self) -> bytes:
        return self._blob


class LegacyChannel:
    def __init__(self, transport: "LegacyTransport"):
        self.t = transport

    def get_pty(self, **_):
        return None

    def invoke_shell(self):
        return None

    def send(self, data: str | bytes) -> int:
        b = data.encode() if isinstance(data, str) else data
        self.t.proc.stdin.write(b)
        self.t.proc.stdin.flush()
        return len(b)

    def recv_ready(self) -> bool:
        if self.t.pending:
            return True
        r, _, _ = select.select([self.t.proc.stdout], [], [], 0)
        return bool(r)

    def recv(self, n: int) -> bytes:
        if self.t.pending:
            b, self.t.pending = self.t.pending, b""
            return b
        return os.read(self.t.proc.stdout.fileno(), n)

    def close(self) -> None:
        self.t.close()


class LegacyTransport:
    """paramiko.Transport stand-in backed by `ssh -tt` with legacy algorithms and a pinned host key."""
    sims_legacy = True

    def __init__(self, host: str, port: int, timeout: float):
        self.host, self.port, self.timeout = host, port, timeout
        self.proc: subprocess.Popen | None = None
        self.pending = b""
        self._dir = tempfile.mkdtemp(prefix="inm-ssh-")
        self._key: _Key | None = None

    def get_remote_server_key(self) -> _Key:
        """Learn the RSA host key over the LEGACY algorithms without authenticating (ssh-keyscan cannot be told to
        use SHA-1 kex). The caller compares its fingerprint with the pinned value before any credential is used."""
        kh = os.path.join(self._dir, "scan_known_hosts")
        subprocess.run(["ssh", "-p", str(self.port), *LEGACY_OPTS, "-o", "StrictHostKeyChecking=accept-new",
                        "-o", f"UserKnownHostsFile={kh}", "-o", "GlobalKnownHostsFile=/dev/null", "-o", "BatchMode=yes",
                        "-o", "PreferredAuthentications=none", "-o", f"ConnectTimeout={int(self.timeout)}",
                        "-o", "LogLevel=ERROR", "-l", "inm-key-scan", self.host, "true"],
                       capture_output=True, timeout=self.timeout + 5, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                                            "HOME": self._dir})
        text = open(kh).read() if os.path.exists(kh) else ""
        m = re.search(r"^\S+\s+(ssh-rsa)\s+([A-Za-z0-9+/=]+)", text, re.M)
        if not m:
            from .errors import ArubaSshError
            raise ArubaSshError(f"Could not read the legacy RSA host key of {self.host}.")
        self._key = _Key(m.group(1), base64.b64decode(m.group(2)))
        return self._key

    def auth_password(self, username: str, password: str) -> None:
        import paramiko
        if self._key is None:
            self.get_remote_server_key()              # never connect without a key to pin
        kh = os.path.join(self._dir, "known_hosts")
        host_field = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        with open(kh, "w") as f:
            f.write(f"{host_field} ssh-rsa {base64.b64encode(self._key.asbytes()).decode()}\n")
        secret = os.path.join(self._dir, "secret")
        fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)
        askpass = os.path.join(self._dir, "askpass.sh")
        fd = os.open(askpass, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "w") as f:
            f.write(f"#!/bin/sh\ncat '{secret}'\n")
        cmd = ["ssh", "-tt", "-p", str(self.port), *LEGACY_OPTS, "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={kh}", "-o", "GlobalKnownHostsFile=/dev/null", "-o", "PubkeyAuthentication=no",
               "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "NumberOfPasswordPrompts=1",
               "-o", f"ConnectTimeout={int(self.timeout)}", "-o", "LogLevel=ERROR", "-l", username, self.host]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force",
               "DISPLAY": ":0", "HOME": self._dir}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.proc.stdout], [], [], 0.1)
            if r:
                chunk = os.read(self.proc.stdout.fileno(), 4096)
                if chunk:
                    self.pending += chunk
                    text = self.pending.decode("utf-8", "replace")
                    if "Permission denied" in text:
                        self.close()
                        raise paramiko.AuthenticationException("legacy SSH login rejected")
                    if "Host key verification failed" in text or "REMOTE HOST IDENTIFICATION HAS CHANGED" in text:
                        from .errors import ArubaSshError
                        self.close()
                        raise ArubaSshError("SSH host key changed during connection. Nothing was sent.")
                    if re.search(r"[#>]\s?$", text.rstrip("\r\n").split("\n")[-1]):
                        return                         # authenticated: the CLI prompt is waiting
            if self.proc.poll() is not None:
                text = self.pending.decode("utf-8", "replace")
                self.close()
                if "Permission denied" in text:
                    raise paramiko.AuthenticationException("legacy SSH login rejected")
                from .errors import ArubaSshError
                raise ArubaSshError(f"Legacy SSH session ended: {text.strip()[-160:] or 'no output'}")
        from .errors import ArubaSshError
        self.close()
        raise ArubaSshError("Timed out waiting for the legacy SSH login.")

    def open_session(self) -> LegacyChannel:
        return LegacyChannel(self)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self._dir, ignore_errors=True)
