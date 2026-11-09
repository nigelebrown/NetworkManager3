"""Regression: a previously approved LAN demo must use the safe LAN updater.

The main reload must never run a loopback-only down/up on an already
LAN-bound instance, or expose the temporary demo password to 0.0.0.0.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dispatch_occurs_before_docker_teardown():
    script = (ROOT / "scripts/rebuild-demo.sh").read_text()
    assert 'LAN_EXISTING_BIND=""' in script
    assert 'LAN_EXISTING_CIDR="$(read_value INM_LAN_CLIENT_CIDR)"' in script
    assert 'read_value INM_LAN_DEMO_ACK' in script
    assert 'read_value INM_DEMO_SIMPLE_LOGIN' in script
    assert "Scoped LAN demo update failed" in script
    assert 'bash "$ROOT/scripts/enable-lan-demo.sh" "$LAN_EXISTING_BIND" "$LAN_EXISTING_CIDR"' in script
    assert script.index('if [[ -n "$LAN_EXISTING_BIND" ]]; then') < script.index('Stopping/removing containers for Compose project')
    assert 'Refusing weak password on an unrestricted network.' in script


def test_lan_script_preserves_password_and_rejects_open_networks():
    script = (ROOT / "scripts/enable-lan-demo.sh").read_text()
    assert 'INM_WEB_BIND "$SERVER_IP"' in script
    assert 'network.prefixlen < 24' in script
    assert 'not ip.is_private' in script
    assert "deny all;" in script
    assert 'setvar SIMS_ADMIN_PASSWORD' not in script
    assert 'down -v' not in script
    assert 'docker system prune' not in script


def test_guided_workflow_is_routed_and_visible_to_admin():
    layout = (ROOT / "frontend/src/components/Layout.tsx").read_text()
    app = (ROOT / "frontend/src/App.tsx").read_text()
    assert 'to: "/onboarding", label: "Add device — 7-step wizard"' in layout
    assert 'to: "/onboarding/manual", label: "Manual / CSV import"' in layout
    assert 'path="onboarding" element={<GuidedOnboarding />}' in app
    assert 'path="onboarding/manual" element={<DeviceIntake />}' in app
