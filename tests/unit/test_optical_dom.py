from backend.infrastructure.device_diagnostics import parse_optical_dom

HEAD = "Port Type Channel Temperature Voltage Tx Bias Rx Power Tx Power\n"

def test_optical_dom_parses_units_and_missing_values():
    result = parse_optical_dom(HEAD +
        "1/1/1 SFP+SR 45.1 3.3 8.4 0.08, -10.96 0.63, -2.49\n"
        "1/1/2 SFP+SR n/a n/a n/a n/a n/a\n")
    assert result["state"] == "COLLECTED"
    assert result["ports"]["1/1/1"]["rx_power_dbm"] == -10.96
    assert result["ports"]["1/1/1"]["temperature_c"] == 45.1
    assert result["ports"]["1/1/2"]["rx_power_mw"] is None

def test_optical_dom_reports_unrecognized_or_partial():
    assert parse_optical_dom("command unavailable")["state"] == "UNRECOGNIZED"
    result = parse_optical_dom(HEAD + "1/1/1 QSFP+SR4 1 44 3.3 6.1 0.08, -10.96 0.63, -1.95\n")
    assert result["state"] == "PARTIAL"
    assert not result["ports"]
