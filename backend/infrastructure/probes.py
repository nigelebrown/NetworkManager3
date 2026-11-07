"""Readiness probes for the seven roles, measured from the API container.

Each returns (status, version, detail) or raises. Used by GET /api/v1/system/services and verify.sh.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from ..database.session import get_engine
from .runtime import inm_settings, log_store, nats_settings, telemetry_store


def management_db():
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.config import Config
    from pathlib import Path
    eng = get_engine()
    with eng.connect() as c:
        ver = c.execute(text("SELECT version()")).scalar() if eng.dialect.name == "postgresql" else "sqlite"
        current = MigrationContext.configure(c).get_current_revision()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "database" / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    status = "UP" if current == head and eng.dialect.name == "postgresql" else "DEGRADED"
    why = "" if eng.dialect.name == "postgresql" else "; not PostgreSQL (development database)"
    return status, (ver or "").split(" on ")[0], f"schema {current} (head {head}){why}"


def timeseries_db():
    store = telemetry_store()
    if store is None:
        raise RuntimeError("INM_TELEMETRY_DATABASE_URL not configured")
    store.ping()
    rev = store.schema_revision()
    from .telemetry.timescale import TELEMETRY_HEAD
    ts = store.timescale_version()
    if not ts:
        return "DEGRADED", None, f"reachable but TimescaleDB extension missing (schema {rev})"
    return ("UP" if rev == TELEMETRY_HEAD else "DEGRADED"), f"timescaledb {ts}", f"schema {rev} (head {TELEMETRY_HEAD})"


def logging_role():
    store = log_store()
    if store is None:
        raise RuntimeError("INM_LOKI_URL not configured")
    ok, body = store.ready()
    if not ok:
        return "DOWN", None, f"Loki not ready: {body}"
    return "UP", store.build_info(), "Loki ready (Alloy syslog receiver is checked by verify.sh)"


def event_bus():
    async def go():
        from .bus.nats_bus import EVENTS_STREAM, INGEST_CONSUMER, connect
        nc = await connect(nats_settings("inm-api-probe"), persistent=False)
        try:
            js = nc.jetstream()
            info = await js.stream_info(EVENTS_STREAM)
            cons = await js.consumer_info(EVENTS_STREAM, INGEST_CONSUMER)
            return info, cons, (getattr(nc, "_server_info", {}) or {}).get("version")
        finally:
            await nc.close()
    info, cons, ver = asyncio.run(asyncio.wait_for(go(), 8)) if not _in_loop() else _run_in_thread(lambda: asyncio.wait_for(go(), 8))
    lag = cons.num_pending
    status = "UP" if lag < 1000 else "DEGRADED"
    return status, f"nats-server {ver or 'unknown'}", f"stream {info.config.name}: {info.state.messages} msgs; ingest pending {lag}, unacked {cons.num_ack_pending}"


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _run_in_thread(coro_fn):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn())).result(timeout=10)


def network_worker():
    from ..database.session import session_scope
    from .persistence.repositories import HeartbeatRepo
    with session_scope() as db:
        hb = HeartbeatRepo(db).get("network_worker")
        if hb is None:
            return "DOWN", None, "no heartbeat ever recorded"
        age = (datetime.now(timezone.utc) - hb.beat_at).total_seconds()
        stale = inm_settings().worker_heartbeat_stale_s
        detail = f"last heartbeat {age:.0f}s ago from {hb.instance}"
        if hb.detail:
            detail += f"; {hb.detail.get('summary', '')}"
        if age > stale * 2:
            return "DOWN", hb.version, detail
        return ("UP" if age <= stale else "DEGRADED"), hb.version, detail


def ai_role():
    s = inm_settings()
    if not s.ai_url:
        raise RuntimeError("INM_AI_URL not configured")
    r = httpx.get(s.ai_url.rstrip("/") + "/health", timeout=4)
    r.raise_for_status()
    j = r.json()
    return ("UP" if j.get("ok") else "DEGRADED"), j.get("version"), \
        f"engine {j.get('engine')}; ollama {j.get('ollama', 'not configured')}; device access: {j.get('device_access')}"


def web_api():
    s = inm_settings()
    return "UP", s.version, f"instance {s.instance}; commit {s.git_commit[:12]}"


def all_probes() -> dict:
    s = inm_settings()
    return {
        "web_api": web_api,
        "network_worker": network_worker,
        "management_db": management_db,
        "timeseries_db": timeseries_db if s.telemetry_database_url else None,
        "logging": logging_role if s.loki_url else None,
        "event_bus": event_bus if s.nats_url else None,
        "ai": ai_role if s.ai_url else None,
    }
