"""The uploaded SIMS Wi-Fi Manager's important routes/screens remain mounted.

Source comparator: SIMS-WiFi-Manager-main(1).zip.
This is a route-and-smoke contract, NOT hardware certification; the legacy
backend/tests suite and running browser E2E separately cover feature behaviour.
"""
from pathlib import Path


LEGACY_ROUTES = {
    ("POST", "/api/auth/login"), ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"), ("POST", "/api/auth/change-password"),
    ("GET", "/api/sites"), ("POST", "/api/sites"),
    ("POST", "/api/sites/{site_id}/test"),
    ("POST", "/api/sites/{site_id}/poll"),
    ("POST", "/api/sites/{site_id}/rest-api/check"),
    ("POST", "/api/sites/{site_id}/rest-api/enable"),
    ("GET", "/api/aps"), ("GET", "/api/aps/{ap_id}/history"),
    ("GET", "/api/clients"), ("GET", "/api/dashboard/summary"),
    ("GET", "/api/dashboard/history"), ("GET", "/api/alerts"),
    ("POST", "/api/alerts/{alert_id}/ack"),
    ("POST", "/api/alerts/{alert_id}/resolve"),
    ("GET", "/api/audit"),
    ("GET", "/api/experience/overview"),
    ("GET", "/api/experience/history"),
    ("GET", "/api/experience/clients/{client_id}"),
    ("GET", "/api/experience/events"),
    ("GET", "/api/experience/config"),
    ("GET", "/api/config/sites/{site_id}/ssids"),
    ("POST", "/api/config/sites/{site_id}/pull"),
    ("GET", "/api/config/sites/{site_id}/backups"),
    ("GET", "/api/config/backups/{backup_id}"),
    ("GET", "/api/config/backups/diff"),
    ("POST", "/api/config/sites/{site_id}/ssid/plan"),
    ("POST", "/api/config/sites/{site_id}/ap-rename/plan"),
    ("POST", "/api/config/changes/{change_id}/apply"),
    ("GET", "/api/firmware/compliance"), ("GET", "/api/firmware/images"),
    ("POST", "/api/firmware/sites/{site_id}/upgrade/plan"),
    ("POST", "/api/firmware/sites/{site_id}/certificate/plan"),
    ("GET", "/api/ai/status"), ("POST", "/api/ai/ask"),
    ("POST", "/api/ai/recommend"), ("POST", "/api/ai/summary"),
}

LEGACY_SCREENS = {
    "aps": "APs", "clients": "Clients", "experience": "Experience",
    "assistant": "Assistant", "config": "Configuration", "firmware": "Firmware",
    "sites": "Sites", "alerts": "Alerts",
}


def test_original_sims_routes_survive_integration(client):
    # Check the actual documented API from the running TestClient, not the
    # underlying transport/middleware's potentially wrapped .app.routes.
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    spec = response.json()
    routes = {(method.upper(), path) for path, details in spec["paths"].items()
              for method in details if method.lower() in ("get", "post", "put", "patch", "delete")}
    missing = LEGACY_ROUTES - routes
    assert not missing, f"Original SIMS API routes lost: {sorted(missing)}"


def test_original_sims_screens_survive_react_router_integration():
    root = Path(__file__).resolve().parents[2]
    router_source = (root / "frontend/src/App.tsx").read_text(encoding="utf-8")
    for path, component in LEGACY_SCREENS.items():
        assert f'path="{path}"' in router_source
        assert (root / f"frontend/src/pages/{component}.tsx").exists()


def test_original_dashboard_and_sites_remain_readable_in_demo(client, admin):
    from backend.monitoring.collector import poll_all
    poll_all()  # only local simulated fixture data; no outbound traffic in demo mode
    dashboard = client.get("/api/dashboard/summary", headers=admin)
    assert dashboard.status_code == 200, dashboard.text
    result = dashboard.json()
    assert result["mode"] == "demo" and result["simulated"] is True
    assert result["aps_total"] > 0
    sites = client.get("/api/sites", headers=admin)
    assert sites.status_code == 200
    assert sites.json() and all(site["simulated"] for site in sites.json())
    assert client.get("/api/clients", headers=admin).status_code == 200
    assert client.get("/api/alerts", headers=admin).status_code == 200
    assert client.get("/api/experience/overview", headers=admin).status_code == 200


def test_legacy_sims_hardware_write_paths_remain_locked(client, admin):
    response = client.post("/api/config/changes/1/apply", headers=admin,
                           json={"confirm_text": "DO NOT APPLY"})
    assert response.status_code in (403, 404), response.text
    response = client.post("/api/sites/1/rest-api/enable", headers=admin,
                           json={"host_key_sha256": "SHA256:mockhostkey123",
                                 "confirm_text": "NO WRITES"})
    assert response.status_code in (403, 404, 422), response.text
