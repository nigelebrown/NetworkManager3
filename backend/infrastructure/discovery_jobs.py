"""Durable, restart-safe discovery jobs (E2E-07).

A job is a database row with its full target list and a cursor (`next_index`). Workers take a time-limited
LEASE with a conditional update, process one batch of hosts at the job's rate limit, persist the results and
advance the cursor, then heartbeat/extend the lease. Consequences:

* API restart: nothing is lost — the API only creates the row.
* Worker crash mid-scan: the lease expires; another worker (or the same one after restart) resumes from the
  last persisted cursor. A batch may be re-probed after a crash, but results are keyed by address, so a host is
  never listed twice and a serial is never registered twice (existing devices are recognised).
* Cancel / pause / resume / retry are state transitions honoured between batches.
* No job can stay SCANNING forever: an expired lease is re-taken, and after MAX_ATTEMPTS lease takeovers
  without progress the job is FAILED with the reason.
"""
from __future__ import annotations

import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..database.models_onboarding import OnboardingDiscovery

log = logging.getLogger("inm.discovery")
LEASE_S = 60
MAX_ATTEMPTS = 5
ACTIVE = ("QUEUED", "SCANNING", "PAUSED")
TERMINAL = ("SCANNED", "CANCELLED", "FAILED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create(db: Session, *, credential_id: str, family: str, scope: str, site_name: str, targets: list[str],
           created_by: str, rate_per_s: int = 16, workflow_id: str | None = None) -> tuple[OnboardingDiscovery, bool]:
    """Returns (job, created). An identical active job (same profile + scope) is returned instead of a duplicate."""
    existing = db.scalar(select(OnboardingDiscovery).where(
        OnboardingDiscovery.credential_id == credential_id, OnboardingDiscovery.scope == scope,
        OnboardingDiscovery.status.in_(ACTIVE)))
    if existing is not None:
        return existing, False
    job = OnboardingDiscovery(credential_id=credential_id, site_name=site_name, scope=scope, device_family=family,
                              results=[], created_by=created_by, status="QUEUED", targets=list(targets),
                              next_index=0, attempts=0, error_count=0, rate_per_s=max(1, min(rate_per_s, 64)),
                              workflow_id=workflow_id)
    db.add(job)
    db.flush()
    return job, True


