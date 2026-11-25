"""Typed wireless policy extensions; emulator evidence is not physical certification."""
import pytest

from backend.domain import change_control as CC
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.aruba_instant import regulatory_channels
from backend.infrastructure.writers.emulator import InstantEmulator
from backend.infrastructure.change_executor import _norm


@pytest.mark.parametrize("values", [["36", "36E"], ["36", "36S"], ["36", "40+"],
                                   ["36", "36"], ["36", "52"], ["36", "52+"]])
def test_channel_list_refuses_invalid_width_pairs_duplicates_and_unapproved_dfs(values):
    with pytest.raises(CC.PolicyViolation):
        CC.normalize_channel_list(values)


def test_bonded_secondary_and_dfs_checked():
    assert CC.normalize_channel_list(["40", "36", "36+"]) == ["36", "36+", "40"]
    with pytest.raises(CC.PolicyViolation):
        CC.normalize_channel_list(["36", "36+"], excluded_channels={40})
    assert CC.normalize_channel_list(["52", "52+"], allow_dfs=True) == ["52", "52+"]


@pytest.mark.parametrize("op", [
    {"op": "ap.ssid.roaming", "ssid": "Corp-WiFi", "dot11k": "enabled", "dot11v": "enabled", "dot11r": "enabled"},
    {"op": "ap.ssid.accounting", "ssid": "INM-LAB-1X", "value": "enabled", "interval_minutes": 10},
    {"op": "ap.radio.allowed_channels", "value": ["36", "40", "36+"]},
    {"op": "ap.ssid.guest_auth", "ssid": "INM-LAB-GUEST", "portal_profile": "LAB-PORTAL-B",
     "pre_auth_role": "LAB-PRE", "mac_role": "LAB-MAC", "acknowledge_guest_auth_change": True},
])
def test_wireless_extensions_render_apply_readback_and_restore(op):
    emu = InstantEmulator()
    writer = emu.writer()
    writer.open()
    try:
        before = writer.backup()
        cp = writer.checkpoint()
        rendered = writer.render(op)
        from backend.aruba.ssh import _ALLOWED_CONFIG_LINES
        assert all(any(pattern.fullmatch(line.strip()) for pattern in _ALLOWED_CONFIG_LINES)
                   for line in rendered[0]["lines"])
        writer.apply(op)
        assert writer.readback(op)[0]
        writer.rollback(cp)
        assert _norm(writer.backup(), "access_point") == _norm(before, "access_point")
        assert not writer.readback(op)[0]
    finally:
        writer.close()


def test_unsafe_or_nonexistent_baseline_refused_without_changes():
    emu = InstantEmulator()
    writer = emu.writer()
    writer.open()
    try:
        before = emu.text
        writer.checkpoint()
        with pytest.raises(WriterError):
            writer.apply({"op": "ap.ssid.accounting", "ssid": "Corp-WiFi", "value": "enabled", "interval_minutes": 10})
        with pytest.raises(WriterError):
            writer.apply({"op": "ap.ssid.roaming", "ssid": "absent", "dot11k": "enabled", "dot11v": "enabled", "dot11r": "enabled"})
        with pytest.raises(WriterError):
            writer.apply({"op": "ap.radio.allowed_channels", "value": ["36", "52"], "allow_dfs": True})
        assert emu.text == before
    finally:
        writer.close()


def test_regulatory_parser_uses_intersection_and_does_not_invent_channels():
    raw = ("Allowed Channels for AP Type 505 Country Code JM\n"
           "5GHz (indoor) 36 40 44 48 149 153\n"
           "5GHz (outdoor) 149 153\n"
           "5GHz 40MHz (indoor) 36-40 149-153\n"
           "5GHz 40MHz (outdoor) 149-153\n"
           "5GHz 80MHz (indoor) 36-48")
    assert regulatory_channels(raw) == ("JM", {"149", "153", "149+"})
    with pytest.raises(WriterError):
        regulatory_channels("unrecognized text")
