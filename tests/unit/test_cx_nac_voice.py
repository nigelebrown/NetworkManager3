import pytest

from backend.domain.change_control import validate_operation, PolicyViolation
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator


@pytest.mark.parametrize("method,kind", [("dot1x", "switch.port.auth_dot1x"), ("mac-auth", "switch.port.auth_mab")])
def test_nac_enable_readback_and_rollback(method, kind):
    emu = CxEmulator()
    writer = emu.writer()
    writer.open()
    try:
        op = {"op": kind, "method": method, "port": "1/1/24", "value": "enabled", "acknowledge_auth_change": True}
        before = writer.backup()
        with pytest.raises(WriterError, match="administratively down"):
            writer.apply(op)
        assert emu.writes == 0
        with pytest.raises(PolicyViolation):
            validate_operation(op, family="switch", uplink_ports={"1/1/24"})
        with pytest.raises(PolicyViolation):
            validate_operation({**op, "value": "disabled"}, family="switch")
        checkpoint = writer.checkpoint()
        writer.apply({"op": "switch.port.admin_state", "port": "1/1/24", "value": "down"})
        writer.apply(op)
        assert writer.readback(op)[0]
        assert emu.state["interfaces"]["1/1/24"]["admin"] == "down"
        writer.rollback(checkpoint)
        assert writer.backup() == before
        assert not writer.readback(op)[0]
    finally:
        writer.close()


def test_nac_fallback_role_and_unprovisioned_method_are_refused():
    emu = CxEmulator()
    writer = emu.writer()
    writer.open()
    try:
        op = {"op": "switch.port.auth_dot1x", "method": "dot1x", "port": "1/1/24",
              "value": "enabled", "acknowledge_auth_change": True}
        emu.state["interfaces"]["1/1/24"].update(admin="down", port_access_reject_role="permit-all")
        with pytest.raises(WriterError, match="fallback"):
            writer.apply(op)
        assert emu.writes == 0
    finally:
        writer.close()


def test_voice_preserves_vlan_and_restores_original_state():
    emu = CxEmulator()
    writer = emu.writer()
    writer.open()
    try:
        writer.apply({"op": "switch.vlan.define", "value": 200, "name": "Voice"})
        before = writer.backup()
        checkpoint = writer.checkpoint()
        op = {"op": "switch.vlan.voice", "vlan": 200, "value": "enabled", "acknowledge_voice_change": True}
        writer.apply(op)
        assert writer.readback(op)[0]
        assert emu.state["vlans"]["200"]["name"] == "Voice"
        writer.rollback(checkpoint)
        assert writer.backup() == before
        with pytest.raises(PolicyViolation):
            validate_operation({**op, "vlan": 1}, family="switch")
    finally:
        writer.close()
