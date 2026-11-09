"""SIMS Wi-Fi Manager - FastAPI application entry point.

Run (from project root, venv active):
    uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .api import ai, auth, config, data, experience, firmware, sites, system, quick_devices, printers, discovery_ranges, onboarding_workflow, config_templates, phase4_monitoring
from .api import collection, attribution, vault, change_control, replacement, learning, optimization, platform_ops, operations_center, production_ops
from .config import ROOT_DIR, get_settings
from .database.models import User
from .database.session import assert_schema_current, create_schema, init_engine, session_scope
from .monitoring.collector import ensure_demo_sites, poll_all
from .presentation import api_v1, ws
from .security.context import begin_request
from .security.crypto import decode_access_token, hash_password
from .security.deps import audit, rate_limit_api

log = logging.getLogger("sims")
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


class _RedactFilter(logging.Filter):
    """Belt-and-braces: scrub obvious secrets if one ever reaches a log record."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        if any(t in msg for t in ("passwd=", "password=", "sid=", "authorization:")):
            record.msg, record.args = "[redacted log line containing a secret-like token]", ()
        return True


def _bootstrap_admin() -> None:
    s = get_settings()
    with session_scope() as db:
        if db.scalar(select(User).limit(1)):
            return
        if s.admin_password == "CHANGE_ME_MIN_12_CHARS" or (
            len(s.admin_password) < 12 and not s.demo_simple_login
        ):
            raise RuntimeError("Set a 12+ character administrator password (weak passwords require restricted demo-only mode).")
        db.add(User(username=s.admin_username, password_hash=hash_password(s.admin_password), role="admin",
                    must_change_password=s.force_initial_password_change))
        db.flush()
        audit(db, "bootstrap_admin_created", username="installer", target=s.admin_username,
              detail="initial administrator; password change forced" if s.force_initial_password_change else
              "initial administrator", channel="INSTALLER", actor_kind="installer")
        log.info("Created initial administrator '%s'", s.admin_username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactFilter())
    problems = s.validate_secrets() + s.validate_profile()
    if problems:
        raise RuntimeError("Configuration error:\n  - " + "\n  - ".join(problems))
    if not s.aruba_verify_tls:
        log.warning("INSECURE: Aruba TLS certificate verification is disabled (development setting).")
    if s.mode == "demo":
        log.warning("DEMO MODE: all data shown is SIMULATED.")

    if s.device_write_lock:
        log.warning("DEVICE WRITE LOCK: every device write path is disabled (Phase 1/2).")

    init_engine()
    if s.auto_migrate:
        create_schema()
        from .infrastructure.runtime import telemetry_store
        if telemetry_store() is not None:   # same idempotent, versioned migration the installer runs
            telemetry_store().migrate()
    else:
        assert_schema_current()  # the installer migrates; a newer/older schema here means stop, not guess
    _bootstrap_admin()
    if s.mode == "demo":
        with session_scope() as db:
            from backend.infrastructure.demo_dataset import demo_allowed_at_startup
            if demo_allowed_at_startup(db):  # an explicit "Remove demo data" is respected across restarts
                ensure_demo_sites(db)

    scheduler = None
    # Only the original single-process mode polls inside the web process. In INM the network worker owns
    # all polling (role=worker), so the API can be restarted or scaled without duplicating jobs.
    if s.role == "standalone" and not s.disable_scheduler:
        poll_all()  # first poll immediately so the dashboard has data
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(poll_all, "interval", seconds=s.poll_interval_seconds,
                          max_instances=1, coalesce=True, id="poll_all")
        # WP-02 scheduled read-only collection (the INM worker role runs the same job in its own process).
        import os as _os
        if _os.environ.get("INM_SCHEDULED_COLLECTION", "true").lower() not in ("0", "false", "no"):
            scheduler.add_job(_run_collection, "interval", seconds=30, max_instances=1, coalesce=True,
                              id="collection")
        from .infrastructure import scheduled_jobs as J
        scheduler.add_job(J.safe(J.vault_captures), "interval", minutes=5, max_instances=1, coalesce=True, id="vault")
        scheduler.add_job(J.safe(J.scheduled_analysis), "interval", hours=1, max_instances=1, coalesce=True, id="analysis")
        scheduler.add_job(J.safe(J.optimizer), "interval", minutes=5, max_instances=1, coalesce=True, id="optimizer")
        scheduler.start()

    stop = asyncio.Event()
    bg: list[asyncio.Task] = []
    from .infrastructure.runtime import inm_settings
    if s.role == "api" and inm_settings().nats_url:
        bg.append(asyncio.create_task(ws.run_ui_subscriber(stop)))
        bg.append(asyncio.create_task(_bus_publisher(app, stop)))
    yield
    stop.set()
    for task in bg:
        task.cancel()
    if getattr(app.state, "nc", None) is not None:
        try:
            await app.state.nc.close()
        except Exception:
            pass
    if scheduler:
        scheduler.shutdown(wait=False)


