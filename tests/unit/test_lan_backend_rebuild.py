"""Regression for CentOS LAN enabling: API image must be rebuilt, not merely recreated."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_lan_script_builds_backend_owner_service_before_api():
    script = (ROOT / "scripts/enable-lan-demo.sh").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    # Compose's 'migrate' service owns the backend Dockerfile, and 'api'
    # consumes the same inm/backend image. An API-only build is a no-op.
    assert "build migrate web" in script
    assert "build api web" not in script
    assert "up -d --no-deps --force-recreate --wait --wait-timeout 120 api" in script
    assert "up -d --no-deps --force-recreate --wait --wait-timeout 120 web" in script
    assert "dockerfile: deployment/docker/backend.Dockerfile" in compose


def test_lan_mode_does_not_reset_password_or_erase_data():
    script = (ROOT / "scripts/enable-lan-demo.sh").read_text(encoding="utf-8")
    assert 'demo123!' in script
    assert 'setvar SIMS_ADMIN_PASSWORD' not in script
    assert 'down -v' not in script
    assert 'volume prune' not in script
    assert 'docker system prune' not in script
    assert 'trap rollback EXIT' in script
