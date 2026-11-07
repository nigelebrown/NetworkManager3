"""Seven-role service health. Every status comes from a measured probe; nothing is hardcoded green.

A probe that raises is DOWN; a probe that cannot run (not configured) is UNKNOWN; a slow or partial probe is
DEGRADED. The overall state is the worst required role.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable

from ..domain.ports import DependencyCheck

ROLES = [
    ("web_api", "01 Web/API"),
    ("network_worker", "02 Network worker"),
    ("management_db", "03 Management database (PostgreSQL)"),
    ("timeseries_db", "04 Time-series database (TimescaleDB)"),
    ("logging", "05 Logging (Alloy + Loki)"),
    ("event_bus", "06 Event bus (NATS JetStream)"),
    ("ai", "07 AI / analytics (isolated)"),
]
_RANK = {"UP": 0, "UNKNOWN": 1, "DEGRADED": 2, "DOWN": 3}

# A probe returns (status, version, detail). Raising means DOWN with the exception text.
Probe = Callable[[], tuple[str, str | None, str]]


def run_probe(name: str, role: str, probe: Probe | None, slow_ms: float = 1500) -> DependencyCheck:
    now = datetime.now(timezone.utc)
    if probe is None:
        return DependencyCheck(name, role, "UNKNOWN", None, None, "no probe configured for this deployment", now)
    t0 = time.perf_counter()
    try:
        status, version, detail = probe()
    except Exception as exc:  # measured failure
        return DependencyCheck(name, role, "DOWN", round((time.perf_counter() - t0) * 1000, 1), None,
                               f"{type(exc).__name__}: {str(exc)[:200]}", now)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if status == "UP" and ms > slow_ms:
        status, detail = "DEGRADED", f"{detail}; slow probe ({ms} ms)"
    return DependencyCheck(name, role, status, ms, version, detail, now)


def collect(probes: dict[str, Probe | None]) -> dict:
    checks = [run_probe(key, label, probes.get(key)) for key, label in ROLES]
    worst = max(checks, key=lambda c: _RANK[c.status]).status
    overall = "UP" if worst == "UP" else ("DOWN" if worst == "DOWN" else "DEGRADED")
    return {"overall": overall, "checked_at": datetime.now(timezone.utc).isoformat(),
            "services": [{**asdict(c), "checked_at": c.checked_at.isoformat()} for c in checks]}
