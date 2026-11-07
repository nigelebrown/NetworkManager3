"""WebSocket invalidation gateway (WebEventPort).

Protocol (JSON text frames) on /ws/v1/events:
  client -> {"type":"auth","token":"<bearer>","since":<cursor|null>}       first frame, within 10 s
  server -> {"type":"hello","cursor":N,"replayed":k}  or  {"type":"resync","cursor":N}
  server -> {"type":"invalidate","seq":N,"topics":[...],"event_id":...,"correlation_id":...}
  server -> {"type":"ping"}                                                every 25 s
The token travels in a frame, never in the URL (URLs end up in proxy logs). Messages are compact
invalidations, never data: the browser re-queries REST, which applies the normal RBAC and site scope.
Cursor = JetStream stream sequence of INM_UI, so a reconnecting client replays exactly what it missed, or is
told to resync when the gap is older than the in-memory buffer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..database.session import session_scope
from ..security.deps import resolve_user, user_site_scope

log = logging.getLogger("inm.ws")
router = APIRouter()

PRIVILEGED_TOPICS = {"audit": {"admin", "auditor"}, "release": {"admin", "auditor"}}
BUFFER = 2000


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    username: str
    role: str
    scope: frozenset[int] | None
    exp: float
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))


class Hub:
    def __init__(self) -> None:
        self.buffer: deque[dict] = deque(maxlen=BUFFER)
        self.clients: set[Conn] = set()
        self.cursor = 0
        self.traces: dict[str, dict] = {}  # correlation_id -> delivery evidence (synthetic round-trip)
        self.connected_bus = False

    @staticmethod
    def allowed(conn: Conn, msg: dict) -> dict | None:
        topics = [t for t in msg.get("topics", []) if conn.role in PRIVILEGED_TOPICS.get(t, {conn.role})]
        if not topics:
            return None
        sites = msg.get("legacy_site_ids")
        if conn.scope is not None and sites is not None and not (set(sites) & conn.scope):
            return None  # cross-site event for a scoped user: not even the fact it happened is disclosed
        if conn.scope is not None and sites is None and set(topics) - {"system"}:
            # Unscoped (global) data events are sent to scoped users only as non-data topics.
            topics = [t for t in topics if t in ("system",)]
            if not topics:
                return None
        return {**msg, "topics": topics}

    async def publish(self, seq: int, msg: dict) -> None:
        item = {"type": "invalidate", "seq": seq, **msg}
        self.buffer.append(item)
        self.cursor = max(self.cursor, seq)
        delivered = 0
        for c in list(self.clients):
            out = self.allowed(c, item)
            if out is None:
                continue
            try:
                c.queue.put_nowait(out)
                delivered += 1
            except asyncio.QueueFull:  # slow client: make it resync instead of growing memory
                await self._drop(c, 4408, "client too slow; reconnect to resync")
        cid = msg.get("correlation_id")
        if cid:
            self.traces[cid] = {"seq": seq, "delivered_to_clients": delivered, "at": time.time(), "topics": msg.get("topics")}
            if len(self.traces) > 500:
                self.traces.pop(next(iter(self.traces)))

    def replay(self, conn: Conn, since: int | None) -> tuple[str, list[dict]]:
        if since is None:
            return "hello", []
        if since >= self.cursor:
            return "hello", []
        if not self.buffer or since < self.buffer[0]["seq"] - 1:
            return "resync", []
        items = [m for m in self.buffer if m["seq"] > since]
        return "hello", [o for o in (self.allowed(conn, m) for m in items) if o]

    async def _drop(self, c: Conn, code: int, reason: str) -> None:
        self.clients.discard(c)
        try:
            await c.ws.close(code=code, reason=reason)
        except Exception:
            pass


hub = Hub()


@router.websocket("/ws/v1/events")
async def events(ws: WebSocket):
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if first.get("type") != "auth" or not isinstance(first.get("token"), str):
            raise ValueError("first frame must be auth")
    except Exception:
        await ws.close(code=4401, reason="authentication required")
        return
    try:
        with session_scope() as db:
            user, payload = resolve_user(db, first["token"])
            if user.must_change_password:
                await ws.close(code=4403, reason="password change required")
                return
            conn = Conn(ws, user.username, user.role, user_site_scope(db, user), float(payload["exp"]))
    except Exception:
        await ws.close(code=4401, reason="invalid or expired session")
        return

    since = first.get("since") if isinstance(first.get("since"), int) else None
    kind, items = hub.replay(conn, since)
    await ws.send_json({"type": kind, "cursor": hub.cursor, "replayed": len(items), "bus": hub.connected_bus})
    for m in items:
        await ws.send_json(m)
    hub.clients.add(conn)

    async def sender():
        while True:
            try:
                msg = await asyncio.wait_for(conn.queue.get(), timeout=25)
            except asyncio.TimeoutError:
                msg = {"type": "ping", "cursor": hub.cursor}
            if time.time() >= conn.exp:
                await ws.close(code=4401, reason="session expired")
                return
            await ws.send_json(msg)

    async def receiver():
        while True:
            await ws.receive_text()  # pongs / keepalive; content ignored

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        hub.clients.discard(conn)


async def run_ui_subscriber(stop: asyncio.Event) -> None:
    """Follow INM_UI with an ordered (ephemeral) consumer and feed the hub. Reconnects forever."""
    from nats.js.api import DeliverPolicy
    from ..infrastructure.bus.nats_bus import UI_SUBJECT, connect, ensure_streams
    from ..infrastructure.runtime import nats_settings

    while not stop.is_set():
        nc = None
        try:
            nc = await connect(nats_settings("inm-api-ws"))
            js = nc.jetstream()
            await ensure_streams(js)
            info = await js.stream_info("INM_UI")
            hub.cursor = max(hub.cursor, info.state.last_seq)

            async def cb(m):
                try:
                    body = json.loads(m.data)
                except ValueError:
                    return
                await hub.publish(m.metadata.sequence.stream, body)

            await js.subscribe(UI_SUBJECT, cb=cb, ordered_consumer=True, deliver_policy=DeliverPolicy.NEW)
            hub.connected_bus = True
            log.info("UI invalidation subscriber connected (cursor %s)", hub.cursor)
            while not stop.is_set() and nc.is_connected:
                await asyncio.sleep(1)
        except Exception as exc:
            log.warning("UI subscriber error: %s", exc)
        finally:
            hub.connected_bus = False
            if nc:
                try:
                    await nc.close()
                except Exception:
                    pass
        if not stop.is_set():
            await asyncio.sleep(3)
