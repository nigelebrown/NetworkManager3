"""Printer Intelligence contract: real database migrations and REST flows, no real devices."""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import text

from tests.conftest import make_user
from backend.database.session import get_engine


def register(client, headers, model="M428", serial="HP-JCF-001", manufacturer="HP"):
    return client.post("/api/printers", headers=headers, json={
        "manufacturer": manufacturer, "model": model, "serial_number": serial,
        "hostname": "HQ-PRN-01", "management_ip": "10.103.44.21",
        "area": "Area 1", "division": "Kingston", "station": "Head Office",
        "section_unit": "General Office", "building": "HQ Building",
    })


def test_expand_only_printer_schema_exists(client, admin):
    from sqlalchemy import inspect
    tables = set(inspect(get_engine()).get_table_names())
    assert {"printer_devices", "printer_meter_readings", "printer_supply_readings",
            "printer_consumable_cycles", "printer_maintenance_events"}.issubset(tables)
    assert client.get("/api/printers", headers=admin).json() == []


def test_register_and_update_without_duplicate_or_device_write(client, admin):
    response = register(client, admin)
    assert response.status_code == 201, response.text
    device = response.json()
    assert device["provenance"] == "MANUAL"
    assert device["section_unit"] == "General Office"
    assert device["management_ip"] == "10.103.44.21"
    again = register(client, admin)
    assert again.status_code == 201
    assert again.json()["id"] == device["id"]
    collision = register(client, admin, model="Another incompatible model")
    assert collision.status_code == 409
    assert len(client.get("/api/printers", headers=admin).json()) == 1


def test_meter_samples_report_counter_reset_instead_of_negative_pages(client, admin):
    did = register(client, admin).json()["id"]
    for counter in (99800, 100500, 100):  # simulated service meter reset
        r = client.post(f"/api/printers/{did}/meters", headers=admin,
                        json={"meter_kind": "impressions_total", "counter_value": counter})
        assert r.status_code == 201, r.text
    detail = client.get(f"/api/printers/{did}", headers=admin).json()
    assert detail["meter_deltas"]["impressions_total"]["quality"] == "RESET_OR_ROLLOVER"
    assert detail["meter_deltas"]["impressions_total"]["delta"] is None
    assert len(detail["meters"]) == 3


def test_sheets_and_impressions_separate_and_large_counters_supported(client, admin):
    did = register(client, admin).json()["id"]
    for kind, n in (("sheets_total", 33000), ("impressions_total", 4_000_000_000)):
        r = client.post(f"/api/printers/{did}/meters", headers=admin,
                        json={"meter_kind": kind, "counter_value": n})
        assert r.status_code == 201, r.text
    rows = client.get(f"/api/printers/{did}", headers=admin).json()["meters"]
    assert {r["meter_kind"]: r["counter_value"] for r in rows} == {
        "sheets_total": 33000, "impressions_total": 4_000_000_000}


def test_low_toner_alerts_only_for_known_measurements(client, admin):
    did = register(client, admin).json()["id"]
    for colour, percent in (("black", 12), ("cyan", None), ("yellow", 3)):
        r = client.post(f"/api/printers/{did}/supplies", headers=admin,
                        json={"supply_name": "Toner", "colour": colour, "percent_remaining": percent})
        assert r.status_code == 201, r.text
    stats = client.get("/api/printers/dashboard", headers=admin).json()
    assert stats["total"] == 1
    assert len(stats["low_supplies"]) == 2
    assert {x["severity"] for x in stats["low_supplies"]} == {"LOW", "CRITICAL"}


def test_consumable_cycle_actual_cost_is_based_on_meter_and_flagged_as_proxy(client, admin):
    did = register(client, admin).json()["id"]
    opened = client.post(f"/api/printers/{did}/cycles", headers=admin, json={
        "consumable_kind": "toner", "colour": "black", "start_meter": 1000,
        "cost_amount": 9000, "currency": "JMD", "declared_yield": 2000,
        "part_number": "HP-JCF-TONER",
    })
    assert opened.status_code == 201, opened.text
    assert opened.json()["analysis"]["cost_per_1000_impressions"] is None
    duplicate = client.post(f"/api/printers/{did}/cycles", headers=admin, json={
        "consumable_kind": "toner", "colour": "black", "start_meter": 1000})
    assert duplicate.status_code == 409
    cid = opened.json()["id"]
    closed = client.post(f"/api/printers/{did}/cycles/{cid}/close", headers=admin,
                         json={"end_meter": 2800})
    assert closed.status_code == 200, closed.text
    report = closed.json()["analysis"]
    assert report["counter_delta"] == 1800
    assert report["cost_per_1000_impressions"] == 5000
    assert report["declared_yield_ratio_percent"] == 90.0
    assert report["quality"] == "CALCULATED_PROXY"
    assert client.post(f"/api/printers/{did}/cycles/{cid}/close", headers=admin,
                       json={"end_meter": 4000}).status_code == 409


