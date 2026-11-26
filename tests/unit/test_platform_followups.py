"""AP dashboard follow-ups (memory MiB, uplink rates, monitor tables) and syslog year-as-host mapping.

Fixtures are synthetic text in the documented shapes; hardware-captured fixtures are still NOT_RUN, which is why
values carry PARSED_FORMAT_UNVERIFIED."""
import pytest

from backend.aruba import telemetry as T
from backend.domain.syslog_host import resolve_host
from backend.monitoring.collector import merge_observations

SYS = """CPU and Memory Usage
...
MemTotal:         491520 kB
MemFree:           81920 kB
MemAvailable:     163840 kB
eth0      Link encap:Ethernet  HWaddr 00:1A:1E:00:00:01
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:1000 errors:0 dropped:0 overruns:0 frame:0
          RX bytes:123456789 (117.7 MiB)  TX bytes:987654321 (941.9 MiB)
lo        Link encap:Local Loopback
          RX bytes:5 (5.0 B)  TX bytes:5 (5.0 B)
"""


def test_memory_free_prefers_available_and_never_guesses():
    assert T.parse_memory_free_mib(SYS) == {"memory_free_mib": 160.0, "basis": "MemAvailable", "format": "PARSED_FORMAT_UNVERIFIED"}
    assert T.parse_memory_free_mib("MemFree: 2048 kB")["memory_free_mib"] == 2.0
    assert T.parse_memory_free_mib("Memory Util(%) 45") is None


def test_uplink_counters_ifconfig_and_proc_net_dev():
    measured = "bond0 is up, line protocol is up\nReceived bytes 1200\nTransmitted bytes 3400\n"
    assert T.parse_uplink_counters(measured)["tx_bytes"] == 3400
    assert T.parse_uplink_counters(SYS) == {"interface": "eth0", "rx_bytes": 123456789, "tx_bytes": 987654321,
                                            "format": "PARSED_FORMAT_UNVERIFIED"}
    proc = "Inter-|   Receive\n face |bytes packets\n  eth0: 5000 10 0 0 0 0 0 0 7000 12 0 0 0 0 0 0\n"
    assert T.parse_uplink_counters(proc)["tx_bytes"] == 7000
    assert T.parse_uplink_counters("no counters here") is None


@pytest.mark.parametrize("prev,cur,dt,quality,rx", [
    ({"interface": "eth0", "rx_bytes": 1000, "tx_bytes": 0}, {"interface": "eth0", "rx_bytes": 2000, "tx_bytes": 0}, 10, "VERIFIED_DELTA", 800.0),
    ({"interface": "eth0", "rx_bytes": 2**32 - 100, "tx_bytes": 0}, {"interface": "eth0", "rx_bytes": 900, "tx_bytes": 0}, 10, "ROLLOVER_32", 800.0),
    ({"interface": "eth0", "rx_bytes": 5000, "tx_bytes": 0}, {"interface": "eth0", "rx_bytes": 10, "tx_bytes": 0}, 10, "COUNTER_RESET", None),
    ({"interface": "eth0", "rx_bytes": 1, "tx_bytes": 0}, {"interface": "eth0", "rx_bytes": 2, "tx_bytes": 0}, 7200, "GAP", None),
    (None, {"interface": "eth0", "rx_bytes": 2, "tx_bytes": 0}, 10, "FIRST_SAMPLE", None),
    ({"interface": "bond0", "rx_bytes": 1, "tx_bytes": 0}, {"interface": "eth0", "rx_bytes": 2, "tx_bytes": 0}, 10, "FIRST_SAMPLE", None),
])
def test_uplink_rates_only_from_valid_deltas(prev, cur, dt, quality, rx):
    from datetime import datetime, timedelta
    t0 = datetime(2026, 11, 14, 12, 0)
    r = T.uplink_rates(prev, cur, t0.isoformat(), (t0 + timedelta(seconds=dt)).isoformat())
    assert r["quality"] == quality and r["rx_bps"] == rx


def test_merge_observations_derives_rates_across_polls():
    a = merge_observations(None, {"uplink_counters": {"interface": "eth0", "rx_bytes": 0, "tx_bytes": 0}}, "2026-11-14T12:00:00+00:00")
    assert a["uplink_rates"]["quality"] == "FIRST_SAMPLE"
    b = merge_observations(a, {"uplink_counters": {"interface": "eth0", "rx_bytes": 7500, "tx_bytes": 15000}}, "2026-11-14T12:01:00+00:00")
    assert b["uplink_rates"] == {"interval_s": 60.0, "quality": "VERIFIED_DELTA", "rx_bps": 1000.0, "tx_bps": 2000.0}


MONITOR_AP = """Monitored AP Table
------------------
bssid              essid          chan  ap-type     phy-type  dt/mt  ut/it  encr      sig
-----              -----          ----  -------     --------  -----  -----  ----      ---
00:1a:1e:00:00:10  CORP           36    valid       80211ac   1/1    0/0    wpa2      -55
00:1a:1e:00:00:11  CORP           6     valid       80211n    1/1    0/0    wpa2      -61
aa:bb:cc:dd:ee:01  Neighbour-WiFi 149   interfering 80211ax   2/2    0/0    wpa3      -78
aa:bb:cc:dd:ee:01  Neighbour-WiFi 149   interfering 80211ax   2/2    0/0    wpa3      -78
Num APs:3
"""


def test_monitor_tables_count_unique_over_the_air_neighbours_by_band():
    r = T.parse_monitor_list(MONITOR_AP, "ap")
    assert r["total"] == 3 and r["by_band"] == {"5GHz": 2, "2.4GHz": 1}
    assert r["by_type"] == {"valid": 2, "interfering": 2}
    clients = "mac                chan  sig\n11:22:33:44:55:66  1     -70\n11:22:33:44:55:67  44    -60\n"
    assert T.parse_monitor_list(clients, "client")["by_band"] == {"2.4GHz": 1, "5GHz": 1}
    assert T.parse_monitor_list("Unknown command", "ap") is None


@pytest.mark.parametrize("host,line,expected", [
    ("2026", "19:31:02 AP-HQ-01 cli[123]: <341004> config change", ("AP-HQ-01", "YEAR_AS_HOST_CORRECTED")),
    ("2026", "19:31:02.123 10.103.5.20 stm[9]: assoc", ("10.103.5.20", "YEAR_AS_HOST_CORRECTED")),
    ("AP-HQ-01", "anything", ("AP-HQ-01", None)),
    ("2026", "garbled message", ("2026", "YEAR_AS_HOST_UNRESOLVED")),
    (None, "<134>Nov 14 2026 19:31:02 cx-core-1 hpe-restd: audit", ("cx-core-1", "HOST_FROM_HEADER")),
    ("19:31:02", "19:31:02 sw2 lldp: up", ("sw2", "TIME_AS_HOST_CORRECTED")),
    ("2026", "19:31:02 2027 x", ("2026", "YEAR_AS_HOST_UNRESOLVED")),
    # rc18: year AFTER the time, as stored by the shipped Alloy RFC 3164 parser (verified with real packets)
    ("2026", "10.103.5.20 stm[3442]: <501106> <NOTI> assoc", ("10.103.5.20", "YEAR_AS_HOST_CORRECTED")),
    ("2026", "iap-b cli: config saved", ("iap-b", "YEAR_AS_HOST_CORRECTED")),
    ("2026", "just some text", ("2026", "YEAR_AS_HOST_UNRESOLVED")),
    ("iap-c", "iap-x cli[1]: not a year host", ("iap-c", None)),
])
def test_syslog_year_as_host_resolution(host, line, expected):
    assert resolve_host(host, line) == expected
