"""P10-03 (rc18 test for the rc17 read-only UniFi adapter): a UniFi AP is linked to its controller identity and its
controller-reported state is collected through the real collection pipeline, against an isolated emulated UniFi
Network controller (EMULATED; no network). Only POST /api/login and GET /api/s/{site}/stat/device are ever sent;
a rejected credential is AUTH_REJECTED (no retry storm), a serial missing from the controller is PARSE_ERROR, and
out-of-range or absent values are omitted, never defaulted."""
import json
from datetime import datetime, timedelta, timezone

import httpx

from backend.infrastructure.adapters import snmp_switch as SS


class Controller:
    def __init__(self):
        self.password = "controller-pass"
        self.requests: list[tuple[str, str]] = []
        self.devices = [{"serial": "U6PRO-0001", "model": "U6PRO", "version": "6.6.77", "state": 1, "num_sta": 14,
                         "system-stats": {"cpu": "12.5", "mem": "140"}},          # mem out of range -> omitted
                        {"serial": "OTHER-1", "model": "U6LITE", "state": 0}]

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/api/login":
            body = json.loads(req.content)
            ok = body.get("username") == "inm-ro" and body.get("password") == self.password
            return httpx.Response(200 if ok else 400, json={"meta": {"rc": "ok" if ok else "error"}, "data": []})
        if req.method == "GET" and req.url.path == "/api/s/default/stat/device":
            return httpx.Response(200, json={"meta": {"rc": "ok"}, "data": self.devices})
        return httpx.Response(404)


def test_unifi_ap_collected_read_only_through_the_controller(client, admin, monkeypatch):
    monkeypatch.setenv("INM_SNMP_EMULATION", "true")
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.78.0.0/24")
    from backend.infrastructure.runtime import telemetry_store
    telemetry_store().migrate()
    ctl = Controller()
    SS.EMULATORS["unifi:10.78.0.2"] = httpx.MockTransport(ctl.handler)
    try:
        did = client.post("/api/v1/quick-devices", headers=admin, json={
            "site_name": "Annex", "family": "access_point", "hostname": "u6-annex", "serial_number": "U6PRO-0001",
            "model": "U6-Pro", "management_ip": "10.78.0.2", "status": "unknown", "metrics": {}}).json()["id"]
        from backend.database.models_inm import InmDevice
        from backend.database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
        from backend.database.session import session_scope
        from backend.security.crypto import encrypt_secret
        with session_scope() as db:
            db.get(InmDevice, did).vendor = "Ubiquiti"
            p = OnboardingCredential(name="UniFi RO", device_family="access_point", protocol="unifi_controller",
                                     username="inm-ro", password_enc=encrypt_secret("controller-pass"), created_by="t")
            db.add(p)
            db.flush()
            db.add(OnboardingDeviceCredential(device_id=did, credential_id=p.id, bound_by="t"))
        plan = client.post("/api/v1/collection/plans", headers=admin, json={
            "device_id": did, "metric_families": ["system", "clients"], "interval_seconds": 300})
        assert plan.status_code == 201, plan.text
        assert plan.json()["adapter_key"] == "unifi_controller"
        from backend.infrastructure.collection_runner import run_due
        t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
        with session_scope() as db:
            s1 = run_due(db, now=t0, store=telemetry_store(), worker="t", plan_ids=[plan.json()["id"]])
        assert s1["succeeded"] == 1, s1
        series = client.get(f"/api/v1/devices/{did}/metrics/series?metric=clients&hours=2", headers=admin).json()
        assert series["points"][-1]["v"] == 14.0
        cpu = client.get(f"/api/v1/devices/{did}/metrics/series?metric=cpu_percent&hours=2", headers=admin).json()
        assert cpu["points"][-1]["v"] == 12.5
        mem = client.get(f"/api/v1/devices/{did}/metrics/series?metric=memory_percent&hours=2", headers=admin).json()
        assert not mem.get("points")                                       # 140 % is not a reading: omitted
        assert set(ctl.requests) == {("POST", "/api/login"), ("GET", "/api/s/default/stat/device")}
        # controller rejects the credential -> AUTH_REJECTED, no repeated logins
        ctl.password, ctl.requests = "rotated", []
        with session_scope() as db:
            s2 = run_due(db, now=t0 + timedelta(minutes=10), store=telemetry_store(), worker="t",
                         plan_ids=[plan.json()["id"]])
        assert s2["succeeded"] == 0 and ctl.requests == [("POST", "/api/login")]
        from backend.database.models_phases import CollectionPlan
        with session_scope() as db:
            assert db.get(CollectionPlan, plan.json()["id"]).last_failure_category == "AUTH_REJECTED"
            db.get(CollectionPlan, plan.json()["id"]).state = "ACTIVE"
        # the device is no longer in the controller's list -> PARSE_ERROR (identity never guessed)
        ctl.password, ctl.devices = "controller-pass", ctl.devices[1:]
        from backend.domain import collection as C
        from backend.infrastructure.collection_runner import CollectionFailure
        with session_scope() as db:
            dev, pl = db.get(InmDevice, did), db.get(CollectionPlan, plan.json()["id"])
            try:
                SS.unifi_controller_collector(dev, pl, db)
                raise AssertionError("expected PARSE_ERROR")
            except CollectionFailure as exc:
                assert exc.category == "PARSE_ERROR"
        assert C is not None
    finally:
        SS.EMULATORS.pop("unifi:10.78.0.2", None)
