"""Live/lab-readonly Aruba AP REST-only enablement security contract.

No real connections or host firewall writes occur in these unit tests.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import onboarding_workflow as workflow
from backend.api import management_approvals
from backend.aruba import ssh
from backend.aruba.errors import ArubaUnsupportedError
from backend.database.models_onboarding import OnboardingCredential

ROOT = Path(__file__).resolve().parents[2]
IP = "10.190.97.211"
DEVICE = "00000000-0000-0000-0000-000000000001"
PROFILE = "00000000-0000-0000-0000-000000000002"


def locked_policy():
    return SimpleNamespace(mode="live", inm_profile="lab-readonly",
                           device_write_lock=True, allow_config_changes=False,
                           device_writes_permitted=False)


def test_model_and_host_exact_ip_gate(monkeypatch):
    gate = workflow._locked_ap_rest_bootstrap_allowed
    monkeypatch.setattr(workflow, "get_settings", locked_policy)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {IP})
    device = SimpleNamespace(model="Aruba AP-505 (RW)", vendor="Aruba")
    monkeypatch.setattr(management_approvals, "_registered_target",
                        lambda db, device_id, profile_id, ip: device)
    profile = SimpleNamespace(id=PROFILE, device_family="access_point")
    target = workflow.RestTarget(management_ip=IP, device_id=DEVICE)
    assert gate(None, profile, target)
    device.model = "AP-535"
    assert gate(None, profile, target)
    device.model = "CX-6200"
    assert not gate(None, profile, target)
    device.model = "AP-515"
    assert not gate(None, profile, target)
    device.model = "AP-505"
    device.vendor = "Other"
    assert not gate(None, profile, target)
    device.vendor = "Aruba"
    assert not gate(None, profile, workflow.RestTarget(management_ip=IP))
    assert not gate(None, SimpleNamespace(id=PROFILE, device_family="switch"), target)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: set())
    assert not gate(None, profile, target)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {IP})
    monkeypatch.setattr(workflow, "get_settings", lambda: SimpleNamespace(
        mode="live", inm_profile="lab-readonly", device_write_lock=False,
        allow_config_changes=True, device_writes_permitted=True))
    assert not gate(None, profile, target)


def test_wrong_registration_fails_closed(monkeypatch):
    monkeypatch.setattr(workflow, "get_settings", locked_policy)
    monkeypatch.setattr(management_approvals, "host_applied_ips", lambda: {IP})
    def invalid_registration(*args):
        raise HTTPException(404, "not enrolled")
    monkeypatch.setattr(management_approvals, "_registered_target", invalid_registration)
    assert not workflow._locked_ap_rest_bootstrap_allowed(
        None, SimpleNamespace(id=PROFILE, device_family="access_point"),
        workflow.RestTarget(management_ip=IP, device_id=DEVICE))


class Key:
    def asbytes(self):
        return b"ssh-device-host-key"


class FakeChannel:
    def __init__(self):
        self.commands = []
        self.queue = ["device# "]

    def get_pty(self):
        pass

    def invoke_shell(self):
        pass

    def recv_ready(self):
        return bool(self.queue)

    def recv(self, size):
        return self.queue.pop(0).encode()

    def send(self, command):
        self.commands.append(command.strip())
        self.queue.append(command.strip() + "\r\nOK\r\ndevice# ")


class FakeTransport:
    def __init__(self):
        self.ch = FakeChannel()
        self.auth = []
        self.closed = False

    def get_remote_server_key(self):
        return Key()

    def auth_password(self, username, password):
        self.auth.append((username, password))

    def open_session(self):
        return self.ch

    def close(self):
        self.closed = True


def test_locked_api_allows_only_explicit_ap_rest_sequence(monkeypatch):
    monkeypatch.setattr(ssh, "get_settings", locked_policy)
    fake = FakeTransport()
    monkeypatch.setattr(ssh, "_open_transport", lambda *args: fake)
    sha = ssh.fingerprint(Key())
    with pytest.raises(ArubaUnsupportedError):
        ssh.enable_rest_api(IP, "admin", "SECRET", sha)
    assert not fake.auth and not fake.ch.commands
    assert ssh.enable_rest_api(IP, "admin", "SECRET", sha,
                               approved_ap_rest_only=True) == [
        {"command": command, "ok": True} for command in ssh.ENABLE_REST_COMMANDS
    ]
    assert fake.ch.commands == list(ssh.ENABLE_REST_COMMANDS)
    assert fake.auth == [("admin", "SECRET")]
    assert fake.closed


def test_locked_api_denies_when_host_fingerprint_differs_before_auth(monkeypatch):
    from backend.aruba.errors import ArubaSshError
    monkeypatch.setattr(ssh, "get_settings", locked_policy)
    fake = FakeTransport()
    monkeypatch.setattr(ssh, "_open_transport", lambda *args: fake)
    with pytest.raises(ArubaSshError, match="does not match"):
        ssh.enable_rest_api(IP, "admin", "SECRET", "SHA256:WRONG",
                            approved_ap_rest_only=True)
    assert not fake.auth and not fake.ch.commands and fake.closed


def test_cent_os_python3_script_exact_ip_ledger_and_fixed_commands(monkeypatch, tmp_path):
    path = ROOT / "scripts/inm_ap_rest_only.py"
    spec = importlib.util.spec_from_file_location("inm_ap_rest_only", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.COMMANDS == ssh.ENABLE_REST_COMMANDS
    assert script.canonical_private_ip(IP) == IP
    for bad in ("8.8.8.8", "10.190.97.0/24", "127.0.0.1", "10.190.097.211"):
        with pytest.raises(ValueError):
            script.canonical_private_ip(bad)
    ledger = tmp_path / "applied.json"
    ledger.write_text(json.dumps({"ips": [IP]}))
    monkeypatch.setattr(script, "APPROVAL_FILE", ledger)
    script.require_host_approved(IP)
    with pytest.raises(PermissionError):
        script.require_host_approved("10.190.97.201")
    assert script.UNSAFE_KEX == ("diffie-hellman-group1-sha1", "diffie-hellman-group-exchange-sha1")
    source = path.read_text()
    assert "eval(" not in source
    assert "Host 10.*" not in source
    assert 'getpass.getpass(' in source
    assert "transport.set_keepalive(30)" in source
