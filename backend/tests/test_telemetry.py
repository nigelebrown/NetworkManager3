"""Phase 2b: RF/system telemetry. Fixtures reproduce the layouts of a real AP-505/AP-535 cluster on
Instant 8.13 (`show ap arm rf-summary`, `show ap debug system-status`) with invented values and addresses."""
import httpx
import pytest

from backend.aruba.client import ArubaClient, ArubaTarget
from backend.aruba.monitoring import collect_snapshot
from backend.aruba import telemetry as t
from backend.database.models import AccessPoint, Alert, ApSample, Site
from backend.database.session import session_scope
from backend.monitoring.collector import apply_snapshot
from sqlalchemy import select

from .conftest import aruba_handler
from .test_experience import _snap, fresh_db, site  # noqa: F401

RF_SUMMARY = """cli output:

COMMAND=show ap arm rf-summary

Channel Summary
---------------
phy-type  channel  retry  phy-err  mac-err  noise  util(Qual)     cov-idx(Total)  intf_idx(Total)
--------  -------  -----  -------  -------  -----  ----------     --------------  ---------------
5GHz      149      0      0        0        92     0/0/0/0/100    0/0(0)          4/0//0/0(4)
5GHz      153      0      0        0        92     2/2/0/0/100    0/0(0)          2/1//0/0(3)
5GHz      165      0      0        0        96     18/10/8/0/100  6/0(6)          0/0//0/0(0)
2.4GHz    1        0      0        0        94     41/41/0/0/100  0/0(0)          35/18//0/0(53)
2.4GHz    6        0      0        0        94     54/54/0/0/100  5/0(5)          53/18//0/0(71)
2.4GHz    11       42     0        11       96*    46/40/6/0/100  4/0(4)          34/9//0/0(43)

Columns:util(Qual): ch-util/rx/tx/ext-ch-util/quality
Noise followed by "*" indicates that the last scan on this channel was a split-scan.

HT/VHT Channel Summary
----------------------
Phy-Type  Bandwidth  Channel range  Total interference index
--------  ---------  -------------  ------------------------

Interface Name           :wifi0
Phy-Type                 :5GHz
Current ARM Assignment   :165/18.0
Covered channels 5/2.4GHz     :5/0

Interface Name           :wifi1
Phy-Type                 :2.4GHz
Current ARM Assignment   :11/12.0
Covered channels 5/2.4GHz     :0/3
"""

SYSTEM_STATUS = """cli output:

COMMAND=show ap debug system-status

Reboot Information
------------------
AP rebooted caused by cold HW reset(power loss)

CPU and Memory Usage
--------------------
Timestamp            CPU Util(%)  Memory Util(%)
---------            -----------  --------------
2026-11-07 00:22:49  2            32
2026-11-07 00:22:39  2            32
2026-11-07 00:21:49  4            31

Peak CPU Util in the last one hour
----------------------------------
Timestamp            CPU Util(%)  Memory Util(%)
---------            -----------  --------------
2026-11-07 00:01:37  96           99

AP Uptime
---------
6 days 5 hours 54 mins 51 secs

-------------------------------
"""


def test_channel_summary_rows_and_noise_sign():
    rows = t.parse_channel_summary(RF_SUMMARY)
    assert len(rows) == 6
    r165 = next(r for r in rows if r["channel"] == 165)
    assert r165["noise_floor_dbm"] == -96 and r165["util_pct"] == 18 and r165["tx_pct"] == 8
    r11 = next(r for r in rows if r["band"] == "2.4GHz" and r["channel"] == 11)
    assert r11["noise_floor_dbm"] == -96 and r11["retry"] == 42   # '*' split-scan marker is ignored


def test_radios_use_their_own_current_channel():
    rf = t.parse_rf_summary(RF_SUMMARY)
    r5, r24 = rf["radios"]
    assert (r5["index"], r5["band"], r5["channel"], r5["tx_power_dbm"]) == (0, "5GHz", "165", 18.0)
    assert r5["util_percent"] == 18 and r5["noise_floor_dbm"] == -96
    assert r24["channel"] == "11" and r24["util_percent"] == 46 and r24["tx_power_dbm"] == 12.0


def test_radio_without_a_row_for_its_channel_has_no_measurements():
    text = RF_SUMMARY.replace(":165/18.0", ":36/18.0")
    r5 = t.parse_rf_summary(text)["radios"][0]
    assert r5["channel"] == "36" and "util_percent" not in r5 and "noise_floor_dbm" not in r5


@pytest.mark.parametrize("bad", [None, "", "garbage", "Channel Summary\n---\n"])
def test_unusable_rf_output_gives_none(bad):
    assert t.parse_rf_summary(bad) is None


def test_system_status_uses_latest_sample_not_peak():
    st = t.parse_system_status(SYSTEM_STATUS)
    assert st == {"uptime_seconds": 6 * 86400 + 5 * 3600 + 54 * 60 + 51, "cpu_percent": 2.0, "memory_percent": 32.0}


@pytest.mark.parametrize("line,secs", [("5 mins 3 secs", 303), ("1 day 2 hours 3 mins 4 secs", 93784), ("42 secs", 42)])
def test_uptime_formats(line, secs):
    assert t.parse_uptime(f"AP Uptime\n---------\n{line}\n") == secs


