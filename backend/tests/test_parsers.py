from backend.aruba import parsers

from .conftest import AP_TABLE, CLIENT_TABLE, VERSION_TEXT


def test_text_table_ap_parsing():
    rows = parsers.extract_rows({"Command output": AP_TABLE}, "show aps")
    aps = parsers.normalize_aps(rows)
    assert len(aps) == 2
    a = aps[0]
    assert a["name"] == "lab-ap-1" and a["ip"] == "10.190.2.71"
    assert a["client_count"] == 2
    assert a["uptime_seconds"] == 86400 + 2 * 3600 + 3 * 60 + 4
    assert [r["band"] for r in a["radios"]] == ["5GHz", "2.4GHz"]
    assert a["mac"] is None  # not in this table -> unavailable, not invented


def test_structured_json_rows():
    resp = {"Status": "Success", "show aps": [{"Name": "x", "IP Address": "1.2.3.4", "MAC Address": "AA-BB-CC-DD-EE-FF"}]}
    aps = parsers.normalize_aps(parsers.extract_rows(resp, "show aps"))
    assert aps[0]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_client_parsing_and_quality():
    clients = parsers.normalize_clients(parsers.extract_rows({"Command output": CLIENT_TABLE}, "show clients"))
    assert len(clients) == 2
    c = clients[0]
    assert c["mac"] == "aa:bb:cc:00:11:22" and c["snr_db"] == 42 and c["quality"] == "good"
    assert c["rssi_dbm"] is None  # never estimated
    assert c["speed_mbps"] == 866.0
    assert clients[1]["quality"] == "poor"


def test_quality_from_snr_thresholds():
    assert parsers.quality_from_snr(None) == "unavailable"
    assert parsers.quality_from_snr(25) == "good"
    assert parsers.quality_from_snr(24) == "fair"
    assert parsers.quality_from_snr(14) == "poor"


def test_version_and_uptime():
    assert parsers.parse_version({"Command output": VERSION_TEXT}) == "8.10.0.21-8.10.0.21"
    assert parsers.parse_uptime("45m:10s") == 2710
    assert parsers.parse_uptime("garbage") is None


def test_empty_output_returns_no_rows():
    assert parsers.extract_rows({"Status": "Success"}, "show aps") == []


# Shape captured from a real AP-505 (8.10.0.21): title, dashes, header, header-underline, row.
REAL_AP_505 = """1 Access Points
---------------
Name                IP Address     Mode    Spectrum  Clients  Type         IPv6 Address  Mesh Role  Zone  Serial #
----                ----------     ----    --------  -------  ----         ------------  ---------  ----  --------
34:8a:12:aa:bb:cc   10.190.2.71*   access  disable   0        505(indoor)  --            Portal     N/A   CNXX00000
"""


def test_header_underline_is_not_an_ap_and_master_ip_is_cleaned():
    aps = parsers.normalize_aps(parsers.extract_rows({"Command output": REAL_AP_505}, "show aps"))
    assert len(aps) == 1
    a = aps[0]
    assert a["ip"] == "10.190.2.71" and a["is_master"] is True
    assert a["mac"] == "34:8a:12:aa:bb:cc"  # AP is named after its MAC
    assert a["model"] == "AP-505"
    assert a["client_count"] == 0
    assert a["uptime_seconds"] is None  # not in this table: stays unavailable


def test_normalize_model():
    assert parsers.normalize_model("505(indoor)") == "AP-505"
    assert parsers.normalize_model("AP-515") == "AP-515"
    assert parsers.normalize_model(None) is None