def progress(job: OnboardingDiscovery) -> dict:
    total = len(job.targets or [])
    done = min(job.next_index or 0, total)
    return {"total": total, "done": done, "percent": round(100 * done / total, 1) if total else 100.0,
            "found": sum(1 for r in job.results or [] if r.get("status") == "FOUND"),
            "failed": sum(1 for r in job.results or [] if r.get("status") == "FAILED"),
            "errors": job.error_count or 0, "attempts": job.attempts or 0,
            "lease_owner": job.lease_owner, "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None}


def set_state(db: Session, job: OnboardingDiscovery, action: str) -> OnboardingDiscovery:
    """pause | resume | cancel | retry — validated transitions."""
    allowed = {"pause": ({"QUEUED", "SCANNING"}, "PAUSED"), "resume": ({"PAUSED"}, "QUEUED"),
               "cancel": (set(ACTIVE), "CANCELLED"), "retry": ({"FAILED", "CANCELLED"}, "QUEUED")}
    if action not in allowed:
        raise ValueError("Unknown action.")
    src, dst = allowed[action]
    if job.status not in src:
        raise ValueError(f"Cannot {action} a job that is {job.status}.")
    job.status = dst
    if dst in ("PAUSED", "CANCELLED", "QUEUED"):
        job.lease_owner, job.lease_expires_at = None, None
    if action == "retry":
        job.attempts, job.finished_at = 0, None
        # a retry re-probes the failed hosts only: keep FOUND results, rewind cursor to the first failure
        keep = [r for r in job.results or [] if r.get("status") == "FOUND"]
        job.results, job.next_index = keep, 0
    if dst == "CANCELLED":
        job.finished_at = _now()
    db.flush()
    return job


def _take_lease(db: Session, job_id: str, owner: str) -> bool:
    now = _now()
    res = db.execute(update(OnboardingDiscovery).where(
        OnboardingDiscovery.id == job_id, OnboardingDiscovery.status.in_(("QUEUED", "SCANNING")),
        or_(OnboardingDiscovery.lease_owner.is_(None), OnboardingDiscovery.lease_owner == owner,
            OnboardingDiscovery.lease_expires_at < now)
    ).values(lease_owner=owner, lease_expires_at=now + timedelta(seconds=LEASE_S), heartbeat_at=now,
             status="SCANNING", attempts=OnboardingDiscovery.attempts + 1).execution_options(synchronize_session=False))
    db.flush()
    return bool(res.rowcount)


def run_job(session_factory, job_id: str, owner: str, identify: Callable[[str, str], dict], *,
            time_budget_s: float = 20.0, sleep: Callable[[float], None] | None = None) -> str:
    """Process batches of one job until done, paused/cancelled, or the time budget is used. Returns status."""
    import time
    started = time.monotonic()
    with session_factory() as db:
        if not _take_lease(db, job_id, owner):
            job = db.get(OnboardingDiscovery, job_id)
            return job.status if job else "MISSING"
        job = db.get(OnboardingDiscovery, job_id)
        db.refresh(job)
        if (job.attempts or 0) > MAX_ATTEMPTS:
            job.status, job.finished_at, job.lease_owner = "FAILED", _now(), None
            job.results = list(job.results or []) + [{"status": "FAILED", "management_ip": "-", "enrollable": False,
                                                      "result_note": f"Job stalled: {MAX_ATTEMPTS} lease takeovers "
                                                                     "without completing. Retry to resume."}]
            return job.status
        profile_id, targets, rate = job.credential_id, list(job.targets or []), job.rate_per_s or 16
    while True:
        with session_factory() as db:
            job = db.get(OnboardingDiscovery, job_id)
            if job.status != "SCANNING" or job.lease_owner != owner:
                return job.status              # paused / cancelled / taken over: stop between batches
            start = job.next_index or 0
            if start >= len(targets):
                job.status, job.finished_at, job.lease_owner, job.lease_expires_at = "SCANNED", _now(), None, None
                return job.status
        batch = targets[start:start + rate]
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=min(len(batch), 16)) as pool:
            out = list(pool.map(lambda ip: _safe(identify, profile_id, ip), batch))
        with session_factory() as db:
            job = db.get(OnboardingDiscovery, job_id)
            if job.lease_owner != owner:
                return job.status              # lost the lease (e.g. paused/cancelled meanwhile): discard batch
            by_ip = {r["management_ip"]: r for r in job.results or []}
            serials = {r.get("serial_number") for r in by_ip.values() if r.get("serial_number")}
            for r in out:
                if r["status"] == "NO_RESPONSE":
                    continue
                if r.get("serial_number") and r["serial_number"] in serials and r["management_ip"] not in by_ip:
                    r = {**r, "enrollable": False, "status": "DUPLICATE",
                         "result_note": "Same serial already answered on another address (VC/cluster or NAT)."}
                by_ip[r["management_ip"]] = r
                if r.get("serial_number"):
                    serials.add(r["serial_number"])
            job.results = sorted(by_ip.values(), key=lambda r: ipaddress.ip_address(r["management_ip"])
                                 if r["management_ip"] != "-" else ipaddress.ip_address("0.0.0.0"))
            job.error_count = (job.error_count or 0) + sum(1 for r in out if r.get("status") == "ERROR")
            job.next_index = start + len(batch)
            now = _now()
            job.heartbeat_at, job.lease_expires_at = now, now + timedelta(seconds=LEASE_S)
            job.attempts = 1                   # progress made: reset stall counter
        if time.monotonic() - started > time_budget_s:
            with session_factory() as db:          # hand back so another tick (or worker) continues
                job = db.get(OnboardingDiscovery, job_id)
                if job.lease_owner == owner:
                    job.lease_owner, job.lease_expires_at = None, None
                return job.status
        wait = 1.0 - (time.monotonic() - t0)       # rate limit: <= rate_per_s new hosts per second
        if wait > 0 and sleep is not None and start + len(batch) < len(targets):
            sleep(wait)


def _safe(identify, profile_id: str, ip: str) -> dict:
    try:
        return identify(profile_id, ip)
    except Exception as exc:  # one host must never kill the job
        return {"management_ip": ip, "status": "ERROR", "enrollable": False,
                "result_note": f"Probe error ({type(exc).__name__})"}


def tick(session_factory, owner: str, identify: Callable[[str, str], dict], *, time_budget_s: float = 20.0,
         sleep: Callable[[float], None] | None = None) -> dict:
    """Pick up QUEUED jobs and SCANNING jobs whose lease expired (crashed worker) and run them."""
    now = _now()
    with session_factory() as db:
        ids = [j.id for j in db.scalars(select(OnboardingDiscovery).where(
            OnboardingDiscovery.status.in_(("QUEUED", "SCANNING"))).order_by(OnboardingDiscovery.created_at))
               if j.status == "QUEUED" or j.lease_owner in (None, owner) or (_aware(j.lease_expires_at) or now) < now]
    out = {}
    for jid in ids:
        out[jid] = run_job(session_factory, jid, owner, identify, time_budget_s=time_budget_s, sleep=sleep)
    return out