async def _bus_publisher(app: FastAPI, stop: asyncio.Event) -> None:
    """Shared NATS connection for request/reply jobs and UI notices from the API (reconnects forever)."""
    from .infrastructure.bus.nats_bus import connect
    from .infrastructure.runtime import nats_settings
    while not stop.is_set():
        try:
            app.state.nc = await connect(nats_settings("inm-api"))
            while not stop.is_set() and not app.state.nc.is_closed:
                await asyncio.sleep(1)
        except Exception as exc:
            log.warning("API bus connection failed: %s", exc)
            app.state.nc = None
            await asyncio.sleep(3)


_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# Every route that can change a device. In Phase 1/2 these are refused and audited at the edge, in addition
# to the guards inside the Aruba REST/SSH clients (defence in depth: UI, API edge, command handler, adapter).
_DEVICE_WRITE_ROUTES = [
    re.compile(r"^/api/config/changes/\d+/apply$"),
    re.compile(r"^/api/sites/\d+/rest-api/enable$"),
]


class RequestContextMiddleware:
    """Pure ASGI: installs the per-request context (request ID) before any dependency runs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        hdr = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = hdr if _REQUEST_ID_OK.match(hdr) else uuid.uuid4().hex
        begin_request(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)
        return await self.app(scope, receive, send_with_id)


def _run_collection() -> None:
    from .infrastructure.collection_runner import run_due
    from .infrastructure.runtime import telemetry_store
    try:
        with session_scope() as db:
            run_due(db, store=telemetry_store(), worker="standalone")
    except Exception as exc:  # never let one bad plan stop the scheduler
        log.warning("collection cycle failed: %s", type(exc).__name__)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="SIMS Wi-Fi Manager", version="0.1.0", lifespan=lifespan,
                  dependencies=[Depends(rate_limit_api)])
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def device_write_lock(request: Request, call_next):
        st = get_settings()
        if st.device_write_lock and request.method == "POST" and any(r.match(request.url.path) for r in _DEVICE_WRITE_ROUTES):
            auth_hdr = request.headers.get("authorization", "")
            payload = decode_access_token(auth_hdr[7:]) if auth_hdr.lower().startswith("bearer ") else None
            who = payload.get("sub") if payload else None
            from fastapi.concurrency import run_in_threadpool

            def _record():
                with session_scope() as db:
                    audit(db, "device_write_denied", username=who or "anonymous", target=request.url.path,
                          detail="device write lock is on (Phase 1/2); request refused before any device contact",
                          request=request, success=False)
            try:
                await run_in_threadpool(_record)
            except Exception:  # audit store down: still deny (fail closed), and say so in the service log
                log.error("device_write_denied for %s on %s could not be audited (management DB unavailable)",
                          who or "anonymous", request.url.path)
            return JSONResponse({"detail": "Device writes are disabled in this release (Phase 1 safety lock). "
                                           "Nothing was sent to any device.", "code": "device_write_locked"},
                                status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else resp.headers.get("Cache-Control", "no-cache")
        return resp

    for r in (auth.router, system.router, sites.router, data.router, config.router, experience.router, ai.router, firmware.router, firmware.download_router, quick_devices.router, printers.router, discovery_ranges.router, onboarding_workflow.router, config_templates.router, phase4_monitoring.router,
              collection.router, attribution.router, vault.router, change_control.router, replacement.router,
              learning.router, optimization.router, platform_ops.router, operations_center.router, production_ops.router):
        app.include_router(r)
    app.include_router(api_v1.router)
    app.include_router(ws.router)
    app.add_middleware(RequestContextMiddleware)  # added last = outermost: runs before everything else

    @app.get("/api/health", include_in_schema=False)
    def health():
        return {"ok": True}

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = (FRONTEND_DIST / full_path).resolve()
            if full_path and FRONTEND_DIST in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()
