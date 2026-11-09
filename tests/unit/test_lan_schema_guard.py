"""LAN-enabled demo: schema plan is safe, idempotent and backup-first."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/ensure-lan-schema.sh"
LAN = ROOT / "scripts/enable-lan-demo.sh"


def plan(tmp_path: Path, current: dict, release: dict) -> subprocess.CompletedProcess:
    source = HELPER.read_text()
    marker = '''decision="$(python3 -I - "$before" "$ROOT/release.json" <<'PY'\n'''
    assert marker in source
    py = source.split(marker, 1)[1].split("\nPY\n", 1)[0]
    target = tmp_path / "release.json"
    target.write_text(json.dumps(release))
    return subprocess.run([sys.executable, "-I", "-", json.dumps(current), str(target)],
                          input=py, text=True, capture_output=True, timeout=5)


@pytest.mark.parametrize("mgmt,result", [("0004", "MIGRATE"), ("0005", "MIGRATE"), ("0006", "MIGRATE"),
                                         ("0007", "MIGRATE"), ("0008", "MIGRATE"), ("0009", "CURRENT")])
def test_expected_or_previous_schema_accepted(tmp_path, mgmt, result):
    manifest = json.loads((ROOT / "release.json").read_text())
    output = plan(tmp_path, {"management": mgmt, "telemetry": "t001"}, manifest)
    assert output.returncode == 0, output.stderr
    assert output.stdout.strip() == result


@pytest.mark.parametrize("mgmt,telemetry", [
    ("0003", "t001"), ("9999", "t001"), ("0004", "unexpected"), (None, "t001")
])
def test_unsupported_schema_refused_without_migration(tmp_path, mgmt, telemetry):
    manifest = json.loads((ROOT / "release.json").read_text())
    output = plan(tmp_path, {"management": mgmt, "telemetry": telemetry}, manifest)
    assert output.returncode != 0


def test_missing_expand_only_and_write_lock_refused(tmp_path):
    manifest = json.loads((ROOT / "release.json").read_text())
    manifest["migrations_expand_only"] = False
    assert plan(tmp_path, {"management": "0004", "telemetry": "t001"}, manifest).returncode != 0
    manifest["migrations_expand_only"] = True
    manifest["device_writes"] = "ENABLED"
    assert plan(tmp_path, {"management": "0004", "telemetry": "t001"}, manifest).returncode != 0


def test_one_click_lan_script_runs_schema_gate_before_binding_api():
    lan = LAN.read_text()
    schema = HELPER.read_text()
    assert 'bash "$ROOT/scripts/ensure-lan-schema.sh"' in lan
    assert lan.index('bash "$ROOT/scripts/ensure-lan-schema.sh"') < lan.index('setvar INM_WEB_BIND "$SERVER_IP"')
    assert 'build migrate web' in lan
    assert 'run -T --rm --no-deps migrate' in schema
    assert 'bash "$ROOT/deployment/centos9/backup.sh" --label pre-lan-schema-upgrade' in schema
    assert 'sha256sum -c' in schema
    assert schema.index('backup.sh" --label') < schema.index('run -T --rm --no-deps migrate \\')
    assert "down -v" not in lan + schema
    assert "docker volume prune" not in lan + schema
    assert 'setvar SIMS_ADMIN_PASSWORD' not in lan + schema
