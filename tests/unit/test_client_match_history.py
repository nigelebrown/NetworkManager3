from datetime import datetime, timedelta, timezone
import pytest
from backend.infrastructure import client_match_history as cm

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
MAC = "aa:bb:cc:dd:ee:ff"

def table(*rows, count=None):
    return "Client Match Action Table\n" + "\n".join(rows) + "\nTotal " + str(len(rows) if count is None else count) + " Records\n"

def row(age="10s"):
    return f"{MAC} Idle Active Band steer 1 {age}"

def test_parse_pseudonym_and_estimated_time(monkeypatch):
    monkeypatch.setattr(cm, "get_settings", lambda: type("Settings", (), {"jwt_secret": "test-key"})())
    result = cm.parse(table(row()), site_id=2, collected=NOW)
    assert result["state"] == "COLLECTED"
    record = result["records"][0]
    assert MAC not in str(result)
    assert record["estimated_at"] == (NOW - timedelta(seconds=10)).isoformat()
    assert record["client_token"] != cm.token(MAC, 3)

def test_invalid_and_empty_tables(monkeypatch):
    monkeypatch.setattr(cm, "get_settings", lambda: type("Settings", (), {"jwt_secret": "test-key"})())
    assert cm.parse(table(), site_id=2, collected=NOW)["records"] == []
    assert cm.parse(table(row("999d:0s")), site_id=2, collected=NOW)["state"] == "PARTIAL"
    with pytest.raises(ValueError):
        cm.parse("permission denied", site_id=2, collected=NOW)
    with pytest.raises(ValueError):
        cm.parse(table("bad row"), site_id=2, collected=NOW)

def test_dedupe_does_not_collapse_same_capture(monkeypatch):
    monkeypatch.setattr(cm, "get_settings", lambda: type("Settings", (), {"jwt_secret": "test-key"})())
    first = cm.parse(table(row(), row()), site_id=2, collected=NOW)["records"]
    assert len(cm.merge([], first, NOW)) == 2
    later = NOW + timedelta(seconds=10)
    second = cm.parse(table(row("20s"), row("20s")), site_id=2, collected=later)["records"]
    assert len(cm.merge(first, second, later)) == 2
    assert cm.merge(first, [], NOW + timedelta(days=8)) == []


def test_absolute_device_time_is_preserved_without_inventing_utc(monkeypatch):
    monkeypatch.setattr(cm,"get_settings",lambda:type("Settings",(),{"jwt_secret":"test-key"})())
    native=table(f"{MAC} Normal Normal Client associated 0 2026-11-25 09:18:05")
    parsed=cm.parse(native,site_id=2,collected=NOW)
    assert parsed["state"]=="COLLECTED" and parsed["clock_alignment"]=="DEVICE_LOCAL_UNVERIFIED"
    record=parsed["records"][0]
    assert record["estimated_at"] is None and record["age_seconds_at_capture"] is None
    assert record["device_local_time"]=="2026-11-25 09:18:05" and MAC not in str(parsed)
    assert len(cm.merge(parsed["records"],cm.parse(native,site_id=2,collected=NOW+timedelta(seconds=20))["records"],NOW+timedelta(seconds=20)))==1
    assert cm.merge(parsed["records"],[],NOW+timedelta(days=8))==[]

def test_invalid_device_calendar_timestamp_is_not_accepted(monkeypatch):
    monkeypatch.setattr(cm,"get_settings",lambda:type("Settings",(),{"jwt_secret":"test-key"})())
    parsed=cm.parse(table(f"{MAC} Normal Normal Client associated 0 2026-19-55 09:18:05"),site_id=2,collected=NOW)
    assert parsed["state"]=="PARTIAL" and not parsed["records"]
