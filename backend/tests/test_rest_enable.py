"""Enabling the REST API over SSH: host-key pinning, fixed commands, no secret leakage. Fake SSH only."""
import hashlib
import base64

import httpx
import pytest
from sqlalchemy import select

from backend.aruba import onboarding, ssh
from backend.aruba import authentication as auth_mod
from backend.aruba.client import ArubaClient, ArubaTarget
from backend.aruba.errors import ArubaAuthError, ArubaRestDisabledError, ArubaSshError, ArubaUnsupportedError
from backend.config import get_settings
from backend.database.models import AuditLog, Site
from backend.database.session import session_scope
from backend.security.crypto import encrypt_secret

KEY_BYTES = b"fake-host-key-bytes"
GOOD_FP = "SHA256:" + base64.b64encode(hashlib.sha256(KEY_BYTES).digest()).decode().rstrip("=")
PASSWORD = "AP-Admin-Secret-9"


class FakeKey:
    def asbytes(self): return KEY_BYTES
    def get_name(self): return "ssh-ed25519"


class FakeChannel:
    def __init__(self, reject=None):
        self.sent, self._out, self.reject = [], ["(Instant AP)# "], reject

    def get_pty(self): pass
    def invoke_shell(self): pass
    def recv_ready(self): return bool(self._out)
    def recv(self, n): return self._out.pop(0).encode()

    def send(self, data):
        cmd = data.strip()
        self.sent.append(cmd)
        if cmd == self.reject:
            self._out.append(f"{cmd}\r\n% Parse error\r\n(Instant AP)# ")
        else:
            prompt = "(Instant AP)(config)# " if cmd in ("configure terminal", "allow-rest-api") else "(Instant AP)# "
            self._out.append(f"{cmd}\r\n{prompt}")


class FakeTransport:
    def __init__(self, reject=None, bad_login=False):
        self.chan, self.bad_login, self.auth_calls, self.closed = FakeChannel(reject), bad_login, [], False

    def get_remote_server_key(self): return FakeKey()

    def auth_password(self, user, pw):
        self.auth_calls.append((user, pw))
        if self.bad_login:
            import paramiko
            raise paramiko.AuthenticationException()

    def open_session(self): return self.chan
    def close(self): self.closed = True


@pytest.fixture
def writable(app_env):
    app_env.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    get_settings.cache_clear()


def patch_transport(monkeypatch, t):
    monkeypatch.setattr(ssh, "_open_transport", lambda host, port, timeout: t)


def test_scan_host_key_sends_no_credentials(monkeypatch):
    t = FakeTransport(); patch_transport(monkeypatch, t)
    r = ssh.scan_host_key("10.0.0.1")
    assert r == {"fingerprint": GOOD_FP, "key_type": "ssh-ed25519", "legacy_algorithms": False} and t.auth_calls == [] and t.closed


def test_blocked_when_config_changes_disabled(app_env, monkeypatch):
    patch_transport(monkeypatch, FakeTransport())
    with pytest.raises(ArubaUnsupportedError, match="disabled"):
        ssh.enable_rest_api("10.0.0.1", "admin", PASSWORD, GOOD_FP)


def test_fingerprint_mismatch_aborts_before_password(writable, monkeypatch):
    t = FakeTransport(); patch_transport(monkeypatch, t)
    with pytest.raises(ArubaSshError, match="does not match"):
        ssh.enable_rest_api("10.0.0.1", "admin", PASSWORD, "SHA256:somethingelse")
    assert t.auth_calls == [] and t.chan.sent == [] and t.closed  # password never offered, nothing run


def test_runs_only_the_fixed_commands(writable, monkeypatch):
    t = FakeTransport(); patch_transport(monkeypatch, t)
    steps = ssh.enable_rest_api("10.0.0.1", "admin", PASSWORD, GOOD_FP)
    assert t.chan.sent == ["configure terminal", "allow-rest-api", "end", "commit apply"]
    assert all(s["ok"] for s in steps) and t.auth_calls == [("admin", PASSWORD)]
    assert PASSWORD not in repr(steps) and all(PASSWORD not in c for c in t.chan.sent)  # never typed into the shell


