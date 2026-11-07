"""Scoring engine, inferred events, alert rules and the experience API (no hardware)."""
import pytest
from sqlalchemy import select

from backend.aruba.monitoring import SiteSnapshot
from backend.database.models import Alert, AppSetting, ClientEvent, ClientRecord, AccessPoint, Site, SiteSample, utcnow
from backend.database.session import session_scope
from backend.experience.config import ExperienceConfig, Range
from backend.experience.scoring import ApMetrics, ClientMetrics, band_for, lerp, score_ap, score_client, summarize
from backend.monitoring.collector import apply_snapshot, poll_site

CFG = ExperienceConfig()


# ---- scoring ----------------------------------------------------------------------------------------------
def test_lerp_both_directions_and_clamping():
    assert lerp(35, Range(worst=10, best=35)) == 100 and lerp(10, Range(worst=10, best=35)) == 0
    assert lerp(22.5, Range(worst=10, best=35)) == 50
    assert lerp(0, Range(worst=6, best=0)) == 100 and lerp(9, Range(worst=6, best=0)) == 0  # lower is better


def test_client_score_uses_only_what_was_measured():
    r = score_client(ClientMetrics(snr_db=35, speed_mbps=150), CFG)
    assert r["score"] == 100 and r["coverage"] == 0.65 and r["partial"] is True
    assert set(r["unavailable"]) == {"stability", "retries"}
    assert abs(sum(i["weight"] for i in r["inputs"].values()) - 1) < 0.01  # weights renormalized
    assert r["inputs"]["signal"]["source"] == "AP-reported"


def test_nothing_measured_means_no_score_not_zero():
    r = score_client(ClientMetrics(), CFG)
    assert r["score"] is None and r["band"] == "unavailable"


def test_too_little_coverage_gives_no_score():
    cfg = ExperienceConfig(min_coverage=0.9)
    assert score_client(ClientMetrics(snr_db=30, speed_mbps=100), cfg)["score"] is None


def test_rssi_used_only_when_snr_missing():
    r = score_client(ClientMetrics(rssi_dbm=-60, speed_mbps=150), CFG)
    assert r["inputs"]["signal"]["value"].endswith("RSSI") and r["inputs"]["signal"]["subscore"] == 100


def test_stability_needs_observation_time_and_counts_disconnects():
    short = score_client(ClientMetrics(snr_db=35, speed_mbps=150, disconnects=0, observed_minutes=5), CFG)
    assert "stability" in short["unavailable"]  # a brand-new client does not get a free 100
    flappy = score_client(ClientMetrics(snr_db=35, speed_mbps=150, disconnects=6, observed_minutes=600), CFG)
    assert flappy["inputs"]["stability"]["subscore"] == 0 and flappy["inputs"]["stability"]["source"] == "inferred from polls"
    assert flappy["score"] < 100


def test_bands_and_thresholds_are_configurable():
    assert band_for(75, CFG) == "good" and band_for(74, CFG) == "fair" and band_for(49, CFG) == "poor"
    strict = ExperienceConfig(bands={"good_min": 90, "fair_min": 70})
    assert band_for(80, strict) == "fair"
    with pytest.raises(ValueError):
        ExperienceConfig(bands={"good_min": 50, "fair_min": 60})


def test_ap_score_offline_and_partial():
    assert score_ap(ApMetrics(status="down"), CFG)["band"] == "offline"
    r = score_ap(ApMetrics(client_scores=[80, 60], client_count=10), CFG)
    assert r["score"] is not None and "utilization" in r["unavailable"]
    busy = score_ap(ApMetrics(client_scores=[80], util_pct=95, client_count=10), CFG)
    assert busy["inputs"]["utilization"]["subscore"] == 0


def test_summarize():
    s = summarize([90, 60, 20, None], CFG)
    assert s["good"] == 1 and s["fair"] == 1 and s["poor"] == 1 and s["unscored"] == 1 and s["score"] == 56.7


# ---- events, scoring in the poll, rules -------------------------------------------------------------------
def _snap(clients, aps=None, ok=True):
    return SiteSnapshot(
        aps=aps if aps is not None else [{"name": "ap1", "mac": "aa:aa:aa:00:00:01", "ip": "10.0.0.2", "status": "up", "client_count": len(clients)}],
        clients=clients, firmware="8.10.0.21", clients_ok=ok)


