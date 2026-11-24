"""Local AI deadline and context limits must survive retries and remain explicit."""
import json
import httpx
from backend.ai.ollama import OllamaClient,OllamaError
import pytest

def test_arm_explanation_has_bounded_context_and_network_deadline():
    seen=[]
    def handle(request):
        seen.append(request)
        return httpx.Response(200,json={"message":{"content":"Native ARM is healthy; no change is justified."}})
    c=OllamaClient("http://127.0.0.1:11434","test",120,transport=httpx.MockTransport(handle))
    try:
        assert "healthy" in c.chat([{"role":"user","content":"Measured facts"}],max_tokens=100,
            context_window=3072,timeout_seconds=60,retry_empty=False)
    finally:c.close()
    body=json.loads(seen[0].content)
    assert body["options"]["num_predict"]==100 and body["options"]["num_ctx"]==3072
    assert seen[0].extensions["timeout"]["read"]==60

def test_arm_explanation_does_not_expand_a_hidden_thinking_budget():
    seen=[]
    def handle(request):
        seen.append(request)
        return httpx.Response(200,json={"message":{"content":"","thinking":"hidden"}})
    c=OllamaClient("http://127.0.0.1:11434","test",120,transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(OllamaError,match="empty answer"):
            c.chat([{"role":"user","content":"facts"}],max_tokens=100,timeout_seconds=60,retry_empty=False)
    finally:c.close()
    assert len(seen)==1