def test_device_rejection_and_bad_login(writable, monkeypatch):
    patch_transport(monkeypatch, FakeTransport(reject="allow-rest-api"))
    with pytest.raises(ArubaSshError, match="rejected 'allow-rest-api'"):
        ssh.enable_rest_api("10.0.0.1", "admin", PASSWORD, GOOD_FP)
    patch_transport(monkeypatch, FakeTransport(bad_login=True))
    with pytest.raises(ArubaAuthError, match="SSH login rejected"):
        ssh.enable_rest_api("10.0.0.1", "admin", PASSWORD, GOOD_FP)


def test_login_distinguishes_rest_disabled_from_bad_credentials():
    html = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>login</html>"))
    notfound = httpx.MockTransport(lambda r: httpx.Response(404, text="nope"))
    badcred = httpx.MockTransport(lambda r: httpx.Response(401, json={"Status": "Failure", "Message": "bad credentials"}))
    for tr, exc in ((html, ArubaRestDisabledError), (notfound, ArubaRestDisabledError)):
        with ArubaClient(ArubaTarget("10.0.0.1", 4343, "u", "p"), transport=tr) as c, pytest.raises(exc):
            c.login()
    with ArubaClient(ArubaTarget("10.0.0.1", 4343, "u", "p"), transport=badcred) as c, pytest.raises(ArubaAuthError) as e:
        c.login()
    assert not isinstance(e.value, ArubaRestDisabledError)


# ---- API --------------------------------------------------------------------------------------------
def _site():
    with session_scope() as db:
        s = Site(name="Lab", vc_host="10.0.0.1", vc_port=4343, api_username="admin",
                 api_password_enc=encrypt_secret(PASSWORD), simulated=False)
        db.add(s); db.flush()
        return s.id


@pytest.fixture
def rest_state(monkeypatch):
    """Controls whether the fake controller's REST API answers."""
    state = {"on": False}
    real = auth_mod.client_for_site

    def handler(request):
        if request.url.path == "/rest/login":
            if not state["on"]:
                return httpx.Response(200, text="<html>web ui</html>")
            return httpx.Response(200, json={"Status": "Success", "sid": "s"})
        return httpx.Response(200, json={"Status": "Success"})
    monkeypatch.setattr(onboarding, "client_for_site", lambda site, transport=None: real(site, httpx.MockTransport(handler)))
    monkeypatch.setattr(onboarding, "REST_WAIT_SECONDS", 1)
    monkeypatch.setattr(onboarding.time, "sleep", lambda s: None)
    return state


def test_check_endpoint_reports_disabled_and_returns_fingerprint(client, admin_headers, rest_state, monkeypatch):
    patch_transport(monkeypatch, FakeTransport())
    sid = _site()
    r = client.post(f"/api/sites/{sid}/rest-api/check", headers=admin_headers).json()
    assert r["rest_enabled"] is False and r["ssh"]["fingerprint"] == GOOD_FP
    assert r["confirm_phrase"] == "ENABLE REST API Lab" and "allow-rest-api" in r["commands"]
    rest_state["on"] = True
    r2 = client.post(f"/api/sites/{sid}/rest-api/check", headers=admin_headers).json()
    assert r2["rest_enabled"] is True and "ssh" not in r2


def test_enable_endpoint_gates_and_confirmation(client, admin_headers, rest_state, monkeypatch):
    patch_transport(monkeypatch, FakeTransport())
    sid = _site()
    body = {"host_key_sha256": GOOD_FP, "confirm_text": "ENABLE REST API Lab"}
    r = client.post(f"/api/sites/{sid}/rest-api/enable", json=body, headers=admin_headers)
    assert r.status_code == 422 and "disabled" in r.json()["detail"]  # SIMS_ALLOW_CONFIG_CHANGES is false


def test_enable_end_to_end(client, admin_headers, writable, rest_state, monkeypatch):
    t = FakeTransport(); patch_transport(monkeypatch, t)
    sid = _site()
    bad = client.post(f"/api/sites/{sid}/rest-api/enable", json={"host_key_sha256": GOOD_FP, "confirm_text": "yes"}, headers=admin_headers)
    assert bad.status_code == 422 and t.auth_calls == []
    # REST comes up once the commands have run
    orig = t.chan.send
    def send(d):
        orig(d)
        if d.strip() == "commit apply": rest_state["on"] = True
    t.chan.send = send
    r = client.post(f"/api/sites/{sid}/rest-api/enable", json={"host_key_sha256": GOOD_FP, "confirm_text": "ENABLE REST API Lab"}, headers=admin_headers).json()
    assert r["ok"] is True and r["rest_enabled"] is True and "no allow-rest-api" in r["rollback"]
    with session_scope() as db:
        details = [a.detail or "" for a in db.scalars(select(AuditLog).where(AuditLog.action == "rest_api_enable"))]
        assert len(details) == 2 and any("succeeded" in d for d in details)
        assert not any(PASSWORD in d for d in details)


