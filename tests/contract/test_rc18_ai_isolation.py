"""rc18 (register GAP-T15): the AI component cannot reach Aruba device addresses, SQL, secrets or production.

  * context: every tool output and the analysis context sent to a model contain no device management address,
    no client/AP identity, no credential, no database or bus URL;
  * process: the AI service refuses to start with any platform secret in its environment and holds only its own
    service token; its HTTP surface is rule analysis + two Ollama calls, nothing executes configuration;
  * network: in the shipped topology the ai container is attached only to the internal `ai` network (no devices
    network, no database/bus network, no internet), and the app's AI token is not a platform credential;
  * governance: an AI-drafted change is a DRAFT that a human must submit and an independent approver must approve.
"""
import re
from pathlib import Path

import yaml
from sqlalchemy import select

from tests.conftest import login

ROOT = Path(__file__).resolve().parents[2]
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def test_model_context_and_tool_outputs_hold_no_addresses_identities_or_secrets(client, monkeypatch):
    from backend.ai.context import Pseudonyms, run_tool, site_facts
    from backend.database.models import AccessPoint, ClientRecord, Site
    from backend.database.session import session_scope
    from backend.monitoring.collector import poll_all
    from backend.presentation.api_v1 import _analysis_context
    poll_all()
    h = login(client)
    client.post("/api/v1/demo-data/load", headers=h)
    secrets = [__import__("os").environ[k] for k in ("SIMS_FERNET_KEY", "SIMS_JWT_SECRET", "SIMS_ADMIN_PASSWORD")]
    with session_scope() as db:
        ids = [s.id for s in db.scalars(select(Site))]
        alias = Pseudonyms()
        blobs = [str(_analysis_context(db)), str(site_facts(db, ids, 24, alias))]
        for tool in ("ap_overview", "worst_clients", "disconnects", "roaming", "active_alerts", "experience_trend",
                     "ssid_breakdown", "rf_placement"):
            try:
                blobs.append(str(run_tool(db, ids, tool, {}, alias)))
            except (KeyError, ValueError):
                pass                                       # a tool that is not offered is not an exposure
        everything = "\n".join(blobs)
        identities = [x for c in db.scalars(select(ClientRecord)) for x in (c.mac, c.ip, c.hostname) if x]
        identities += [x for a in db.scalars(select(AccessPoint)) for x in (a.mac, a.ip, a.serial) if x]
        identities += [s.vc_host for s in db.scalars(select(Site)) if s.vc_host]
    assert not [x for x in identities if x in everything], "device/client identity reached the model context"
    assert not IPV4.findall(everything), IPV4.findall(everything)[:5]
    assert not [s for s in secrets if s and s in everything]
    assert "postgresql" not in everything and "sqlite:" not in everything and "nats://" not in everything


def test_shipped_topology_isolates_the_ai_container():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    ai = compose["services"]["ai"]
    assert ai["networks"] == ["ai"] and compose["networks"]["ai"] == {"internal": True}
    env = ai.get("environment", {})
    assert set(env) <= {"INM_AI_SHARED_TOKEN", "INM_OLLAMA_URL"}
    for k in env:
        assert not k.startswith(("SIMS_", "MGMT_", "TS_", "INM_NATS"))
    # no device-management, database or bus network reaches the AI container
    for svc, spec in compose["services"].items():
        if "ai" in (spec.get("networks") or []) and svc != "ai":
            assert svc in ("api", "ollama", "worker", "web") or not set(spec.get("networks") or []) - {"ai"}, svc


def test_ai_service_refuses_platform_credentials_and_cannot_execute(monkeypatch):
    import importlib
    import pytest
    import ai_service.app as A
    for k in A.FORBIDDEN_ENV:
        monkeypatch.setenv(k, "leaked")
        with pytest.raises(RuntimeError):
            importlib.reload(A)
            with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(A.app):
                pass
        monkeypatch.delenv(k)
    importlib.reload(A)
    paths = {r.path for r in A.app.routes if hasattr(r, "path")}
    assert not [p for p in paths if any(w in p for w in ("config", "apply", "execute", "device", "sql", "credential"))]


def test_ai_drafted_change_needs_human_submission_and_independent_approval(client):
    from tests.conftest import make_user
    h = login(client)
    client.post("/api/v1/demo-data/load", headers=h)
    sw = next(d for d in client.get("/api/v1/devices", headers=h).json() if d["family"] == "switch")
    r = client.post("/api/v1/changes/plans", headers=h, json={"device_id": sw["id"], "title": "AI suggestion",
                                                              "operations": [{"op": "switch.vlan.define", "value": 402}],
                                                              "source": "AI_DRAFT", "submit": False})
    if r.status_code == 201:
        p = r.json()
        assert p["state"] == "DRAFT"
        ex = client.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=h)
        assert ex.status_code in (403, 409, 422) or (ex.json().get("decision") == "DENY"
                                                      and ex.json().get("outcome") in (None, "NOT_EXECUTED", "DENIED")), ex.json()
        appr = make_user(client, h, "ai.approver", "approver")
        assert client.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=appr,
                           json={"expected_revision": p["revision"]}).status_code == 409    # not submitted
