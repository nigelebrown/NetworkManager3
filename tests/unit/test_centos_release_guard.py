"""Regression: CentOS reload must accept a LOCKED JSON release and reject unsafe ones.

Extracts the exact Python guard embedded in the shell script, so this test
catches accidental changes to quoting, JSON access and failure semantics.
No Docker, root privileges or network connections are needed.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELOAD = ROOT / "scripts" / "rebuild-demo.sh"
MARKER = 'if ! python3 -I - "$ROOT/release.json" "$ROOT/VERSION" <<\'PY\'\n'


def _guard() -> str:
    script = RELOAD.read_text(encoding="utf-8")
    assert MARKER in script, "JSON release guard missing from Git demo reload script"
    return script.split(MARKER, 1)[1].split("\nPY\n", 1)[0] + "\n"


def _invoke(tmp_path: Path, manifest: str, version: str = "1.0.0-phase1"):
    release = tmp_path / "release.json"
    version_file = tmp_path / "VERSION"
    release.write_text(manifest, encoding="utf-8")
    version_file.write_text(version + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-", str(release), str(version_file)],
        input=_guard(), capture_output=True, text=True, timeout=5,
    )


def test_checked_in_locked_release_is_accepted(tmp_path):
    checked_in = (ROOT / "release.json").read_text(encoding="utf-8")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    result = _invoke(tmp_path, checked_in, version)
    assert result.returncode == 0, result.stderr
    assert "device writes LOCKED" in result.stdout


@pytest.mark.parametrize("manifest,version", [
    ({"device_writes": "ENABLED", "version": "1.0.0-phase1"}, "1.0.0-phase1"),
    ({"device_writes": "LOCKED", "version": "wrong-version"}, "1.0.0-phase1"),
    ({"version": "1.0.0-phase1"}, "1.0.0-phase1"),
    ({"device_writes": True, "version": "1.0.0-phase1"}, "1.0.0-phase1"),
])
def test_incorrect_release_refuses_to_proceed(tmp_path, manifest, version):
    result = _invoke(tmp_path, json.dumps(manifest), version)
    assert result.returncode != 0


def test_malformed_release_json_refuses_to_proceed(tmp_path):
    result = _invoke(tmp_path, '{"device_writes": "LOCKED"')
    assert result.returncode != 0
