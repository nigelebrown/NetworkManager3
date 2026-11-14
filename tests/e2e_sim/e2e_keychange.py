import re, sys, time
sys.argv = [sys.argv[0], "none"]
sys.path.insert(0, "/home/claude/scripts")
import e2e
from e2e import B, API, H, Run, RESULTS, expect
import httpx, json
from playwright.sync_api import sync_playwright
e2e.ONLY = None
with sync_playwright() as pw:
    r = Run(pw); pg = r.pg; r.login()
    def keychange():
        r.go("/aps", 3)
        pg.get_by_role("button", name="Scan host keys").click()
        expect(pg.get_by_role("status").first).to_contain_text("CHANGED since approval", timeout=60000)
        expect(pg.get_by_text("KEY CHANGED — verify before approving")).to_be_visible()
        pg.locator("label", has_text="KEY CHANGED").locator("input").check()
        pg.get_by_role("button", name="Approve selected").click()
        expect(pg.get_by_role("status").first).to_contain_text("1 key(s) approved", timeout=15000)
        httpx.post(API + "/sites/poll-all", headers=H())
        for _ in range(40):
            time.sleep(3)
            f = httpx.get(API + "/v1/wireless/fleet", headers=H()).json()
            c = next(c for c in f["clusters"] if c["name"] == "Instant ICTD-Admin")
            if c["last_poll_ok"]:
                break
        r.go("/aps", 3)
        assert c["last_poll_ok"], c["last_poll_error"]
        return "changed key flagged, re-approved, cluster polling over SSH again"
    r.step("aps changed SSH host key is refused, flagged and re-approved", keychange)
    r.br.close()
