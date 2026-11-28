"""A queued database checkout during WebSocket login must not stall HTTP or socket cleanup."""
import asyncio
from contextlib import contextmanager
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from backend.presentation import ws


@pytest.mark.asyncio
async def test_websocket_authentication_wait_does_not_block_the_event_loop(monkeypatch):
    release = threading.Event()

    @contextmanager
    def waiting_database():
        release.wait(3)  # resembles pool exhaustion; a synchronous call would stall ASGI for three seconds
        yield object()

    monkeypatch.setattr(ws, "session_scope", waiting_database)
    monkeypatch.setattr(ws, "resolve_user", lambda db, token: (
        SimpleNamespace(username="operator", role="viewer", must_change_password=False), {"exp": time.time()+60}))
    monkeypatch.setattr(ws, "user_site_scope", lambda db, user: frozenset({42}))

    class Socket:
        first = True
        async def accept(self): pass
        async def receive_text(self):
            if self.first:
                self.first = False
                return json.dumps({"type": "auth", "token": "isolated-test"})
            raise WebSocketDisconnect()
        async def send_json(self, message): pass
        async def close(self, **kwargs): pass

    started = time.monotonic()
    task = asyncio.create_task(ws.events(Socket()))
    try:
        await asyncio.sleep(.05)
        assert time.monotonic()-started < 1, "Database checkout blocked the API event loop"
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=5)