def test_enable_reports_when_rest_never_comes_up(client, admin_headers, writable, rest_state, monkeypatch):
    patch_transport(monkeypatch, FakeTransport())
    sid = _site()
    r = client.post(f"/api/sites/{sid}/rest-api/enable", json={"host_key_sha256": GOOD_FP, "confirm_text": "ENABLE REST API Lab"}, headers=admin_headers).json()
    assert r["ok"] is False and "did not answer" in r["message"]


def test_enable_requires_admin_and_live_site(client, admin_headers, writable):
    demo = client.get("/api/sites", headers=admin_headers).json()[0]["id"]
    assert client.post(f"/api/sites/{demo}/rest-api/check", headers=admin_headers).status_code == 422


def _serve_once(keys, kex, key):
    """A real paramiko SSH server on localhost offering only the given host-key/kex algorithms."""
    import socket
    import threading

    import paramiko
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(2)

    def serve():
        for _ in range(2):  # the client may reconnect once (modern attempt, then legacy)
            try:
                conn, _ = ls.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            if keys:
                t._preferred_keys = keys
            if kex:
                t._preferred_kex = kex
            t.add_server_key(key)
            try:
                t.start_server(server=paramiko.ServerInterface())
                t.join(2)
            except Exception:  # noqa: BLE001 - clients abort handshakes by design
                pass
            finally:
                t.close()

    threading.Thread(target=serve, daemon=True).start()
    return ls, ls.getsockname()[1]


def test_modern_ssh_server_uses_modern_algorithms():
    import paramiko
    from backend.aruba import ssh
    key = paramiko.RSAKey.generate(2048)
    ls, port = _serve_once(("rsa-sha2-256",), ("curve25519-sha256@libssh.org",), key)
    try:
        info = ssh.scan_host_key("127.0.0.1", port, timeout=6)
    finally:
        ls.close()
    assert info["legacy_algorithms"] is False and info["fingerprint"] == ssh.fingerprint(key)


def test_legacy_only_ssh_server_falls_back_to_ssh_rsa():
    """Server offers ONLY ssh-rsa + SHA-1 kex (real handshake). SIMS retries with the legacy set and still
    reports the host-key fingerprint so the user can pin it."""
    import paramiko
    from backend.aruba import ssh
    key = paramiko.RSAKey.generate(2048)
    ls, port = _serve_once(("ssh-rsa",), ("diffie-hellman-group14-sha1",), key)
    try:
        info = ssh.scan_host_key("127.0.0.1", port, timeout=6)
    finally:
        ls.close()
    assert info["legacy_algorithms"] is True and info["fingerprint"] == ssh.fingerprint(key)


def test_legacy_fallback_still_enforces_pinned_fingerprint(monkeypatch):
    import paramiko
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaSshError
    from backend.config import get_settings
    monkeypatch.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    get_settings.cache_clear()
    key = paramiko.RSAKey.generate(2048)
    ls, port = _serve_once(("ssh-rsa",), ("diffie-hellman-group14-sha1",), key)
    try:
        with pytest.raises(ArubaSshError, match="does not match the fingerprint"):
            ssh.enable_rest_api("127.0.0.1", "admin", "pw", "SHA256:wrong", port=port, timeout=6)
    finally:
        ls.close()
        get_settings.cache_clear()


def test_server_accepting_neither_gives_manual_steps():
    import paramiko
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaSshError
    ls, port = _serve_once(None, ("sntrup761x25519-sha512@openssh.com",), paramiko.RSAKey.generate(2048))  # kex we never support
    try:
        with pytest.raises(ArubaSshError, match="HostKeyAlgorithms=\\+ssh-rsa"):
            ssh.scan_host_key("127.0.0.1", port, timeout=6)
    finally:
        ls.close()
