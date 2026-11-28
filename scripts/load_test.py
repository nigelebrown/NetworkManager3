"""Mixed-load capacity harness (isolated; EMULATED devices only; never touches a real network).

  python scripts/load_test.py serve  --workdir /tmp/inm-load --port 8840 --mgmt-url postgresql+psycopg://...  &
  python scripts/load_test.py load   --url http://127.0.0.1:8840 --users 25 --seconds 120 --out evidence/load/result.json

`serve` runs ONE uvicorn API process (as the production image does) on a PostgreSQL management database with
TimescaleDB telemetry (--ts-url; SQLite in --workdir when omitted) and seeds inventory, emulated CX switches (demo dataset) and
emulated SNMP printers. `worker` is a SEPARATE process (as in production) that repeatedly polls every switch plan and
every due printer, recording cycle durations:
  python scripts/load_test.py worker --workdir /tmp/inm-load --mgmt-url postgresql+psycopg://... & `load` drives N authenticated users through dashboard/inventory/monitoring reads with
think time and reports latency percentiles, error rate, server RSS, PostgreSQL connections and worker cycle times.

Harness-only adjustment: the per-IP login limiter (8/min) and per-user API limiter (300/min) are raised inside the
`serve` process so many virtual users can share one source IP. Production limits are unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATS = {"switch_cycles": [], "printer_cycles": [], "errors": 0}
PW = "Load-Test-Admin-2026!"


def _worker_stats(work: Path, plans: int) -> dict:
    try:
        w = json.loads((work / "worker_stats.json").read_text())
    except (OSError, ValueError):
        w = {"switch_cycles": [], "printer_cycles": [], "errors": 0}
    return {"switch_plans": plans, "switch_cycles": w["switch_cycles"][-50:], "printer_cycles": w["printer_cycles"][-50:],
            "worker_errors": w["errors"]}


def _env(a, work: Path, fernet: str):
    os.environ.update({
        "SIMS_DATABASE_URL": a.mgmt_url,
        "INM_TELEMETRY_DATABASE_URL": getattr(a, "ts_url", None) or f"sqlite:///{work / 'ts.db'}",
        "SIMS_MODE": "demo", "SIMS_INM_PROFILE": "demo", "SIMS_ROLE": "standalone", "SIMS_DEVICE_WRITE_LOCK": "true",
        "SIMS_JWT_SECRET": "load-" + "x" * 44, "SIMS_FERNET_KEY": fernet,
        "SIMS_ADMIN_PASSWORD": PW, "SIMS_FORCE_INITIAL_PASSWORD_CHANGE": "false", "INM_VAULT_DIR": str(work / "vault"),
        "INM_PRINTER_EMULATION": "true", "INM_READONLY_DEVICE_CIDRS": "10.250.0.0/24,10.250.1.0/24,10.250.2.0/24",
        "SIMS_DISABLE_SCHEDULER": "true", "INM_SCHEDULED_COLLECTION": "false",
    })


def _emulators(n: int):
    from backend.printers import collector as C, service as S
    for i in range(n):
        ip = f"10.250.{i // 250}.{i % 250 + 2}"
        S.EMULATORS[ip] = C.EmulatedPrinterAgent(C.emulated_printer(serial=f"LDP-{i:04d}", impressions=10000 + i,
                                                                    supplies=[{"descr": "TK-1150 Black", "level": 60}]))
    return S


def worker(a):
    """Separate worker process (as in production): polls every switch plan and every due printer in a loop."""
    import asyncio
    work = Path(a.workdir)
    _env(a, work, (work / "fernet").read_text())
    from backend.database import session as dbs
    from backend.infrastructure.collection_runner import demo_collector, run_due
    from backend.infrastructure.runtime import telemetry_store
    plans = json.loads((work / "seeded").read_text())["plans"]
    S = _emulators(a.printers)
    dbs.init_engine()
    stats = {"switch_cycles": [], "printer_cycles": [], "errors": 0}
    while True:
        t = time.perf_counter()
        try:
            with dbs.session_scope() as db:
                run_due(db, now=datetime.now(timezone.utc), store=telemetry_store(), worker="load", plan_ids=plans,
                        collector=demo_collector)
            stats["switch_cycles"].append((len(plans), time.perf_counter() - t))
            t = time.perf_counter()
            r = asyncio.run(S.run_due(dbs.session_scope, "load", limit=500, concurrency=32))
            stats["printer_cycles"].append((r.get("polled", r.get("claimed", 0)) if isinstance(r, dict) else 0,
                                            time.perf_counter() - t))
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            print("worker error", type(exc).__name__, exc, flush=True)
        (work / "worker_stats.json").write_text(json.dumps(stats))
        time.sleep(a.worker_pause)


def serve(a):
    from cryptography.fernet import Fernet
    work = Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    (work / "fernet").write_text(Fernet.generate_key().decode())
    _env(a, work, (work / "fernet").read_text())
    import asyncio
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from backend.database import session as dbs
    from backend.main import create_app
    from backend.security.deps import api_limiter, login_limiter
    with TestClient(create_app()) as c:                          # schema + admin bootstrap
        h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PW}).json()["access_token"]}
        for start in range(0, a.inventory, 100):                 # REAL-labelled manual inventory (no network)
            c.post("/api/v1/quick-devices/bulk", headers=h, json={"devices": [
                {"family": "access_point" if i % 4 else "switch", "site_name": f"Site {i % 40:02d}",
                 "hostname": f"load-{i:05d}", "serial_number": f"LOAD-{i:05d}", "model": "AP-505" if i % 4 else "CX 6100",
                 "status": "unknown", "metrics": {}} for i in range(start, min(start + 100, a.inventory))]})
        for u in range(a.users):
            c.post("/api/v1/users", headers=h, json={"username": f"load{u:03d}", "role": "viewer" if u % 3 else "operator",
                                                     "temporary_password": "Load-User-2026!x", "site_scope": "all"})
    from backend.infrastructure import demo_dataset
    from backend.infrastructure.collection_runner import demo_collector, run_due
    from backend.infrastructure.runtime import telemetry_store
    from backend.database.models_inm import InmDevice
    from backend.database.models_phases import CollectionPlan
    from backend.printers import collector as C, service as S
    dbs.init_engine()
    telemetry_store().migrate()
    with dbs.session_scope() as db:
        demo_dataset.load(db, actor="load", store=telemetry_store())
    with dbs.session_scope() as db:
        plans = [p.id for p in db.scalars(select(CollectionPlan).join(InmDevice, InmDevice.id == CollectionPlan.device_id)
                                          .where(InmDevice.family == "switch"))]
    _emulators(a.printers)
    from backend.printers import service as S
    with dbs.session_scope() as db:
        for i in range(a.printers):
            ip = f"10.250.{i // 250}.{i % 250 + 2}"
            p, _ = S.register_or_update(db, {"manufacturer": "Kyocera", "model": "ECOSYS M2640idw",
                                             "serial_number": f"LDP-{i:04d}", "management_ip": ip}, provenance="MANUAL")
            m = S.monitoring(db, p.id)
            m.enabled, m.interval_seconds, m.next_poll_at, m.approved_by = True, 60, datetime.now(timezone.utc), "load"
    login_limiter.limit = api_limiter.limit = 100000

    (work / "seeded").write_text(json.dumps({"plans": plans}))
    app = create_app()

    @app.get("/__load_stats", include_in_schema=False)
    def load_stats():
        import resource
        rss = int(next(l.split()[1] for l in open("/proc/self/status") if l.startswith("VmRSS")))
        with dbs.session_scope() as db:
            from sqlalchemy import text
            conns = db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar()
            size = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        return {"rss_kb": rss, "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "pg_connections": conns,
                "mgmt_db_bytes": size, "ts_db_bytes": (work / "ts.db").stat().st_size if (work / "ts.db").exists() else 0,
                **_worker_stats(work, len(plans))}

    app.router.routes.insert(0, app.router.routes.pop())   # ahead of the SPA catch-all
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


def load(a):
    import httpx
    base = a.url
    with httpx.Client(base_url=base, timeout=30) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": PW}).json()["access_token"]}
        gid = c.post("/api/v1/port-groups", headers=admin, json={"name": f"Load group {time.time():.0f}", "members": []}).json()["id"]
        sample_dev = next(d["id"] for d in c.get("/api/v1/devices", headers=admin).json() if d["family"] == "switch")
        tokens = []
        for u in range(a.users):
            name = f"load{u:03d}"
            r = c.post("/api/auth/login", json={"username": name, "password": "Load-User-2026!xy"})
            if r.status_code != 200:
                r = c.post("/api/auth/login", json={"username": name, "password": "Load-User-2026!x"})
            assert r.status_code == 200, (name, r.status_code, r.text)
            tok = r.json()["access_token"]
            if r.json().get("must_change_password"):
                ch = c.post("/api/auth/change-password", headers={"Authorization": "Bearer " + tok},
                            json={"current_password": "Load-User-2026!x", "new_password": "Load-User-2026!xy"})
                assert ch.status_code == 200, (name, ch.status_code, ch.text)
                tok = ch.json()["access_token"]
            tokens.append(tok)
        before = c.get("/__load_stats").json()
    endpoints = ["/api/v1/devices", "/api/v1/noc/overview", "/api/dashboard/summary", "/api/v1/alerts",
                 "/api/printers", "/api/printers/dashboard", f"/api/v1/port-groups/{gid}/dashboard",
                 f"/api/v1/devices/{sample_dev}/ports", "/api/v1/sites", "/api/v1/port-groups"]
    lat: dict[str, list[float]] = {e: [] for e in endpoints}
    errors: dict[str, int] = {}
    forbidden: dict[str, int] = {}
    stop = time.time() + a.seconds

    def user(tok):
        rnd = random.Random(tok)
        with httpx.Client(base_url=base, timeout=30, headers={"Authorization": "Bearer " + tok}) as uc:
            while time.time() < stop:
                e = rnd.choice(endpoints)
                t = time.perf_counter()
                try:
                    r = uc.get(e)
                    ok = r.status_code < 400
                    if r.status_code == 403:            # role-restricted endpoint (expected for viewers)
                        forbidden[e] = forbidden.get(e, 0) + 1
                        ok = True
                except httpx.HTTPError:
                    ok = False
                dt = time.perf_counter() - t
                lat[e].append(dt)
                if not ok:
                    errors[e] = errors.get(e, 0) + 1
                time.sleep(rnd.uniform(0.5 * a.think, 1.5 * a.think))

    threads = [threading.Thread(target=user, args=(t,)) for t in tokens]
    peak_rss = [0]

    def sampler():
        with httpx.Client(base_url=base, timeout=30) as sc:
            while time.time() < stop:
                try:
                    peak_rss[0] = max(peak_rss[0], sc.get("/__load_stats").json()["rss_kb"])
                except httpx.HTTPError as _exc:
                    print(f"warning: RSS sample request failed; peak memory may be under-reported: {type(_exc).__name__}", file=__import__("sys").stderr)
                time.sleep(5)

    started = time.time()
    for t in threads:
        t.start()
    st = threading.Thread(target=sampler)
    st.start()
    for t in threads:
        t.join()
    st.join()
    elapsed = time.time() - started
    with httpx.Client(base_url=base, timeout=30) as c:
        after = c.get("/__load_stats").json()

    def pct(xs, p):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))] * 1000, 1) if xs else None
    every = [x for v in lat.values() for x in v]
    sw = [d for _, d in after["switch_cycles"]]
    pr = [d for _, d in after["printer_cycles"]]
    out = {"users": a.users, "seconds": round(elapsed, 1), "think_time_s": a.think, "requests": len(every),
           "throughput_rps": round(len(every) / elapsed, 1), "error_count": sum(errors.values()), "errors": errors,
           "forbidden_by_role": forbidden,
           "latency_ms": {"p50": pct(every, 50), "p95": pct(every, 95), "p99": pct(every, 99), "max": pct(every, 100)},
           "per_endpoint_p95_ms": {e: pct(v, 95) for e, v in lat.items()},
           "server": {"rss_kb_before": before["rss_kb"], "rss_kb_peak": peak_rss[0], "rss_kb_after": after["rss_kb"],
                      "pg_connections_after": after["pg_connections"], "mgmt_db_bytes_before": before["mgmt_db_bytes"],
                      "mgmt_db_bytes_after": after["mgmt_db_bytes"], "ts_db_bytes_before": before["ts_db_bytes"],
                      "ts_db_bytes_after": after["ts_db_bytes"], "worker_errors": after["worker_errors"]},
           "worker": {"switch_plans_per_cycle": after["switch_plans"],
                      "switch_cycle_s_median": round(statistics.median(sw), 2) if sw else None,
                      "switch_cycle_s_max": round(max(sw), 2) if sw else None,
                      "printer_cycle_s_median": round(statistics.median(pr), 2) if pr else None,
                      "printer_cycle_s_max": round(max(pr), 2) if pr else None,
                      "printer_polls_last_cycles": [n for n, _ in after["printer_cycles"]][-10:]}}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker")
    w.add_argument("--workdir", required=True)
    w.add_argument("--mgmt-url", required=True)
    w.add_argument("--printers", type=int, default=500)
    w.add_argument("--worker-pause", type=float, default=5.0)
    w.add_argument("--ts-url", default=None, help="TimescaleDB telemetry URL (default: SQLite in --workdir)")
    s = sub.add_parser("serve")
    s.add_argument("--workdir", required=True)
    s.add_argument("--port", type=int, default=8840)
    s.add_argument("--mgmt-url", required=True)
    s.add_argument("--inventory", type=int, default=600)
    s.add_argument("--printers", type=int, default=500)
    s.add_argument("--users", type=int, default=25)
    s.add_argument("--worker-pause", type=float, default=5.0)
    s.add_argument("--ts-url", default=None, help="TimescaleDB telemetry URL (default: SQLite in --workdir)")
    ld = sub.add_parser("load")
    ld.add_argument("--url", required=True)
    ld.add_argument("--users", type=int, default=25)
    ld.add_argument("--seconds", type=int, default=120)
    ld.add_argument("--think", type=float, default=2.0)
    ld.add_argument("--out", required=True)
    a = p.parse_args()
    {"serve": serve, "worker": worker, "load": load}[a.cmd](a)
