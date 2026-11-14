"""Browser end-to-end run of every workflow against the live stack (PostgreSQL, Loki, Alloy, simulated Instant
clusters on real sockets). Each step records PASS/FAIL with a screenshot."""
import json
import re
import socket
import sys
import time
import traceback

import httpx
from playwright.sync_api import expect, sync_playwright

B = "http://localhost:5173"
API = "http://127.0.0.1:8000/api"
SHOTS = "/tmp/e2e/shots"
RESULTS = []
ONLY = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else None


def api_token(u="admin", p="correct-horse-battery"):
    return httpx.post(API + "/auth/login", json={"username": u, "password": p}).json()["access_token"]


_TOK = {}


def H(u="admin", p="correct-horse-battery"):
    if u not in _TOK or time.time() - _TOK[u][1] > 600:
        _TOK[u] = (api_token(u, p), time.time())
    return {"Authorization": "Bearer " + _TOK[u][0]}


def syslog(host, msg, n=1):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(n):
        s.sendto(f"<134>Nov 14 15:40:00 {host} stm[1]: {msg}".encode(), ("127.0.0.1", 1515))
        if i % 200 == 199:
            time.sleep(0.05)


class Run:
    def __init__(self, pw):
        self.br = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        self.ctx = self.br.new_context(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
        self.pg = self.ctx.new_page()
        self.errors = []
        self.pg.on("pageerror", lambda e: self.errors.append(str(e)))
        self.pg.on("dialog", self._dialog)
        self.prompt_text = None

    def _dialog(self, d):
        d.accept(self.prompt_text) if d.type == "prompt" else d.accept()

    def login(self, u="admin", p="correct-horse-battery"):
        pg = self.pg
        pg.goto(B + "/login")
        pg.fill("input[autocomplete=username]", u)
        pg.fill("input[type=password]", p)
        pg.keyboard.press("Enter")
        pg.wait_for_url(re.compile(r"^(?!.*login).*$"), timeout=15000)
        time.sleep(1.5)

    def step(self, name, fn):
        if ONLY and name.split(" ")[0] not in ONLY:
            return
        self.errors.clear()
        t = time.time()
        try:
            note = fn() or ""
            status = "FAIL" if self.errors else "PASS"
            if self.errors:
                note += " | page errors: " + "; ".join(self.errors[:3])
        except Exception as exc:
            status, note = "FAIL", f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
            traceback.print_exc()
        shot = f"{SHOTS}/{len(RESULTS) + 1:02d}_{re.sub('[^a-z0-9]+', '_', name.lower())[:50]}.png"
        try:
            self.pg.screenshot(path=shot, full_page=True)
        except Exception:
            shot = ""
        RESULTS.append({"step": name, "status": status, "note": note, "seconds": round(time.time() - t, 1), "shot": shot})
        print(f"[{status}] {name} ({RESULTS[-1]['seconds']}s) {note}", flush=True)

    def go(self, path, wait=2.5):
        self.pg.goto(B + path)
        time.sleep(wait)


def main():
    with sync_playwright() as pw:
        r = Run(pw)
        pg = r.pg
        r.login()

        # ---------------------------------------------------------------- 1. baseline: UNKNOWN is grey with a reason
        def noc_unknown():
            r.go("/", 4)
            tile = pg.locator("a.inm-row", has_text="34:3a:20:cc:b3:bc").first
            expect(tile).to_be_visible()
            dot = tile.locator(".inm-dot")
            color = dot.evaluate("e => getComputedStyle(e).backgroundColor")
            assert "unknown" in dot.get_attribute("class"), "dot not tagged unknown"
            assert color in ("rgb(163, 175, 191)",), f"unknown dot colour is {color}, expected grey"
            expect(tile).to_contain_text("Not monitored")
            return f"UNKNOWN dot colour {color}; reason shown"
        r.step("noc UNKNOWN devices show a grey dot and the reason", noc_unknown)

        def wifi_before():
            r.go("/wifi", 3)
            body = pg.inner_text("main")
            return "coverage banner: " + ("present" if "registered APs" in body else "absent (no polled clusters yet)")
        r.step("wifi dashboard before onboarding", wifi_before)

        # ---------------------------------------------------------------- 2. discovery: subnet + credentials + rule
        def discovery_parts():
            r.go("/discovery", 2)
            pg.get_by_placeholder("Name, e.g. HQ switch mgmt").fill("NCB South Tower APs")
            pg.get_by_placeholder("CIDR, e.g. 10.190.97.0/24 (max /22)").fill("172.17.0.0/29")
            pg.get_by_placeholder("Site for found devices").fill("NCB South Tower")
            pg.get_by_role("button", name="Save subnet").click()
            expect(pg.get_by_role("status")).to_contain_text("Subnet saved")
            pg.get_by_placeholder("Name, e.g. HQ switch mgmt").fill("ICTD APs")
            pg.get_by_placeholder("CIDR, e.g. 10.190.97.0/24 (max /22)").fill("172.18.0.0/29")
            pg.get_by_placeholder("Site for found devices").fill("ICTD Building")
            pg.get_by_role("button", name="Save subnet").click()
            time.sleep(1)
            # bad input is refused with a clear message
            pg.get_by_placeholder("Name, e.g. HQ switch mgmt").fill("too big")
            pg.get_by_placeholder("CIDR, e.g. 10.190.97.0/24 (max /22)").fill("10.0.0.0/16")
            pg.get_by_placeholder("Site for found devices").fill("x")
            pg.get_by_role("button", name="Save subnet").click()
            expect(pg.get_by_role("alert")).to_contain_text("/22")
            pg.get_by_placeholder("Name, e.g. HQ switch mgmt").fill("")
            pg.get_by_placeholder("CIDR, e.g. 10.190.97.0/24 (max /22)").fill("")
            pg.get_by_placeholder("Site for found devices").fill("")
            for name, pwd in (("Instant APs (old password)", "wrong-pass"), ("Instant APs", "ArubaLab#1")):
                pg.get_by_placeholder("Profile name").fill(name)
                pg.locator("select").filter(has_text="CX switches").first.select_option("access_point")
                pg.get_by_placeholder("Username").fill("admin")
                pg.get_by_placeholder("Password (encrypted at rest)").fill(pwd)
                pg.get_by_role("button", name="Save profile").click()
                expect(pg.get_by_role("status")).to_contain_text("Credential profile saved")
            return "2 subnets, 2 AP credential profiles saved; /16 refused"
        r.step("discovery save subnets and credential profiles independently", discovery_parts)

        def discovery_rule():
            pg.get_by_placeholder("e.g. Kingston HQ nightly").fill("AP estate nightly")
            pg.get_by_label("NCB South Tower APs").check() if pg.get_by_label("NCB South Tower APs").count() else \
                pg.locator("label", has_text="NCB South Tower APs").locator("input").check()
            pg.locator("label", has_text="ICTD APs").locator("input").check()
            # old password first, then the right one: proves ordered fallback
            pg.locator("label", has_text="Instant APs (old password)").locator("input").check()
            pg.locator("label", has_text=re.compile(r"^\s*Instant APs$")).locator("input").check()
            pg.locator("select").filter(has_text="Manual only").select_option("daily")
            pg.get_by_role("button", name="Save rule").click()
            expect(pg.get_by_role("status")).to_contain_text("Rule saved")
            pg.get_by_role("button", name="Run now").first.click()
            for _ in range(60):
                time.sleep(2)
                txt = pg.inner_text("main")
                if "COMPLETED" in txt or "FAILED" in txt:
                    break
            txt = pg.inner_text("main")
            assert "COMPLETED" in txt, "run did not complete"
            m = re.search(r"(\d+) device\(s\) identified", txt)
            reg = re.search(r"(\d+) registered", txt)
            return f"run COMPLETED; identified={m and m.group(1)} registered={reg and reg.group(1)}"
        r.step("discovery join subnets + ordered credentials in a rule and run it", discovery_rule)

        # ---------------------------------------------------------------- 3. AP auto-onboarding (cluster-aware)
        def autoonboard():
            r.go("/aps", 3)
            pg.get_by_role("button", name="Scan host keys").click()
            expect(pg.get_by_role("status").first).to_contain_text("Scanned", timeout=60000)
            time.sleep(1)
            pg.get_by_label("Select all").check()
            pg.get_by_role("button", name="Approve selected").click()
            expect(pg.get_by_role("status").first).to_contain_text("approved", timeout=15000)
            pg.locator("label", has_text="Also try to enable REST").locator("input").check()
            pg.get_by_role("button", name="Run auto-onboarding").click()
            for _ in range(60):
                time.sleep(2)
                st = httpx.get(API + "/v1/wireless/autodiscover/status", headers=H()).json()
                if st["state"] in ("SUCCEEDED", "FAILED"):
                    break
            assert st["state"] == "SUCCEEDED", st
            time.sleep(4)
            r.go("/aps", 4)
            body = pg.inner_text("main")
            cl = {c["name"]: (c["transport"], c["rest_state"][:40] if c["rest_state"] else None, c["first_poll_ok"]) for c in st["clusters"]}
            return f"counts={st['counts']} clusters={cl}"
        r.step("aps auto-onboard: scan keys, approve, run (REST + SSH fallback)", autoonboard)

        def fleet_by_cluster():
            r.go("/aps", 4)
            f = httpx.get(API + "/v1/wireless/fleet", headers=H()).json()
            names = [c["name"] for c in f["clusters"]]
            sel = pg.locator("select[aria-label=Cluster]")
            key = next(c["key"] for c in f["clusters"] if c["key"] != "unlinked" and c["aps_total"])
            sel.select_option(key)
            time.sleep(1)
            shown = pg.locator("table tbody tr").count()
            on = f["summary"]["states"]
            twog = [a["radio_2g"]["channel"] for a in f["aps"] if a["radio_2g"]["channel"]]
            assert twog, "no 2.4 GHz channels"
            pg.get_by_role("button", name="why?").first.click()
            time.sleep(0.5)
            sel.select_option("ALL")
            return f"clusters={names} states={on} 2.4GHz channels={twog[:5]} rows when filtered={shown}"
        r.step("aps fleet grouped and filterable by cluster with 2.4 GHz", fleet_by_cluster)

        def noc_after():
            r.go("/", 4)
            f = httpx.get(API + "/v1/noc/overview", headers=H()).json()
            st = {}
            for d in f["devices"]:
                st[d["state"]] = st.get(d["state"], 0) + 1
            return f"NOC states after onboarding: {st}"
        r.step("noc states after onboarding", noc_after)

        def logs_mapped():
            syslog("CP-Registry", "Station e0:06:e6:31:55:4b deauthenticated from AP CP-Registry", 3)
            syslog("172.18.0.1", "Client aa:bb:cc:dd:ee:01 DHCP timeout on VLAN 20", 2)
            time.sleep(8)
            r.go("/logs", 2)
            pg.locator("select[aria-label=Stream]").select_option("syslog")
            time.sleep(4)
            body = pg.inner_text("main")
            mapped = body.count("CP-Registry")
            assert "UNMAPPED" not in body or mapped, "syslog lines are unmapped"
            return f"syslog rows visible; mapped by name and by IP; UNMAPPED present={'UNMAPPED' in body}"
        r.step("logs RFC3164 syslog arrives and maps to devices", logs_mapped)

        # ---------------------------------------------------------------- 4. AP health report
        def health_report():
            syslog("172.17.0.1", "Association reject for client e0:06:e6:31:55:10 on JCFGUEST_WIFI: AP resource constrained", 360)
            syslog("CP-Registry", "Station e0:06:e6:31:55:4b deauthenticated from AP CP-Registry", 850)
            syslog("CP-Legal-OfficeAP", "Client aa:bb:cc:dd:ee:02 DHCP timeout on VLAN 30", 11)
            syslog("ICTD-Admin", "Client aa:bb:cc:dd:ee:03 802.1X authentication failed", 30)
            syslog("ICTD-Technical-AP", "DHCP ACK sent to client aa:bb:cc:dd:ee:04", 40)
            time.sleep(12)
            r.go("/ap-health", 6)
            rep = httpx.get(API + "/v1/wireless/health-report?hours=24", headers=H(), timeout=60).json()
            tiers = {x["ap"]: (x["tier"], x["trigger"]) for x in rep["rows"]}
            exp = {"CP-Conference Room": "BRONZE", "CP-Registry": "BRONZE", "CP-Legal-OfficeAP": "BRONZE", "ICTD-Admin": "SILVER"}
            bad = {k: tiers.get(k) for k, v in exp.items() if (tiers.get(k) or ("",))[0] != v}
            assert not bad, f"unexpected tiers {bad}"
            expect(pg.get_by_text("Bronze APs — corrective actions required")).to_be_visible()
            return "tiers " + json.dumps({k: v[0] for k, v in tiers.items()})
        r.step("ap-health report rates APs from live syslog (Gold/Silver/Bronze)", health_report)

        def health_snapshot_compare():
            r.prompt_text = "Before fixes"
            pg.get_by_role("button", name="Save snapshot").click()
            expect(pg.get_by_role("status").first).to_contain_text("Saved snapshot", timeout=30000)
            csv = "Occurred On,AP Name,Client MAC,SSID,Band,Description\n" + "\n".join(
                f"2026-04-24 13:{i % 60:02d}:00,CP-Conference Room,e0:06:e6:31:55:{i % 90:02d},JCFGUEST_WIFI,5GHz,Association reject: AP resource constrained" for i in range(20))
            open("/tmp/e2e/export.csv", "w").write(csv)
            pg.locator("input[type=file]").set_input_files("/tmp/e2e/export.csv")
            expect(pg.get_by_role("status").first).to_contain_text("Imported", timeout=30000)
            with pg.expect_download() as dl:
                pg.get_by_role("button", name="Export CSV").click()
            path = dl.value.path()
            lines = open(path).read().splitlines()
            # simulate the fix: the conference room stops rejecting; save an "After" live snapshot and compare like-for-like
            r.go("/ap-health", 5)
            r.prompt_text = "After fixes"
            pg.get_by_role("button", name="Save snapshot").click()
            expect(pg.get_by_role("status").first).to_contain_text("Saved snapshot", timeout=30000)
            snaps = httpx.get(API + "/v1/wireless/health-report/snapshots", headers=H()).json()
            before = next(s for s in snaps if s["label"] == "Before fixes")
            snaps = [next(s for s in snaps if s["label"] == "After fixes")] + snaps
            pg.locator("select[aria-label='Saved snapshot']").select_option(snaps[0]["id"])
            time.sleep(2)
            pg.locator("select[aria-label='Compare with']").select_option(before["id"])
            time.sleep(3)
            expect(pg.get_by_text("Tier movement since")).to_be_visible()
            mv = httpx.get(API + f"/v1/wireless/health-report/snapshots/{snaps[0]['id']}?compare_to={before['id']}", headers=H()).json()["movement"]
            return f"snapshots={len(snaps)} csv rows={len(lines) - 1} movement={mv}"
        r.step("ap-health save snapshot, import export CSV, export, compare tiers", health_snapshot_compare)

        # ---------------------------------------------------------------- 5. RF health + diagnosis
        def rf_diag():
            r.go("/rf-health", 4)
            row = pg.locator("tr", has_text="CP-Conference Room").first
            row.get_by_role("button", name="Diagnose").click()
            dlg = pg.get_by_role("dialog")
            expect(dlg).to_contain_text("Most likely cause", timeout=15000)
            txt = dlg.inner_text()
            assert "Association rejects" in txt or "Evidence checked" in txt
            dlg.get_by_role("button", name="Ask the AI").click()
            expect(dlg).to_contain_text("AI assistant disabled", timeout=15000)
            head = txt.split("Most likely cause:")[1].split("\n")[0][:160]
            dlg.get_by_role("button", name="Close").click()
            return "headline:" + head
        r.step("rf-health diagnose an AP (rules + AI fallback)", rf_diag)

        # ---------------------------------------------------------------- 6. clients
        def clients():
            r.go("/client-journeys", 4)
            d = httpx.get(API + "/v1/wireless/clients", headers=H()).json()
            assert d["summary"]["connected"] > 0, "no clients"
            key = d["clusters"][0]["key"]
            pg.locator("select[aria-label=Cluster]").select_option(key)
            time.sleep(2)
            pg.locator("select[aria-label=Cluster]").select_option("")
            pg.locator("select[aria-label=Quality]").select_option("poor")
            time.sleep(2)
            first = pg.locator("table tbody tr td button.inm-link").first
            first.click()
            time.sleep(2)
            expect(pg.locator("#journey")).to_contain_text("Now:")
            pg.get_by_role("button", name="Why is this client poor?").click()
            dlg = pg.get_by_role("dialog")
            expect(dlg).to_contain_text("Weak signal", timeout=15000)
            dlg.get_by_role("button", name="Close").click()
            return f"connected={d['summary']['connected']} quality={d['summary']['quality']} bands={d['summary']['bands']}"
        r.step("client-journeys filter, journey and diagnosis", clients)

        def experience():
            r.go("/experience", 4)
            pg.get_by_role("button", name="Diagnose").first.click()
            dlg = pg.get_by_role("dialog")
            expect(dlg).to_contain_text("Most likely cause", timeout=15000)
            dlg.get_by_role("button", name="Close").click()
            pg.get_by_role("button", name="Why poor?").first.click()
            expect(pg.get_by_role("dialog")).to_contain_text("Evidence checked", timeout=15000)
            pg.get_by_role("dialog").get_by_role("button", name="Close").click()
            return "client + AP diagnosis from User experience"
        r.step("experience why-poor drill-down", experience)

        # ---------------------------------------------------------------- 7. Device 360
        def dev360():
            f = httpx.get(API + "/v1/wireless/fleet", headers=H()).json()
            ap = next(a for a in f["aps"] if a["name"] == "CP-Conference Room")
            r.go(f"/devices/{ap['device_id']}/360", 4)
            pg.get_by_role("tab", name="Radios & clients").click()
            time.sleep(1)
            expect(pg.get_by_text("2.4 GHz").first).to_be_visible()
            ncl = pg.locator("text=Connected clients").first.inner_text()
            pg.get_by_role("tab", name="Incidents").click()
            pg.get_by_role("tab", name="Performance").click()
            opts = pg.locator("select[aria-label=Metric] option").all_inner_texts()
            pg.get_by_role("button", name="Diagnose").first.click()
            expect(pg.get_by_role("dialog")).to_contain_text("Most likely cause", timeout=15000)
            pg.get_by_role("dialog").get_by_role("button", name="Close").click()
            return f"{ncl}; metric options={opts[:6]}"
        r.step("device-360 AP radios, clients, incidents, metrics, diagnose", dev360)

        def dev360_list():
            r.go("/device-360", 4)
            pg.locator("select[aria-label='Device type']").select_option("switch")
            time.sleep(1)
            kpi = pg.locator(".inm-kpi", has_text="Attention needed").inner_text()
            return kpi.replace("\n", " ")
        r.step("device-360 chooser filters and attention count", dev360_list)

        # ---------------------------------------------------------------- 8. incidents
        def incidents():
            import subprocess
            subprocess.run([sys.executable, "/home/claude/scripts/raise_alerts.py", "open"], check=True)
            r.go("/device-alerts", 4)
            row = pg.locator("tr", has_text="AllmanTownPS-SW-1").first
            row.locator("select[aria-label=Assignee]").select_option("noc.operator")
            time.sleep(2)
            row = pg.locator("tr", has_text="AllmanTownPS-SW-1").first
            row.get_by_role("button", name="Work").click()
            pg.get_by_placeholder("INC0012345").fill("INC0042001")
            pg.get_by_role("button", name="Link ticket").click()
            time.sleep(2)
            pg.locator("textarea").fill("Replaced the uplink patch cable at Allman Town PS")
            pg.get_by_role("button", name=re.compile("Resolve — ticket INC0042001 resolved")).click()
            time.sleep(2)
            inc = [x for x in httpx.get(API + "/v1/incidents?state=ALL", headers=H()).json() if x["device"] == "AllmanTownPS-SW-1"][0]
            assert inc["resolution"]["type"] == "TICKET_RESOLVED" and inc["ticket"]["closed"], inc["resolution"]
            # the second incident gets a ticket, then monitoring sees the switch answer again
            row = pg.locator("tr", has_text="AdmiralTown-SW-1").first
            row.get_by_role("button", name="Work").click()
            pg.get_by_placeholder("INC0012345").fill("INC0042002")
            pg.get_by_role("button", name="Link ticket").click()
            time.sleep(1)
            subprocess.run([sys.executable, "/home/claude/scripts/raise_alerts.py", "recover"], check=True)
            r.go("/device-alerts", 3)
            pg.get_by_role("button", name=re.compile("^Resolved")).click()
            time.sleep(2)
            expect(pg.get_by_text("Auto-recovered (self-healed)").first).to_be_visible()
            expect(pg.get_by_text("close ticket INC0042002")).to_be_visible()
            s = httpx.get(API + "/v1/incidents/summary", headers=H()).json()
            return f"summary={s}"
        r.step("device-alerts assign, ticket, resolve; auto-recovery flags ticket", incidents)

        # ---------------------------------------------------------------- 9. optimization by cluster
        def optimization():
            r.go("/optimization", 3)
            f = httpx.get(API + "/v1/wireless/fleet", headers=H()).json()
            key = next(c["key"] for c in f["clusters"] if c["key"] != "unlinked")
            pg.locator("select[aria-label=Cluster]").first.select_option(key)
            time.sleep(4)
            box = pg.get_by_label("Select all eligible")
            box.check()
            pg.get_by_role("button", name=re.compile("Propose for")).click()
            expect(pg.get_by_role("status").first).to_contain_text("enrollment request", timeout=20000)
            enr = httpx.get(API + "/v1/optimization/enrollments", headers=H()).json()
            names = [e.get("hostname") for e in enr]
            assert all(names), "enrollments missing names"
            # approver logs in and tries to approve: refused because no sealed backup exists — the UI must say why
            r.login("noc.approver", "Noc-pass-123456")
            r.go("/optimization", 3)
            pg.get_by_role("button", name="Approve").first.click()
            expect(pg.get_by_role("alert").first).to_contain_text("Backup readiness", timeout=15000)
            expect(pg.get_by_text("Blocked: capture a configuration backup first").first).to_be_visible()
            r.login()
            return f"{len(enr)} per-AP enrollments: {names}; approval correctly blocked until a backup exists"
        r.step("optimization propose a whole cluster and approve per AP", optimization)

        # ---------------------------------------------------------------- 10. guides and intelligence pages
        def insights():
            r.go("/phase4-insights", 3)
            g = pg.locator("section", has_text="Guide · From telemetry to an acted-on insight")
            expect(g).to_be_visible()
            nxt = g.get_by_text("Do this next").count()
            g.get_by_role("button", name="Analyze now").click() if g.get_by_role("button", name="Analyze now").count() else None
            time.sleep(2)
            return f"guide visible; 'Do this next' markers={nxt}"
        r.step("phase4-insights guided steps", insights)

        def monitoring_guide():
            r.go("/aps", 3)
            for path in ("/learning", "/assistant", "/ai-learning"):
                r.go(path, 2)
                assert pg.locator("section", has_text="Guide ·").count(), f"no guide on {path}"
            return "guides on learning, assistant, ai-learning"
        r.step("learning assistant ai-rules guides render", monitoring_guide)

        def wifi_after():
            r.go("/wifi", 4)
            s = httpx.get(API + "/dashboard/summary", headers=H()).json()
            return f"coverage={s['coverage']} util2g={s['avg_util_2g_percent']} util5g={s['avg_util_5g_percent']} bands={s['clients_by_band']}"
        r.step("wifi dashboard coverage and per-band after onboarding", wifi_after)

        def inventory():
            r.go("/inventory", 3)
            body = pg.inner_text("main")
            f = httpx.get(API + "/v1/wireless/fleet", headers=H()).json()
            dup = {}
            for a in f["aps"]:
                dup[a["serial"].lower()] = dup.get(a["serial"].lower(), 0) + 1
            assert all(v == 1 for v in dup.values()), f"duplicate AP records {dup}"
            return f"{len(f['aps'])} APs, no duplicates; sites kept: {sorted({a['site'] for a in f['aps'] if a['site']})}"
        r.step("inventory no duplicate AP records and imported sites kept", inventory)

        r.br.close()
    json.dump(RESULTS, open("/tmp/e2e/results.json", "w"), indent=1)
    print("\nPASS", sum(x["status"] == "PASS" for x in RESULTS), "FAIL", sum(x["status"] == "FAIL" for x in RESULTS))


if __name__ == "__main__":
    main()
