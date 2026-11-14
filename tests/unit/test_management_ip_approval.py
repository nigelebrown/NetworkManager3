"""Boundary tests for exact-IP approval and the host-only reconciler."""
from pathlib import Path
import json
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_admin_approval_ip_validation_and_bounded_sets():
    from backend.api.management_approvals import approved_ips, valid_exact_ip
    assert valid_exact_ip("10.190.97.211") == "10.190.97.211"
    assert approved_ips({"ips": ["10.190.97.211", "10.190.97.211", "10.190.97.201"]}) == [
        "10.190.97.201", "10.190.97.211"
    ]
    for addr in ("1.1.1.1", "127.0.0.1", "169.254.1.1", "224.1.1.1", "192.0.2.1", "10.190.97.0/24",
                 "172.32.1.1", "192.168.001.1", "::1"):
        with pytest.raises(ValueError):
            valid_exact_ip(addr)
    with pytest.raises(ValueError):
        approved_ips({"ips": ["10.190.97.211"] * 1001})


def test_nonroot_api_does_not_trust_unapplied_database_requests(monkeypatch, tmp_path):
    from backend.api import management_approvals as m
    fake = tmp_path / "applied.json"
    monkeypatch.setattr(m, "APPLIED_FILE", fake)
    assert m.host_applied_ips() == set()
    fake.write_text(json.dumps({"ips": ["10.190.97.211"]}))
    assert m.host_applied_ips() == {"10.190.97.211"}
    fake.write_text('{"ips":["1.1.1.1"]}')
    assert m.host_applied_ips() == set()


def test_host_reconciler_rejects_public_and_wide_approval_ranges():
    agent = runpy.run_path(str(ROOT / "scripts/inm_management_approval_agent.py"))
    exact = agent["exact_ips"]
    assert exact(["10.190.97.211", "10.190.97.211", "10.190.97.201"]) == [
        "10.190.97.201", "10.190.97.211"
    ]
    for bad in ("8.8.8.8", "127.0.0.1", "192.0.2.21", "10.0.0.0/8", "172.32.1.4"):
        with pytest.raises(ValueError):
            exact([bad])
    with pytest.raises(ValueError):
        exact(["10.190.97.211"] * 2001)


def test_worker_approval_is_readonly_without_docker_socket():
    compose = (ROOT / "compose.yaml").read_text()
    assert compose.count("/var/lib/inm/approvals:/run/inm-approvals:ro,z") == 2
    assert "docker.sock" not in compose
    guard = (ROOT / "scripts/inm_worker_egress_guard.sh").read_text()
    assert "INM_APPROVED_DEVICE_IPS" in guard
    assert 'iptables -w -A INM_WKR_EGRESS -j DROP' in guard
    install = (ROOT / "deployment/centos9/install.sh").read_text()
    assert "inm_management_approval_agent.py" in install
    assert "--install" in install


def test_live_installer_migrates_legacy_missing_write_lock_settings_fail_closed():
    """Legacy env files used Compose locked defaults without persisting them.

    The host reconciler must receive explicit locked flags, while any explicit
    operator override remains untouched and causes installation to stop.
    """
    install = (ROOT / "deployment/centos9/install.sh").read_text()
    guard = (ROOT / "scripts/inm_management_approval_agent.py").read_text()
    assert 'if ! grep -q \'^INM_DEVICE_WRITE_LOCK=\' "$ENV_FILE"; then' in install
    assert 'set_env_value INM_DEVICE_WRITE_LOCK true' in install
    assert 'if ! grep -q \'^INM_ALLOW_CONFIG_CHANGES=\' "$ENV_FILE"; then' in install
    assert 'set_env_value INM_ALLOW_CONFIG_CHANGES false' in install
    assert '"$(env_get INM_DEVICE_WRITE_LOCK)" == true' in install
    assert '"$(env_get INM_ALLOW_CONFIG_CHANGES)" == false' in install
    assert 'die "live/lab-readonly installation requires device writes LOCKED' in install
    assert 'data.get("INM_DEVICE_WRITE_LOCK") != "true"' in guard
    assert 'data.get("INM_ALLOW_CONFIG_CHANGES") == "true"' in guard
    assert "docker volume rm" not in install
    assert "down -v" not in install


def test_host_reconciler_requires_explicit_locked_policy(tmp_path):
    """A legacy env may omit the lock, but only the installer may migrate it.

    The reconciler itself must never infer permission from an absent setting.
    """
    agent = runpy.run_path(str(ROOT / "scripts/inm_management_approval_agent.py"))
    check = agent["env_fields"]
    env_path = tmp_path / "inm.env"
    check.__globals__["ENV"] = env_path

    prefix = "INM_MODE=live\nINM_PROFILE=lab-readonly\n"
    env_path.write_text(prefix)
    with pytest.raises(ValueError, match="device-write lock"):
        check()

    env_path.write_text(prefix + "INM_DEVICE_WRITE_LOCK=true\nINM_ALLOW_CONFIG_CHANGES=false\n")
    original, values = check()
    assert values["INM_DEVICE_WRITE_LOCK"] == "true"
    assert "INM_PROFILE=lab-readonly" in original

    env_path.write_text(prefix + "INM_DEVICE_WRITE_LOCK=false\nINM_ALLOW_CONFIG_CHANGES=false\n")
    with pytest.raises(ValueError, match="device-write lock"):
        check()

    env_path.write_text(prefix + "INM_DEVICE_WRITE_LOCK=true\nINM_ALLOW_CONFIG_CHANGES=true\n")
    with pytest.raises(ValueError, match="device-write lock"):
        check()
