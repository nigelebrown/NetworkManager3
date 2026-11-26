"""rc18 (register INTEG-P3-01): recompute client / AP / site experience scores on a frozen historical fixture and
compare every result with an independent implementation of the documented model (docs/SCORING.md). The fixture
holds inputs only (no stored scores); missing inputs, short observation and coverage rules are exercised."""
import json
from pathlib import Path

from backend.experience.config import ExperienceConfig
from backend.experience.scoring import ApMetrics, ClientMetrics, score_ap, score_client, summarize

FIX = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "experience_frozen_history.json").read_text())


def _line(v, worst, best):                           # SCORING.md: straight line worst -> best, clamped 0..100
    return 100.0 * max(0.0, min(1.0, (v - worst) / (best - worst)))


def _documented_client(r):
    parts = []                                       # (subscore, weight) of the inputs that could be measured
    if r["snr_db"] is not None:
        parts.append((_line(r["snr_db"], 10, 35), 0.45))
    elif r["rssi_dbm"] is not None:
        parts.append((_line(r["rssi_dbm"], -85, -60), 0.45))
    if r["speed_mbps"] is not None:
        parts.append((_line(r["speed_mbps"], 12, 150), 0.20))
    if r["disconnects"] is not None and r["observed_minutes"] is not None and r["observed_minutes"] >= 30:
        parts.append((_line(r["disconnects"], 6, 0), 0.25))
    # retries (0.10) is never collected -> always missing
    measured = sum(w for _, w in parts)
    coverage = measured / 1.0
    if not parts or coverage < 0.30:
        return None, round(coverage, 2)
    return int(round(sum(s * w for s, w in parts) / measured)), round(coverage, 2)


def test_defaults_match_the_documented_model():
    cfg = ExperienceConfig()
    w = cfg.client.weights
    assert (w.signal, w.rate, w.stability, w.retries) == (0.45, 0.20, 0.25, 0.10)
    assert (cfg.client.snr.worst, cfg.client.snr.best) == (10, 35)
    assert (cfg.client.rssi.worst, cfg.client.rssi.best) == (-85, -60)
    assert (cfg.client.rate_mbps.worst, cfg.client.rate_mbps.best) == (12, 150)
    assert cfg.client.stability.worst_disconnects == 6 and cfg.client.stability.min_observation_minutes == 30
    assert cfg.min_coverage == 0.30 and (cfg.bands.good_min, cfg.bands.fair_min) == (75, 50)


def test_frozen_history_recomputes_to_the_documented_scores():
    cfg = ExperienceConfig()
    by_ap: dict[str, list] = {}
    unscored = 0
    for r in FIX["rows"]:
        got = score_client(ClientMetrics(snr_db=r["snr_db"], rssi_dbm=r["rssi_dbm"], speed_mbps=r["speed_mbps"],
                                         disconnects=r["disconnects"], observed_minutes=r["observed_minutes"]), cfg)
        want, cov = _documented_client(r)
        assert (got["score"], got["coverage"]) == (want, cov), r
        assert "retries" in got["unavailable"]                               # never estimated
        if r["observed_minutes"] < 30 and r["disconnects"] is not None:
            assert "stability" in got["unavailable"]                         # no free 100 for new clients
        unscored += want is None
        by_ap.setdefault(r["ap"], []).append(got["score"])
    assert 0 < unscored < len(FIX["rows"])                                   # the fixture exercises both paths
    site = summarize([s for v in by_ap.values() for s in v], cfg)
    scored = [s for v in by_ap.values() for s in v if s is not None]
    assert site["score"] == round(sum(scored) / len(scored), 1) and site["unscored"] == unscored
    assert site["good"] + site["fair"] + site["poor"] == len(scored)
    for ap, scores in by_ap.items():                                         # AP: mean of scored clients + load
        got = score_ap(ApMetrics(client_scores=[s for s in scores if s is not None], client_count=len(scores)), cfg)
        mean = sum(s for s in scores if s is not None) / len([s for s in scores if s is not None])
        parts = [(mean, 0.50), (_line(len(scores), 60, 20), 0.15)]
        assert got["score"] == int(round(sum(s * w for s, w in parts) / 0.65)) and got["coverage"] == 0.65
    assert score_ap(ApMetrics(status="down"), cfg)["band"] == "offline"
    # recomputation is deterministic: same frozen inputs, same outputs
    again = [score_client(ClientMetrics(**{k: r[k] for k in ("snr_db", "rssi_dbm", "speed_mbps", "disconnects",
                                                             "observed_minutes")}), cfg)["score"] for r in FIX["rows"]]
    assert again == [s for r in FIX["rows"] for s in [_documented_client(r)[0]]]
