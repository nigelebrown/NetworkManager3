"""Guard the administrator-only device connection approval UI and deployment checks.

These source-contract tests supplement existing API RBAC/host-policy contract tests.
They do not contact Aruba devices or modify host firewall rules.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_has_separate_explicit_exact_ip_approval_button():
    page = (ROOT / "frontend/src/pages/DeviceInventory.tsx").read_text()
    assert "Approve IP &amp; Test" in page
    assert 'data-inm-approval-action="exact-ip-v2"' in page
    assert "onClick={() => void testConnection(result.profileId, d, null, true)}" in page
    assert "Test connection" in page
    assert "APPROVE IP " in page
    assert "managementIPApprovalStatus" in page
    assert 'status.state === "APPLIED"' in page
    assert "window.confirm(" in page


def test_one_click_update_detects_old_web_bundle_and_checks_host_timer():
    updater = (ROOT / "scripts/inm_one_click_update_8787.sh").read_text()
    assert "'data-inm-approval-action'" in updater
    assert "inm-management-approval.timer" in updater
    assert "Refusing" not in updater or "untracked" not in updater
    assert "down -v" not in updater
    assert "git -C" in updater and "merge --ff-only" in updater
