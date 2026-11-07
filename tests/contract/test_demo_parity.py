"""INTEG-P1-02: the integrated worker path produces the same demo outputs as the original in-process scheduler.

Both runs use the same frozen clock, so the seeded SIMS demo generator produces identical inputs. Run A is the
original SIMS behaviour (role=standalone, poll inside the app). Run B is INM (role=api; the network worker's
lease-guarded poll). Every dashboard/site/AP/client/alert/experience indicator must match.
"""
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import login

FROZEN = 1_794_000_000.0


def _snapshot(c, h):
    summ = c.get("/api/dashboard/summary", headers=h).json()
    summ.pop("last_poll_at", None)
    aps = sorted((a["name"], a["status"], a["client_count"], a["exp_score"], a["model"]) for a in c.get("/api/aps", headers=h).json())
    clients = sorted((x["mac"], x["exp_score"], x["quality"], x["ap_name"]) for x in c.get("/api/clients", headers=h).json())
    alerts = sorted((a["kind"], a["severity"], a["message"]) for a in c.get("/api/alerts", headers=h).json())
    exp = c.get("/api/experience/overview", headers=h).json()["summary"]
    sites = sorted((s["name"], s["ap_count"], s["last_poll_ok"]) for s in c.get("/api/sites", headers=h).json())
    return {"summary": summ, "aps": aps, "clients": clients, "alerts": alerts, "experience": exp, "sites": sites}


def _run(app_env, tmp_path, role, monkeypatch):
    from backend.config import get_settings
    from backend.database import session as dbs
    monkeypatch.setenv("SIMS_DATABASE_URL", f"sqlite:///{tmp_path / f'{role}.db'}")
    monkeypatch.setenv("SIMS_ROLE", role)
    if role == "standalone":
        monkeypatch.setenv("SIMS_INM_PROFILE", "standalone")
        monkeypatch.setenv("SIMS_DEVICE_WRITE_LOCK", "false")
    get_settings.cache_clear()
    dbs._engine = dbs._SessionLocal = None
    from backend.main import create_app
    with TestClient(create_app()) as c:
        if role == "standalone":
            from backend.monitoring.collector import poll_all
            poll_all()  # exactly what the original lifespan scheduler runs
        else:
            from workers.network_worker.main import Worker
            w = Worker.__new__(Worker)
            w.s = get_settings(); w.instance = "parity-worker"
            w.stats = {"polls": 0}
            from backend.infrastructure.runtime import inm_settings
            w.inm = inm_settings()
            events = w._poll_slot(slot=int(FROZEN // 60), interval=60)
            assert events, "worker path produced no events"
            assert any(e.event_type == "site.poll.completed" for e in events)
        return _snapshot(c, login(c))


def test_worker_path_matches_original_scheduler(app_env, tmp_path, monkeypatch):
    import backend.monitoring.demo as demo
    monkeypatch.setattr(demo.time, "time", lambda: FROZEN)
    a = _run(app_env, tmp_path, "standalone", monkeypatch)
    b = _run(app_env, tmp_path, "api", monkeypatch)
    assert a["summary"]["simulated"] is True
    for k in a:
        assert a[k] == b[k], f"parity difference in {k}"
    assert a["summary"]["aps_total"] == 12 and a["summary"]["sites_total"] == 3
