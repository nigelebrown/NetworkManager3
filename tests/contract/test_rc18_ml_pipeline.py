"""rc18 (2.1) trained breach-risk model lifecycle: feature extraction, labelled datasets (live telemetry and
clearly synthetic), temporal split with embargo, selection on validation, holdout metrics vs climatology and the
statistical engine, versioned registry, independent promotion, PRIMARY/SHADOW inference, scoring, performance,
drift (PSI), automatic fallback, rollback, scheduled retraining and administrative controls."""
from datetime import timedelta

import pytest

from backend.database.models_phases import MlModel
from backend.database.models_rc18 import MlInference
from backend.database.session import session_scope
from backend.infrastructure import clock
from backend.ml import breach_model as B
from backend.ml import service as MLS
from tests.conftest import login, make_user
from tests.contract.test_network_intelligence import NOW, device, write

T = "PRED_CPU_BREACH"


@pytest.fixture
def intel(app_env, tmp_path):
    """Live-mode installation (REAL telemetry is the LIVE dataset) with the SQLite telemetry store."""
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c, login(c)


# ------------------------------------------------------------------------------------------ pure parts
def test_features_use_only_the_past_and_labels_need_an_observed_window():
    pts = B.synthetic_series(7, days=20, end=NOW)
    h = B.hourly(pts)
    cut = NOW - timedelta(days=5)
    x = B.extract(h, cut, 90, 24)
    h2 = dict(h)
    for t in list(h2):
        if t >= cut:
            h2[t] = 100.0                                   # rewrite the future
    assert B.extract(h2, cut, 90, 24) == x                   # features unchanged: no look-ahead
    assert B.label(h2, cut, 90, 24) is True
    gap = {t: v for t, v in h.items() if not (cut <= t < cut + timedelta(hours=24))}
    assert B.label(gap, cut, 90, 24) is None                 # unobserved window is never "no breach"
    assert B.extract({}, cut, 90, 24) is None


def test_logistic_regression_learns_separable_data_and_psi_detects_shift():
    xs = [[i / 100, (i % 7) / 7] + [0.0] * (len(B.FEATURES) - 2) for i in range(200)]
    ys = [i >= 120 for i in range(200)]
    params = B.fit(xs, ys, l2=0.001)
    ps = [B.predict(params, x) for x in xs]
    m = B.metrics(ps, ys)
    assert m["auc"] >= 0.99 and m["accuracy"] >= 0.97 and m["brier_skill"] > 0.8
    prof = B.training_profile(xs)
    assert B.drift(prof, xs)["state"] == "STABLE"
    shifted = [[x[0] + 3] + x[1:] for x in xs]
    d = B.drift(prof, shifted)
    assert d["state"] == "DRIFT" and d["features"]["last_ratio"] >= 0.25
    c = B.confidence(params, shifted[0], 0.9)
    assert "last_ratio" in c["out_of_range_features"] and c["level"] != "HIGH"


# ------------------------------------------------------------------------------------------ API lifecycle
def test_synthetic_model_trains_but_is_never_production_validated_or_promotable_live(intel):
    client, admin = intel
    r = client.post("/api/v1/ai/ml/train", headers=admin, json={"target": T, "synthetic": True}).json()
    assert r["run"]["status"] == "SUCCEEDED" and r["run"]["dataset_source"] == "SYNTHETIC"
    m = r["model"]
    assert m["status"] == "CANDIDATE" and m["production_validated"] is False and "SYNTHETIC" in m["validation_note"]
    assert m["target"] == T and m["version"] == 1 and m["algorithm"] == B.ALGORITHM
    met = m["metrics"]
    assert set(m["split"]) == {"train", "validation", "holdout"} and min(m["split"].values()) >= 20
    for k in ("auc", "brier", "brier_skill", "precision", "recall", "f1", "log_loss", "calibration"):
        assert k in met["holdout"]
    assert met["statistical_holdout_brier"] is not None and len(met["selection"]) == 4
    approver = make_user(client, admin, "ml.approver", "approver")
    assert client.post(f"/api/v1/ai/models/{m['id']}/promote", headers=admin).status_code == 403   # trainer
    res = client.post(f"/api/v1/ai/models/{m['id']}/promote", headers=approver)
    assert res.status_code == 409 and "SYNTHETIC" in res.text
    assert client.post("/api/v1/ai/ml/train", headers=approver, json={"target": T}).status_code == 403
    assert client.post("/api/v1/ai/ml/train", headers=admin, json={"target": "NOPE"}).status_code == 422


def test_demo_installation_may_shadow_a_synthetic_model_but_it_never_drives_predictions(client, admin):
    r = client.post("/api/v1/ai/ml/train", headers=admin, json={"target": T, "synthetic": True}).json()
    approver = make_user(client, admin, "ml.demo.approver", "approver")
    assert client.post(f"/api/v1/ai/models/{r['model']['id']}/promote", headers=approver).json()["status"] == "PROMOTED"
    st = {t["target"]: t for t in client.get("/api/v1/ai/ml/status", headers=admin).json()["targets"]}
    assert st[T]["mode"] == "SHADOW" and "not production-validated" in st[T]["reason"]


