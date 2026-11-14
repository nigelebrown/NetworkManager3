"""Regression fixtures derived from an authorized, read-only Aruba Instant REST test.

The snippets omit device identity, sessions, credentials, and client information.
No tests contact real equipment or perform configuration writes.
"""
import httpx
import pytest

from backend.aruba.client import ArubaClient, ArubaTarget
from backend.aruba.errors import ArubaCommandError
from backend.aruba.telemetry import parse_radio_stats, parse_rf_summary
from backend.aruba.monitoring import SiteSnapshot, enrich_with_telemetry


RF_SUMMARY = """
Channel Summary
---------------
phy-type  channel  retry  phy-err  mac-err  noise  util(Qual)     cov-idx(Total)  intf_idx(Total)
--------  -------  -----  -------  -------  -----  ----------     --------------  ---------------
5GHz      165      1      0        0        96     9/3/5/0/99     6/0(6)          3/0//0/0(3)
2.4GHz    6        4      0        0        96     67/3/57/0/93   4/0(4)          69/23//0/0(92)

Interface Name           :wifi0
Phy-Type                 :5GHz
Current ARM Assignment   :165/18.0
Interface Name           :wifi1
Phy-Type                 :2.4GHz
Current ARM Assignment   :6/12.0
"""

RADIO_STATS = """
RADIO Stats
-----------
Parameter                        Value
---------                        -----
Total Radio Resets               33680
TX Power Changes                 2
Channel Changes                  3
Current Noise Floor              96
EIRP                             18.0
MAX EIRP                         28.5
Tx Frames Transmitted            245399594
Tx Frames Dropped                31168
Tx Success With Retry            29838523
Tx Multiple Retries              57924704
Tx Missed ACKs                   32196973
Rx Last SNR                      52
Rx Frames Received               201851019
Rx Bad Frames                    894623
Rx Retry Frames                  7372753
Channel Busy 1s                  13
Channel Busy 4s                  32
Channel Busy 64s                 35
Rx CRC Errors                    790119
Rx RADAR Events                  0
CCA percentage of our bss        11
CCA percentage of other bss      1
CCA percentage of interference   1
"""


def test_verified_rf_summary_parses_current_radios_and_quality():
    result = parse_rf_summary(RF_SUMMARY)
    assert result is not None
    assert len(result["channels"]) == 2
    assert result["channels"][0]["quality_percent"] == 99
    assert result["channels"][1]["util_pct"] == 67
    assert result["channels"][1]["quality_percent"] == 93
    five, two = result["radios"]
    assert (five["band"], five["channel"], five["tx_power_dbm"]) == ("5GHz", "165", 18.0)
    assert (five["util_percent"], five["noise_floor_dbm"]) == (9.0, -96.0)
    assert (two["band"], two["channel"], two["tx_power_dbm"]) == ("2.4GHz", "6", 12.0)
    assert two["util_percent"] == 67.0


def test_verified_radio_stats_extracts_only_compact_metrics():
    data = parse_radio_stats(RADIO_STATS)
    assert data is not None
    assert data["channel_busy_1s_percent"] == 13.0
    assert data["channel_busy_64s_percent"] == 35.0
    assert data["tx_frames_transmitted"] == 245399594
    assert data["rx_crc_errors"] == 790119
    assert data["cca_interference_percent"] == 1.0
    assert data["noise_floor_dbm"] == -96.0
    assert data["eirp_dbm"] == 18.0
    assert "client_mac" not in data
    assert parse_radio_stats("% Parse error.") is None


def test_aruba_http_200_cli_error_is_not_success():
    def handler(request):
        if request.url.path == "/rest/login":
            return httpx.Response(200, json={"Status": "Success", "sid": "test-sid"})
        if request.url.path == "/rest/show-cmd":
            return httpx.Response(200, json={
                "Status": "Failed", "Status-code": 6,
                "Error message": "cli output: COMMAND=show arm rf-summary % Parse error.",
            })
        return httpx.Response(200, json={"Status": "Success"})

    with ArubaClient(ArubaTarget("10.0.0.10", username="admin", password="test"),
                     transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ArubaCommandError, match="API code 6"):
            client.show("show arm rf-summary")


def test_monitoring_uses_verified_commands_and_preserves_partial_snapshot(monkeypatch):
    from backend.config import get_settings

    calls = []

    class FakeClient:
        def show(self, command, *, iap_ip=None):
            calls.append((command, iap_ip))
            if command == "show ap arm rf-summary":
                return {"Command output": RF_SUMMARY}
            if command == "show ap debug radio-stats":
                return {"Command output": RADIO_STATS}
            if command == "show ap debug system-status":
                raise ArubaCommandError("unsupported on test fixture")
            raise AssertionError(f"Unexpected command: {command}")

    snap = SiteSnapshot(aps=[{"name": "test-ap", "ip": "10.0.0.10", "status": "up"}])
    enrich_with_telemetry(FakeClient(), snap)
    ap = snap.aps[0]
    assert ap["util_5g_percent"] == 9.0
    assert ap["util_2g_percent"] == 67.0
    assert ap["radios"][0]["quality_percent"] == 99
    assert ap["radio_stats"]["rx_crc_errors"] == 790119
    assert len(ap["rf_channels"]) == 2
    assert not snap.warnings
    assert [c[0] for c in calls] == [
        "show ap arm rf-summary", "show ap debug radio-stats", "show ap debug system-status"
    ]
