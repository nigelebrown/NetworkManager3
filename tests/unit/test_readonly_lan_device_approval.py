"""Regression for read-only Aruba target authorization in restricted LAN demo.

Does not run docker or contact a device. CI shell syntax validates the helper
and source assertions protect the demo-login network boundary.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_readonly_helper_is_valid_bash():
    script = ROOT / "scripts/allow-readonly-device-range.sh"
    p = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_readonly_helper_supports_approved_lan_without_opening_login_to_all_networks():
    script = (ROOT / "scripts/allow-readonly-device-range.sh").read_text()
    assert 'INM_LAN_DEMO_ACK' in script
    assert 'INM_LAN_CLIENT_CIDR' in script
    assert 'INM_NGINX_ACCESS_FILE' in script
    assert 'net.prefixlen>=24' in script
    assert 'deny all;' in script
    assert 'SIMS_ADMIN_PASSWORD' in script
    assert 'get("device_writes","")' in script
    assert "Existing" not in script or "old allowlist" not in script  # no global resets
    assert '"$(getenv INM_READONLY_DEVICE_CIDRS)" "$CIDRS"' in script
    assert "config --quiet" in script
    assert "--wait --wait-timeout 120 api" in script
    assert "down -v" not in script


def test_wizard_supports_bounded_cidr_scans_and_retains_allowlist_checks():
    ui = (ROOT / "frontend/src/pages/GuidedOnboarding.tsx").read_text()
    api = (ROOT / "backend/api/onboarding_workflow.py").read_text()
    # The superseded single-IP-only UI was intentionally expanded to /24.
    assert "Management IP or CIDR (up to /24)" in ui
    assert 'wf.discover(w.id, scope.trim())' in ui
    assert "net.num_addresses > max_addresses" in api
    assert "max_addresses=256" in api
    assert "body.batch_size" in api
    assert "_permit_probe(ip)" in api
