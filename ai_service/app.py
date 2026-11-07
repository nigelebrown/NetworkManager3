"""Role 07 — isolated AI / analytics service.

Isolation guarantees (enforced by deployment AND checked here):
  * no database credentials, no NATS credentials, no device credentials are given to this container;
  * it sits only on the internal `ai` Docker network (no route to device management networks or the internet);
  * it exposes rule analysis and a two-endpoint Ollama gateway (/api/tags, /api/chat) used by the legacy
    assistant. It cannot execute configuration, and nothing it returns is executed by the platform.
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .rules import RULESET_VERSION, analyze

VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip() \
    if (Path(__file__).resolve().parent.parent / "VERSION").exists() else "0.0.0-dev"
OLLAMA_URL = os.environ.get("INM_OLLAMA_URL", "").rstrip("/")
TOKEN = os.environ.get("INM_AI_SHARED_TOKEN", "")
# Fail closed if the isolated AI container is accidentally given any management,
# authentication, database, bus or device credential. It only needs its own service token.
FORBIDDEN_ENV = (
    "SIMS_FERNET_KEY", "SIMS_DATABASE_URL", "SIMS_JWT_SECRET",
    "SIMS_ADMIN_PASSWORD", "INM_TELEMETRY_DATABASE_URL",
    "MGMT_DB_PASSWORD", "TS_DB_PASSWORD",
    "INM_NATS_URL", "INM_NATS_USER", "INM_NATS_PASSWORD",
    "INM_NATS_API_PASSWORD", "INM_NATS_WORKER_PASSWORD",
    "SIMS_ARUBA_PASSWORD", "SIMS_SSH_PASSWORD",
)



@asynccontextmanager
async def lifespan(_app):
    bad = isolation_violations()
    if bad:
        raise RuntimeError(f"AI service must not receive platform secrets: {bad}")
    yield


app = FastAPI(title="INM AI service", version=VERSION, docs_url=None, redoc_url=None, lifespan=lifespan)


def isolation_violations() -> list[str]:
    return [k for k in FORBIDDEN_ENV if os.environ.get(k)]


def require_token(authorization: str = Header(default="")) -> None:
    if not TOKEN:
        raise HTTPException(503, "INM_AI_SHARED_TOKEN not configured")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(401, "invalid service token")


@app.get("/health")
def health():
    ollama = "not configured"
    if OLLAMA_URL:
        try:
            r = httpx.get(OLLAMA_URL + "/api/tags", timeout=3)
            ollama = f"reachable ({len(r.json().get('models', []))} models)" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as exc:
            ollama = f"unreachable ({type(exc).__name__})"
    return {"ok": not isolation_violations(), "version": VERSION, "engine": f"RULE {RULESET_VERSION}",
            "trained_model": False, "ollama": ollama, "device_access": "none (no credentials, internal network only)"}


@app.post("/v1/analyze", dependencies=[Depends(require_token)])
def v1_analyze(body: dict):
    ctx = body.get("context") or {}
    if not isinstance(ctx, dict) or not isinstance(ctx.get("sites", []), list):
        raise HTTPException(422, "context.sites must be a list")
    return {"engine": "RULE", "ruleset_version": RULESET_VERSION, "trained_model": False,
            "source_type": ctx.get("source_type", "DEMO"), "recommendations": analyze(ctx)}


# ---- Ollama gateway (legacy assistant) -------------------------------------------------
_ALLOWED = {("GET", "api/tags"), ("POST", "api/chat")}


@app.api_route("/ollama/{path:path}", methods=["GET", "POST"])
async def ollama_gateway(path: str, request: Request):
    if (request.method, path) not in _ALLOWED:
        raise HTTPException(403, "only /api/tags and /api/chat are forwarded")
    if not OLLAMA_URL:
        return JSONResponse({"error": "Ollama is not configured in this deployment (enable the 'ollama' profile)"},
                            status_code=503)
    body = await request.body()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as c:
        r = await c.request(request.method, f"{OLLAMA_URL}/{path}", content=body or None,
                            headers={"Content-Type": "application/json"})
    return JSONResponse(r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)
