"""rc18 (register INTEG-P3-02): legacy SIMS AP sample history -> INM telemetry with timezone and provenance checks,
idempotent re-run, unmapped APs reported, simulated history never attached to a REAL device."""
from datetime import datetime, timedelta, timezone

from backend.database.models import AccessPoint, ApSample, Site
from backend.database.models_inm import InmDevice
from backend.database.session import session_scope
from backend.infrastructure.legacy_history import migrate_ap_samples
from backend.infrastructure.runtime import telemetry_store

T0 = datetime(2026, 3, 29, 0, 30)                     # naive (as SQLite returns SIMS rows): UTC by definition


def test_ap_samples_migrate_to_utc_with_provenance_idempotently(client):
    store = telemetry_store()
    with session_scope() as db:
        site = Site(name="Legacy HQ", vc_host="10.20.0.2", api_username="admin")
        db.add(site); db.flush()
        real_ap = AccessPoint(site_id=site.id, name="hq-ap-1", mac="aa:bb:cc:dd:ee:01", status="up")
        sim_ap = AccessPoint(site_id=site.id, name="hq-ap-2", mac="aa:bb:cc:dd:ee:02", status="up")
        orphan = AccessPoint(site_id=site.id, name="old-ap", mac="aa:bb:cc:dd:ee:03", status="down")
        db.add_all([real_ap, sim_ap, orphan]); db.flush()
        dev = InmDevice(source_type="REAL", environment="live", vendor="Aruba", family="access_point", model="AP-505",
                        serial_number="LEG-1", hostname="hq-ap-1", adapter="aruba_instant", legacy_ap_id=real_ap.id)
        demo = InmDevice(source_type="DEMO", environment="demo", vendor="Aruba", family="access_point", model="AP-505",
                         serial_number="LEG-2", hostname="hq-ap-2", adapter="aruba_instant", legacy_ap_id=sim_ap.id)
        db.add_all([dev, demo]); db.flush()
        ids = (dev.id, demo.id)
        for i in range(6):
            db.add(ApSample(ap_id=real_ap.id, site_id=site.id, ts=T0 + timedelta(minutes=30 * i), status="up",
                            client_count=10 + i, cpu_percent=None if i == 2 else 20.0 + i, memory_percent=40.0,
                            util_5g_percent=None, exp_score=77.0))
        # an aware value in another zone is converted, not shifted twice
        db.add(ApSample(ap_id=real_ap.id, site_id=site.id, status="up", client_count=99,
                        ts=datetime(2026, 3, 29, 8, 0, tzinfo=timezone(timedelta(hours=-5)))))
        db.add(ApSample(ap_id=sim_ap.id, site_id=site.id, ts=T0, status="up", client_count=3, simulated=True))
        db.add(ApSample(ap_id=real_ap.id, site_id=site.id, ts=T0 + timedelta(hours=5), status="up", client_count=1,
                        simulated=True))                                 # simulated row on a REAL device: refused
        db.add(ApSample(ap_id=orphan.id, site_id=site.id, ts=T0, status="down", client_count=0))
    with session_scope() as db:
        dry = migrate_ap_samples(db, store, dry_run=True)
        assert dry["points_written"] == 0 and dry["points_planned"] > 0
        out = migrate_ap_samples(db, store)
    assert out["samples"] == 10 and out["unmapped_samples"] == 1 and out["refused_simulated_on_real"] == 1
    assert out["points_written"] == out["points_planned"] == dry["points_planned"]
    rows = store.query(device_id=ids[0], metric_name=None, since=T0.replace(tzinfo=timezone.utc) - timedelta(days=1), limit=500)
    clients = sorted((r["observed_at"], r["value"]) for r in rows if r["metric_name"] == "clients")
    first = clients[0][0]
    first = first if isinstance(first, datetime) else datetime.fromisoformat(str(first).replace("Z", "+00:00"))
    assert (first.replace(tzinfo=timezone.utc) if first.tzinfo is None else first) == T0.replace(tzinfo=timezone.utc)
    assert any(v == 99.0 for _, v in clients)
    at99 = next(t for t, v in clients if v == 99.0)
    at99 = at99 if isinstance(at99, datetime) else datetime.fromisoformat(str(at99).replace("Z", "+00:00"))
    from backend.config import get_settings
    if not get_settings().database_url.startswith("sqlite"):
        # PostgreSQL keeps the offset of an aware legacy value: 08:00-05:00 is 13:00 UTC (SQLite stores naive
        # values only; SIMS itself always wrote UTC there)
        assert (at99 if at99.tzinfo else at99.replace(tzinfo=timezone.utc)) == datetime(2026, 3, 29, 13, 0, tzinfo=timezone.utc)
    cpu = [r for r in rows if r["metric_name"] == "cpu_percent"]
    assert len(cpu) == 5                                             # the missing CPU sample stays missing
    assert not [r for r in rows if r["metric_name"] == "exp_score"]   # derived score is not a measurement
    r0 = rows[0]
    assert r0["collection_method"] == "LEGACY_SIMS_MIGRATION" and r0["source_type"] == "REAL"
    tags = r0["tags"] if isinstance(r0["tags"], dict) else __import__("json").loads(r0["tags"])
    assert tags["migrated_from"] == "sims.ap_samples" and tags["legacy_sample_id"]
    demo_rows = store.query(device_id=ids[1], metric_name="clients", since=T0.replace(tzinfo=timezone.utc) - timedelta(days=1))
    assert [r["source_type"] for r in demo_rows] == ["DEMO"]
    with session_scope() as db:                                      # idempotent re-run
        again = migrate_ap_samples(db, store)
    assert again["points_written"] == 0 and again["points_planned"] == out["points_planned"]
