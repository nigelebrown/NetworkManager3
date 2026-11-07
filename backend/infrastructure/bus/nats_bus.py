"""EventBusPort on NATS JetStream (role 06).

Streams
  INM_EVENTS  inm.events.>  durable, file storage, 2-minute publish de-duplication window (Nats-Msg-Id = event_id)
  INM_UI      inm.ui.>      short-lived UI invalidations; JetStream sequence = WebSocket resume cursor
  INM_DLQ     inm.dlq.>     events that exhausted delivery attempts
Consumers
  ingest      durable pull consumer on INM_EVENTS (explicit ack, max_deliver, backoff) owned by the network worker
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, RetentionPolicy, StorageType, StreamConfig

from ...domain.events import EventEnvelope

log = logging.getLogger("inm.bus")

EVENTS_STREAM, UI_STREAM, DLQ_STREAM = "INM_EVENTS", "INM_UI", "INM_DLQ"
INGEST_CONSUMER = "ingest"
UI_SUBJECT = "inm.ui.invalidate"
SYNTHETIC_JOB_SUBJECT = "inm.jobs.synthetic"
MAX_DELIVER = 8


@dataclass
class NatsSettings:
    url: str
    user: str | None = None
    password: str | None = None
    name: str = "inm"


async def connect(s: NatsSettings, *, persistent: bool = True, **kw):
    """persistent=True: long-lived service connection that reconnects forever (API hub, worker).
    persistent=False: one-shot probes/CLI — fail fast so health checks report DOWN instead of hanging."""
    if persistent:
        return await nats.connect(s.url, user=s.user, password=s.password, name=s.name, connect_timeout=5,
                                  max_reconnect_attempts=-1, reconnect_time_wait=2, allow_reconnect=True, **kw)
    return await asyncio.wait_for(nats.connect(s.url, user=s.user, password=s.password, name=s.name, connect_timeout=3,
                                               allow_reconnect=False, max_reconnect_attempts=0, **kw), timeout=6)


async def ensure_streams(js) -> None:
    """Idempotent stream provisioning (installer and every service start)."""
    specs = [
        StreamConfig(name=EVENTS_STREAM, subjects=["inm.events.>"], storage=StorageType.FILE,
                     retention=RetentionPolicy.LIMITS, max_age=7 * 24 * 3600, duplicate_window=120, max_msgs=2_000_000),
        StreamConfig(name=UI_STREAM, subjects=["inm.ui.>"], storage=StorageType.FILE,
                     retention=RetentionPolicy.LIMITS, max_age=3600, max_msgs=20_000, duplicate_window=60),
        StreamConfig(name=DLQ_STREAM, subjects=["inm.dlq.>"], storage=StorageType.FILE,
                     retention=RetentionPolicy.LIMITS, max_age=30 * 24 * 3600),
    ]
    for cfg in specs:
        try:
            await js.stream_info(cfg.name)
            await js.update_stream(cfg)
        except nats.js.errors.NotFoundError:
            await js.add_stream(cfg)


async def ensure_ingest_consumer(js) -> None:
    cfg = ConsumerConfig(durable_name=INGEST_CONSUMER, ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL,
                         ack_wait=30, max_deliver=MAX_DELIVER, filter_subject="inm.events.>", max_ack_pending=500)
    try:
        await js.consumer_info(EVENTS_STREAM, INGEST_CONSUMER)
    except nats.js.errors.NotFoundError:
        await js.add_consumer(EVENTS_STREAM, cfg)


class JetStreamBus:
    """EventBusPort implementation."""

    def __init__(self, js):
        self.js = js

    async def publish(self, event: EventEnvelope) -> int:
        ack = await self.js.publish(event.subject, event.to_json(), headers={"Nats-Msg-Id": event.event_id}, timeout=5)
        if ack.duplicate:
            log.info("JetStream de-duplicated event %s", event.event_id)
        return ack.seq

    async def publish_ui(self, topics: list[str], *, legacy_site_ids: list[int] | None, event_id: str | None,
                         correlation_id: str | None = None) -> int:
        body = json.dumps({"topics": sorted(set(topics)), "legacy_site_ids": legacy_site_ids, "event_id": event_id,
                           "correlation_id": correlation_id}).encode()
        headers = {"Nats-Msg-Id": f"ui-{event_id}"} if event_id else None
        ack = await self.js.publish(UI_SUBJECT, body, headers=headers, timeout=5)
        return ack.seq

    async def dead_letter(self, raw: bytes, reason: str) -> None:
        await self.js.publish("inm.dlq.events", raw, headers={"Inm-Dlq-Reason": reason[:200]}, timeout=5)
