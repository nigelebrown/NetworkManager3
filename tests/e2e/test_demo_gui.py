"""Current-design React browser smoke on CI's TLS-protected demo stack.

This complements (does not replace) the live-mode emulator suites in
tests/e2e/run_isolated.sh. No network management targets are contacted.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

URL = os.environ.get("INM_E2E_URL")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e"


def test_redesigned_dashboard_and_protected_workflow_navigation():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
        page = context.new_page()
        faults: list[str] = []
        page.on("pageerror", lambda e: faults.append(f"browser: {e}"))
        page.on("response", lambda r: faults.append(f"HTTP {r.status} {r.url}")
                if r.status >= 500 else None)

        page.goto(URL + "/login")
        pw = os.environ["INM_E2E_ADMIN_PASSWORD"]
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill(pw)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_timeout(1200)
        if page.url.endswith("/change-password"):
            # Also exercise the mandatory first-login password-change workflow
            # if a deployment enforces it. Never print the secret to CI logs.
            token = page.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
            next_pw = pw + "-Changed!"
            changed = page.request.post(URL + "/api/auth/change-password",
                headers={"Authorization": "Bearer " + token},
                data={"current_password": pw, "new_password": next_pw})
            assert changed.status == 200, f"First-login change rejected: {changed.status}"
            page.goto(URL + "/login")
            page.get_by_label("Username").fill("admin")
            page.get_by_label("Password").fill(next_pw)
            page.get_by_role("button", name="Sign in").click()
        try:
            page.get_by_role("heading", name="Network command center").wait_for(timeout=12000)
        except Exception as exc:
            page.screenshot(path=str(EVID / "login-diagnostic.png"), full_page=True)
            headings = page.locator("h1").all_inner_texts()
            visible = page.locator("body").inner_text()[:850]
            raise AssertionError(f"After login: url={page.url!r}; h1={headings!r}; "
                                 f"browser_errors={faults[:4]!r}; visible={visible!r}") from exc
        page.screenshot(path=str(EVID / "command-center.png"), full_page=True)

        screens = {
            "/inventory": "Device inventory",
            "/onboarding": "Add & configure devices",
            "/changes": "Configuration change workflow",
            "/system": "Platform health",
            "/platform": "Platform: vendors, roles & rules",
        }
        for route, heading in screens.items():
            page.goto(URL + route)
            page.get_by_role("heading", name=heading).first.wait_for(timeout=30000)
            page.screenshot(path=str(EVID / (route.lstrip("/") + ".png")), full_page=True)

        page.goto(URL + "/")
        page.get_by_role("heading", name="Network command center").wait_for()
        # UX approval: every wireframe navigation destination must render an
        # accessible page heading using the real React app. These screens may
        # show limited/CONCEPT status where corresponding services are not ready.
        wireframe_routes = [
            "/", "/executive", "/system", "/sites", "/inventory", "/aps",
            "/switches", "/clients", "/printers", "/onboarding", "/discovery",
            "/collection", "/rf-health", "/port-health", "/client-journeys",
            "/device-alerts", "/topology", "/logs", "/device-360",
            "/config-templates", "/ssid-policies", "/switch-config",
            "/changes", "/approvals", "/deployments", "/vault", "/replace",
            "/certification", "/optimization", "/recommendations",
            "/predictive", "/analytics", "/compliance", "/accountability",
            "/audit", "/users", "/credentials", "/notifications",
            "/firmware", "/integrations", "/data", "/platform",
        ]
        assert len(wireframe_routes) == 42
        for route in wireframe_routes:
            page.goto(URL + route)
            page.locator("#inm-main-content h1").first.wait_for(timeout=12000)
            assert page.locator("#inm-main-content").inner_text().strip(), route
        page.goto(URL + "/")
        page.get_by_role("heading", name="Network command center").wait_for()
        # The current printer overview links to the inventory workspace. Exercise
        # the durable discovery/approval UI with synthetic responses: no SNMP
        # packets are sent, and the fixture is never approved on the real API.
        page.route("**/api/printers/credential-profiles", lambda route: route.fulfill(json=[
            {"id": "ci-profile", "name": "CI printers", "version": "2c"},
        ]))
        job_printer = {
            "management_ip": "192.0.2.21", "hostname": "CI Discovery Printer",
            "manufacturer": "HP", "model": "CI Printer", "serial_number": "CI-DISCOVERY-001",
            "confidence": "HIGH", "profile": "ci-profile", "existing_printer_id": None,
        }
        discovery_body = {"credential_profile_id": "ci-profile", "management_range": "192.0.2.20/30"}
        job = {
            "id": "ci-discovery", "kind": "DISCOVERY", "status": "SUCCEEDED",
            "params": discovery_body, "progress": 2, "total": 2,
            "result": {"printers": [job_printer]}, "errors": [], "warnings": [],
            "requested_by": "admin",
        }
        calls = []

        def start_discovery(route):
            calls.append(("discover", route.request.method, route.request.post_data_json))
            route.fulfill(json=job)

        def approve_discovery(route):
            calls.append(("approve", route.request.method, route.request.post_data_json))
            route.fulfill(json={"created": ["ci-printer"], "updated": []})

        page.route("**/api/printers/discovery", start_discovery)
        page.route("**/api/printers/jobs/ci-discovery", lambda route: route.fulfill(json=job))
        page.route("**/api/printers/discovery/ci-discovery/approve", approve_discovery)
        page.goto(URL + "/printers")
        page.get_by_role("heading", name="Printer fleet overview").wait_for()
        page.get_by_role("navigation", name="Printer workspaces").get_by_role("link", name="Inventory").click()
        page.get_by_role("heading", name="Printer inventory").wait_for()
        page.get_by_label("Credential profile", exact=True).select_option("ci-profile")
        page.get_by_label("CIDR (/22–/32) or IP list").fill("192.0.2.20/30")
        page.get_by_role("button", name="Start discovery").click()
        selection = page.get_by_label("Select 192.0.2.21")
        selection.wait_for()
        assert calls == [("discover", "POST", discovery_body)]
        approve = page.get_by_role("button", name="Approve selected for monitoring")
        assert approve.is_disabled()
        selection.check()
        approve.click()
        page.get_by_text("1 registered, 0 updated; monitoring enabled (read-only).", exact=True).wait_for()
        assert calls == [
            ("discover", "POST", discovery_body),
            ("approve", "POST", {"items": [{
                key: job_printer[key] for key in
                ("management_ip", "manufacturer", "model", "serial_number", "hostname")
            }]}),
        ]
        page.screenshot(path=str(EVID / "printer-discovery-approval.png"), full_page=True)

        # Preserve the real database registration check from the original smoke
        # test via the still-supported one-off discovery panel.
        page.get_by_text("Manual registration, credential profiles and one-off SNMP checks", exact=True).click()
        page.route("**/api/printers/credential-profiles/ci-profile/discover", lambda route: route.fulfill(json={
            "scanned": 2,
            "printers": [{"management_ip": "192.0.2.25", "hostname": "CI Test Printer",
                          "system_description": "HP CI Printer", "meter": None}],
        }))
        page.get_by_role("button", name="Discover printers", exact=True).click()
        panel = page.locator("#printer-discovery")
        panel.get_by_label("Management IPs or CIDR").fill("192.0.2.24/30")
        panel.get_by_label("Saved profile").select_option("ci-profile")
        panel.get_by_role("button", name="Discover", exact=True).click()
        panel.get_by_role("button", name="Review and register").click()
        panel.get_by_label("Model", exact=True).fill("CI Printer")
        panel.get_by_label("Serial number").fill("CI-PRINTER-001")
        panel.get_by_role("button", name="Register printer").click()
        page.get_by_text("Printer registered. You can assign its location in Printer inventory.", exact=True).wait_for()
        page.reload()
        page.get_by_text("CI-PRINTER-001", exact=True).first.wait_for()
        page.screenshot(path=str(EVID / "printer-discovery-registration.png"), full_page=True)

        page.goto(URL + "/")
        page.get_by_role("heading", name="Network command center").wait_for()
        # Verify design tokens from approved HTML: navy sidebar, light canvas.
        colors = page.evaluate("""() => ({
            sidebar: getComputedStyle(document.querySelector('.inm-sidebar')).backgroundColor,
            canvas: getComputedStyle(document.querySelector('.inm-body')).backgroundColor,
            cards: (() => {
                const element = document.querySelector('.inm-card');
                return element ? getComputedStyle(element).backgroundColor : null;
            })()
        })""")
        assert colors["sidebar"] == "rgb(16, 32, 57)", colors
        assert colors["canvas"] == "rgb(244, 247, 250)", colors
        if colors["cards"] is not None:
            assert colors["cards"] == "rgb(255, 255, 255)", colors
        token = page.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
        auth = {"Authorization": "Bearer " + token}
        # Preserve the original CI cross-restart acceptance check: import two
        # explicitly REAL manual records. This does NOT contact Aruba hardware.
        # A later CI step recreates the API and checks these exact records survive.
        for family, hostname, serial, model in (
            ("access_point", "CI-AP-535", "CI-SMOKE-AP535", "AP-535"),
            ("switch", "CI-CX6200F", "CI-SMOKE-CX6200", "CX 6200F 48G"),
        ):
            imported = page.request.post(URL + "/api/v1/quick-devices", headers=auth,
                data={"family": family, "site_name": "CI Live Inventory",
                      "hostname": hostname, "serial_number": serial, "model": model,
                      "firmware": "8.10.0.21" if family == "access_point" else "10.14.1020",
                      "status": "unknown", "metrics": {}})
            assert imported.status == 201, imported.text()
            assert imported.json()["source_type"] == "REAL"
        response = page.request.get(URL + "/api/v1/devices", headers=auth)
        assert response.status == 200, response.text()
        devices = response.json()
        assert isinstance(devices, list)
        assert {"CI-AP-535", "CI-CX6200F"} <= {
            d["hostname"] for d in devices if d.get("source_type") == "REAL"}
        assert not faults, faults

        page.get_by_role("button", name="Sign out").click()
        page.wait_for_url(re.compile(r".*/login$"))
        context.close()
        browser.close()
