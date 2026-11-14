"""Regression contract for safe low-disk updates on already-running INM test installations.

Does not contact Docker or reclaim disk. The clean-install 40 GiB floor remains.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_update_below_fresh_install_disk_floor_requires_explicit_running_test_instance():
    doctor = (ROOT / "deployment/centos9/doctor.sh").read_text()
    updater = (ROOT / "scripts/inm_one_click_update_8787.sh").read_text()
    assert '(( disk_gb >= 40 ))' in doctor
    assert '(( disk_gb >= 30 ))' in doctor
    assert 'INM_EXISTING_TEST_UPDATE:-0' in doctor
    assert '-f "$ENV_FILE"' in doctor
    assert 'com.docker.compose.project=inm' in doctor
    assert 'com.docker.compose.service=web' in doctor
    assert 'INM_EXISTING_TEST_UPDATE=1 bash "$ROOT/deployment/centos9/install.sh" --mode live' in updater
    assert 'docker system df' in updater
    assert 'docker system prune' not in updater
    assert 'docker volume prune' not in updater
    assert 'docker builder prune' not in updater
    assert 'down -v' not in updater


def test_install_sh_does_not_override_sizing_implicitly():
    installer = (ROOT / "deployment/centos9/install.sh").read_text()
    assert 'preflight' in installer
    assert 'INM_EXISTING_TEST_UPDATE=1' not in installer
    assert 'down -v' not in installer
