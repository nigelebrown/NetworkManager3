"""Pure-function tests: predictive model, rule catalogue validation, assistant fallback routing."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.ai import fallback
from backend.domain import predictive as PR
from backend.domain import rule_catalog as RC

T = datetime(2026, 10, 1, tzinfo=timezone.utc)


def series(days, fn):
    return [(T + timedelta(hours=h), fn(T + timedelta(hours=h))) for h in range(days * 24)]


def test_forecast_needs_history_and_never_uses_future_data():
    pts = series(3, lambda t: 50.0)
    assert PR.forecast(pts, cutoff=T + timedelta(days=3), threshold=90)["state"] == "INSUFFICIENT_DATA"
    pts = series(14, lambda t: 95.0 if t.hour == 14 else 30.0)
    cut = T + timedelta(days=10)
    a = PR.forecast(pts, cutoff=cut, threshold=90)
    poisoned = [(t, 0.0 if t >= cut else v) for t, v in pts]          # changing the future changes nothing
    assert PR.forecast(poisoned, cutoff=cut, threshold=90) == a
    assert a["state"] == "AT_RISK" and a["probability"] > 0.9 and a["predicted_peak_at"].endswith("14:00:00+00:00")


def test_forecast_low_risk_and_bounded_trend():
    pts = series(14, lambda t: 40.0 + (t.hour % 2))
    f = PR.forecast(pts, cutoff=T + timedelta(days=14), threshold=90)
    assert f["state"] == "NO_RISK_EXPECTED" and f["probability"] < 0.01
    ramp = series(14, lambda t: 20 + (t - T).total_seconds() / 3600 * 0.5)       # strong upward ramp
    g = PR.forecast(ramp, cutoff=T + timedelta(days=14), threshold=1000)
    assert abs(g["features"]["trend_per_hour"]) <= g["features"]["sigma"] / 4 + 1e-9


def test_backtest_and_evaluation_states():
    good = series(21, lambda t: 96.0 if 13 <= t.hour <= 15 else 35.0)
    calm = series(21, lambda t: 35.0)
    wins = PR.backtest(good, threshold=90) + PR.backtest(calm, threshold=90) + PR.backtest(good, threshold=90)
    ev = PR.evaluate(wins)
    assert ev["windows"] >= 30 and ev["state"] == "VALIDATED" and ev["brier_skill"] > 0.9
    assert ev["precision"] == 1.0 and ev["recall"] == 1.0 and ev["false_alert_rate"] == 0.0
    assert PR.evaluate(wins[:4])["state"] == "INSUFFICIENT_HISTORY"
    constant = PR.evaluate([{"p": 0.1, "breach": False}] * 40)
    assert constant["state"] == "EXPERIMENTAL" and "never varied" in constant["reason"]
    wrong = PR.evaluate([{"p": 0.95, "breach": False}] * 20 + [{"p": 0.05, "breach": True}] * 20)
    assert wrong["state"] == "EXPERIMENTAL" and wrong["brier_skill"] < 0


def test_drift_detection():
    pts = series(17, lambda t: 30.0 + (t.hour % 3)) + [(T + timedelta(days=17, hours=h), 80.0) for h in range(72)]
    assert PR.drift(pts, now=T + timedelta(days=20))["state"] == "DRIFT"
    assert PR.drift(series(20, lambda t: 30.0 + (t.hour % 3)), now=T + timedelta(days=20))["state"] == "STABLE"
    assert PR.drift(series(2, lambda t: 1.0), now=T + timedelta(days=2))["state"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize("params,ok", [({"threshold": 75}, True), ({"threshold": 5}, False), ({"threshold": "x"}, False),
                                       ({"window_minutes": 2.5}, False), ({"nope": 1}, False)])
def test_rule_parameter_validation(params, ok):
    if ok:
        assert RC.validate_params("PHASE4_CPU_PERCENT_SUSTAINED", params) == {"threshold": 75.0}
    else:
        with pytest.raises(ValueError):
            RC.validate_params("PHASE4_CPU_PERCENT_SUSTAINED", params)


def test_every_builtin_rule_is_complete_and_defaults_validate():
    for r in RC.BUILTIN:
        assert r["inputs"] and r["condition"] and r["output"] and r["risk"] in ("low", "medium", "high")
        assert RC.validate_params(r["rule_id"], r["params"]) == {k: (int(v) if RC.SCHEMA[r["rule_id"]][k][0] == "int" else float(v))
                                                              for k, v in r["params"].items()}
    assert len({r["precedence"] for r in RC.BUILTIN if r["engine"] == "DEVICE_INSIGHTS"}) == \
        len([r for r in RC.BUILTIN if r["engine"] == "DEVICE_INSIGHTS"])


@pytest.mark.parametrize("q,tool", [("Which APs are offline?", "ap_overview"), ("who has the worst client experience", "worst_clients"),
                                    ("disconnects in the last day", "disconnects"), ("show roaming", "roaming"),
                                    ("any open alerts?", "active_alerts"), ("clients per SSID", "ssid_breakdown"),
                                    ("is experience getting worse over time", "experience_trend"),
                                    ("AP spacing and co-channel overlap", "rf_placement"), ("what is 2+2", None)])
def test_fallback_routing(q, tool):
    assert fallback.route(q) == tool


def test_fallback_flags_write_intent_but_only_reads():
    text = fallback.answer("ap_overview", {"aps": [{"ap": "a1", "status": "down", "clients": 0, "score": None}]},
                           "reboot ap a1 and set 80 MHz")
    assert text.startswith("Note: this assistant is read-only") and "1 not up" in text
