"""Per-request context (request ID, authenticated actor, site scope).

FastAPI runs sync dependencies in worker threads with a *copy* of the context, so a ContextVar set inside
`get_current_user` would be lost before the endpoint runs. Instead the request middleware installs one mutable
RequestCtx object per request; dependencies mutate that shared object, and every copied context sees it.
Background jobs (the network worker) have no RequestCtx: no actor and an unrestricted scope.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    username: str
    role: str
    session_id_hash: str | None
    kind: str = "human"


@dataclass
class RequestCtx:
    request_id: str | None = None
    actor: Actor | None = None
    # None = unrestricted (legacy behaviour / background jobs); frozenset = the only legacy site ids visible.
    scope: frozenset[int] | None = None


_ctx: ContextVar[RequestCtx | None] = ContextVar("inm_request_ctx", default=None)


def begin_request(request_id: str) -> RequestCtx:
    ctx = RequestCtx(request_id=request_id)
    _ctx.set(ctx)
    return ctx


def _get() -> RequestCtx:
    ctx = _ctx.get()
    if ctx is None:  # outside an HTTP request (tests, CLI): create a private one for this context
        ctx = RequestCtx()
        _ctx.set(ctx)
    return ctx


def get_request_id() -> str | None:
    c = _ctx.get()
    return c.request_id if c else None


def get_actor() -> Actor | None:
    c = _ctx.get()
    return c.actor if c else None


def set_actor(actor: Actor | None) -> None:
    _get().actor = actor


def get_scope() -> frozenset[int] | None:
    c = _ctx.get()
    return c.scope if c else None


def set_scope(scope: frozenset[int] | None) -> None:
    _get().scope = scope


def session_hash(jti: str | None) -> str | None:
    """Audit stores a hash of the session id, never the token or the id itself."""
    return hashlib.sha256(jti.encode()).hexdigest()[:32] if jti else None
