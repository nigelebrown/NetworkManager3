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
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill(os.environ["INM_E2E_ADMIN_PASSWORD"])
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("heading", name="Network command center").wait_for(timeout=30000)
        page.screenshot(path=str(EVID / "command-center.png"), full_page=True)

        screens = {
            "/inventory": "Device inventory",
            "/onboarding": "Add & configure devices",
            "/changes": "Configuration change workflow",
            "/system": "System health",
            "/platform": "Platform: vendors, roles & rules",
        }
        for route, heading in screens.items():
            page.goto(URL + route)
            page.get_by_role("heading", name=heading).first.wait_for(timeout=30000)
            page.screenshot(path=str(EVID / (route.lstrip("/") + ".png")), full_page=True)

        page.goto(URL + "/")
        page.get_by_role("heading", name="Network command center").wait_for()
        token = page.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
        response = page.request.get(URL + "/api/v1/devices",
                                    headers={"Authorization": "Bearer " + token})
        assert response.status == 200, response.text()
        assert isinstance(response.json(), list)
        assert not faults, faults

        page.get_by_role("button", name="Sign out").click()
        page.wait_for_url(re.compile(r".*/login$"))
        context.close()
        browser.close()
