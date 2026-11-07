"""Local AI assistant (Ollama): explain a score, summarize, answer questions, suggest what to review.

Advisory only. The model never sees client identities, never gets write access, and can only ask the database
questions from the allow-listed tools in backend/ai/context.py.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai import context, prompts
from ..ai.ollama import OllamaClient, OllamaError, build_client
from ..config import get_settings
from ..database.models import AppSetting, ClientRecord, User, utcnow
from ..database.session import get_db
from ..security.deps import audit, require_role
from .experience import _site_ids, client_score_result

router = APIRouter(prefix="/api/ai", tags=["ai"])
_busy = threading.Lock()  # one generation at a time: protects a small server from being saturated
KEY = "ai"


def _enabled() -> None:
    if not get_settings().ollama_enabled:
        raise HTTPException(409, "The AI assistant is disabled. Set SIMS_OLLAMA_ENABLED=true in .env and restart SIMS.")


def effective_model(db: Session) -> str:
    row = db.get(AppSetting, KEY)
    return (row.value.get("model") if row and isinstance(row.value, dict) else None) or get_settings().ollama_model or ""


class _Session:
    """Holds the generation lock + client for one request."""

    def __init__(self, db: Session):
        _enabled()
        if not _busy.acquire(blocking=False):
            raise HTTPException(429, "The assistant is busy with another request. Try again in a moment.")
        try:
            self.client: OllamaClient = build_client(effective_model(db))
        except OllamaError as exc:
            _busy.release()
            raise HTTPException(503, str(exc)) from None

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()
        _busy.release()


def _chat(sess: _Session, user_prompt: str, **kw: Any) -> str:
    try:
        return sess.client.chat([{"role": "system", "content": prompts.SYSTEM}, {"role": "user", "content": user_prompt}], **kw)
    except OllamaError as exc:
        raise HTTPException(503, str(exc)) from None


def _reply(kind: str, text: str, model: str, facts: dict[str, Any], simulated: bool, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "text": text, "ai_generated": True, "model": model, "facts": facts, "simulated": simulated, **extra}


def _audit(db: Session, request: Request, user: User, what: str, model: str) -> None:
    audit(db, "ai_query", username=user.username, target=what, detail=f"model={model}", request=request)


# ---- status / model selection --------------------------------------------------------------------
@router.get("/status")
def status(db: Session = Depends(get_db), _: User = Depends(require_role("operator"))):
    s = get_settings()
    out: dict[str, Any] = {"enabled": s.ollama_enabled, "url": s.ollama_url, "model": effective_model(db) or None,
                           "reachable": None, "models": [], "model_installed": None, "error": None}
    if not s.ollama_enabled:
        return out
    try:
        c = build_client(None)
        try:
            out["models"] = c.models()
        finally:
            c.close()
        out["reachable"] = True
        out["model_installed"] = out["model"] in out["models"] if out["model"] else None
    except OllamaError as exc:
        out["reachable"], out["error"] = False, str(exc)
    return out


class ModelBody(BaseModel):
    model: str = Field(min_length=1, max_length=100)


@router.put("/model")
def set_model(body: ModelBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _enabled()
    try:
        c = build_client(None)
        try:
            installed = c.models()
        finally:
            c.close()
    except OllamaError as exc:
        raise HTTPException(503, str(exc)) from None
    if body.model not in installed:
        raise HTTPException(422, f"'{body.model}' is not installed in Ollama. Installed: {', '.join(installed) or 'none'}")
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value={"model": body.model}, updated_by=admin.username))
    else:
        row.value, row.updated_by, row.updated_at = {"model": body.model}, admin.username, utcnow()
    audit(db, "ai_model", username=admin.username, target="ai", detail=f"model={body.model}", request=request)
    return {"model": body.model}


# ---- the four features -----------------------------------------------------------------------------
@router.post("/explain/client/{client_id}")
def explain_client(client_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    c = db.get(ClientRecord, client_id)
    if not c or c.site_id not in _site_ids(db, None):
        raise HTTPException(404, "Client not found")
    alias = context.Pseudonyms()
    result = client_score_result(db, c)
    facts = {"client": alias(c.mac), "ap": c.ap_name, "ssid": c.ssid, "score": result["score"], "band": result["band"],
             "inputs": result["inputs"], "unavailable": result["unavailable"], "partial": result["partial"],
             "not_measured": context.NOT_MEASURED}
    with _Session(db) as s:
        text = _chat(s, prompts.EXPLAIN.format(data=prompts.data_block(facts)))
        _audit(db, request, user, "explain_client", s.client.model or "")
        return _reply("explain", text, s.client.model or "", facts, c.simulated)


class SummaryBody(BaseModel):
    site_id: int | None = None
    hours: int = Field(24, ge=1, le=168)


@router.post("/summary")
def summary(body: SummaryBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    ids = _site_ids(db, body.site_id)
    facts = context.site_facts(db, ids, body.hours, context.Pseudonyms())
    with _Session(db) as s:
        text = _chat(s, prompts.SUMMARY.format(hours=body.hours, data=prompts.data_block(facts)), max_tokens=900)
        _audit(db, request, user, "summary", s.client.model or "")
        return _reply("summary", text, s.client.model or "", facts, facts["simulated"])


class RecommendBody(BaseModel):
    site_id: int | None = None


@router.post("/recommend")
def recommend(body: RecommendBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    ids = _site_ids(db, body.site_id)
    facts = context.site_facts(db, ids, 24, context.Pseudonyms())
    with _Session(db) as s:
        text = _chat(s, prompts.RECOMMEND.format(data=prompts.data_block(facts)), max_tokens=900)
        _audit(db, request, user, "recommend", s.client.model or "")
        return _reply("recommend", text, s.client.model or "", facts, facts["simulated"])


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    site_id: int | None = None


def _parse_tool_choice(raw: str) -> tuple[str, dict[str, Any]] | None:
    try:
        obj = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
        except ValueError:
            obj = None
    if not isinstance(obj, dict) or obj.get("tool") not in context.TOOLS:
        return None
    args = obj.get("args")
    return obj["tool"], args if isinstance(args, dict) else {}


@router.post("/ask")
def ask(body: AskBody, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    """Two steps: the model PICKS one allow-listed tool; SIMS runs it with validated arguments; the model words the answer."""
    ids = _site_ids(db, body.site_id)
    question = re.sub(r"[\x00-\x1f\x7f]+", " ", body.question).strip()
    alias = context.Pseudonyms()
    tools = "\n".join(f"- {n}: {d}" for n, (d, _, _) in context.TOOLS.items())
    with _Session(db) as s:
        raw = _chat(s, prompts.ROUTER.format(tools=tools, question=question), json_mode=True, max_tokens=120)
        choice = _parse_tool_choice(raw)
        if choice is None:
            _audit(db, request, user, "ask:no-tool", s.client.model or "")
            return _reply("ask", "I can't answer that from the data SIMS collects. I can answer questions about: "
                          + "; ".join(d.rstrip('.') for d, _, _ in context.TOOLS.values()) + ".",
                          s.client.model or "", {}, False, tool=None)
        name, args = choice
        try:
            facts = context.run_tool(db, ids, name, args, alias)
        except ValueError:  # the model produced out-of-range arguments: fall back to defaults, never to free-form access
            facts = context.run_tool(db, ids, name, {}, alias)
        facts["not_measured"] = context.NOT_MEASURED
        text = _chat(s, prompts.ANSWER.format(question=question, data=prompts.data_block(facts)))
        _audit(db, request, user, f"ask:{name}", s.client.model or "")
        return _reply("ask", text, s.client.model or "", facts, get_settings().mode == "demo", tool=name)
