"""rc18 (register P1-08 / P1-DEP-15): socket-level network trace of a demo-mode session.

Every outbound socket operation in the process (connect, connect_ex, sendto, getaddrinfo, create_connection) is
recorded while a demo session runs the main read and workflow paths (dashboard, inventory, alerts, demo collection,
analysis, predictive run, change planning on DEMO devices, printers, reports). The trace must contain no
non-loopback destination: demo mode never contacts a device or an external service."""
import ipaddress
import socket

from tests.conftest import login


def _is_local(host) -> bool:
    if host is None:
        return True
    host = host.decode() if isinstance(host, bytes) else str(host)
    if host in ("localhost", "testserver", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def test_demo_session_makes_no_non_loopback_network_calls(client, monkeypatch):
    trace: list[tuple[str, str]] = []
    real_connect, real_connect_ex, real_sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto
    real_gai, real_cc = socket.getaddrinfo, socket.create_connection

    def addr_host(addr):
        return addr[0] if isinstance(addr, tuple) else addr

    def connect(self, addr):
        trace.append(("connect", str(addr_host(addr))))
        if not _is_local(addr_host(addr)):
            raise OSError("blocked by demo network trace")
        return real_connect(self, addr)

    def connect_ex(self, addr):
        trace.append(("connect_ex", str(addr_host(addr))))
        if not _is_local(addr_host(addr)):
            return 111
        return real_connect_ex(self, addr)

    def sendto(self, data, *args):
        addr = args[-1]
        trace.append(("sendto", str(addr_host(addr))))
        if not _is_local(addr_host(addr)):
            raise OSError("blocked by demo network trace")
        return real_sendto(self, data, *args)

    def gai(host, *a, **k):
        trace.append(("getaddrinfo", str(host)))
        if not _is_local(host):
            raise socket.gaierror("blocked by demo network trace")
        return real_gai(host, *a, **k)

    def cc(addr, *a, **k):
        trace.append(("create_connection", str(addr[0])))
        if not _is_local(addr[0]):
            raise OSError("blocked by demo network trace")
        return real_cc(addr, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(socket, "create_connection", cc)

    h = login(client)
    assert client.post("/api/v1/demo-data/load", headers=h).status_code == 200
    devices = client.get("/api/v1/devices", headers=h).json()
    assert devices and all(d["source_type"] == "DEMO" for d in devices)
    for path in ("/api/v1/system/features", "/api/v1/devices", "/api/v1/alerts", "/api/sites", "/api/aps",
                 "/api/v1/intelligence/predictive", "/api/v1/intelligence/analytics", "/api/printers",
                 "/api/v1/collection/plans", "/api/v1/changes/plans", "/api/v1/integrations",
                 "/api/v1/ai/ml/status"):
        r = client.get(path, headers=h)
        assert r.status_code < 500, (path, r.status_code)
    client.post("/api/v1/intelligence/predictive/run", headers=h)
    client.post("/api/v1/ai/analyze", headers=h)
    sw = next(d for d in devices if d["family"] == "switch")
    plan = client.post("/api/v1/changes/plans", headers=h, json={"device_id": sw["id"], "title": "demo trace",
                                                                  "operations": [{"op": "switch.vlan.define", "value": 401}]})
    assert plan.status_code in (201, 409, 422)
    client.post("/api/v1/ai/ml/train", headers=h, json={"target": "PRED_CPU_BREACH", "synthetic": True})
    external = sorted({(op, host) for op, host in trace if not _is_local(host)})
    assert external == [], f"demo mode attempted external network access: {external}"
