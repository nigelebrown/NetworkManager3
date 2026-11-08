"""Pure printer analytics: avoid fabricated precision and bad counter arithmetic."""
from decimal import Decimal
from types import SimpleNamespace

from backend.printers.analytics import cycle_analysis, meter_change, low_supply_alert, compare_model_cycles
from backend.printers.snmp import parse_printer_marker, permitted_target


def cycle(**kwargs):
    base = dict(
        id="cycle-1", status="closed", meter_kind="impressions_total",
        start_meter=1000, end_meter=2800, currency="JMD", colour="black",
        cost_amount=Decimal("9500.00"), declared_yield=2000,
        measured_consumed_ml=None, quantity_added_ml=None, partial_refill=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_actual_counter_delta_not_equivalent_to_declared_yield():
    report = cycle_analysis(cycle())
    assert report["impressions_during_cycle"] == 1800
    assert report["cost_per_1000_impressions"] == 5277.78
    assert report["declared_yield_ratio_percent"] == 90.0
    assert report["quality"] == "CALCULATED_PROXY"
    assert "does not prove" in " ".join(report["warnings"])


def test_reset_and_missing_readings_do_not_create_fake_usage():
    assert meter_change(21000, 1000)["quality"] == "RESET_OR_ROLLOVER"
    assert meter_change(None, 10)["delta"] is None
    report = cycle_analysis(cycle(end_meter=500))
    assert report["cost_per_impression"] is None
    assert report["quality"] == "RESET_OR_ROLLOVER"


def test_zero_and_open_cycle_have_no_per_page_cost():
    assert cycle_analysis(cycle(end_meter=1000))["cost_per_impression"] is None
    assert cycle_analysis(cycle(status="open", end_meter=None))["impressions_during_cycle"] is None


def test_refill_added_volume_is_not_treated_as_consumed_volume():
    r = cycle_analysis(cycle(quantity_added_ml=Decimal("200"), partial_refill=True))
    assert r["measured_ink_ml_per_impression"] is None
    assert r["pages_per_ml_added"] == 9.0
    assert "NOT assumed consumed" in " ".join(r["warnings"])


def test_measured_ink_volume_available_separately_from_refill_proxy():
    r = cycle_analysis(cycle(measured_consumed_ml=Decimal("27"), quantity_added_ml=Decimal("35")))
    assert r["measured_ink_ml_per_impression"] == 0.015
    assert r["pages_per_ml_added"] is None


def test_sheets_are_not_relabelled_impressions():
    report = cycle_analysis(cycle(meter_kind="sheets_total"))
    assert report["impressions_during_cycle"] is None
    assert report["cost_per_1000_impressions"] is None


def test_model_comparison_requires_three_valid_cycles():
    results = compare_model_cycles([("Model X", cycle(id="1")), ("Model X", cycle(id="2"))])
    assert results[0]["quality"] == "INSUFFICIENT_EVIDENCE"
    assert results[0]["mean_consumable_cost_per_1000_impressions"] is None
    results = compare_model_cycles([("Model X", cycle(id=str(i))) for i in range(3)])
    assert results[0]["verified_cycles"] == 3
    assert results[0]["quality"] == "COMPARABLE_COST_ONLY"


def test_low_supply_does_not_infer_missing_or_bad_value():
    assert low_supply_alert(None) is None
    assert low_supply_alert(-1) is None
    assert low_supply_alert(101) is None
    assert low_supply_alert(6) == "LOW"
    assert low_supply_alert(5) == "CRITICAL"


def test_snmpv3_acl_needs_explicit_private_narrow_ranges():
    assert not permitted_target("10.101.20.10", "")
    assert not permitted_target("127.0.0.1", "127.0.0.0/24")
    assert not permitted_target("10.101.20.10", "10.0.0.0/8")
    assert not permitted_target("8.8.8.8", "8.8.8.0/24")
    assert permitted_target("10.101.20.10", "10.101.20.10/32")
    assert permitted_target("10.101.20.10", "10.101.20.0/24")
    assert not permitted_target("10.101.21.10", "10.101.20.0/24")


def test_printer_marker_life_count_requires_reported_unit():
    assert parse_printer_marker(7, 4500)["meter_kind"] == "impressions_total"
    assert parse_printer_marker(8, 1500)["meter_kind"] == "sheets_total"
    assert parse_printer_marker(9, 1500) is None
    assert parse_printer_marker(None, 1500) is None
