import os
import sys
from pathlib import Path

# Make `backend` importable when running `pytest` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SIMS_JWT_SECRET", "t" * 48)
os.environ.setdefault("SIMS_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SIMS_ADMIN_PASSWORD", "correct-horse-battery")
os.environ["SIMS_DISABLE_SCHEDULER"] = "true"
# This suite regresses the legacy SIMS device protocol paths; the two-person approval now required in front
# of them is covered by backend/tests/test_unified_write_approval.py.
os.environ["SIMS_REQUIRE_INDEPENDENT_APPROVAL"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.security.deps import api_limiter, login_limiter  # noqa: E402


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Fresh DB + settings per test. Mode defaults to demo."""
    monkeypatch.setenv("SIMS_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SIMS_MODE", "demo")
    get_settings.cache_clear()
    login_limiter.reset()
    api_limiter.reset()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def client(app_env):
    from backend.main import create_app
    with TestClient(create_app()) as c:
        yield c


def login(client: TestClient, username="admin", password="correct-horse-battery") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return login(client)


# ---- canned Aruba responses (shapes pending verification against a real AP-505) ----------
AP_TABLE = """2 Access Points
---------------
Name        IP Address    Mode    Spectrum  Clients  Type  Serial #   Radio 0 Channel  Radio 1 Channel  Uptime
lab-ap-1    10.190.2.71   access  disable   2        505   CNAB12345  36E              6                1d:2h:3m:4s
lab-ap-2    10.190.2.72   access  disable   0        505   CNAB67890  149+             11               45m:10s
"""

CLIENT_TABLE = """Client List
-----------
Name     IP Address    MAC Address        OS       ESSID      Access Point  Channel  Type  Role  Signal    Speed (mbps)
laptop1  10.190.20.5   AA:BB:CC:00:11:22  Windows  Corp-WiFi  lab-ap-1      36E      AX    Corp  42(good)  866(good)
phone1   10.190.20.6   aa:bb:cc:00:11:33  iOS      Corp-WiFi  lab-ap-1      6        N     Corp  12(poor)  24(poor)
"""

VERSION_TEXT = """Aruba Operating System Software.
ArubaOS (MODEL: 505), Version 8.10.0.21-8.10.0.21
Website: http://www.arubanetworks.com
"""


def aruba_handler(*, fail_login=False, expire_first_sid=False, ap_table=AP_TABLE):
    calls = {"login": 0, "show": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/login":
            calls["login"] += 1
            if fail_login:
                return httpx.Response(401, json={"Status": "Failure", "Message": "bad credentials"})
            return httpx.Response(200, json={"Status": "Success", "Status-code": 0, "sid": f"sid-{calls['login']}"})
        if path == "/rest/logout":
            return httpx.Response(200, json={"Status": "Success"})
        if path == "/rest/show-cmd":
            calls["show"] += 1
            if expire_first_sid and request.url.params.get("sid") == "sid-1":
                return httpx.Response(401, json={"Status": "Failure", "Message": "invalid sid"})
            cmd = request.url.params.get("cmd")
            text = {"show aps": ap_table, "show clients": CLIENT_TABLE, "show version": VERSION_TEXT}.get(cmd)
            if text is None:
                return httpx.Response(200, json={"Status": "Failure", "Message": "Command not supported"})
            return httpx.Response(200, json={"Status": "Success", "Status-code": 0, "Command": cmd,
                                             "Command output": text})
        return httpx.Response(404)

    handler.calls = calls
    return handler
