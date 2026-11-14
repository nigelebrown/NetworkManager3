"""Production gate unit tests. The fixture is SYNTHETIC and certifies NO device.

A passing evaluator test validates integrity rules, not hardware readiness.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.domain import release_readiness as R
from backend.domain.adapters import CX_TYPED_OPS, INSTANT_TYPED_OPS

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
SHA = "a" * 40


def build(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir(exist_ok=True)
    f = root / "artifacts.txt"
    f.write_text("SYNTHETIC TEST FIXTURE — NOT REAL DEVICE ACCEPTANCE", encoding="utf-8")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    register = root / "acceptance.csv"
    with register.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("test_id", "phase", "status", "actual_result", "artifact_link"))
        for i in range(220):
            writer.writerow((f"TEST-{i:03d}", f"P{1 + i % 11}", "PASS",
                             "SYNTHETIC UNIT FIXTURE - NOT PHYSICAL EVIDENCE", "artifacts.txt"))
    register_hash = hashlib.sha256(register.read_bytes()).hexdigest()
    pass_ref = {"status": "PASS", "evidence": ["artifacts.txt"]}
    m = {
        "schema_version": 1, "release_commit": SHA, "deployment_profile": "live",
        "prepared_by": "developer",
        "created_at": (NOW - timedelta(minutes=5)).isoformat(),
        "ci": {"commit": SHA, "conclusion": "success",
               "run_url": "https://github.com/org/repo/actions/runs/1",
               "completed_at": (NOW - timedelta(minutes=10)).isoformat()},
        "artifacts": {"artifacts.txt": digest, "acceptance.csv": register_hash},
        "acceptance_register": "acceptance.csv",
        "phases": {key: dict(pass_ref) for key in R.PHASES},
        "deployment_gates": {key: dict(pass_ref) for key in R.DEPLOYMENT_GATES},
        "gap_closure": {key: dict(pass_ref) for key in R.PRIMARY_GAPS},
        "device_certifications": [
            {"model": model,
             "serial_number": "PHYSICAL-TEST-SERIAL-" + str(i),
             "firmware": ("8.10.0.21" if model.startswith("AP-") else "10.14.1020"),
             "environment": "PHYSICAL",
             "checks": {key: dict(pass_ref) for key in R.DEVICE_CHECKS},
             "certified_operations": {
                 name: dict(pass_ref) for name in (
                     INSTANT_TYPED_OPS if model.startswith("AP-") else CX_TYPED_OPS
                 )
             }}
            for i, model in enumerate(R.MODELS)
        ],
        "signatures": [],
    }
    private = {
        "review.security": Ed25519PrivateKey.generate(),
        "review.operations": Ed25519PrivateKey.generate(),
    }
    keyring = {"reviewers": {
        who: {"role": ("security" if who == "review.security" else "operations"),
              "ed25519_public_key": base64.b64encode(
                  key.public_key().public_bytes(
                      encoding=serialization.Encoding.Raw,
                      format=serialization.PublicFormat.Raw
                  )).decode()}
        for who, key in private.items()}}
    return m, root, keyring, private


def sign(m, keys):
    m["signatures"] = [
        {"reviewer": who, "signature": base64.b64encode(key.sign(R.signed_payload(m))).decode()}
        for who, key in keys.items()
    ]


def verdict(m, root, keys):
    return R.evaluate(m, evidence_root=root, trusted_keyring=keys,
                      expected_commit=SHA, now=NOW)


def test_valid_synthetic_signed_input_exercises_gate_only(tmp_path):
    m, root, trusted, private = build(tmp_path)
    sign(m, private)
    result = verdict(m, root, trusted)
    assert result["decision"] == "GO", result["failures"]
    assert result["verified_artifacts"] == 2
    assert result["acceptance_register"]["passed"] == 220
    assert result["models_required"] == list(R.MODELS)
    # GO here asserts the evaluator's *fixture*, not external-device evidence.


def test_empty_and_unsigned_releases_are_never_ready(tmp_path):
    assert verdict({}, tmp_path, {})["decision"] == "NO_GO"
    m, root, trusted, private = build(tmp_path)
    assert verdict(m, root, trusted)["decision"] == "NO_GO"
    sign(m, private)
    m["phases"]["11"]["status"] = "BLOCKED"
    assert any("phase-11" in e for e in verdict(m, root, trusted)["failures"])


def test_missing_physical_device_and_operation_blocks_release(tmp_path):
    m, root, trusted, private = build(tmp_path)
    m["device_certifications"] = m["device_certifications"][:-1]
    sign(m, private)
    assert any("CX 6200" in e for e in verdict(m, root, trusted)["failures"])
    m, root, trusted, private = build(tmp_path)
    del m["device_certifications"][0]["certified_operations"]["ap.radio.width"]
    sign(m, private)
    assert any("ap.radio.width" in e for e in verdict(m, root, trusted)["failures"])


def test_does_not_accept_emulated_as_physical(tmp_path):
    m, root, trusted, private = build(tmp_path)
    m["device_certifications"][0]["environment"] = "EMULATED"
    m["device_certifications"][0]["serial_number"] = "EMU-AP-535"
    sign(m, private)
    assert any("physical" in e for e in verdict(m, root, trusted)["failures"])


def test_sha256_tampering_release_sha_and_key_tampering_fail(tmp_path):
    m, root, trusted, private = build(tmp_path)
    sign(m, private)
    (root / "artifacts.txt").write_text("tampered!")
    assert any("SHA-256 mismatch" in e for e in verdict(m, root, trusted)["failures"])
    m, root, trusted, private = build(tmp_path)
    sign(m, private)
    m["gap_closure"]["dfs_roaming"] = {"status": "PASS", "evidence": ["artifacts.txt"], "note": "tampered"}
    assert any("invalid signature" in e for e in verdict(m, root, trusted)["failures"])
    m, root, trusted, private = build(tmp_path)
    sign(m, private)
    result = R.evaluate(m, evidence_root=root, trusted_keyring=trusted, expected_commit="b" * 40, now=NOW)
    assert result["decision"] == "NO_GO" and "release commit differs" in " ".join(result["failures"])


def test_untrusted_duplicate_preparer_and_old_reviewer_fail(tmp_path):
    m, root, trusted, private = build(tmp_path)
    sign(m, private)
    trusted["reviewers"]["review.operations"]["role"] = "viewer"
    assert verdict(m, root, trusted)["decision"] == "NO_GO"
    m, root, trusted, private = build(tmp_path)
    m["prepared_by"] = "review.security"
    sign(m, private)
    assert verdict(m, root, trusted)["decision"] == "NO_GO"
    m, root, trusted, private = build(tmp_path)
    m["created_at"] = (NOW - timedelta(days=32)).isoformat()
    sign(m, private)
    assert any("timestamp" in e for e in verdict(m, root, trusted)["failures"])


def test_path_traversal_and_symlinks_not_read(tmp_path):
    m, root, trusted, private = build(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("private data")
    m["artifacts"] = {"../outside.txt": hashlib.sha256(target.read_bytes()).hexdigest()}
    sign(m, private)
    result = verdict(m, root, trusted)
    assert result["decision"] == "NO_GO"
    assert any("invalid relative path" in e for e in result["failures"])


def test_cli_fails_closed_on_missing_inputs(tmp_path):
    cli = Path(__file__).resolve().parents[2] / "scripts" / "production-readiness.py"
    p = subprocess.run([
        sys.executable, str(cli), "--manifest", str(tmp_path / "missing.json"),
        "--keyring", str(tmp_path / "missing-keys.json"),
        "--evidence-root", str(tmp_path),
        "--expected-commit", SHA, "--json",
    ], capture_output=True, text=True, timeout=20)
    assert p.returncode == 3, p.stderr
    result = json.loads(p.stdout)
    assert result["decision"] == "NO_GO"


def test_acceptance_register_rejects_partial_missing_actual_and_duplicates(tmp_path):
    m, root, trusted, private = build(tmp_path)
    register = root / "acceptance.csv"
    rows = register.read_text().splitlines()
    rows[1] = rows[1].replace("PASS", "PARTIAL")
    register.write_text("\n".join(rows) + "\n")
    m["artifacts"]["acceptance.csv"] = hashlib.sha256(register.read_bytes()).hexdigest()
    sign(m, private)
    assert any("acceptance" in e for e in verdict(m, root, trusted)["failures"])

    m, root, trusted, private = build(tmp_path)
    rows = (root / "acceptance.csv").read_text().splitlines()
    rows.pop()
    (root / "acceptance.csv").write_text("\n".join(rows) + "\n")
    m["artifacts"]["acceptance.csv"] = hashlib.sha256((root / "acceptance.csv").read_bytes()).hexdigest()
    sign(m, private)
    assert any("minimum 220" in e for e in verdict(m, root, trusted)["failures"])


def test_generated_template_cannot_be_misread_as_production_pass(tmp_path):
    cli = Path(__file__).resolve().parents[2] / "scripts" / "init-release-attestation.py"
    out = tmp_path / "acceptance.json"
    p = subprocess.run([
        sys.executable, str(cli), "--output", str(out), "--commit", SHA,
        "--prepared-by", "implementer",
    ], capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, p.stderr
    m = json.loads(out.read_text())
    assert all(v["status"] == "NOT_RUN" for v in m["phases"].values())
    assert all(v["environment"] == "UNVERIFIED" for v in m["device_certifications"])
    assert verdict(m, tmp_path, {})["decision"] == "NO_GO"
    assert out.stat().st_mode & 0o777 == 0o600
    again = subprocess.run([
        sys.executable, str(cli), "--output", str(out), "--commit", SHA,
        "--prepared-by", "implementer",
    ], capture_output=True, text=True, timeout=20)
    assert again.returncode != 0  # cannot overwrite a previously reviewed file
