"""Deployment wiring for live (production) mode and scheduled collection."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def compose():
    return yaml.safe_load((ROOT / "compose.yaml").read_text())


def test_mode_and_write_controls_come_from_env_with_safe_defaults():
    env = compose()["x-app-env"]
    assert env["SIMS_MODE"] == "${INM_MODE:-demo}"
    assert env["SIMS_DEVICE_WRITE_LOCK"] == "${INM_DEVICE_WRITE_LOCK:-true}"
    assert env["SIMS_ALLOW_CONFIG_CHANGES"] == "${INM_ALLOW_CONFIG_CHANGES:-false}"


def test_worker_can_collect_only_from_allowlisted_cidrs_and_persists_data():
    w = compose()["services"]["worker"]
    assert "devices" in w["networks"] and "edge" not in w["networks"] and "ports" not in w
    assert w["environment"]["INM_READONLY_DEVICE_CIDRS"] == "${INM_READONLY_DEVICE_CIDRS:-}"
    assert "app-data:/data" in w["volumes"]
    assert "api worker" in (ROOT / "scripts/allow-readonly-device-range.sh").read_text()


def test_installer_supports_live_mode_and_refuses_demo_password():
    s = (ROOT / "deployment/centos9/install.sh").read_text()
    assert '"$MODE" == live' in s and "set_env_value INM_MODE live" in s and "set_env_value INM_PROFILE lab-readonly" in s
    assert "the demo password cannot be used in live mode" in s
    assert 'exec bash "$APP/install.sh" --mode "$MODE"' in (ROOT / "scripts/inm.sh").read_text()


def test_release_manifest_head_matches_latest_migration():
    import json
    rel = json.loads((ROOT / "release.json").read_text())
    versions = sorted(p.name[:4] for p in (ROOT / "backend/database/migrations/versions").glob("0*.py"))
    assert rel["management_schema_head"] == versions[-1] == "0009"
    assert rel["device_writes"] == "LOCKED"
    assert set(rel["upgradable_management_schemas"]) == set(versions[3:-1])
