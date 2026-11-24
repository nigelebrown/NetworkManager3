"""Role 02 — network worker.

Owns everything that used to run inside the SIMS web process, plus the event pipeline:
  * poll loop      — legacy SIMS collector (demo generator in Phase 1), guarded by a per-interval lease so
                     two worker replicas never poll the same interval twice (INTEG-P1-05);
  * projection     — legacy inventory -> generic Site/Cluster/Device/Capability graph;
  * publisher      — one EventEnvelope per device/site per interval to JetStream (deterministic event IDs,
                     Nats-Msg-Id de-duplication);
  * ingest         — durable JetStream consumer: TimescaleDB + management record + Loki, then UI invalidation;
  * synthetic jobs — request/reply job that emits one correlated synthetic event (verify.sh, System Health);
  * heartbeat      — measured liveness for the System Health page.
Device writes are impossible here: the profile check refuses to start with writes enabled, the Aruba clients
refuse writes, and in Docker this container has no route to any device network.

Run: python -m workers.network_worker.main
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from backend.application.ingest import IngestService
from backend.config import get_settings
from backend.database.models import AccessPoint, Site
from backend.database.models_inm import InmDevice
from backend.database.session import assert_schema_current, create_schema, init_engine, session_scope
from backend.domain.events import EnvelopeError, new_event, parse_event
from backend.infrastructure.adapters.demo_switch import DEMO_SWITCHES, DemoSwitchAdapter
from backend.infrastructure.bus.nats_bus import (
    EVENTS_STREAM, INGEST_CONSUMER, MAX_DELIVER, SYNTHETIC_JOB_SUBJECT, JetStreamBus, connect, ensure_ingest_consumer,
    ensure_streams,
)
from backend.infrastructure.persistence.inventory import sync_inventory
from backend.infrastructure.persistence.repositories import HeartbeatRepo, LeaseRepo, SqlEventLedger
from backend.infrastructure.runtime import inm_settings, log_store, nats_settings, telemetry_store
from backend.monitoring.collector import (
    MAX_POLL_CONCURRENCY, configured_poll_concurrency, ensure_demo_sites, poll_worker_sites, visible_sites,
)

log = logging.getLogger("inm.worker")
NS = uuid.UUID("6f1d3c52-7a3e-4c55-9d8b-1f6a0e9b2c11")  # namespace for deterministic event IDs
SYNTHETIC_DEVICE_ID = str(uuid.uuid5(NS, "synthetic-probe-device"))
SERVICE = "network-worker"


class Worker:
    def __init__(self) -> None:
        self.s, self.inm = get_settings(), inm_settings()
        self.instance = f"{socket.gethostname()}:{os.getpid()}"
        self.stats = {"polls": 0, "polls_skipped_lease": 0, "published": 0, "ingested": 0, "duplicates": 0,
                      "dead_lettered": 0, "last_poll_at": None, "last_error": None}
        self.stop = asyncio.Event()
        self.nc = self.js = self.bus = None
        self.switch_pool = ThreadPoolExecutor(max_workers=MAX_POLL_CONCURRENCY, thread_name_prefix="cx-poll")

    # ------------------------------------------------------------------ startup
    def check_startup(self) -> None:
        problems = self.s.validate_secrets() + self.s.validate_profile()
        if self.s.role != "worker":
            problems.append("SIMS_ROLE must be 'worker' for the network worker")
        if problems:
            raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))
        init_engine()
        if self.s.auto_migrate:
            create_schema()
        else:
            assert_schema_current()
        store = telemetry_store()
        if store is None or log_store() is None or not self.inm.nats_url:
            raise SystemExit("INM_TELEMETRY_DATABASE_URL, INM_LOKI_URL and INM_NATS_URL are required for the worker")
        from backend.infrastructure.telemetry.timescale import TELEMETRY_HEAD
        if store.schema_revision() != TELEMETRY_HEAD:
            raise SystemExit(f"Telemetry schema is {store.schema_revision()!r}, expected {TELEMETRY_HEAD!r}: run the installer")
        if self.s.mode == "demo":
            with session_scope() as db:
                from backend.infrastructure.demo_dataset import demo_allowed_at_startup
                if demo_allowed_at_startup(db):  # an explicit "Remove demo data" is respected across restarts
                    ensure_demo_sites(db)

    async def connect_bus(self) -> None:
        while not self.stop.is_set():
            try:
                self.nc = await connect(nats_settings("inm-worker"))
                self.js = self.nc.jetstream()
                await ensure_streams(self.js)
                await ensure_ingest_consumer(self.js)
                self.bus = JetStreamBus(self.js)
                log.info("connected to NATS JetStream")
                return
            except Exception as exc:
                self.stats["last_error"] = f"bus: {exc}"
                log.warning("NATS not available (%s); retrying", exc)
                await asyncio.sleep(3)

    # ------------------------------------------------------------------ heartbeat
    async def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            detail = {**self.stats, "bus_connected": bool(self.nc and self.nc.is_connected),
                      "summary": f"polls {self.stats['polls']}, ingested {self.stats['ingested']}, "
                                 f"duplicates {self.stats['duplicates']}, bus {'up' if self.nc and self.nc.is_connected else 'DOWN'}"}
            try:
                await asyncio.to_thread(self._beat, detail)
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)
            await self._sleep(10)

    def _beat(self, detail: dict) -> None:
        from backend.infrastructure.resource_pressure import resource_pressure

        detail["container_resources"] = resource_pressure()["api_container"]
        with session_scope() as db:
            HeartbeatRepo(db).beat("network_worker", self.instance, self.inm.version, detail)

    # ------------------------------------------------------------------ WP-02 scheduled read-only collection
    async def collection_loop(self) -> None:
        """Run due read-only collection plans. Dedupe keys make replicas/restarts safe; failures are
        recorded and retried with bounded backoff, never converted to zero readings."""
        if os.environ.get("INM_SCHEDULED_COLLECTION", "true").lower() in ("0", "false", "no"):
            log.info("scheduled collection disabled by INM_SCHEDULED_COLLECTION")
            return
        from backend.infrastructure.collection_runner import run_due
        from backend.database.models_phases import CollectionPlan
        from backend.domain.collection import in_window
        from sqlalchemy import or_, select
        from datetime import datetime, timezone

        def due_plans():
            now = datetime.now(timezone.utc)
            with session_scope() as db:
                rows = db.execute(select(CollectionPlan.id, CollectionPlan.window_start_hour,
                                         CollectionPlan.window_end_hour, InmDevice.family, CollectionPlan.interval_seconds)
                    .join(InmDevice, InmDevice.id == CollectionPlan.device_id).where(
                    CollectionPlan.state == "ACTIVE",
                    or_(CollectionPlan.next_due_at.is_(None),
                        CollectionPlan.next_due_at <= now),
                ).order_by(CollectionPlan.next_due_at.asc().nullsfirst(), CollectionPlan.id))
                eligible = [row for row in rows if in_window(now, row.window_start_hour, row.window_end_hour)]
                from backend.infrastructure.poll_capacity import adjust_capacity
                demand = sum(row.family == "switch" for row in eligible)
                interval = min((row.interval_seconds for row in eligible if row.family == "switch"), default=300)
                switch_limit = adjust_capacity(db, "switch", demand, interval) if self.s.mode == "live" else 8
                switches = [row.id for row in eligible if row.family == "switch"][:switch_limit]
                other = [row.id for row in eligible if row.family != "switch"][:1]
                return switch_limit, switches, other

        def poll(plan_id):
            with session_scope() as db:
                return run_due(db, store=telemetry_store(), worker=self.instance, plan_ids=[plan_id])

        while not self.stop.is_set():
            try:
                switch_limit, switches, other = await asyncio.to_thread(due_plans)
                self.stats["switch_poll_limit"] = switch_limit
                if switches or other:
                    loop = asyncio.get_running_loop()
                    started = time.monotonic()
                    other_work = [asyncio.create_task(asyncio.to_thread(poll, i)) for i in other]
                    results = await asyncio.gather(
                        *(loop.run_in_executor(self.switch_pool, poll, i) for i in switches), return_exceptions=True)
                    if switches:
                        self.stats["switch_poll_batch"] = self._poll_batch(len(switches), started)
                    results.extend(await asyncio.gather(*other_work, return_exceptions=True))
                    summary = {}
                    for result in results:
                        if isinstance(result, BaseException):
                            log.warning("collection plan failed: %s", type(result).__name__)
                            continue
                        for key, value in result.items():
                            summary[key] = summary.get(key, 0) + value
                    self.stats["collection"] = summary
                    # Let plans with duplicate leases or closed windows yield before retrying.
                    if summary.get("events_queued"):
                        continue
            except Exception as exc:  # never crash the worker on one bad plan
                log.warning("collection cycle failed: %s", type(exc).__name__)
                self.stats["last_error"] = f"collection: {type(exc).__name__}"
            await self._sleep(10)

    async def jobs_loop(self) -> None:
        """Scheduled vault captures (5 min), optimizer (5 min, only if the feature is on), analysis (hourly)."""
        from backend.infrastructure import scheduled_jobs as J
        tick = 0
        while not self.stop.is_set():
            await asyncio.to_thread(J.safe(J.vault_captures))
            await asyncio.to_thread(J.safe(J.optimizer))
            await asyncio.to_thread(J.safe(J.recovery_sweep))
            await asyncio.to_thread(J.safe(J.discovery_rules))
            if tick % 12 == 0:
                await asyncio.to_thread(J.safe(J.scheduled_analysis))
            if tick % 72 == 0:
                await asyncio.to_thread(J.safe(J.predictive))
            tick += 1
            await self._sleep(300)

    async def printer_loop(self) -> None:
        """Printer Intelligence: leased read-only SNMP polling (60 s), job recovery (30 s), daily retention/recs."""
        from backend.infrastructure import scheduled_jobs as J
        from backend.printers import service as PS
        tick = 0
        while not self.stop.is_set():
            try:
                with session_scope() as db:
                    from backend.infrastructure.poll_capacity import adjust_capacity
                    from backend.database.models_printer_srs import PrinterMonitoring
                    from sqlalchemy import select, func
                    demand = db.scalar(select(func.count()).select_from(PrinterMonitoring).where(
                        PrinterMonitoring.enabled.is_(True))) or 0
                    printer_limit = adjust_capacity(db, "printer", demand, 60)
                self.stats["printer_poll_limit"] = printer_limit
                started = time.monotonic()
                summary = await PS.run_due(session_scope, f"worker-{self.instance}",
                                 limit=int(os.environ.get("INM_PRINTER_POLL_BATCH", "200")),
                                 concurrency=printer_limit)
                if summary["claimed"]:
                    self.stats["printer_poll_batch"] = self._poll_batch(summary["claimed"], started)
                with session_scope() as db:
                    PS.schedule_reports(db)
                await PS.tick_jobs(session_scope, f"worker-{self.instance}")
                if tick % 1440 == 0:
                    await asyncio.to_thread(J.safe(J.printer_daily))
            except Exception as exc:  # never crash the worker on one bad printer or job
                log.warning("printer cycle failed: %s", type(exc).__name__)
                self.stats["last_error"] = f"printers: {type(exc).__name__}"
            tick += 1
            await self._sleep(60)

    # ------------------------------------------------------------------ polling + publishing
    async def poll_loop(self) -> None:
        interval = max(10, self.s.poll_interval_seconds)
        while not self.stop.is_set():
            slot = int(time.time() // interval)
            try:
                events = await asyncio.to_thread(self._poll_slot, slot, interval)
                if events is None:
                    self.stats["polls_skipped_lease"] += 1
                else:
                    for e in events:
                        await self._publish(e)
            except Exception as exc:
                self.stats["last_error"] = f"poll: {type(exc).__name__}: {exc}"
                log.exception("poll cycle failed")
            now = time.time()
            # An overrun has already reached a new lease slot. Do not wait another full slot.
            delay = 0.5 if int(now // interval) > slot else interval - (now % interval) + 0.5
            await self._sleep(delay)

    def _poll_slot(self, slot: int, interval: int):
        with session_scope() as db:
            if not LeaseRepo(db, self.instance).try_acquire_slot("poll_all", slot, ttl_s=interval * 2):
                return None
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        polled = poll_worker_sites()
        if polled:
            self.stats["ap_poll_batch"] = self._poll_batch(polled, started)
        with session_scope() as db:
            counts = sync_inventory(db)
            events = self._build_events(db, slot, counts, started_at)
        store = telemetry_store()
        if slot % 60 == 0 and not store.has_retention_policy():
            store.prune()
        self.stats["polls"] += 1
        self.stats["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        return events

    @staticmethod
    def _poll_batch(count: int, started: float) -> dict:
        return {"count": count, "seconds": round(time.monotonic() - started, 2),
                "finished_at": datetime.now(timezone.utc).isoformat()}

    def _build_events(self, db, slot: int, counts: dict, started_at: datetime) -> list:
        src = "DEMO" if self.s.mode == "demo" else "REAL"
        cid = str(uuid.uuid5(NS, f"poll:{slot}"))
        now = datetime.now(timezone.utc)
        events = []
        devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type == src)))
        aps = {a.id: a for a in db.scalars(select(AccessPoint))}
        sites = visible_sites(db)
        if src == "REAL":
            sites = [s for s in sites if s.last_poll_at and
                     (s.last_poll_at.replace(tzinfo=timezone.utc) if s.last_poll_at.tzinfo is None else s.last_poll_at) >= started_at]
        successful_sites = {s.id: s.last_poll_at for s in sites if s.last_poll_ok}
        switch_ports = {serial: ports for _s, _h, _m, serial, ports in DEMO_SWITCHES}
        demo_sw = DemoSwitchAdapter()
        for d in devices:
            eid = str(uuid.uuid5(NS, f"telemetry:{d.id}:{slot}"))
            points = []
            if d.family == "access_point" and d.legacy_ap_id in aps:
                a = aps[d.legacy_ap_id]
                if src == "REAL" and a.site_id not in successful_sites:
                    continue
                observed_at = successful_sites[a.site_id] if src == "REAL" else now
                radios = a.radios or []
                util = {"2.4": None, "5": None}
                for r in radios:
                    band = str(r.get("band") or "")
                    key = "2.4" if band.startswith("2") else "5" if band.startswith("5") else None
                    if key and r.get("util_percent") is not None:
                        util[key] = r["util_percent"]
                method = "demo_generator" if src == "DEMO" else "aruba_instant_rest"
                for metric, val, unit in (("ap_up", 1.0 if a.status == "up" else 0.0, "bool"),
                                          ("client_count", a.client_count, "count"), ("cpu_percent", a.cpu_percent, "%"),
                                          ("memory_percent", a.memory_percent, "%"), ("util_2g_percent", util["2.4"], "%"),
                                          ("util_5g_percent", util["5"], "%"), ("exp_score", a.exp_score, "score")):
                    if val is not None:  # absent metrics stay absent (unknown), never zero
                        points.append({"metric": metric, "value": float(val), "unit": unit, "collection_method": method,
                                       "observed_at": observed_at.isoformat(), "sample_period_s": self.s.poll_interval_seconds})
            elif d.family == "switch" and d.source_type == "DEMO":
                from backend.domain.model import Device, DeviceFamily, SourceType
                dev = Device(id=d.id, source_type=SourceType.DEMO, vendor=d.vendor, family=DeviceFamily.SWITCH,
                             model=d.model, serial_number=d.serial_number)
                for p in demo_sw.fetch_metrics(dev, switch_ports.get(d.serial_number, 24), eid, d.site_id):
                    points.append({"metric": p.metric_name, "value": p.value, "unit": p.unit,
                                   "collection_method": p.collection_method, "observed_at": p.observed_at.isoformat(),
                                   "sample_period_s": p.sample_period_s})
            if not points:
                continue
            events.append(new_event("device.telemetry.observed", source_service=SERVICE, source_type=src,
                                    actor_id=SERVICE, correlation_id=cid, event_id=eid, occurred_at=now,
                                    site_id=d.site_id, legacy_site_id=d.legacy_site_id, device_id=d.id,
                                    adapter_version=d.adapter, payload={"points": points,
                                    "summary": f"{d.family} {d.hostname}: {len(points)} metrics ({src})"}))
        for site in sites:
            events.append(new_event(
                "site.poll.completed" if site.last_poll_ok else "site.poll.failed", source_service=SERVICE,
                source_type="DEMO" if site.simulated else "REAL", actor_id=SERVICE, correlation_id=cid,
                event_id=str(uuid.uuid5(NS, f"site:{site.id}:{slot}")), occurred_at=now, legacy_site_id=site.id,
                payload={"summary": f"poll {'ok' if site.last_poll_ok else 'failed'}: {site.name}"}))
        events.append(new_event("inventory.synchronised", source_service=SERVICE, source_type=src, actor_id=SERVICE,
                                correlation_id=cid, event_id=str(uuid.uuid5(NS, f"inventory:{slot}")), occurred_at=now,
                                payload={"summary": f"inventory projection {counts}", "counts": counts}))
        return events

    async def _publish(self, e) -> None:
        for attempt in range(5):
            try:
                if self.bus is None or not self.nc.is_connected:
                    raise ConnectionError("bus not connected")
                await self.bus.publish(e)
                self.stats["published"] += 1
                return
            except Exception as exc:  # NATS outage: bounded retry with backoff; same event_id keeps it idempotent
                self.stats["last_error"] = f"publish: {exc}"
                await asyncio.sleep(min(2 ** attempt, 15))
        log.error("event %s not published after retries (will be regenerated next interval)", e.event_id)

    # ------------------------------------------------------------------ ingest consumer
    async def ingest_loop(self) -> None:
        sub = None
        while not self.stop.is_set():
            try:
                if sub is None:
                    sub = await self.js.pull_subscribe("inm.events.>", durable=INGEST_CONSUMER, stream=EVENTS_STREAM)
                msgs = await sub.fetch(50, timeout=2)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                if "timeout" in type(exc).__name__.lower():
                    continue
                self.stats["last_error"] = f"ingest fetch: {exc}"
                log.warning("ingest fetch failed: %s", exc)
                sub = None
                await asyncio.sleep(2)
                continue
            for m in msgs:
                await self._ingest_one(m)

    async def _ingest_one(self, m) -> None:
        try:
            e = parse_event(m.data)
        except EnvelopeError as exc:
            await self.bus.dead_letter(m.data, f"invalid envelope: {exc}")
            await m.term()
            self.stats["dead_lettered"] += 1
            return
        try:
            result = await asyncio.to_thread(self._apply, e)
        except Exception as exc:
            delivered = m.metadata.num_delivered
            self.stats["last_error"] = f"ingest {e.event_id}: {type(exc).__name__}: {exc}"
            if delivered >= MAX_DELIVER:
                await self.bus.dead_letter(m.data, f"failed {delivered} times: {exc}")
                await m.term()
                self.stats["dead_lettered"] += 1
            else:
                await m.nak(delay=min(2 ** delivered, 30))
            return
        await m.ack()
        if result.status == "duplicate":
            self.stats["duplicates"] += 1
            return
        self.stats["ingested"] += 1
        if result.topics:
            try:
                await self.bus.publish_ui(result.topics, legacy_site_ids=None if e.legacy_site_id is None else [e.legacy_site_id],
                                          event_id=e.event_id, correlation_id=e.correlation_id)
            except Exception as exc:  # UI notice is best-effort; browsers also poll as a fallback
                log.warning("UI invalidation publish failed: %s", exc)

    def _apply(self, e):
        with session_scope() as db:
            from backend.infrastructure.event_effects import apply_effects
            svc = IngestService(ledger=SqlEventLedger(db, INGEST_CONSUMER), telemetry=telemetry_store(), logs=log_store(),
                                commit=db.commit, rollback=db.rollback, effects=lambda ev: apply_effects(db, ev))
            return svc.handle(e)

    async def discovery_loop(self) -> None:
        """Durable /24 discovery jobs (E2E-07): leased, resumable after restarts, pause/cancel aware."""
        from backend.infrastructure import scheduled_jobs as J
        while not self.stop.is_set():
            try:
                res = await asyncio.to_thread(J.discovery, f"worker-{self.instance}")
                if res:
                    self.stats["discovery"] = res
            except Exception as exc:
                log.warning("discovery cycle failed: %s", type(exc).__name__)
            await self._sleep(2)

    async def arm_measurements_loop(self) -> None:
        from backend.infrastructure import arm_measurements as ARM
        while not self.stop.is_set():
            try:
                self.stats["arm_measurements"] = await asyncio.to_thread(ARM.tick, session_scope, self.instance)
            except Exception as exc:
                log.warning("ARM measurement cycle failed: %s", type(exc).__name__)
            await self._sleep(5)

    async def automatic_onboarding_loop(self) -> None:
        from backend.infrastructure import automatic_onboarding as AO
        from backend.database.session import session_scope
        while not self.stop.is_set():
            try:
                result = await asyncio.to_thread(AO.tick, session_scope, f"worker-{self.instance}")
                self.stats["automatic_onboarding"] = result
            except Exception as exc:
                log.warning("automatic onboarding cycle failed: %s", type(exc).__name__)
            await self._sleep(4)

    async def notifications_loop(self) -> None:
        """Alert notifications and escalation (E2E-20); idempotent across replicas via delivery dedupe keys."""
        from backend.infrastructure import scheduled_jobs as J
        while not self.stop.is_set():
            res = await asyncio.to_thread(J.safe(J.notifications))
            if res:
                self.stats["notifications"] = res
            await self._sleep(15)

    # ------------------------------------------------------------------ transactional outbox relay (E2E-03)
    async def outbox_loop(self) -> None:
        """Publish PENDING outbox rows (scheduled collection, change engine) to JetStream with Nats-Msg-Id =
        event_id. A row is marked DELIVERED only after the broker acknowledged it; failures back off and
        dead-letter after MAX_ATTEMPTS. The ingest consumer then applies the event like every other one."""
        from backend.infrastructure import outbox
        from backend.database.models_remediation import EventOutbox
        from sqlalchemy import select

        def _due():
            now = datetime.now(timezone.utc)
            with session_scope() as db:
                rows = db.scalars(select(EventOutbox).where(EventOutbox.state == "PENDING")
                                  .order_by(EventOutbox.created_at).limit(200)).all()
                return [(r.event_id, r.envelope) for r in rows
                        if r.next_attempt_at is None or
                        (r.next_attempt_at if r.next_attempt_at.tzinfo else r.next_attempt_at.replace(tzinfo=timezone.utc)) <= now]

        def _mark(event_id: str, error: str | None):
            with session_scope() as db:
                row = db.get(EventOutbox, event_id)
                if row is None or row.state != "PENDING":
                    return
                if error is None:
                    row.state, row.delivered_at, row.last_error, row.next_attempt_at = \
                        "DELIVERED", datetime.now(timezone.utc), None, None
                    row.attempts += 1
                else:
                    row.attempts += 1
                    row.last_error = error[:300]
                    if row.attempts >= outbox.MAX_ATTEMPTS:
                        row.state = "DLQ"
                    else:
                        row.next_attempt_at = datetime.now(timezone.utc) + outbox._backoff(row.attempts)

        while not self.stop.is_set():
            try:
                for event_id, env in await asyncio.to_thread(_due):
                    try:
                        await self.bus.publish(parse_event(env.encode()))
                        await asyncio.to_thread(_mark, event_id, None)
                        self.stats["outbox_published"] = self.stats.get("outbox_published", 0) + 1
                    except Exception as exc:
                        await asyncio.to_thread(_mark, event_id, f"{type(exc).__name__}: {exc}")
                        self.stats["last_error"] = f"outbox publish: {type(exc).__name__}"
            except Exception as exc:
                log.warning("outbox relay cycle failed: %s", type(exc).__name__)
            await self._sleep(2)

    # ------------------------------------------------------------------ synthetic job
    async def synthetic_responder(self) -> None:
        async def handle(msg):
            try:
                req = json.loads(msg.data)
                cid = str(uuid.UUID(req["correlation_id"]))
                requested_by = str(req.get("requested_by", "unknown"))[:64]
            except Exception:
                await msg.respond(json.dumps({"error": "bad request"}).encode())
                return
            now = datetime.now(timezone.utc)
            e = new_event("synthetic.probe", source_service=SERVICE, source_type="DEMO", actor_type="human",
                          actor_id=requested_by, correlation_id=cid, occurred_at=now, device_id=SYNTHETIC_DEVICE_ID,
                          payload={"summary": f"synthetic probe requested by {requested_by} (DEMO)",
                                   "points": [{"metric": "synthetic_probe", "value": 1.0, "unit": "bool",
                                               "collection_method": "synthetic", "observed_at": now.isoformat()}]})
            await self._publish(e)
            await msg.respond(json.dumps({"event_id": e.event_id, "subject": e.subject, "worker": self.instance}).encode())
        await self.nc.subscribe(SYNTHETIC_JOB_SUBJECT, queue="network-workers", cb=handle)

    # ------------------------------------------------------------------ plumbing
    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=max(0.1, seconds))
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=28, thread_name_prefix="inm-worker"))
        await asyncio.to_thread(self.check_startup)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        hb = asyncio.create_task(self.heartbeat_loop())
        await self.connect_bus()
        await self.synthetic_responder()
        tasks = [hb, asyncio.create_task(self.poll_loop()), asyncio.create_task(self.ingest_loop()),
                 asyncio.create_task(self.collection_loop()), asyncio.create_task(self.jobs_loop()),
                 asyncio.create_task(self.outbox_loop()), asyncio.create_task(self.discovery_loop()),
                 asyncio.create_task(self.notifications_loop()), asyncio.create_task(self.printer_loop()),
                 asyncio.create_task(self.automatic_onboarding_loop()), asyncio.create_task(self.arm_measurements_loop())]
        await self.stop.wait()
        log.info("shutting down")
        # Threads running blocking DB calls cannot be cancelled; never let shutdown hang past the grace period.
        import threading
        threading.Timer(15, lambda: os._exit(0)).start()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.switch_pool.shutdown(wait=False, cancel_futures=True)
        if self.nc:
            try:
                await asyncio.wait_for(self.nc.drain(), timeout=5)
            except Exception:
                await self.nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from backend.security.log_redaction import install
    install()
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
