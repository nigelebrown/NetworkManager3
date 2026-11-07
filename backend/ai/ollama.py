"""Minimal Ollama client. Local-only by default: a non-local host is refused unless explicitly allowed.

Only /api/tags (list models) and /api/chat (non-streaming) are used. The model never gets tools or write access;
SIMS decides what data it sees (see context.py).
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import get_settings

log = logging.getLogger("sims.ai")
_TRANSPORT: httpx.BaseTransport | None = None  # tests inject a mock transport
MAX_REPLY_CHARS = 6000


class OllamaError(Exception):
    """Message is safe to show to the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def validate_url(url: str, allow_remote: bool) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise OllamaError("SIMS_OLLAMA_URL must look like http://127.0.0.1:11434")
    host = u.hostname
    local = host == "localhost"
    if not local:
        try:
            ip = ipaddress.ip_address(host)
            local = ip.is_loopback or ip.is_private
        except ValueError:
            local = False
    if not local and not allow_remote:
        raise OllamaError("Refusing to send data to a non-local Ollama host. If you really run Ollama on another "
                          "machine, set SIMS_OLLAMA_ALLOW_REMOTE=true.")
    return url.rstrip("/")


class OllamaClient:
    def __init__(self, base_url: str, model: str | None, timeout: float, transport: httpx.BaseTransport | None = None):
        self.model = model
        self._http = httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout, connect=5.0),
                                  transport=transport or _TRANSPORT)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.ConnectError:
            raise OllamaError(f"Cannot reach Ollama at {self._http.base_url}. Is it running? (start the Ollama app or "
                              "run `ollama serve`)") from None
        except httpx.TimeoutException:
            raise OllamaError("Ollama took too long to answer. A larger model on CPU can be slow; try a smaller model.") from None
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {type(exc).__name__}") from None
        if r.status_code == 404:
            raise OllamaError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}")
        if r.status_code != 200:
            raise OllamaError(f"Ollama returned HTTP {r.status_code}.", r.status_code)
        try:
            data = r.json()
        except ValueError:
            raise OllamaError("Ollama returned an unreadable response.") from None
        return data if isinstance(data, dict) else {}

    def models(self) -> list[str]:
        data = self._request("GET", "/api/tags")
        return sorted({m.get("name", "") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")})

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, max_tokens: int = 700) -> str:
        """One non-streaming chat call.

        "Thinking" (reasoning) models can spend the whole token budget on hidden reasoning and return an empty answer.
        So: ask for no thinking (`think: false`); if the model rejects that field, retry without it; and if the visible
        answer is still empty because thinking used the budget, retry once with a larger budget.
        """
        if not self.model:
            raise OllamaError("No model selected. An admin can choose one on the Assistant page.")
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "think": False,
                                "options": {"temperature": 0.2, "num_predict": max_tokens}}
        if json_mode:
            body["format"] = "json"

        def call() -> dict[str, Any]:
            try:
                return self._request("POST", "/api/chat", json=body)
            except OllamaError as exc:
                if exc.status == 400 and "think" in body:  # model does not accept the think option
                    del body["think"]
                    return self._request("POST", "/api/chat", json=body)
                raise

        data = call()
        msg = data.get("message") or {}
        text = msg.get("content")
        if (not isinstance(text, str) or not text.strip()) and msg.get("thinking"):
            body["options"]["num_predict"] = max_tokens * 4  # reasoning ate the budget: give it room, once
            data = call()
            text = (data.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise OllamaError("The model returned an empty answer. If it is a reasoning model, try a non-reasoning "
                              "model (for example an 'instruct' variant) on the Assistant page.")
        return text.strip()[:MAX_REPLY_CHARS]


def build_client(model: str | None = None) -> OllamaClient:
    s = get_settings()
    return OllamaClient(validate_url(s.ollama_url, s.ollama_allow_remote), model, s.ollama_timeout_seconds)