def _c(mac, ap="ap1", snr=40, speed=144, ssid="Corp"):
    return {"mac": mac, "ap_name": ap, "ssid": ssid, "snr_db": snr, "speed_mbps": speed, "quality": "good"}


@pytest.fixture
def fresh_db(app_env):
    """Point the global engine at this test's own database (tests that do not start the app need this)."""
    from backend.database.session import create_schema, init_engine
    init_engine()
    create_schema()


@pytest.fixture
def site(fresh_db):
    with session_scope() as db:
        s = Site(name="S", vc_host="10.0.0.1", simulated=False)
        db.add(s); db.flush()
        return s.id


def _run(site_id, snap):
    with session_scope() as db:
        apply_snapshot(db, db.get(Site, site_id), snap)


def _events(site_id):
    with session_scope() as db:
        return [(e.kind, e.ap_from, e.ap_to) for e in db.scalars(select(ClientEvent).order_by(ClientEvent.id))]


def test_events_inferred_from_polls(site):
    m = "11:11:11:00:00:01"
    _run(site, _snap([_c(m)]))
    assert _events(site) == []                       # initial inventory is not "connect" activity
    _run(site, _snap([_c(m, ap="ap2")]))
    assert _events(site) == [("roam", "ap1", "ap2")]
    _run(site, _snap([]))
    _run(site, _snap([_c(m)]))
    assert [e[0] for e in _events(site)] == ["roam", "disconnect", "connect"]


def test_unreadable_client_list_does_not_invent_disconnects(site):
    m = "11:11:11:00:00:02"
    _run(site, _snap([_c(m)]))
    _run(site, _snap([], ok=False))                  # `show clients` failed this poll
    assert _events(site) == []
    with session_scope() as db:
        assert db.scalar(select(ClientRecord)).is_connected is True


def test_scores_are_stored_and_samples_carry_them(site):
    _run(site, _snap([_c("11:11:11:00:00:03"), _c("11:11:11:00:00:04", snr=8, speed=6)]))
    with session_scope() as db:
        good, bad = (db.scalar(select(ClientRecord).where(ClientRecord.mac == m)) for m in ("11:11:11:00:00:03", "11:11:11:00:00:04"))
        assert good.exp_band == "good" and bad.exp_band == "poor" and bad.exp_score < 30
        ss = db.scalar(select(SiteSample))
        assert ss.exp_good == 1 and ss.exp_poor == 1 and ss.exp_score is not None
        ap = db.scalar(select(AccessPoint))
        assert ap.exp_score is not None


def test_poor_experience_alert_needs_sustained_streak_and_auto_resolves(site):
    m = "11:11:11:00:00:05"
    for _ in range(4):
        _run(site, _snap([_c(m, snr=5, speed=6)]))
    with session_scope() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "poor_experience")) is None  # default needs 5 polls
    _run(site, _snap([_c(m, snr=5, speed=6)]))
    with session_scope() as db:
        a = db.scalar(select(Alert).where(Alert.kind == "poor_experience"))
        assert a and a.status == "active" and "11:11:11:xx:xx:xx" in a.message and m not in a.message  # masked in text
    _run(site, _snap([_c(m, snr=40, speed=144)]))
    with session_scope() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "poor_experience")).status == "resolved"


def test_excessive_disconnects_alert(site):
    m = "11:11:11:00:00:06"
    _run(site, _snap([_c(m)]))
    for _ in range(5):
        _run(site, _snap([])); _run(site, _snap([_c(m)]))
    with session_scope() as db:
        a = db.scalar(select(Alert).where(Alert.kind == "excessive_disconnects"))
        assert a and "inferred" in a.message


