"""INM operations CLI (run inside the api container by the deployment scripts).

  python -m backend.cli migrate                 management (Alembic) + telemetry schema + NATS streams
  python -m backend.cli schema-status           JSON: current management/telemetry revisions
  python -m backend.cli record-release ...      append a deployment receipt + audit event (F046)
  python -m backend.cli verify [--json]         end-to-end readiness; exit 1 naming each failed dependency
  python -m backend.cli audit-verify            recompute the audit hash chain; exit 1 if broken
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select


def _init(migrate: bool = False):
    from .config import get_settings
    from .database.session import init_engine
    s = get_settings()
    problems = s.validate_secrets() + s.validate_profile()
    if problems:
        raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))
    init_engine()
    return s


def cmd_migrate(_args) -> int:
    from .database.session import create_schema
    from .infrastructure.runtime import inm_settings, nats_settings, telemetry_store
    _init()
    create_schema()
    out = {"management": "ok"}
    store = telemetry_store()
    if store is not None:
        out["telemetry_applied"] = store.migrate()
        out["timescaledb"] = store.timescale_version()
    if inm_settings().nats_url:
        async def go():
            from .infrastructure.bus.nats_bus import connect, ensure_ingest_consumer, ensure_streams
            nc = await connect(nats_settings("inm-migrate"), persistent=False)
            try:
                js = nc.jetstream()
                await ensure_streams(js)
                await ensure_ingest_consumer(js)
            finally:
                await nc.close()
        asyncio.run(go())
        out["nats"] = "streams and ingest consumer ensured"
    print(json.dumps(out))
    return 0


def schema_status() -> dict:
    from alembic.runtime.migration import MigrationContext
    from .database.session import get_engine
    from .infrastructure.runtime import telemetry_store
    with get_engine().connect() as c:
        mgmt = MigrationContext.configure(c).get_current_revision()
    store = telemetry_store()
    return {"management": mgmt, "telemetry": store.schema_revision() if store else None}


def cmd_schema_status(_args) -> int:
    _init()
    print(json.dumps(schema_status()))
    return 0


def cmd_record_release(a) -> int:
    from .database.models_inm import ReleaseEvent
    from .database.session import session_scope
    from .security.deps import audit
    s = _init()
    with session_scope() as db:
        rev = schema_status()
        ev = ReleaseEvent(operation=a.operation, operator=a.operator, version=a.version, git_commit=a.commit,
                          image_digests=json.loads(a.digests_json or "{}"), schema_revisions=rev,
                          environment=s.inm_profile, outcome=a.outcome, detail=a.detail)
        db.add(ev)
        db.flush()
        audit(db, f"release_{a.operation}", username=a.operator, target=a.version,
              detail=f"outcome={a.outcome} commit={a.commit} receipt={ev.id}", channel="INSTALLER", actor_kind="installer",
              success=a.outcome == "success")
        print(json.dumps({"receipt_id": ev.id, "schema": rev}))
    return 0


def cmd_audit_verify(_args) -> int:
    from .database.session import session_scope
    from .security.deps import verify_audit_chain
    _init()
    with session_scope() as db:
        r = verify_audit_chain(db)
    print(json.dumps(r))
    return 0 if r["ok"] else 1


def cmd_heartbeat_check(a) -> int:
    """Container healthcheck for the worker: fail when its own heartbeat is older than --max-age seconds."""
    from .database.session import session_scope
    from .infrastructure.persistence.repositories import HeartbeatRepo
    _init()
    with session_scope() as db:
        hb = HeartbeatRepo(db).get("network_worker")
        if hb is None:
            print("no heartbeat"); return 1
        age = (datetime.now(timezone.utc) - hb.beat_at).total_seconds()
    print(f"heartbeat age {age:.0f}s")
    return 0 if age <= a.max_age else 1


# ---------------------------------------------------------------- verify
def _check(results: list, name: str, fn) -> None:
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:240]}"
    results.append({"check": name, "ok": bool(ok), "detail": detail, "ms": round((time.perf_counter() - t0) * 1000)})


def _await_loki_startup(max_wait_s: float = 40.0) -> bool:
    """Wait for Loki's transient startup readiness delay without masking an actual outage.

    Readiness probes remain instantaneous for the dashboard; only the release
    verifier waits, so a clean deployment does not fail during ingester warm-up.
    """
    from .infrastructure.runtime import log_store
    store = log_store()
    if store is None:
        return False
    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        try:
            ready, _ = store.ready()
            if ready:
                return True
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(2.0, remaining))


def cmd_verify(a) -> int:
    import httpx
    from .application.health import collect
    from .config import get_settings
    from .infrastructure import probes
    from .infrastructure.runtime import inm_settings, log_store, nats_settings, telemetry_store
    _init()
    s, inm = get_settings(), inm_settings()
    _await_loki_startup()
    api = a.api_url.rstrip("/")
    results: list[dict] = []

    health = collect(probes.all_probes())
    for svc in health["services"]:
        results.append({"check": f"role:{svc['name']}", "ok": svc["status"] == "UP",
                        "detail": f"{svc['status']} {svc['version'] or ''} — {svc['detail']}", "ms": svc["latency_ms"]})

    _check(results, "api:legacy-health", lambda: (httpx.get(api + "/api/health", timeout=5).json().get("ok") is True, "GET /api/health"))

    def ready():
        j = httpx.get(api + "/api/v1/ready", timeout=5).json()
        return j.get("ready") is True, json.dumps(j)
    _check(results, "api:readiness", ready)

    def write_lock():
        if not s.device_write_lock:
            return False, "SIMS_DEVICE_WRITE_LOCK is not true"
        r = httpx.post(api + "/api/config/changes/1/apply", json={}, timeout=5)
        return r.status_code == 403 and "device_write_locked" in r.text, f"HTTP {r.status_code}"
    _check(results, "safety:device-write-lock", write_lock)
    _check(results, "safety:profile", lambda: (s.inm_profile == "demo" and s.mode == "demo" and not s.device_writes_permitted,
                                              f"profile={s.inm_profile} mode={s.mode} writes={s.device_writes_permitted}"))

    def ai_isolated():
        j = httpx.get(inm.ai_url.rstrip("/") + "/health", timeout=5).json()
        return j.get("ok") is True and j.get("trained_model") is False and j.get("device_access", "").startswith("none"), json.dumps(j)
    _check(results, "ai:isolation-contract", ai_isolated)

    def syslog():
        token = f"inm-verify-{uuid.uuid4().hex[:12]}"
        msg = f"<134>1 {datetime.now(timezone.utc).isoformat()} demo-hq-cx6100-01 inm-verify - - - {token} DEMO synthetic syslog"
        with socket.create_connection((a.syslog_host, a.syslog_port), timeout=5) as sock:
            sock.sendall(f"{len(msg.encode())} {msg}".encode())  # RFC 6587 octet counting
        store = log_store()
        for _ in range(30):
            hits = store.query(f'{{source="syslog"}} |= "{token}"', datetime.now(timezone.utc) - timedelta(minutes=5), 5)
            if hits:
                return True, f"found in Loki with labels {hits[0]['labels']}"
            time.sleep(1)
        return False, "syslog line not found in Loki within 30 s"
    _check(results, "logging:alloy-syslog-to-loki", syslog)

    def round_trip():
        return asyncio.run(_synthetic_round_trip(api))
    _check(results, "pipeline:worker->nats->timescale/postgres/loki->ui", round_trip)

    failed = [r for r in results if not r["ok"]]
    if a.json:
        print(json.dumps({"ok": not failed, "results": results}, indent=2))
    else:
        for r in results:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['check']}: {r['detail']}")
        print(f"\nRESULT: {'all checks passed' if not failed else 'FAILED: ' + ', '.join(r['check'] for r in failed)}")
    return 0 if not failed else 1


async def _synthetic_round_trip(api: str):
    import httpx
    from nats.js.api import DeliverPolicy
    from .database.models_inm import EventRecord
    from .database.session import session_scope
    from .infrastructure.bus.nats_bus import SYNTHETIC_JOB_SUBJECT, UI_SUBJECT, connect
    from .infrastructure.runtime import log_store, nats_settings, telemetry_store

    nc = await connect(nats_settings("inm-verify"), persistent=False)
    try:
        js = nc.jetstream()
        cid = str(uuid.uuid4())
        seen: asyncio.Future = asyncio.get_running_loop().create_future()

        async def cb(m):
            try:
                if json.loads(m.data).get("correlation_id") == cid and not seen.done():
                    seen.set_result(m.metadata.sequence.stream)
            except ValueError:
                pass
        sub = await js.subscribe(UI_SUBJECT, cb=cb, ordered_consumer=True, deliver_policy=DeliverPolicy.NEW)
        reply = await nc.request(SYNTHETIC_JOB_SUBJECT, json.dumps({"correlation_id": cid, "requested_by": "verify.sh"}).encode(), timeout=8)
        event_id = json.loads(reply.data)["event_id"]
        ui_seq = await asyncio.wait_for(seen, timeout=30)
        await sub.unsubscribe()
    finally:
        await nc.close()
    with session_scope() as db:
        rec = db.scalar(select(EventRecord).where(EventRecord.event_id == event_id))
    pts = telemetry_store().query(device_id=None, metric_name="synthetic_probe",
                                  since=datetime.now(timezone.utc) - timedelta(hours=1), limit=10, source_event_id=event_id)
    logs = log_store().query(f'{{app="inm"}} |= "{cid}"', datetime.now(timezone.utc) - timedelta(minutes=10), 5)
    cursor = 0
    async with httpx.AsyncClient(timeout=5) as c:
        for _ in range(20):
            cursor = (await c.get(api + "/api/v1/ready")).json().get("ws_cursor", 0)
            if cursor >= ui_seq:
                break
            await asyncio.sleep(0.5)
    ok = bool(rec and pts and logs and cursor >= ui_seq)
    return ok, (f"event {event_id}: postgres={'yes' if rec else 'NO'} timescale={len(pts)} loki={len(logs)} "
                f"ui_seq={ui_seq} api_ws_cursor={cursor}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="inm")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("schema-status")
    sub.add_parser("audit-verify")
    hc = sub.add_parser("heartbeat-check")
    hc.add_argument("--max-age", type=int, default=60)
    r = sub.add_parser("record-release")
    r.add_argument("--operation", required=True, choices=["install", "update", "rollback", "backup", "restore", "verify"])
    r.add_argument("--operator", required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--commit", default=None)
    r.add_argument("--outcome", required=True, choices=["success", "failed", "blocked"])
    r.add_argument("--digests-json", default="{}")
    r.add_argument("--detail", default=None)
    v = sub.add_parser("verify")
    v.add_argument("--api-url", default="http://127.0.0.1:8000")
    v.add_argument("--syslog-host", default="alloy")
    v.add_argument("--syslog-port", type=int, default=1514)
    v.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    return {"migrate": cmd_migrate, "schema-status": cmd_schema_status, "record-release": cmd_record_release,
            "verify": cmd_verify, "audit-verify": cmd_audit_verify,
            "heartbeat-check": cmd_heartbeat_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
