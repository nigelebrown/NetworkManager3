"""Aruba device-originated logs are read-only, bounded, redacted and RBAC protected.

These tests are EMULATED only: passing does not certify REST support or log shape
on AP-505/AP-535/CX 6100/CX 6200 physical hardware.
"""
from __future__ import annotations

import json

import pytest

from backend.aruba.client import validate_command
from backend.aruba.errors import ArubaUnsupportedError
from backend.infrastructure.device_audit import normalize_cx, normalize_instant, sanitize
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator, InstantEmulator
from backend.infrastructure import change_executor as X
from tests.contract.test_production_read_write import env, live  # noqa: F401
from tests.conftest import make_user


def test_cx_logs_are_readonly_redacted_and_capped():
    emu = CxEmulator()
    writer = emu.writer()
    writer.open()
    try:
        before = emu.writes
        audit = writer.read_audit_logs(kind="audit", limit=2)
        assert len(audit) == 2
        assert audit[0]["actor"] == "lab-admin"
        assert audit[0]["action"] == "https-server"
        assert audit[0]["source"] == "cx.audit"
        assert "example-secret" not in json.dumps(audit)
        assert "[REDACTED]" in audit[1]["message"]
        events = writer.read_audit_logs(kind="event", limit=1)
        assert len(events) == 1 and "Interface 1/1/3" in events[0]["message"]
        assert emu.writes == before
        with pytest.raises(WriterError) as exc:
            writer.read_audit_logs(kind="audit", limit=1000)
        assert exc.value.category == "INVALID"
        with pytest.raises(WriterError):
            writer.read_audit_logs(kind="debug")
        assert emu.writes == before
    finally:
        writer.close()


def test_instant_log_reader_is_readonly_and_injection_proof():
    emu = InstantEmulator()
    writer = emu.writer()
    writer.open()
    try:
        base = emu.text
        system = writer.read_audit_logs(kind="system", limit=20)
        assert len(system) == 2
        assert "System started" in system[0]["message"]
        assert "example-secret" not in json.dumps(system)
        assert "[REDACTED]" in system[-1]["message"]
        assert writer.read_audit_logs(kind="security", limit=10)[0]["source"] == "instant.security"
        assert writer.read_audit_logs(kind="user", limit=10)[0]["source"] == "instant.user"
        assert emu.text == base
        with pytest.raises(WriterError) as exc:
            writer.read_audit_logs(kind="system;show running-config", limit=20)
        assert exc.value.category == "INVALID"
        with pytest.raises(WriterError):
            writer.read_audit_logs(kind="system", limit=1000)
        with pytest.raises(ArubaUnsupportedError):
            validate_command("show log security\nconfigure terminal")
    finally:
        writer.close()


def test_generic_log_normalization_drops_unknown_and_secret_fields():
    cx = {"items": [
        {"timestamp": "2026-11-13T12:00:00Z", "message": "GET /?sid=S3CRET",
         "password": "should-not-be-returned", "cookie": "sensitive"},
        {"time": "now", "data": "Bearer ABCDEFG.private_token",
         "authorization": "private"},
    ]}
    result = normalize_cx(cx, limit=2)
    assert len(result) == 2
    assert "S3CRET" not in json.dumps(result)
    assert "should-not-be-returned" not in json.dumps(result)
    assert "ABCDEFG.private_token" not in json.dumps(result)
    assert len(normalize_cx(cx, limit=1)) == 1
    assert "credentials" not in sanitize("Basic text")
    with pytest.raises(ValueError):
        normalize_instant({"Command output": 1234})
    with pytest.raises(ValueError):
        normalize_cx("unstructured text")
    with pytest.raises(ValueError):
        normalize_instant({"Command output": "a" * 300000})


def test_api_exposes_bounded_emulated_logs_without_device_writes(env):
    c, adm, _appr, sw, ap = env
    for dev, kind, expected_source in ((sw, "audit", "cx.audit"), (ap, "security", "instant.security")):
        url = f"/api/v1/devices/{dev['id']}/device-logs"
        res = c.get(url, headers=adm, params={"kind": kind, "limit": 20})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["device_id"] == dev["id"]
        assert body["environment"] == "EMULATED"
        assert body["hardware_verified"] is False
        assert body["count"] > 0
        assert all(item["source"] == expected_source for item in body["records"])
        assert "example-secret" not in res.text
        assert c.get(url, headers=adm, params={"kind": "invalid"}).status_code == 422
        assert c.get(url, headers=adm, params={"limit": 1000}).status_code == 422
    assert X._EMULATORS[sw["id"]].writes == 0
    viewer = make_user(c, adm, "read-only-viewer", "viewer")
    assert c.get(f"/api/v1/devices/{sw['id']}/device-logs", headers=viewer).status_code == 403
