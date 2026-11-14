"""Fixed, read-only Aruba diagnostics. Emulator evidence is not hardware certification."""
from __future__ import annotations

import pytest
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator, InstantEmulator
from tests.contract.test_production_read_write import env, live  # noqa: F401
from tests.conftest import make_user


def test_cx_diagnostics_are_show_only_with_protocol_catalogue_and_no_write():
    for model, vsf in (("CX 6100 24G", False), ("CX 6200F 48G", True)):
        emu = CxEmulator(model=model)
        w = emu.writer()
        w.open()
        try:
            for kind in ("port_auth", "poe", "qos", "lacp"):
                result = w.read_diagnostics(kind=kind)
                assert result["command"].startswith("show ")
                assert result["source"] == "CX_REST_ANYCLI"
                assert "EMULATED SHOW" in result["output"]
                assert "configuration" not in result["command"]
            if vsf:
                assert w.read_diagnostics(kind="vsf")["command"] == "show vsf detail"
            else:
                with pytest.raises(WriterError) as exc:
                    w.read_diagnostics(kind="vsf")
                assert exc.value.category == "UNSUPPORTED"
            with pytest.raises(WriterError):
                w.read_diagnostics(kind="configure terminal")
            assert emu.writes == 0
            assert not emu.violations
        finally:
            w.close()


def test_instant_diagnostics_reject_unlisted_commands_and_do_not_mutate_config():
    emu = InstantEmulator()
    w = emu.writer()
    w.open()
    before = emu.text
    try:
        for kind in ("rf", "clients", "system", "wireless"):
            result = w.read_diagnostics(kind=kind)
            assert result["command"].startswith("show ")
            assert result["source"] == "INSTANT_REST_SHOW"
            assert result["output"]
        assert emu.text == before
        with pytest.raises(WriterError) as exc:
            w.read_diagnostics(kind="reload")
        assert exc.value.category == "UNSUPPORTED"
    finally:
        w.close()


def test_diagnostics_endpoint_has_role_gates_and_no_free_form_commands(env):
    c, adm, _appr, sw, ap = env
    for d, kind, expected in ((sw, "port_auth", "show port-access clients"),
                              (ap, "rf", "show ap arm rf-summary")):
        route = f"/api/v1/devices/{d['id']}/diagnostics"
        response = c.get(route, headers=adm, params={"kind": kind})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["device_writes"] == 0
        assert body["environment"] == "EMULATED"
        assert body["hardware_verified"] is False
        assert body["command"] == expected
        assert c.get(route, headers=adm, params={"kind": "configure terminal"}).status_code == 422
    viewer = make_user(c, adm, "diag.viewer", "viewer")
    assert c.get(f"/api/v1/devices/{sw['id']}/diagnostics",
                 headers=viewer, params={"kind": "poe"}).status_code == 403
