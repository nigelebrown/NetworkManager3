"""Adjust polling concurrency from demand and measured host/database headroom."""
from datetime import datetime, timezone
from math import ceil

from sqlalchemy import select, text

from ..database.models import AppSetting
from ..database.models_inm import ServiceHeartbeat
from .probes import database_pressure
from .resource_pressure import resource_pressure


def capacity_decision(current, demand, interval, batch, resources, database, maximum):
    vm = resources["vm"]
    cpus, load = vm.get("logical_cpus"), vm.get("load_5m")
    total, available = vm.get("memory_total"), vm.get("memory_available")
    if not cpus or load is None or not total or available is None or database.get("status") == "UNAVAILABLE":
        return current, "Holding capacity while resource measurements are unavailable"
    ratio = database["connections"] / database["connection_limit"]
    container = resources.get("api_container") or {}
    memory_limit = container.get("memory_limit")
    container_pressure = bool(memory_limit and (container.get("memory_current") or 0) > memory_limit * 0.8)
    if load > cpus or available / total < 0.15 or ratio > 0.8 or database.get("lock_waits") or container_pressure:
        return max(2, current - max(1, ceil(current * 0.2))), "Reducing concurrency under CPU, memory or database pressure"
    ceiling = max(2, min(maximum, cpus * 24, int(available / (32 * 1024**2)),
                         int(database["connection_limit"] * 0.65 / 3)))
    if current > ceiling:
        return max(ceiling, current - max(1, ceil(current * 0.2))), "Reducing toward the VM and database capacity budget"
    if not demand:
        return max(2, current - max(1, ceil(current * 0.2))), "Reducing idle polling capacity"
    duration = batch.get("seconds") if batch else None
    count = batch.get("count") if batch else None
    if duration is None or not count:
        target = min(ceiling, max(2, demand))
    else:
        seconds_per_poll = duration * min(current, count) / count
        target = min(ceiling, max(2, ceil(demand * seconds_per_poll / max(1, interval) * 1.25)))
    step = max(1, ceil(current * 0.2))
    if load > cpus * 0.75 or available / total < 0.25 or ratio > 0.65:
        target = min(current, target)
    if target > current:
        return min(target, current + step), "Increasing capacity to keep scheduled polls on time"
    if target < current * 0.7:
        return max(target, current - step), "Reducing capacity to match current demand"
    return current, "Capacity matches observed demand"


def adjust_capacity(db, family, demand, interval):
    from ..monitoring.collector import MAX_POLL_CONCURRENCY, POLL_CAPACITY_KEYS, configured_poll_concurrency
    configured = db.get(AppSetting, POLL_CAPACITY_KEYS[family])
    if configured and configured.value.get("mode", "auto") == "manual":
        return configured_poll_concurrency(db, family)
    if db.get_bind().dialect.name == "postgresql":
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"),
                         {"key": 70300 + ("ap", "switch", "printer").index(family)}):
            return effective_capacity(db, family)
    key = f"poll-capacity-runtime:{family}"
    row = db.scalar(select(AppSetting).where(AppSetting.key == key).with_for_update())
    now = datetime.now(timezone.utc)
    previous = row.value if row else {}
    current = previous.get("concurrency", configured_poll_concurrency(db, family))
    maximum = min(MAX_POLL_CONCURRENCY, configured.value["concurrency"]
                  if configured and "mode" in configured.value else MAX_POLL_CONCURRENCY)
    current = min(current, maximum)
    if previous.get("checked_at") and (now - datetime.fromisoformat(previous["checked_at"])).total_seconds() < 60:
        return current
    heartbeat = db.get(ServiceHeartbeat, "network_worker")
    batch = (heartbeat.detail or {}).get(f"{family}_poll_batch") if heartbeat else None
    if batch and batch.get("finished_at"):
        at = datetime.fromisoformat(batch["finished_at"])
        if (now - at).total_seconds() > max(600, interval * 3):
            batch = None
    resources = resource_pressure()
    database = database_pressure(db.get_bind())
    chosen, reason = capacity_decision(current, demand, interval, batch, resources, database, maximum)
    chosen = min(maximum, max(1, chosen))
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = {"concurrency": chosen, "checked_at": now.isoformat(), "reason": reason,
                 "demand": demand, "maximum": maximum}
    row.updated_at, row.updated_by = now, "capacity-controller"
    if chosen != current:
        from ..security.deps import audit
        audit(db, "poll_capacity_auto_adjusted", username="capacity-controller", target=family,
              detail=f"concurrency {current} -> {chosen}; {reason}", actor_kind="service")
    return chosen


def effective_capacity(db, family):
    from ..monitoring.collector import POLL_CAPACITY_KEYS, configured_poll_concurrency
    configured = db.get(AppSetting, POLL_CAPACITY_KEYS[family])
    if configured and configured.value.get("mode", "auto") == "manual":
        return configured_poll_concurrency(db, family)
    row = db.get(AppSetting, f"poll-capacity-runtime:{family}")
    return min(configured.value["concurrency"] if configured and "mode" in configured.value else 100,
               row.value["concurrency"]) if row else configured_poll_concurrency(db, family)
