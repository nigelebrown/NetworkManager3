from copy import deepcopy

from backend.infrastructure.poll_capacity import capacity_decision


RESOURCES = {"vm": {"logical_cpus": 8, "load_5m": 1, "memory_total": 16 * 1024**3,
                     "memory_available": 10 * 1024**3}}
DATABASE = {"status": "UP", "connections": 20, "connection_limit": 600, "lock_waits": 0}


def test_capacity_grows_gradually_to_catch_up_and_shrinks_when_idle():
    up, reason = capacity_decision(30, 150, 60, {"seconds": 70, "count": 60},
                                   RESOURCES, DATABASE, 100)
    assert up == 36 and "Increasing" in reason
    down, _ = capacity_decision(30, 0, 60, None, RESOURCES, DATABASE, 100)
    assert down == 24


def test_capacity_respects_cpu_memory_database_and_admin_bounds():
    for change in ({"load_5m": 9}, {"memory_available": 1024**3}):
        resources = deepcopy(RESOURCES)
        resources["vm"].update(change)
        assert capacity_decision(30, 150, 60, None, resources, DATABASE, 100)[0] < 30
    assert capacity_decision(30, 150, 60, None, RESOURCES,
                             {**DATABASE, "connections": 550}, 100)[0] < 30
    assert capacity_decision(30, 150, 60, None, RESOURCES,
                             {**DATABASE, "lock_waits": 1}, 100)[0] < 30
    assert capacity_decision(20, 150, 60, None, RESOURCES, DATABASE, 20)[0] <= 20
    resources = {**RESOURCES, "api_container": {"memory_limit": 1024**3, "memory_current": 950 * 1024**2}}
    assert capacity_decision(30, 150, 60, None, resources, DATABASE, 100)[0] < 30


def test_rate_limits_isolate_signed_users_behind_same_proxy(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from starlette.requests import HTTPConnection
    from backend.security import deps

    monkeypatch.setattr(deps, "api_limiter", deps.RateLimiter(limit=1, window_seconds=60))
    monkeypatch.setattr(deps, "decode_access_token", lambda token: {"sub": token} if token in ("alice", "bob") else None)
    def conn(token):
        return HTTPConnection({"type": "http", "client": ("127.0.0.1", 1234),
                               "headers": [(b"authorization", f"Bearer {token}".encode())]})
    deps.rate_limit_api(conn("alice"))
    deps.rate_limit_api(conn("bob"))
    with pytest.raises(HTTPException) as limited:
        deps.rate_limit_api(conn("alice"))
    assert limited.value.status_code == 429 and limited.value.headers["Retry-After"] == "60"
    deps.rate_limit_api(conn("invalid"))
    with pytest.raises(HTTPException):
        deps.rate_limit_api(conn("another-invalid"))


def test_missing_measurements_hold_capacity_and_fast_batches_reduce_it():
    assert capacity_decision(30, 150, 60, None, RESOURCES,
                             {"status": "UNAVAILABLE"}, 100)[0] == 30
    resources = deepcopy(RESOURCES)
    resources["vm"]["load_5m"] = None
    assert capacity_decision(30, 150, 60, None, resources, DATABASE, 100)[0] == 30
    assert capacity_decision(30, 26, 60, {"seconds": 10, "count": 26},
                             RESOURCES, DATABASE, 100)[0] == 24
