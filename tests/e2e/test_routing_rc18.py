"""Browser regression for the React Router 6 -> 7 migration (rc18) against tests/e2e/ops_stack.py (DEMO data).

Covers authentication routes, protected routes, deep links (incl. lazy chunks), nested routes / view tabs, role-based
navigation, browser history, unknown routes, logout, session expiry, and that no browser error or HTTP 5xx occurs.
  INM_E2E_URL=http://127.0.0.1:8833 python -m pytest tests/e2e/test_routing_rc18.py
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/ops_stack.py)")
RUN = uuid.uuid4().hex[:6].lower()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


def _page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.faults = []
    pg.on("pageerror", lambda e: pg.faults.append(f"browser error: {e}"))
    pg.on("response", lambda r: pg.faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    return ctx, pg


def _login(pg, user, password):
    pg.get_by_label("Username").fill(user)
    pg.get_by_label("Password").fill(password)
    pg.get_by_role("button", name="Sign in").click()


def test_deep_link_requires_login_then_lands_and_lazy_routes_render(browser):
    ctx, pg = _page(browser)
    pg.goto(URL + "/printers/purchasing")                    # protected deep link, not signed in
    expect(pg).to_have_url(re.compile(r"/login$"))
    _login(pg, "admin", PW)
    expect(pg).to_have_url(re.compile(r"/$"))
    expect(pg.get_by_role("link", name="Network command center")).to_have_class(re.compile("active"))
    for path, heading in (("/printers/purchasing", "Supply purchasing"), ("/inventory", "Device inventory"),
                          ("/aps", None), ("/changes", None), ("/platform", None), ("/integrations", "Integrations")):
        pg.goto(URL + path)                                   # full page load = deep link
        expect(pg.locator("main#inm-main-content")).not_to_contain_text("Loading…", timeout=15000)
        if heading:
            expect(pg.get_by_role("heading", name=heading).first).to_be_visible()
        assert pg.locator("a.inm-navitem.active").count() == 1, path
    # nested route + view tabs: client-side navigation keeps the owning entry active
    pg.goto(URL + "/inventory")
    pg.get_by_role("navigation", name="Device inventory views").get_by_role("link", name="CX switches").click()
    expect(pg).to_have_url(re.compile(r"/switches$"))
    expect(pg.get_by_role("link", name="Device inventory")).to_have_class(re.compile("active"))
    # browser history
    pg.go_back()
    expect(pg).to_have_url(re.compile(r"/inventory$"))
    pg.go_forward()
    expect(pg).to_have_url(re.compile(r"/switches$"))
    # unknown route -> home, not a blank page
    pg.goto(URL + "/definitely/not/a/route")
    expect(pg).to_have_url(re.compile(r"/$"))
    assert pg.faults == [], pg.faults
    ctx.close()


def test_role_based_navigation_logout_and_session_expiry(browser):
    ctx, pg = _page(browser)
    pg.goto(URL + "/login")
    _login(pg, "admin", PW)
    expect(pg).to_have_url(re.compile(r"/$"))
    viewer = f"route.viewer{RUN}"
    created = pg.evaluate("""async ([u]) => {
        const s = JSON.parse(sessionStorage.getItem('sims_session'));
        const h = {'Content-Type': 'application/json', Authorization: 'Bearer ' + s.access_token};
        let r = await fetch('/api/v1/users', {method: 'POST', headers: h, body: JSON.stringify({username: u, role: 'viewer',
            temporary_password: 'Route-Temp-2026!x', site_scope: 'all'})});
        if (r.status !== 201) return r.status;
        const t = (await (await fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: u, password: 'Route-Temp-2026!x'})})).json()).access_token;
        r = await fetch('/api/auth/change-password', {method: 'POST', headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + t}, body: JSON.stringify({current_password: 'Route-Temp-2026!x',
            new_password: 'Route-Perm-2026!xy'})});
        return r.status; }""", [viewer])
    assert created == 200
    # logout clears the session; Back cannot show protected content
    pg.get_by_role("button", name="Sign out").click()
    expect(pg).to_have_url(re.compile(r"/login$"))
    pg.go_back()
    expect(pg).to_have_url(re.compile(r"/login$"))
    # viewer: admin-only entries and actions are not offered
    _login(pg, viewer, "Route-Perm-2026!xy")
    expect(pg).to_have_url(re.compile(r"/$"))
    nav = pg.get_by_role("navigation", name="Primary navigation")
    for hidden in ("Users & roles", "Credential profiles", "Add & configure devices", "Data management", "Live collection"):
        expect(nav.get_by_role("link", name=re.compile("^" + re.escape(hidden)))).to_have_count(0)
    expect(nav.get_by_role("link", name="Device inventory")).to_be_visible()
    pg.goto(URL + "/users")                                   # direct URL to an admin screen: server refuses data
    expect(pg.get_by_role("button", name=re.compile("Create user", re.I))).to_have_count(0)
    # session expiry: an expired token is treated as signed out on the next navigation
    pg.evaluate("""() => { const s = JSON.parse(sessionStorage.getItem('sims_session'));
        s.expires_at = new Date(Date.now() - 60000).toISOString(); sessionStorage.setItem('sims_session', JSON.stringify(s)); }""")
    pg.goto(URL + "/inventory")
    expect(pg).to_have_url(re.compile(r"/login$"))
    assert [f for f in pg.faults if "HTTP 5" in f or "browser error" in f] == [], pg.faults
    ctx.close()
