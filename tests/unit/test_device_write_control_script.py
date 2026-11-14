"""The operator write-enable helper is explicit, reversible and lab-only.

This static safety test does not activate device writes or certify hardware.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/device-write-control.sh"


def test_device_write_control_has_bash_validity_and_read_only_defaults():
    p = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    s = SCRIPT.read_text()
    assert "enable --ack-lab-writes" in s
    assert '"$(env_get INM_MODE)" == live' in s
    assert '"$(env_get INM_PROFILE)" == lab-readonly' in s
    assert 'INM_DEMO_SIMPLE_LOGIN' in s
    assert "INM_READONLY_DEVICE_CIDRS" in s
    assert "net.prefixlen < 24" in s
    assert "set_env_value INM_AUTOMATIC_OPTIMIZATION false" in s
    assert "set_env_value INM_DEVICE_WRITE_LOCK false" in s
    assert "set_env_value INM_ALLOW_CONFIG_CHANGES true" in s
    assert "set_env_value INM_DEVICE_WRITE_LOCK true" in s
    assert "set_env_value INM_ALLOW_CONFIG_CHANGES false" in s
    assert "require_independent_approval is True" in s
    assert 'trap restore EXIT' in s
    assert 'compose up -d --no-deps --force-recreate --wait' in s
    assert "device-write" in s
