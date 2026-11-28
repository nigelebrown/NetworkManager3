"""Visit every menu entry and every view tab; check (1) sidebar never overlaps content/topbar/banners,
(2) the active menu entry is unique, (3) no two menu entries point to the same route, (4) every view tab renders.
Screenshots at 1440 and 1180 px wide plus a mobile pass."""
import json, os, re, sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1]; PW = sys.argv[2]; OUT = sys.argv[3]
res = {"overlaps": [], "multi_active": [], "errors": [], "visited": 0, "tabs": 0}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome"), args=["--no-sandbox"])
    for width in (1440, 1180):
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.goto(URL + "/login"); pg.get_by_label("Username").fill("admin"); pg.get_by_label("Password").fill(PW)
        pg.get_by_role("button", name=re.compile("sign in", re.I)).click(); pg.wait_for_url(re.compile(r"/$"), timeout=20000)
        links = pg.eval_on_selector_all("nav[aria-label='Primary navigation'] a.inm-navitem", "els => els.map(e => e.getAttribute('href'))")
        dup = {h for h in links if links.count(h) > 1}
        if dup: res["errors"].append(f"duplicate menu targets {dup}")
        for href in links:
            pg.goto(URL + href); pg.wait_for_timeout(700)
            res["visited"] += 1
            act = pg.eval_on_selector_all("a.inm-navitem.active", "els => els.length")
            if act != 1: res["multi_active"].append((width, href, act))
            box = pg.evaluate("""() => { const r = e => e && e.getBoundingClientRect();
              const sb = r(document.querySelector('.inm-sidebar')), main = r(document.querySelector('.inm-body')),
                    top = r(document.querySelector('.inm-topbar .inm-breadcrumbs'));
              const banners = [...document.querySelectorAll('.inm-banner')].map(r);
              const hit = (a, c) => a && c && a.left < c.right - 1 && c.left < a.right - 1 && a.top < c.bottom - 1 && c.top < a.bottom - 1;
              return { main: hit(sb, main), top: hit(sb, top), banner: banners.some(x => hit(sb, x)),
                       hscroll: document.documentElement.scrollWidth > window.innerWidth + 2 }; }""")
            for k, v in box.items():
                if v: res["overlaps"].append((width, href, k))
            tabs = pg.eval_on_selector_all("nav.inm-viewtabs a", "els => els.map(e => e.getAttribute('href'))")
            for t in tabs:
                pg.goto(URL + t); pg.wait_for_timeout(500); res["tabs"] += 1
                if pg.eval_on_selector_all("a.inm-viewtab.active", "els => els.length") != 1:
                    res["errors"].append(f"tab {t} not uniquely active")
                if pg.eval_on_selector_all("a.inm-navitem.active", "els => els.length") != 1:
                    res["multi_active"].append((width, t, "tab"))
            if width == 1440 and href in ("/", "/inventory", "/device-alerts", "/changes", "/printers", "/clients"):
                pg.screenshot(path=f"{OUT}/nav-{href.strip('/').replace('/', '-') or 'home'}.png", full_page=False)
        pg.close()
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(URL + "/login"); m.get_by_label("Username").fill("admin"); m.get_by_label("Password").fill(PW)
    m.get_by_role("button", name=re.compile("sign in", re.I)).click(); m.wait_for_url(re.compile(r"/$"), timeout=20000)
    m.goto(URL + "/aps"); m.wait_for_timeout(800)
    sb = m.evaluate("() => document.querySelector('.inm-sidebar').getBoundingClientRect().right")
    if sb > 1: res["overlaps"].append((390, "/aps", "sidebar visible while closed"))
    m.screenshot(path=f"{OUT}/nav-mobile-aps.png")
    b.close()
print(json.dumps(res, indent=1))
