"""E2E-07: durable /24 discovery — restart/crash recovery, cancel, pause/resume, retry, rate limit, no
duplicate assets, never stuck SCANNING."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.database.session import session_scope
from backend.infrastructure import discovery_jobs as DJ


@pytest.fixture
def scan(client, admin, monkeypatch):
    from backend.api import onboarding_workflow as flow
    prof = client.post("/api/v1/onboarding/credentials", headers=admin, json={
        "name": "scan", "device_family": "access_point", "protocol": "aruba_instant_rest",
        "username": "admin", "password": "lab-password-1"}).json()
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.110.0.0/24")
    calls = []

    def identify(profile_id, ip):
        calls.append(ip)
        last = int(ip.rsplit(".", 1)[-1])
        if last in (10, 20):
            return {"management_ip": ip, "status": "FOUND", "family": "access_point", "hostname": f"ap{last}",
                    "model": "AP-505", "serial_number": "SN-SAME" if last in (10, 20) and last == 20 else f"SN{last}",
                    "firmware": "8.10.0.9", "existing_device_id": None, "enrollable": True}
        if last == 30:
            return {"management_ip": ip, "status": "FOUND", "family": "access_point", "hostname": "vc-member",
                    "model": "AP-505", "serial_number": "SN-SAME", "firmware": "8.10.0.9", "enrollable": True}
        if last == 40:
            raise TimeoutError("host hung")
        return {"management_ip": ip, "status": "NO_RESPONSE", "enrollable": False}
    r = client.post("/api/v1/onboarding/discover", headers=admin, json={
        "credential_id": prof["id"], "scope": "10.110.0.0/24", "site_name": "HQ", "rate_per_s": 32})
    assert r.status_code == 201 and r.json()["status"] == "QUEUED"
    return r.json()["id"], identify, calls


def get(client, admin, jid):
    return client.get(f"/api/v1/onboarding/discoveries/{jid}", headers=admin).json()


def test_duplicate_request_returns_the_active_job(client, admin, scan):
    jid, _, _ = scan
    prof = get(client, admin, jid)["credential_id"]
    again = client.post("/api/v1/onboarding/discover", headers=admin, json={
        "credential_id": prof, "scope": "10.110.0.0/24", "site_name": "HQ"}).json()
    assert again["id"] == jid and again["duplicate_of_active"] is True


def test_worker_crash_mid_scan_is_resumed_from_cursor_by_another_worker(client, admin, scan):
    jid, identify, calls = scan
    # worker A processes 2 batches then "crashes" (time budget 0 after first batch -> hands back; simulate crash by
    # re-taking the lease and abandoning it with an unexpired lease)
    DJ.run_job(session_scope, jid, "worker-A", identify, time_budget_s=0)
    from backend.database.models_onboarding import OnboardingDiscovery
    with session_scope() as db:
        j = db.get(OnboardingDiscovery, jid)
        done = j.next_index
        assert j.status == "SCANNING" and 0 < done < 254
        j.lease_owner, j.lease_expires_at = "worker-A", datetime.now(timezone.utc) + timedelta(seconds=60)
    # worker B cannot steal a live lease
    assert DJ.tick(session_scope, "worker-B", identify) == {}
    # lease expires (worker A is dead) -> B resumes from the persisted cursor, not from the start
    with session_scope() as db:
        db.get(OnboardingDiscovery, jid).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    before = len(calls)
    assert DJ.tick(session_scope, "worker-B", identify) == {jid: "SCANNED"}
    assert len(calls) - before == 254 - done
    job = get(client, admin, jid)
    ips = [r["management_ip"] for r in job["results"]]
    assert len(ips) == len(set(ips))                               # nothing listed twice
    st = {r["management_ip"]: r["status"] for r in job["results"]}
    assert st["10.110.0.10"] == "FOUND" and st["10.110.0.40"] == "ERROR"
    # the same serial answering on two addresses is flagged, not offered twice for registration
    assert [r["status"] for r in job["results"] if r.get("serial_number") == "SN-SAME"].count("DUPLICATE") == 1
    assert job["progress"]["errors"] == 1


def test_pause_resume_cancel_retry(client, admin, scan):
    jid, identify, calls = scan
    url = f"/api/v1/onboarding/discoveries/{jid}/control"
    assert client.post(f"{url}/pause", headers=admin).json()["status"] == "PAUSED"
    assert DJ.tick(session_scope, "w", identify) == {} and calls == []        # paused jobs are not run
    assert client.post(f"{url}/pause", headers=admin).status_code == 409
    assert client.post(f"{url}/resume", headers=admin).json()["status"] == "QUEUED"
    DJ.run_job(session_scope, jid, "w", identify, time_budget_s=0)           # one batch
    assert client.post(f"{url}/cancel", headers=admin).json()["status"] == "CANCELLED"
    n = len(calls)
    assert DJ.tick(session_scope, "w", identify) == {} and len(calls) == n    # cancelled: nothing more
    assert client.post(f"{url}/retry", headers=admin).json()["status"] == "QUEUED"
    assert DJ.tick(session_scope, "w", identify)[jid] == "SCANNED"
    assert client.post(f"{url}/bogus", headers=admin).status_code == 409


def test_job_never_stays_scanning_forever(client, admin, scan):
    jid, identify, _ = scan
    from backend.database.models_onboarding import OnboardingDiscovery
    with session_scope() as db:
        j = db.get(OnboardingDiscovery, jid)
        j.status, j.attempts = "SCANNING", DJ.MAX_ATTEMPTS
        j.lease_owner, j.lease_expires_at = "dead", datetime.now(timezone.utc) - timedelta(seconds=5)
    assert DJ.tick(session_scope, "w", identify) == {jid: "FAILED"}
    job = get(client, admin, jid)
    assert job["status"] == "FAILED" and "Retry to resume" in job["results"][-1]["result_note"]


def test_rate_limit_spaces_batches(client, admin, scan):
    jid, identify, _ = scan
    sleeps = []
    from backend.api import onboarding_workflow  # noqa: F401
    from backend.database.models_onboarding import OnboardingDiscovery
    with session_scope() as db:
        db.get(OnboardingDiscovery, jid).rate_per_s = 64
    DJ.tick(session_scope, "w", identify, sleep=sleeps.append)
    assert len(sleeps) == 254 // 64            # 4 batches of <=64 hosts, a pause between each (not after the last)
