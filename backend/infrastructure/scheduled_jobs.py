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
