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
from backend.monitoring.collector import ensure_demo_sites, poll_all, visible_sites

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
        with session_scope() as db:
            HeartbeatRepo(db).beat("network_worker", self.instance, self.inm.version, detail)

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
            await self._sleep(interval - (time.time() % interval) + 0.5)

    def _poll_slot(self, slot: int, interval: int):
        with session_scope() as db:
            if not LeaseRepo(db, self.instance).try_acquire_slot("poll_all", slot, ttl_s=interval * 2):
                return None
        poll_all()  # legacy SIMS collector: demo generator in Phase 1 (live mode is refused by the profile)
        with session_scope() as db:
            counts = sync_inventory(db)
            events = self._build_events(db, slot, counts)
        store = telemetry_store()
        if slot % 60 == 0 and not store.has_retention_policy():
            store.prune()
        self.stats["polls"] += 1
        self.stats["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        return events

    def _build_events(self, db, slot: int, counts: dict) -> list:
        src = "DEMO" if self.s.mode == "demo" else "REAL"
        cid = str(uuid.uuid5(NS, f"poll:{slot}"))
        now = datetime.now(timezone.utc)
        events = []
        devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type == src)))
        aps = {a.id: a for a in db.scalars(select(AccessPoint))}
        switch_ports = {serial: ports for _s, _h, _m, serial, ports in DEMO_SWITCHES}
        demo_sw = DemoSwitchAdapter()
        for d in devices:
            eid = str(uuid.uuid5(NS, f"telemetry:{d.id}:{slot}"))
            points = []
            if d.family == "access_point" and d.legacy_ap_id in aps:
                a = aps[d.legacy_ap_id]
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
                                       "observed_at": now.isoformat(), "sample_period_s": self.s.poll_interval_seconds})
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
        for site in visible_sites(db):
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
            svc = IngestService(ledger=SqlEventLedger(db, INGEST_CONSUMER), telemetry=telemetry_store(), logs=log_store(),
                                commit=db.commit, rollback=db.rollback)
            return svc.handle(e)

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
        await asyncio.to_thread(self.check_startup)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        hb = asyncio.create_task(self.heartbeat_loop())
        await self.connect_bus()
        await self.synthetic_responder()
        tasks = [hb, asyncio.create_task(self.poll_loop()), asyncio.create_task(self.ingest_loop())]
        await self.stop.wait()
        log.info("shutting down")
        # Threads running blocking DB calls cannot be cancelled; never let shutdown hang past the grace period.
        import threading
        threading.Timer(15, lambda: os._exit(0)).start()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.nc:
            try:
                await asyncio.wait_for(self.nc.drain(), timeout=5)
            except Exception:
                await self.nc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