def test_live_training_without_enough_labelled_windows_creates_no_model(intel):
    client, admin = intel
    d = device("ml-thin")
    write(d, "cpu_percent", [(NOW - timedelta(hours=h), 40.0) for h in range(1, 72)])
    r = client.post("/api/v1/ai/ml/train", headers=admin, json={"target": T}).json()
    assert r["run"]["status"] == "INSUFFICIENT_DATA" and r["model"] is None and "needs" in r["run"]["summary"]["reason"]


def _live_fleet(n=10, days=45, seed0=200):
    ids = []
    for i in range(n):
        d = device(f"ml-ap-{i:02d}")
        write(d, "cpu_percent", B.synthetic_series(seed0 + i, days=days, end=NOW))  # test fixture data only
        ids.append(d)
    return ids


def test_live_model_promotion_inference_scoring_drift_fallback_and_rollback(intel, monkeypatch):
    client, admin = intel
    approver = make_user(client, admin, "ml.approver2", "approver")
    ids = _live_fleet()
    r = client.post("/api/v1/ai/ml/train", headers=admin, json={"target": T}).json()
    assert r["run"]["status"] == "SUCCEEDED", r["run"]
    m = r["model"]
    assert m["dataset_source"] == "LIVE"
    expect, _ = MLS.validation_status("LIVE", m["metrics"]["holdout"],
                                      None if m["metrics"]["ml_on_statistical_windows_brier"] is None else
                                      m["metrics"]["ml_on_statistical_windows_brier"] <= m["metrics"]["statistical_holdout_brier"])
    assert m["production_validated"] is expect                  # the label follows the published criteria
    assert client.post(f"/api/v1/ai/models/{m['id']}/promote", headers=approver).json()["status"] == "PROMOTED"
    st = {t["target"]: t for t in client.get("/api/v1/ai/ml/status", headers=admin).json()["targets"]}
    assert st[T]["mode"] == ("PRIMARY" if expect else "SHADOW")
    # force the PRIMARY path deterministically to exercise routing (criteria are tested above)
    with session_scope() as db:
        db.get(MlModel, m["id"]).production_validated = True
    with clock.frozen(NOW + timedelta(minutes=5)):
        client.post("/api/v1/intelligence/predictive/run", headers=admin)
    preds = client.get("/api/v1/intelligence/predictive", headers=admin).json()
    rows = preds["items"] if isinstance(preds, dict) and "items" in preds else preds.get("predictions", [])
    with session_scope() as db:
        infs = list(db.query(MlInference).filter(MlInference.model_id == m["id"]))
        assert len(infs) == len(ids) and all(i.used_for_prediction for i in infs)
        from backend.database.models_intelligence import Prediction
        p = db.query(Prediction).filter(Prediction.metric == "cpu_percent").first()
        assert p.features["ml"]["mode"] == "PRIMARY" and p.features["ml"]["model"] == m["name"]
        assert p.probability == p.features["ml"]["probability"]
    # outcomes arrive -> scoring -> performance
    for d in ids:
        write(d, "cpu_percent", [(NOW + timedelta(hours=h), 95.0 if h == 3 else 40.0) for h in range(1, 26)])
    with clock.frozen(NOW + timedelta(hours=25)):
        with session_scope() as db:
            assert MLS.score(db)["scored"] == len(ids)
    perf = client.get(f"/api/v1/ai/ml/models/{m['id']}/performance", headers=admin).json()
    assert perf["scored"] == len(ids) and perf["state"] == "ACCUMULATING"
    # drift: recent behaviour very different from training -> DRIFT -> automatic statistical fallback
    for d in ids:
        write(d, "cpu_percent", [(NOW + timedelta(hours=h), 99.0) for h in range(26, 26 + 24 * 8)])
    with clock.frozen(NOW + timedelta(days=9)):
        dr = client.post(f"/api/v1/ai/ml/models/{m['id']}/drift", headers=admin).json()
    assert dr.get("state") == "DRIFT" and dr["health"] == "DRIFT", dr
    st = {t["target"]: t for t in client.get("/api/v1/ai/ml/status", headers=admin).json()["targets"]}
    assert st[T]["mode"] == "STATISTICAL_FALLBACK" and "DRIFT" in st[T]["reason"]
    assert client.get(f"/api/v1/ai/ml/models/{m['id']}/drift", headers=admin).json()[0]["state"] == "DRIFT"
    # administrator rollback -> rules + statistical only
    rb = client.post(f"/api/v1/ai/models/{m['id']}/rollback", headers=approver).json()
    assert rb["mode"] == "RULES_ONLY"
    st = {t["target"]: t for t in client.get("/api/v1/ai/ml/status", headers=admin).json()["targets"]}
    assert st[T]["mode"] == "STATISTICAL_FALLBACK" and st[T]["model"] is None
    acts = {e["action"] for e in client.get("/api/audit?limit=300", headers=admin).json()}
    assert {"ml_training_run", "ai_model_promoted", "ml_drift_checked", "ai_model_rolled_back"} <= acts