def test_counter_reset_on_closed_cycle_does_not_create_negative_cost(client, admin):
    did = register(client, admin).json()["id"]
    a = client.post(f"/api/printers/{did}/cycles", headers=admin,
                    json={"consumable_kind": "ink_tank", "colour": "magenta", "start_meter": 3500,
                          "quantity_added_ml": 25, "currency": "JMD"})
    assert a.status_code == 201, a.text
    b = client.post(f"/api/printers/{did}/cycles/{a.json()['id']}/close",
                    headers=admin, json={"end_meter": 100})
    assert b.status_code == 200
    assert b.json()["analysis"]["quality"] == "RESET_OR_ROLLOVER"
    assert b.json()["analysis"]["cost_per_1000_impressions"] is None


def test_maintenance_service_history_and_rule_analysis(client, admin):
    did = register(client, admin).json()["id"]
    r = client.post(f"/api/printers/{did}/maintenance", headers=admin, json={
        "component": "pickup roller", "event_type": "service",
        "severity": "warning", "description": "Roller replacement after recurring jams",
        "cost_amount": 2200, "currency": "JMD"})
    assert r.status_code == 201, r.text
    p = client.post(f"/api/printers/{did}/supplies", headers=admin,
                    json={"supply_name": "Black toner", "colour": "black", "percent_remaining": 2})
    assert p.status_code == 201
    result = client.post("/api/printers/analyse-now", headers=admin).json()
    assert result["engine"] == "RULES_V1_NOT_TRAINED_ML"
    assert result["recommendations"][0]["engine"] == "RULE"
    assert result["recommendations"][0]["approval"] == "ADVISORY_ONLY"
    assert len(client.get(f"/api/printers/{did}", headers=admin).json()["maintenance"]) == 1


def test_role_and_site_safety_only_admin_can_view_or_modify_printer_records(client, admin):
    register(client, admin)
    operator = make_user(client, admin, "printer.operator", "operator")
    assert client.get("/api/printers", headers=operator).status_code == 403
    assert register(client, operator, serial="NEW") .status_code == 403
    assert client.get("/api/printers").status_code == 401


def test_snmp_probe_default_off_does_not_expose_credentials(client, admin, monkeypatch):
    monkeypatch.delenv("INM_READONLY_DEVICE_CIDRS", raising=False)
    payload = {"management_ip": "10.103.44.21", "username": "readonly",
               "auth_passphrase": "secret-auth-passphrase",
               "privacy_passphrase": "secret-privacy-passphrase"}
    denied = client.post("/api/printers/probe-snmpv3", headers=admin, json=payload)
    assert denied.status_code == 403
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.44.21/32")
    async def mocked(*_args):
        return {"hostname": "M428", "system_description": "Generic Printer MIB",
                "system_object_id": "1.3.6.1.4.1", "meter": None,
                "source": "SNMPV3", "device_writes": "LOCKED"}
    with patch("backend.api.printers.read_snmpv3", new=mocked):
        good = client.post("/api/printers/probe-snmpv3", headers=admin, json=payload)
    assert good.status_code == 200, good.text
    assert good.json()["source"] == "SNMPV3"
    assert "secret-auth" not in good.text
    assert client.get("/api/printers", headers=admin).json() == []  # probe does not silently onboard


def test_invalid_negative_supply_or_meter_rejected(client, admin):
    did = register(client, admin).json()["id"]
    assert client.post(f"/api/printers/{did}/meters", headers=admin, json={
        "meter_kind": "impressions_total", "counter_value": -5}).status_code == 422
    assert client.post(f"/api/printers/{did}/supplies", headers=admin, json={
        "supply_name": "Toner", "colour": "black", "percent_remaining": 130}).status_code == 422


def test_range_discovery_filters_devices_and_preserves_manual_registration(client, admin, monkeypatch):
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.103.44.0/24")
    async def read(host, *_credentials):
        return {"hostname": "Test printer", "printer_mib": host.endswith(".21"), "meter": None}

    credentials = {"username": "reader", "auth_passphrase": "test-auth", "privacy_passphrase": "test-private"}
    with patch("backend.api.printers.read_snmpv3", side_effect=read) as probe:
        for scope, status in [("10.103.44.0/23", 422), ("10.103.45.0/24", 403)]:
            denied = client.post("/api/printers/discover-snmpv3", headers=admin,
                                 json={**credentials, "management_range": scope})
            assert denied.status_code == status, denied.text
        probe.assert_not_called()
        result = client.post("/api/printers/discover-snmpv3", headers=admin,
                             json={**credentials, "management_range": "10.103.44.20/30"})
        assert result.status_code == 200, result.text
        assert result.json()["scanned"] == 2
        assert [p["management_ip"] for p in result.json()["printers"]] == ["10.103.44.21"]
        assert probe.await_count == 2

    assert client.get("/api/printers", headers=admin).json() == []
    printer = register(client, admin).json()
    client.post(f"/api/printers/{printer['id']}/supplies", headers=admin,
                json={"supply_name": "Toner", "colour": "black", "percent_remaining": 3})
    dashboard = client.get("/api/printers/dashboard", headers=admin).json()
    assert dashboard["low_supplies"][0]["printer_id"] == printer["id"]