def test_reboot_and_utilization_and_firmware_rules_fire_only_with_data(site):
    ap = {"name": "ap1", "mac": "aa:aa:aa:00:00:01", "ip": "10.0.0.2", "status": "up", "uptime_seconds": 90000,
          "util_5g_percent": 90.0}
    for _ in range(5):
        _run(site, _snap([], aps=[dict(ap)]))
    with session_scope() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "high_utilization")) is not None
        assert db.scalar(select(Alert).where(Alert.kind == "ap_reboot")) is None
    _run(site, _snap([], aps=[dict(ap, uptime_seconds=120, util_5g_percent=30.0)]))
    with session_scope() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "ap_reboot")) is not None
        assert db.scalar(select(Alert).where(Alert.kind == "high_utilization")).status == "resolved"
        a2 = AccessPoint(site_id=site, mac="aa:aa:aa:00:00:02", name="ap2", status="up", firmware="8.9.0.0")
        db.add(a2); db.flush()
        for a in db.scalars(select(AccessPoint)):
            a.firmware = a.firmware or "8.10.0.21"
        db.get(AccessPoint, a2.id).firmware = "8.9.0.0"
    # firmware rule runs inside the next poll; the AP not in the snapshot goes down, so use two reporting APs
    two = [dict(ap, uptime_seconds=300), {"name": "ap2", "mac": "aa:aa:aa:00:00:02", "ip": "10.0.0.3", "status": "up"}]
    snap = _snap([], aps=two); snap.firmware = None
    _run(site, snap)
    with session_scope() as db:
        for a in db.scalars(select(AccessPoint)):
            a.firmware = "8.10.0.21" if a.name == "ap1" else "8.9.0.0"
    _run(site, snap)
    with session_scope() as db:
        assert db.scalar(select(Alert).where(Alert.kind == "firmware_mismatch")) is not None


# ---- API ---------------------------------------------------------------------------------------------------
def _poll_demo():
    with session_scope() as db:
        from backend.monitoring.collector import ensure_demo_sites, visible_sites
        ensure_demo_sites(db); db.flush()
        for s in visible_sites(db):
            poll_site(db, s)


def test_overview_history_detail_and_masking(client, admin_headers):
    from .conftest import login
    from backend.security.crypto import hash_password
    from backend.database.models import User
    _poll_demo()
    o = client.get("/api/experience/overview", headers=admin_headers).json()
    assert o["simulated"] is True and o["summary"]["score"] is not None and o["worst_clients"] and o["aps"]
    assert all(c["score"] is not None for c in o["worst_clients"])
    assert o["worst_clients"] == sorted(o["worst_clients"], key=lambda c: c["score"])
    h = client.get("/api/experience/history", headers=admin_headers).json()
    assert h and h[-1]["score"] is not None
    cid = o["worst_clients"][0]["id"]
    d = client.get(f"/api/experience/clients/{cid}", headers=admin_headers).json()
    assert d["result"]["inputs"]["signal"]["source"] == "AP-reported" and "retries" in d["result"]["unavailable"]
    with session_scope() as db:
        db.add(User(username="vw", password_hash=hash_password("viewer-pass-123"), role="viewer"))
    vw = login(client, "vw", "viewer-pass-123")
    ov = client.get("/api/experience/overview", headers=vw).json()
    assert all(c["mac"].endswith(":xx:xx:xx") and c["hostname"] is None for c in ov["worst_clients"])
    assert client.get("/api/experience/config", headers=vw).status_code == 403
    ev = client.get("/api/experience/events", headers=vw).json()
    assert ev["inferred"] is True


def test_config_roundtrip_validation_audit_and_effect(client, admin_headers):
    cfg = client.get("/api/experience/config", headers=admin_headers).json()
    assert cfg["bands"] == {"good_min": 75, "fair_min": 50}
    cfg["bands"] = {"good_min": 40, "fair_min": 20}
    assert client.put("/api/experience/config", json=cfg, headers=admin_headers).status_code == 200
    assert client.get("/api/experience/config", headers=admin_headers).json()["bands"]["good_min"] == 40
    bad = dict(cfg, bands={"good_min": 30, "fair_min": 60})
    assert client.put("/api/experience/config", json=bad, headers=admin_headers).status_code == 422
    bad2 = dict(cfg, client=dict(cfg["client"], snr={"worst": 20, "best": 20}))
    assert client.put("/api/experience/config", json=bad2, headers=admin_headers).status_code == 422
    assert client.delete("/api/experience/config", headers=admin_headers).json()["bands"]["good_min"] == 75
    with session_scope() as db:
        from backend.database.models import AuditLog
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "experience_config")))) == 2


def test_corrupt_stored_config_falls_back_to_defaults(fresh_db):
    from backend.experience.config import load_config
    with session_scope() as db:
        db.add(AppSetting(key="experience", value={"bands": {"good_min": 10, "fair_min": 90}}))
        db.flush()
        assert load_config(db).bands.good_min == 75
