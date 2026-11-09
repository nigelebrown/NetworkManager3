"""WP-02 / GAP-01, GAP-02 — scheduled READ-ONLY collection contracts (pure, no I/O).

A collection plan is a schedule for *reading* a device. It never writes. A failed poll
is recorded as a failure with a category; it is never converted into a zero reading.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Metric families an adapter may be asked to read, per device family.
METRIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "access_point": ("system", "radios", "clients", "rf_quality"),
    "switch": ("system", "interfaces", "poe", "lldp"),
}
MIN_INTERVAL_S = 60          # protect device CPU / REST session limits
MAX_INTERVAL_S = 24 * 3600
MAX_ATTEMPTS = 5             # bounded retries, then DLQ
FAILURE_CATEGORIES = ("UNREACHABLE", "TLS_ERROR", "AUTH_REJECTED", "REST_DISABLED",
                      "RATE_LIMITED", "PARSE_ERROR", "TARGET_NOT_ALLOWED", "UNSUPPORTED",
                      "UNSUPPORTED_API", "CONNECTION_LOST", "PERSIST_FAILED")
PERMANENT = {"AUTH_REJECTED", "REST_DISABLED", "TARGET_NOT_ALLOWED", "UNSUPPORTED", "UNSUPPORTED_API"}
# Failures that mean the device itself did not answer (vs. answered but could not be read / stored).
UNREACHABLE_CATEGORIES = {"UNREACHABLE", "TLS_ERROR", "CONNECTION_LOST"}

# The one operational-state vocabulary shared by collector, NOC, Device 360 and alerting (E2E-10/11).
STATES = ("ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN")


def operational_state(*, monitored: bool, freshness: str, consecutive_failures: int,
                      last_failure_category: str | None, health_band: str | None,
                      reported_status: str | None = None) -> tuple[str, str]:
    """Derive ONE operational state from authoritative collection facts. Never 'ONLINE' without a recent
    PERSISTED observation; a device that stopped answering is OFFLINE even if an old record says 'up'."""
    if consecutive_failures and last_failure_category in UNREACHABLE_CATEGORIES:
        return "OFFLINE", f"Not answering ({last_failure_category}, {consecutive_failures} consecutive failure(s))"
    if consecutive_failures and last_failure_category:
        return "UNKNOWN", f"Reachable but cannot be observed ({last_failure_category})"
    if not monitored:
        return "UNKNOWN", "Not monitored (no approved collection plan)"
    if freshness == "NEVER":
        return "UNKNOWN", "No persisted observation yet"
    if freshness == "GAP":
        return "UNKNOWN", "Telemetry gap: last observation is more than 6 intervals old"
    if freshness == "STALE":
        return "STALE", "Last observation is older than 2 intervals"
    if reported_status == "down":
        return "OFFLINE", "Device reported down"
    if health_band == "POOR":
        return "DEGRADED", "Measured health is poor"
    return "ONLINE", "Fresh persisted observation"


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlanSpec:
    device_family: str
    metric_families: tuple[str, ...]
    interval_seconds: int
    window_start_hour: int | None = None   # optional collection window (UTC)
    window_end_hour: int | None = None
    max_concurrency: int = 1


def validate_plan(spec: PlanSpec) -> PlanSpec:
    allowed = METRIC_FAMILIES.get(spec.device_family)
    if allowed is None:
        raise PlanError("Unsupported device family for collection.")
    if not spec.metric_families:
        raise PlanError("Choose at least one read-only metric family.")
    bad = [m for m in spec.metric_families if m not in allowed]
    if bad:
        raise PlanError(f"Metric families not available for {spec.device_family}: {', '.join(bad)}")
    if len(set(spec.metric_families)) != len(spec.metric_families):
        raise PlanError("Duplicate metric family.")
    if not MIN_INTERVAL_S <= spec.interval_seconds <= MAX_INTERVAL_S:
        raise PlanError(f"Interval must be {MIN_INTERVAL_S}..{MAX_INTERVAL_S} seconds.")
    for h in (spec.window_start_hour, spec.window_end_hour):
        if h is not None and not 0 <= h <= 23:
            raise PlanError("Window hours are 0..23 UTC.")
    if (spec.window_start_hour is None) != (spec.window_end_hour is None):
        raise PlanError("Provide both window start and end, or neither.")
    if not 1 <= spec.max_concurrency <= 4:
        raise PlanError("Concurrency budget is 1..4 per device.")
    return PlanSpec(spec.device_family, tuple(sorted(spec.metric_families)), spec.interval_seconds,
                    spec.window_start_hour, spec.window_end_hour, spec.max_concurrency)


def in_window(now: datetime, start: int | None, end: int | None) -> bool:
    if start is None or end is None:
        return True
    h = now.astimezone(timezone.utc).hour
    return start <= h < end if start <= end else (h >= start or h < end)


def scheduled_window(now: datetime, interval_s: int) -> int:
    """Integer slot index — the same slot for every worker, so dedupe is global."""
    return int(now.timestamp()) // interval_s


def dedupe_key(device_id: str, metric_family: str, window: int) -> str:
    """C1: dedupe id = (device_id, metric_family, scheduled_window)."""
    raw = f"{device_id}:{metric_family}:{window}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def backoff_seconds(attempt: int, *, base: float = 5.0, cap: float = 900.0,
                    rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter. attempt starts at 1."""
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return (rng or random).uniform(ceiling / 2, ceiling)


def next_action_after_failure(category: str, attempt: int) -> str:
    """RETRY (bounded) or DLQ. Never 'SUCCESS'."""
    if category not in FAILURE_CATEGORIES:
        category = "PARSE_ERROR"
    if category in PERMANENT or attempt >= MAX_ATTEMPTS:
        return "DLQ"
    return "RETRY"


def freshness(last_success: datetime | None, interval_s: int, now: datetime) -> str:
    """FRESH / STALE / GAP / NEVER — a missing reading is never presented as healthy."""
    if last_success is None:
        return "NEVER"
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    age = (now - last_success).total_seconds()
    if age <= interval_s * 2:
        return "FRESH"
    if age <= interval_s * 6:
        return "STALE"
    return "GAP"


def experience_kpis(observations: list[dict]) -> dict:
    """GAP-02: time-window KPIs with explicit coverage. Unmeasured metrics stay None."""
    def vals(name):
        return [o["value"] for o in observations
                if o.get("metric_name") == name and o.get("value") is not None
                and o.get("quality") == "OBSERVED"]
    out: dict = {"sample_size": len(observations), "measured": {}, "unmeasured": []}
    for metric in ("client_count", "util_5g_percent", "util_2g_percent", "noise_floor_dbm",
                   "retry_percent", "roam_count", "latency_ms", "loss_percent"):
        v = vals(metric)
        if v:
            out["measured"][metric] = {"n": len(v), "avg": round(sum(v) / len(v), 2),
                                       "min": min(v), "max": max(v)}
        else:
            out["unmeasured"].append(metric)
    total = 8
    out["coverage_percent"] = round(100 * (total - len(out["unmeasured"])) / total, 1)
    out["caveat"] = ("Latency and loss are not measured by device counters; they require an "
                     "active test." if "latency_ms" in out["unmeasured"] else None)
    return out


def due(now: datetime, next_due: datetime | None) -> bool:
    if next_due is None:
        return True
    if next_due.tzinfo is None:
        next_due = next_due.replace(tzinfo=timezone.utc)
    return now >= next_due


def next_due_after(now: datetime, interval_s: int) -> datetime:
    return now + timedelta(seconds=interval_s)
