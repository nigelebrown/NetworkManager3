"""Alert hysteresis (1.3.0-rc16): a value hovering at a threshold must not open/resolve on every poll,
missing data never clears an alert, and per-site clear overrides are honoured."""
from datetime import datetime, timedelta, timezone

from backend.domain import alert_rules as R
from backend.domain.alert_rules import port_rates

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def rule(rid):
    return R.rule_by_id(rid)


def test_clear_levels_by_metric_kind():
    assert R.clear_threshold(rule("SW_UPLINK_UTIL_HIGH"), 85) == 80                # percent: 5-point band
    assert R.clear_threshold(rule("AP_NOISE_HIGH"), -82) == -85                     # dBm: 3 dB band (negative)
    assert R.clear_threshold(rule("PORT_ERRORS"), 10) == 5                          # delta counters: half
    assert R.clear_threshold(rule("SW_FAN_FAILED"), 1) == 1                         # discrete: no band
    assert R.clear_threshold(rule("CPU_HIGH"), 90, {"clear_threshold": 70}) == 70   # site override
    assert R.clear_threshold(rule("CPU_HIGH"), 90, {"clear_threshold": 95}) == 90   # never above the trip level
    assert not R.cleared(rule("SW_UPLINK_UTIL_HIGH"), 82, 85) and R.cleared(rule("SW_UPLINK_UTIL_HIGH"), 79.9, 85)
    assert not R.cleared(rule("AP_NOISE_HIGH"), -84, -82) and R.cleared(rule("AP_NOISE_HIGH"), -86, -82)
    assert R.cleared(rule("SW_FAN_FAILED"), 0, 1) and not R.cleared(rule("SW_FAN_FAILED"), 1, 1)


def _device(db):
    from backend.database.models_inm import InmDevice
    d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch", serial_number="HYS-1",
                  hostname="hys-sw", adapter="aruba_cx")
    db.add(d)
    db.flush()
    return d


def test_flapping_value_keeps_one_alert_and_missing_data_never_resolves(client):
    from sqlalchemy import select
    from backend.database.models_phases import InmAlert
    from backend.database.session import session_scope
    from backend.infrastructure import alert_engine
    with session_scope() as db:
        d = _device(db)
        series = [86, 84, 86, 83, 87, 81]                 # oscillates around 85 % without reaching the 80 % clear level
        for i, v in enumerate(series):
            alert_engine.apply(db, d, {"max_port_util_percent": v}, now=T0 + timedelta(minutes=5 * i))
        rows = list(db.scalars(select(InmAlert).where(InmAlert.rule_id == "SW_UPLINK_UTIL_HIGH")))
        assert len(rows) == 1 and rows[0].state == "OPEN" and rows[0].value == 81
        alert_engine.apply(db, d, {}, now=T0 + timedelta(minutes=40))           # unmeasured: stays open
        assert rows[0].state == "OPEN"
        out = alert_engine.apply(db, d, {"max_port_util_percent": 40}, now=T0 + timedelta(minutes=45))
        assert out["resolved"] == 1 and rows[0].state == "RESOLVED"


def test_port_alert_hysteresis(client):
    from sqlalchemy import select
    from backend.database.models_phases import InmAlert
    from backend.database.session import session_scope
    from backend.infrastructure import alert_engine
    with session_scope() as db:
        d = _device(db)
        for i, v in enumerate([12, 8, 11, 6]):            # PORT_ERRORS trips at 10, clears below 5
            alert_engine.apply_entities(db, d, {"1/1/1": {"port_errors_delta": v}}, now=T0 + timedelta(minutes=i))
        rows = list(db.scalars(select(InmAlert).where(InmAlert.rule_id == "PORT_ERRORS")))
        assert len(rows) == 1 and rows[0].state == "OPEN"
        alert_engine.apply_entities(db, d, {"1/1/1": {"port_errors_delta": 0}}, now=T0 + timedelta(minutes=9))
        assert rows[0].state == "RESOLVED"


# ---- counter rates: rollover/reset, missing counters, speed change, clock skew --------------------------
def _p(name="1/1/1", **kw):
    return {"name": name, "rx_bytes": 0, "tx_bytes": 0, "rx_errors": 0, "tx_errors": 0, "speed_bps": 1e9, **kw}


def test_port_rates_never_fabricate():
    r = port_rates([_p(rx_bytes=1000)], [_p(rx_bytes=500)], 60)                 # counter cleared / wrapped
    assert r["ports"]["1/1/1"]["rx_bps"] is None and "COUNTER_RESET" in r["ports"]["1/1/1"]["notes"]
    r = port_rates([_p(rx_errors=None)], [_p(rx_errors=900)], 60)               # missing previous counter ≠ 0
    assert r["port_errors_delta"] is None
    r = port_rates([_p(speed_bps=1e8)], [_p(rx_bytes=60 * 1e8 / 8)], 60)        # speed changed 100M -> 1G
    assert r["ports"]["1/1/1"]["util_percent"] is None and "SPEED_CHANGED" in r["ports"]["1/1/1"]["notes"]
    r = port_rates([_p()], [_p(rx_bytes=60 * 2e9 / 8)], 60)                     # >100 % of link speed
    assert r["ports"]["1/1/1"]["util_percent"] is None and "UTILISATION_INCONSISTENT" in r["ports"]["1/1/1"]["notes"]
    for seconds in (0, -30, 2):                                                 # clock skew / duplicate poll
        r = port_rates([_p()], [_p(rx_bytes=10)], seconds)
        assert r["ports"] == {} and r["skipped"] == "INTERVAL_TOO_SHORT_OR_CLOCK_SKEW"
    r = port_rates([_p()], [_p(rx_bytes=60 * 5e8 / 8, rx_errors=4)], 60)       # normal interval
    assert r["ports"]["1/1/1"]["util_percent"] == 50.0 and r["port_errors_delta"] == 4
