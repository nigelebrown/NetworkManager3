"""rc18 deterministic clock for predictive analytics: generation, scoring, outcome windows, boundaries, time zones,
scheduled execution. Run instants are chosen ON BOTH SIDES of an hour boundary, the case that made the RC17 test
intermittent."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.domain import predictive as PR
from backend.infrastructure import clock
from tests.contract.test_network_intelligence import NOW, _daily, device, intel, write  # noqa: F401  (fixture)

BOGOTA = timezone(timedelta(hours=-5))


def test_clock_normalises_time_zones_and_naive_values():
    t = datetime(2026, 11, 25, 7, 30, tzinfo=BOGOTA)
    assert clock.utc(t) == datetime(2026, 11, 25, 12, 30, tzinfo=timezone.utc)
    assert clock.utc(datetime(2026, 11, 25, 12, 30)) == datetime(2026, 11, 25, 12, 30, tzinfo=timezone.utc)
    assert clock.utc("2026-11-25T12:30:00Z").tzinfo == timezone.utc
    assert PR._utc(t) == clock.utc(t) and PR._utc(t).tzinfo == timezone.utc
    with clock.frozen(t):
        assert clock.now() == datetime(2026, 11, 25, 12, 30, tzinfo=timezone.utc)
    assert abs((clock.now() - datetime.now(timezone.utc)).total_seconds()) < 5          # unfrozen again


def test_outcome_window_boundaries_are_half_open():
    s = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    e = s + timedelta(hours=24)
    assert PR.observed_peak([(s, 99.0)], s, e) is None                    # at start: excluded
    assert PR.observed_peak([(e, 99.0)], s, e) == 99.0                    # at end: included
    assert PR.observed_peak([(e + timedelta(seconds=1), 99.0)], s, e) is None
    assert PR.observed_peak([(e.astimezone(BOGOTA), 97.0)], s, e) == 97.0  # same instant in another zone


@pytest.mark.parametrize("offset", [timedelta(minutes=59, seconds=59), timedelta(hours=1, seconds=1),
                                    timedelta(hours=2, minutes=30)])
def test_generation_and_scoring_agree_across_hour_boundaries(intel, offset):
    client, admin = intel
    from backend.api.intelligence import run_predictions, score_predictions
    from backend.database.models_intelligence import Prediction
    from backend.database.session import session_scope
    hot = [device(f"clk-hot-{i}") for i in range(3)]
    calm = [device(f"clk-calm-{i}") for i in range(3)]
    for d in hot:
        write(d, "cpu_percent", _daily(96, 35, 21))
    for d in calm:
        write(d, "cpu_percent", _daily(45, 30, 21))
    new = device("clk-new")
    write(new, "cpu_percent", _daily(95, 30, 2))
    issued = NOW + offset
    with clock.frozen(issued):
        with session_scope() as db:
            run = run_predictions(db, actor="t")
            run_id = run.id
            assert run.evaluation["PRED_CPU_BREACH"]["state"] == "VALIDATED"
    with session_scope() as db:
        preds = db.query(Prediction).filter(Prediction.run_id == run_id, Prediction.metric == "cpu_percent").all()
        assert preds and all(clock.utc(p.window_start) == issued for p in preds)
        assert all(clock.utc(p.window_end) == issued + timedelta(hours=24) for p in preds)
        states = {p.device_id: p.state for p in preds}
        assert states[new] == "INSUFFICIENT_DATA"
        end = issued + timedelta(hours=24)
    for d in hot:
        write(d, "cpu_percent", [(issued + timedelta(hours=h), 96.0 if 13 <= (issued + timedelta(hours=h)).hour <= 15
                                  else 35.0) for h in range(1, 25)])
    for d in calm:
        write(d, "cpu_percent", [(issued + timedelta(hours=h), 33.0) for h in range(1, 25)])
    with clock.frozen(end - timedelta(seconds=1)), session_scope() as db:
        assert score_predictions(db)["scored"] == 0                       # window not finished: nothing scored
    with clock.frozen(end), session_scope() as db:
        sc = score_predictions(db)
    assert sc["scored"] >= 6 and sc["live_evaluation"]["windows"] >= 6
    with session_scope() as db:
        out = {p.device_id: p.outcome for p in db.query(Prediction).filter(Prediction.run_id == run_id,
                                                                            Prediction.metric == "cpu_percent")}
    assert all(out[d] == "BREACH" for d in hot) and all(out[d] == "NO_BREACH" for d in calm)
    assert out[new] is None                                              # no probability -> never scored


def test_scheduled_job_scores_then_issues_on_one_instant(intel, monkeypatch):
    from backend.database.models_intelligence import PredictionRun
    from backend.database.session import session_scope
    from backend.infrastructure import scheduled_jobs as J
    monkeypatch.setenv("INM_SCHEDULED_PREDICTIVE", "true")
    d = device("clk-sched")
    write(d, "cpu_percent", _daily(96, 35, 21))
    at = NOW + timedelta(hours=3, minutes=17)
    with clock.frozen(at):
        out = J.predictive()
    assert out and out["run"]
    with session_scope() as db:
        r = db.get(PredictionRun, out["run"])
        assert clock.utc(r.created_at) == at
