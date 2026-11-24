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


def test_arm_ai_summary_omits_raw_configuration_and_prioritizes_verified_state():
    from backend.api.ap_operations import _arm_ai_facts
    facts={"arm":{"freshness":"FRESH","channels":[{"band":"5","channel":165,"util_percent":9,"quality_percent":99}],
                  "config":{"unused":"x"*100000},"history":["x"*100000]},
           "rule_findings":[{"severity":"info","title":"healthy","why":"keep ARM"}],"allowed_changes":[]}
    compact=_arm_ai_facts(facts)
    assert len(json.dumps(compact))<1200 and compact["radios"][0]["channel"]==165
    assert compact["findings"]==[] and compact["allowed_changes"]==[]

def test_arm_ai_does_not_present_stale_radios_as_current_measurements():
    from backend.api.ap_operations import _arm_ai_facts
    compact=_arm_ai_facts({"arm":{"freshness":"STALE","channels":[{"channel":165}]},
                          "allowed_changes":[{"to":36}]})
    assert compact["radios"]==[] and compact["allowed_changes"]==[]
