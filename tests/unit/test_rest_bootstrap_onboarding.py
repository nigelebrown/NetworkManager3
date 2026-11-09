"""REST API prerequisites for Aruba onboarding; no real network, credentials or hardware."""
import base64
import hashlib
from types import SimpleNamespace

import pytest

from backend.aruba import ssh
from backend.aruba.errors import ArubaSshError, ArubaUnsupportedError
from backend.aruba import rest_enablement as rb

KEY = b"trusted-device-ssh-public-key"
FP = "SHA256:" + base64.b64encode(hashlib.sha256(KEY).digest()).decode().rstrip("=")
PW = "NEVER_LOG_THIS_PASSWORD"


class Key:
    def asbytes(self): return KEY
    def get_name(self): return "ssh-ed25519"


class Channel:
    def __init__(self, failed=None):
        self.commands = []
        self.queue = ["switch# "]
        self.failed = failed
    def get_pty(self): pass
    def invoke_shell(self): pass
    def recv_ready(self): return bool(self.queue)
    def recv(self, size): return self.queue.pop(0).encode()
    def send(self, cmd):
        name = cmd.strip()
        self.commands.append(name)
        prompt = "switch(config)# " if name.startswith("https-server") or name == "configure terminal" else "switch# "
        result = "% Invalid input" if name == self.failed else "Success"
        self.queue.append(f"{name}\r\n{result}\r\n{prompt}")


class SSH:
    def __init__(self, failed=None):
        self.channel = Channel(failed)
        self.auth = []
        self.closed = False
    def get_remote_server_key(self): return Key()
    def auth_password(self, username, pw): self.auth.append((username, pw))
    def open_session(self): return self.channel
    def close(self): self.closed = True


def setup(monkeypatch, transport, *, unlocked=True):
    monkeypatch.setattr(rb, "get_settings", lambda: SimpleNamespace(device_writes_permitted=unlocked))
    monkeypatch.setattr(ssh, "_open_transport", lambda host, port, timeout: transport)


def test_switch_only_accepts_fixed_vrf_names():
    assert rb.cx_commands("default") == ("configure terminal", "https-server vrf default", "end", "write memory")
    assert rb.cx_commands("mgmt")[1] == "https-server vrf mgmt"
    with pytest.raises(ArubaUnsupportedError):
        rb.cx_commands("mgmt; reload")


def test_locked_device_refuses_ssh_before_connection(monkeypatch):
    transport = SSH()
    setup(monkeypatch, transport, unlocked=False)
    with pytest.raises(ArubaUnsupportedError):
        rb.enable_cx_rest_api("10.10.0.1", "admin", PW, FP)
    assert transport.auth == [] and transport.channel.commands == []


def test_unpinned_key_refuses_credentials_and_commands(monkeypatch):
    transport = SSH()
    setup(monkeypatch, transport)
    with pytest.raises(ArubaSshError, match="host key mismatch"):
        rb.enable_cx_rest_api("10.10.0.1", "admin", PW, "SHA256:attacker")
    assert transport.auth == [] and transport.channel.commands == [] and transport.closed


def test_fixed_cx_sequence_with_verified_ssh_identity(monkeypatch):
    transport = SSH()
    setup(monkeypatch, transport)
    result = rb.enable_cx_rest_api("10.10.0.1", "admin", PW, FP)
    assert transport.auth == [("admin", PW)]
    assert transport.channel.commands == list(rb.cx_commands())
    assert PW not in str(result) and all(x["ok"] for x in result)
    assert transport.closed


def test_device_cli_error_blocks_false_success(monkeypatch):
    transport = SSH(failed="https-server vrf mgmt")
    setup(monkeypatch, transport)
    with pytest.raises(ArubaSshError, match="rejected"):
        rb.enable_cx_rest_api("10.10.0.1", "admin", PW, FP, vrf="mgmt")
    assert transport.channel.commands == ["configure terminal", "https-server vrf mgmt"]
    assert transport.closed