def test_degraded_live_performance_falls_back_and_admin_can_disable_inference(intel):
    client, admin = intel
    with session_scope() as db:
        m = MlModel(name="pred_cpu_breach-v9", kind="TRAINED_ML", algorithm=B.ALGORITHM, status="PROMOTED",
                    dataset_sha256="x" * 64, split={}, metrics={"holdout": {"base_rate": 0.2}},
                    parameters={"threshold": 90.0, "horizon_hours": 24, "metric": "cpu_percent"}, trained_by="a",
                    target=T, version=9, dataset_source="LIVE", production_validated=True, health="OK",
                    decided_at=NOW)
        db.add(m); db.flush()
        for i in range(60):                                    # confidently wrong on every scored window
            db.add(MlInference(model_id=m.id, target=T, device_id="d", metric="cpu_percent", threshold=90,
                               issued_at=NOW, window_end=NOW, probability=0.95, confidence="HIGH",
                               used_for_prediction=True, outcome="NO_BREACH"))
        db.flush()
        MLS._set_health(db, m)
        assert m.health == "DEGRADED" and MLS.mode_of(db, m)[0] == "STATISTICAL_FALLBACK"
        mid = m.id
    perf = client.get(f"/api/v1/ai/ml/models/{mid}/performance", headers=admin).json()
    assert perf["state"] == "DEGRADED", perf
    viewer = make_user(client, admin, "ml.viewer", "viewer")
    assert client.put("/api/v1/ai/ml/settings", headers=viewer, json={"inference_enabled": False}).status_code == 403
    out = client.put("/api/v1/ai/ml/settings", headers=admin, json={"inference_enabled": False}).json()
    assert out["inference_enabled"] is False
    with session_scope() as db:
        m = db.get(MlModel, mid)
        m.health = "OK"
        assert MLS.mode_of(db, m) == ("STATISTICAL_FALLBACK", "ML inference disabled by an administrator.")


def test_scheduled_retraining_creates_candidates_only(intel, monkeypatch):
    client, admin = intel
    _live_fleet(n=8, seed0=400)
    monkeypatch.setenv("INM_SCHEDULED_ML", "true")
    from backend.infrastructure import scheduled_jobs as J
    with clock.frozen(NOW + timedelta(minutes=1)):
        out = J.ml_retrain()
    assert {r["target"] for r in out["runs"]} >= {T}
    with session_scope() as db:
        assert not db.query(MlModel).filter(MlModel.status == "PROMOTED").count()   # never auto-promoted
        assert db.query(MlModel).filter(MlModel.target == T, MlModel.status == "CANDIDATE").count() == 1
    runs = client.get(f"/api/v1/ai/ml/runs?target={T}", headers=admin).json()
    assert runs[0]["trigger"] == "SCHEDULED" and runs[0]["requested_by"] == "ml-scheduler"
    with clock.frozen(NOW + timedelta(hours=2)):
        assert not [r for r in J.ml_retrain()["runs"] if r["target"] == T]       # not due again yet


def test_model_versions_are_compared_on_the_same_observations(intel):
    client, admin = intel
    m1 = client.post("/api/v1/ai/ml/train", headers=admin, json={"target": T, "synthetic": True}).json()["model"]
    with session_scope() as db:                    # a deliberately weak v2: no learned weights (base-rate only)
        src = db.get(MlModel, m1["id"])
        weak = dict(src.parameters, weights=[0.0] * len(src.parameters["weights"]))
        db.add(MlModel(name=f"{T.lower()}-v2", kind="TRAINED_ML", algorithm=B.ALGORITHM, status="CANDIDATE",
                       dataset_sha256=src.dataset_sha256, split=src.split, metrics=src.metrics, parameters=weak,
                       trained_by="other", target=T, version=2, dataset_source="SYNTHETIC",
                       production_validated=False, training_profile=src.training_profile, health="NEW"))
    cmp = client.get(f"/api/v1/ai/ml/compare?target={T}", headers=admin).json()
    assert cmp["windows"] >= 20 and cmp["dataset_source"] == "SYNTHETIC"
    v = {x["version"]: x for x in cmp["versions"]}
    assert v[1]["comparable"] and v[2]["comparable"]
    assert v[1]["brier"] < v[2]["brier"] and cmp["best_version"] == m1["name"]
    assert cmp["statistical_brier"] is not None and cmp["climatology_brier"] is not None
    assert client.get("/api/v1/ai/ml/compare?target=NOPE", headers=admin).status_code == 422