def test_missing_sections_stay_unavailable():
    assert t.parse_system_status("AP Uptime\n---------\n10 mins 0 secs\n") == {
        "uptime_seconds": 600, "cpu_percent": None, "memory_percent": None}
    assert t.parse_system_status("nothing useful") is None
    assert t.parse_uptime("AP Uptime\n---------\nsoon\n") is None


# ---- collection ---------------------------------------------------------------------------------------------------
def _client(handler):
    return ArubaClient(ArubaTarget(host="10.0.0.1", username="u", password="p"), transport=httpx.MockTransport(handler))


def _handler(fail_ips=()):
    base = aruba_handler()
    seen = []

    def h(request):
        if request.url.path == "/rest/show-cmd":
            cmd, ip = request.url.params.get("cmd"), request.url.params.get("iap_ip_addr")
            if cmd in ("show ap arm rf-summary", "show ap debug system-status"):
                seen.append((cmd, ip))
                if ip in fail_ips:
                    return httpx.Response(200, json={"Status": "Failure", "Message": "Failed"})
                text = RF_SUMMARY if "rf-summary" in cmd else SYSTEM_STATUS
                return httpx.Response(200, json={"Status": "Success", "Command output": text})
        return base(request)
    h.seen = seen
    return h


def test_snapshot_gets_telemetry_per_ap_targeted_at_that_ap():
    h = _handler()
    with _client(h) as c:
        snap = collect_snapshot(c)
    up = [a for a in snap.aps if a.get("ip")]
    assert up and {ip for _, ip in h.seen} == {a["ip"] for a in up}
    a = up[0]
    assert a["util_5g_percent"] == 18 and a["util_2g_percent"] == 46
    assert a["cpu_percent"] == 2.0 and a["uptime_seconds"] == 539691
    assert not any("telemetry" in w for w in snap.warnings)


def test_one_failing_ap_does_not_affect_others():
    probe = _handler()
    with _client(probe) as c:
        aps = [a["ip"] for a in collect_snapshot(c).aps if a.get("ip")]
    if len(aps) < 2:
        pytest.skip("fixture has a single AP")
    with _client(_handler(fail_ips={aps[0]})) as c:
        snap = collect_snapshot(c)
    by_ip = {a["ip"]: a for a in snap.aps if a.get("ip")}
    assert by_ip[aps[0]].get("util_5g_percent") is None and by_ip[aps[0]].get("cpu_percent") is None
    assert by_ip[aps[1]]["util_5g_percent"] == 18
    assert any("telemetry unavailable for 1 AP" in w for w in snap.warnings)


def test_unsupported_cluster_stops_after_repeated_failures():
    from backend.aruba.monitoring import MAX_CONSECUTIVE_TELEMETRY_FAILURES, enrich_with_telemetry, SiteSnapshot
    aps = [{"name": f"ap{i}", "ip": f"10.0.0.{i + 10}", "status": "up"} for i in range(10)]
    h = _handler(fail_ips={a["ip"] for a in aps})
    with _client(h) as c:
        snap = SiteSnapshot(aps=aps)
        enrich_with_telemetry(c, snap)
    assert len(h.seen) == MAX_CONSECUTIVE_TELEMETRY_FAILURES * 2      # then it stops asking
    assert "unavailable for 10 AP" in snap.warnings[0]
    assert all(a.get("util_5g_percent") is None for a in aps)         # never invented


def test_telemetry_can_be_disabled(monkeypatch):
    from backend.config import get_settings
    monkeypatch.setenv("SIMS_TELEMETRY_ENABLED", "false")
    get_settings.cache_clear()
    h = _handler()
    with _client(h) as c:
        collect_snapshot(c)
    get_settings.cache_clear()
    assert h.seen == []


def test_utilization_and_reboot_flow_into_samples_and_alerts(site):
    ap = {"name": "ap1", "mac": "aa:aa:aa:00:00:01", "ip": "10.0.0.2", "status": "up", "client_count": 0,
          "uptime_seconds": 90000, "util_5g_percent": 91.0, "util_2g_percent": 40.0,
          "radios": [{"index": 0, "band": "5GHz", "channel": "36", "util_percent": 91.0}]}
    for _ in range(5):                                                  # utilization_polls default = 5
        with session_scope() as db:
            apply_snapshot(db, db.get(Site, site), _snap([], aps=[dict(ap)]))
    with session_scope() as db:
        assert db.scalars(select(Alert.kind).where(Alert.kind == "high_utilization")).first() == "high_utilization"
        assert db.scalars(select(ApSample.util_5g_percent)).first() == 91.0
        assert db.scalars(select(AccessPoint.radios)).first()[0]["channel"] == "36"
    ap["uptime_seconds"] = 120                                          # went backwards: rebooted
    with session_scope() as db:
        apply_snapshot(db, db.get(Site, site), _snap([], aps=[ap]))
    with session_scope() as db:
        assert db.scalars(select(Alert.kind).where(Alert.kind == "ap_reboot")).first() == "ap_reboot"


def test_iap_ip_must_be_an_ip_address():
    from backend.aruba.errors import ArubaUnsupportedError
    with _client(aruba_handler()) as c:
        with pytest.raises(ArubaUnsupportedError):
            c.show("show aps", iap_ip="10.0.0.2&cmd=reload")
