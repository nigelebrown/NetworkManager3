"""Regression coverage for independently reviewed RC18 backports."""
import csv
import hashlib
from pathlib import Path

import pytest

from backend.domain import alert_rules as A
from backend.domain import release_readiness as R


def _port(**values):
    return {"name": "1/1/1", "rx_bytes": 0, "tx_bytes": 0,
            "rx_errors": 0, "tx_errors": 0, "speed_bps": 1e9, **values}


def test_missing_error_counter_is_not_zero():
    result = A.port_rates([_port(rx_errors=None)], [_port(rx_errors=900)], 60)
    assert result["port_errors_delta"] is None
    assert result["link_resets_delta"] is None


def test_counter_reset_speed_change_and_impossible_utilisation():
    result = A.port_rates([_port(rx_bytes=1000)], [_port(rx_bytes=500)], 60)
    assert result["ports"]["1/1/1"]["rx_bps"] is None
    assert "COUNTER_RESET" in result["ports"]["1/1/1"]["notes"]
    result = A.port_rates([_port(speed_bps=1e8)], [_port(rx_bytes=1000)], 60)
    assert "SPEED_CHANGED" in result["ports"]["1/1/1"]["notes"]
    assert result["ports"]["1/1/1"]["util_percent"] is None
    result = A.port_rates([_port()], [_port(rx_bytes=60 * 2e9 / 8)], 60)
    assert "UTILISATION_INCONSISTENT" in result["ports"]["1/1/1"]["notes"]
    assert result["max_port_util_percent"] is None


@pytest.mark.parametrize("seconds", [0, -30, 2, float("nan"), float("inf")])
def test_invalid_poll_intervals(seconds):
    result = A.port_rates([_port()], [_port(rx_bytes=100)], seconds)
    assert result["ports"] == {}
    assert result["port_errors_delta"] is None


def test_normal_rate_and_hysteresis():
    result = A.port_rates([_port()], [_port(rx_bytes=60 * 5e8 / 8, rx_errors=4)], 60)
    assert result["max_port_util_percent"] == 50
    assert result["port_errors_delta"] == 4
    rule = A.rule_by_id("SW_UPLINK_UTIL_HIGH")
    assert not A.cleared(rule, 82, 85)
    assert A.cleared(rule, 79.9, 85)
    assert not A.evaluate("switch", {"max_port_util_percent": float("nan")})
    assert not A.evaluate("switch", {"max_port_util_percent": True})


def _register(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("test_id", "phase"))
        writer.writerows(rows)


def test_rc18_gate_requires_all_new_and_previous_ids(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "VERSION").write_text("1.3.0-rc18\n")
    monkeypatch.setattr(R, "__file__", str(tmp_path / "backend/domain/release_readiness.py"))
    previous = [(f"PRE-{i}", "old") for i in range(281)]
    added = [(f"RC18-T{i:02d}", "RC18") for i in range(1, 23)]
    _register(docs / "Unified_Acceptance_Register_v4.csv", previous)
    with pytest.raises(FileNotFoundError):
        R.required_acceptance_checks()
    target = docs / "Unified_Acceptance_Register_v5.csv"
    _register(target, previous + added)
    assert len(R.required_acceptance_checks()) == 303
    _register(target, previous + added[:-1] + [("UNRELATED", "RC18")])
    with pytest.raises(ValueError, match="omits or changes"):
        R.required_acceptance_checks()
    _register(target, previous[:-1] + added + [("UNRELATED", "old")])
    with pytest.raises(ValueError, match="omits or changes"):
        R.required_acceptance_checks()


def test_evidence_symlink_parent_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    evidence = actual / "evidence.txt"
    evidence.write_text("synthetic test")
    (tmp_path / "linked").symlink_to(actual, target_is_directory=True)
    errors = []
    result = R._check_artifacts(
        {"linked/evidence.txt": hashlib.sha256(evidence.read_bytes()).hexdigest()},
        tmp_path, errors,
    )
    assert result == {}
    assert errors
