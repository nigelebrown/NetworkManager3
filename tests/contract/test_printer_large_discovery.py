"""Large discovery contracts use emulated agents; no network sweep is performed."""
import asyncio
from ipaddress import IPv4Network

import pytest
from backend.database.session import session_scope
from backend.database.models_printer_srs import PrinterJob, PrinterMonitoring
from backend.printers import collector as C, service as S
from backend.printers.snmp import permitted_network
from tests.contract.test_printer_srs import env, agent, cred_profile


def test_network_permission_checks_the_entire_range():
    assert permitted_network(IPv4Network("10.2.0.0/23"), "10.2.0.0/24,10.2.1.0/24")
    assert not permitted_network(IPv4Network("10.2.0.0/22"), "10.2.0.0/24,10.2.3.0/24")
    assert not permitted_network(IPv4Network("10.128.0.0/9"), "10.192.57.0/24")


@pytest.mark.parametrize("cidr,cursor,expected", [
    ("10.128.0.0/9", 0, ["10.128.0.1", "10.128.0.2"]),
    ("10.128.0.0/9", 8388604, ["10.255.255.253", "10.255.255.254"]),
    ("10.1.0.0/31", 0, ["10.1.0.0", "10.1.0.1"]),
    ("10.1.0.9/32", 0, ["10.1.0.9"]),
])
def test_lazy_batch_boundaries(cidr, cursor, expected):
    assert S.discovery_batch({"network_cidr": cidr}, cursor, 2) == expected


def test_large_plan_does_not_start_scan_or_expand_hosts(env, monkeypatch):
    client, admin, _ = env
    monkeypatch.setattr("backend.api.printers_srs.permitted_network", lambda n: True)
    cid = cred_profile(client, admin)
    body = {"credential_profile_ids": [cid], "management_range": "10.128.0.0/9"}
    p = client.post("/api/printers/discovery/plan", headers=admin, json=body)
    assert p.status_code == 200, p.text
    assert p.json()["addresses"] == 8388606 and not p.json()["starts_scan"]
    assert client.get("/api/printers/discovery/jobs", headers=admin).json() == []
    j = client.post("/api/printers/discovery", headers=admin, json=body).json()
    assert j["status"] == "QUEUED" and j["total"] == 8388606
    assert "hosts" not in j["params"] and len(str(j["params"])) < 1500
    assert client.post(f"/api/printers/jobs/{j['id']}/pause", headers=admin).json()["status"] == "PAUSED"
    assert asyncio.run(S.tick_jobs(session_scope, "worker")) == 0
    assert client.post(f"/api/printers/jobs/{j['id']}/resume", headers=admin).json()["status"] == "QUEUED"
    assert client.post(f"/api/printers/jobs/{j['id']}/cancel", headers=admin).json()["status"] == "CANCELLED"


def test_batches_resume_with_replacement_worker_and_partial_results(env):
    client, admin, _ = env
    cid = cred_profile(client, admin)
    ips = [f"10.103.57.{i}" for i in range(1, 21)]
    for i, ip in enumerate(ips):
        agent(ip, serial=f"SER-{i}")
    j = client.post("/api/printers/discovery", headers=admin, json={
        "credential_profile_ids": [cid], "management_ips": ips, "concurrency": 1}).json()
    jid = j["id"]
    asyncio.run(S.run_job(session_scope, jid, "worker-one"))
    first = client.get(f"/api/printers/jobs/{jid}", headers=admin).json()
    assert first["status"] == "QUEUED" and first["progress"] == 16
    assert len(first["result"]["printers"]) == 16 and first["params"]["next_index"] == 16
    assert client.post(f"/api/printers/jobs/{jid}/pause", headers=admin).status_code == 200
    asyncio.run(S.run_job(session_scope, jid, "worker-two"))
    assert client.get(f"/api/printers/jobs/{jid}", headers=admin).json()["progress"] == 16
    assert client.post(f"/api/printers/jobs/{jid}/resume", headers=admin).status_code == 200
    asyncio.run(S.run_job(session_scope, jid, "worker-two"))
    final = client.get(f"/api/printers/jobs/{jid}", headers=admin).json()
    assert final["status"] == "SUCCEEDED" and final["progress"] == 20
    assert len({p["management_ip"] for p in final["result"]["printers"]}) == 20
    assert client.get("/api/printers/discovery/jobs", headers=admin).json()[0]["found"] == 20


def test_selected_profile_fallback_and_matched_monitoring_assignment(env, monkeypatch):
    client, admin, _ = env
    wrong, right = cred_profile(client, admin), cred_profile(client, admin, "2c")
    good = agent("10.103.58.1", serial="PROFILE-OK")
    calls, closed = [], []
    class Reader:
        def __init__(self, cid):
            self.cid = cid
        async def get(self, oid):
            return await good.get(oid)
        async def walk(self, *args, **kwargs):
            return await good.walk(*args, **kwargs)
        async def close(self):
            closed.append(self.cid)
    def reader(db, host, cid):
        calls.append(cid)
        if cid == wrong:
            class Bad(Reader):
                async def get(self, oid):
                    raise TimeoutError("no SNMP response")
            return Bad(cid)
        return Reader(cid)
    monkeypatch.setattr(S, "make_reader", reader)
    j = client.post("/api/printers/discovery", headers=admin, json={
        "credential_profile_ids": [wrong, right], "management_ips": ["10.103.58.1"]}).json()
    job = client.get(f"/api/printers/jobs/{j['id']}", headers=admin).json()
    p = job["result"]["printers"][0]
    assert p["credential_profile_id"] == right and calls == [wrong, right]
    assert closed == [wrong, right]
    r = client.post(f"/api/printers/discovery/{j['id']}/approve", headers=admin, json={"items": [{
        "management_ip": p["management_ip"], "manufacturer": p["manufacturer"],
        "model": p["model"], "serial_number": p["serial_number"]}]})
    assert r.status_code == 200, r.text
    with session_scope() as db:
        mon = db.get(PrinterMonitoring, r.json()["created"][0])
        assert mon.credential_profile_id == right


def test_profile_selection_and_range_limits_are_validated(env, monkeypatch):
    client, admin, _ = env
    cid = cred_profile(client, admin)
    one = cred_profile(client, admin, "1")
    for ids, net, status in [([], "10.128.0.0/9", 422),
                              ([cid], "10.0.0.0/8", 422),
                              ([cid, one], "10.128.0.0/9", 403)]:
        r = client.post("/api/printers/discovery/plan", headers=admin,
                        json={"credential_profile_ids": ids, "management_range": net})
        assert r.status_code == status, r.text
