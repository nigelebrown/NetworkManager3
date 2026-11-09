"""Background jobs shared by the INM worker role and the standalone scheduler."""
from __future__ import annotations

import logging
import os

from ..database.session import session_scope

log = logging.getLogger("inm.jobs")


def _on(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).lower() not in ("0", "false", "no")


def vault_captures() -> dict | None:
    if not _on("INM_SCHEDULED_VAULT"):
        return None
    from ..api.production_ops import run_due_captures
    with session_scope() as db:
        return run_due_captures(db)


def scheduled_analysis() -> dict | None:
    if not _on("INM_SCHEDULED_ANALYSIS"):
        return None
    from ..api.phase4_monitoring import analyze_recorded
    from .runtime import telemetry_store
    if telemetry_store() is None:
        return None
    with session_scope() as db:
        return analyze_recorded(db, actor="scheduled-analysis")


def optimizer() -> dict | None:
    from .optimizer import feature_enabled, run_optimizer
    if not feature_enabled():
        return None
    with session_scope() as db:
        return run_optimizer(db, actor="optimizer")


def safe(fn):
    def run():
        try:
            return fn()
        except Exception as exc:  # one failing job must not stop the scheduler
            log.warning("job %s failed: %s", fn.__name__, type(exc).__name__)
            return None
    run.__name__ = fn.__name__
    return run


def outbox_relay() -> dict:
    """Standalone (no NATS) retry of PENDING outbox events through the in-process ingest (E2E-03/10)."""
    from . import outbox
    if outbox.mode() != "inprocess":
        return {"skipped": "jetstream mode: the worker relays"}
    with session_scope() as db:
        return outbox.relay(db)


def discovery(owner: str | None = None) -> dict:
    """Run durable discovery jobs (E2E-07). Safe on several processes: jobs are leased."""
    import socket
    import time
    import uuid
    from ..api.onboarding_workflow import _identify
    from . import discovery_jobs as DJ
    return DJ.tick(session_scope, owner or f"scheduler-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}", _identify, sleep=time.sleep)


def recovery_sweep() -> int:
    """Flag executions that died mid-change (stale journal heartbeat) for recovery (E2E-14)."""
    from . import recovery
    with session_scope() as db:
        return recovery.sweep(db)


def notifications() -> dict | None:
    """Alert notifications: enqueue, escalate unacknowledged alerts, deliver with retry (E2E-20)."""
    if not _on("INM_NOTIFICATIONS"):
        return None
    from .notifications import sweep
    with session_scope() as db:
        return sweep(db)
