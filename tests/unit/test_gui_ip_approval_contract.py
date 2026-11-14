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
    # Refusing updates on unknown/uncommitted files is a safety feature.
    assert "down -v" not in updater
    assert "inm_safe_sync_main.sh" in updater
    sync = (ROOT / "scripts/inm_safe_sync_main.sh").read_text()
    assert "git bundle create" in sync and "git bundle verify" in sync
    assert "git update-ref" in sync
    assert 'git reset --hard' not in sync
    assert 'SERVER FIRST:' in sync
    assert 'local main $BEFORE is authoritative' in sync
    assert "deployment stopped with the working server checkout unchanged" in sync
    assert "git status --porcelain --untracked-files=normal" in sync
    assert "merge --ff-only" in sync
    assert "docker volume rm" not in updater + sync
    assert "down -v" not in updater + sync



def test_batch_test_and_onboard_are_admin_only_read_only_and_status_gated():
    page = (ROOT / "frontend/src/pages/DeviceInventory.tsx").read_text()
    api = (ROOT / "backend/api/onboarding_workflow.py").read_text()
    assert "Test &amp; Approve All" in page
    assert "Test All (no approvals)" in page
    assert "Onboard Successful" in page
    assert 'd => checks[d.id]?.restState === "ENABLED"' in page
    assert 'if (live.state !== "ENABLED")' in page
    assert "window.confirm(" in page
    assert "Approve the listed exact /32 device IPs before running REST tests" in page
    assert "await onboardingApi.approveDeviceIP" in page
    assert "await onboardingApi.capture(device.id, result.profileId)" in page
    assert "await ops.previewPlan(plan)" in page
    assert "await ops.createPlan(plan)" in page
    assert 'interval_seconds: 300' in page
    assert 'if (existing)' in page
    assert 'preview.warnings.length' in page
    assert 'await onboardingApi.credentialDevices(savedProfile)' in page
    assert 'require_role("admin")' in api
    assert '@router.get("/credentials/{profile_id}/devices")' in api
    assert 'InmDevice.source_type == "REAL"' in api
    # Data-plane reads and management DB collection plans only: no device writes.
    assert "enableRestApi(" not in page
    assert "executeChange(" not in page
    assert "deleteDevice(" not in page



def test_unreachable_ap_has_explicit_ssh_diagnostic_and_rest_only_button():
    page = (ROOT / "frontend/src/pages/DeviceInventory.tsx").read_text()
    bootstrap = (ROOT / "frontend/src/components/DeviceRestBootstrap.tsx").read_text()
    api = (ROOT / "backend/api/onboarding_workflow.py").read_text()
    assert "Check SSH / Enable REST" in page
    assert "DeviceRestBootstrap" in page
    assert 'deviceId={target.id}' in page
    assert 'checkRestApi(profileId, d.address!, d.id)' in page
    assert "ap_rest_only_authorized" in bootstrap
    assert "device_id: deviceId" in bootstrap
    assert "Ping alone cannot enable REST" in bootstrap
    assert "approved_ap_rest_only=ap_rest_only" in api
    assert "host_applied_ips()" in api
    assert "AP[-" in api
