"""Wireless operations: AP health tiers (vs the 24 Apr 2026 action report), cluster-grouped fleet, inventory
adoption (no duplicate AP records), cluster-aware auto-onboarding, read-only SSH CLI client, client connectivity,
incident workflow and the dashboard coverage figures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.domain import ap_health as H
from tests.conftest import login, make_user

# (tier, AP, negative, rejects, dhcp_to, auth, deauth) — the full categorization table of the PDF report
PDF_ROWS = [
    ("BRONZE", "CP-Conference Room", 7686, 6848, 0, 154, 676), ("BRONZE", "CP-Registry", 3157, 30, 1, 38, 3085),
    ("BRONZE", "ICTD-Admin", 1720, 15, 0, 32, 1669), ("BRONZE", "Commish-Office", 1312, 335, 7, 46, 924),
    ("BRONZE", "CP-Legal-OfficeAP", 898, 92, 11, 49, 746), ("BRONZE", "ICTD-Technical-AP", 877, 7, 0, 46, 821),
    ("SILVER", "CP-FrontDesk-AP", 804, 95, 3, 49, 656), ("SILVER", "CCN-Production", 618, 27, 3, 22, 566),
    ("SILVER", "ACP-Admin", 578, 23, 4, 26, 525), ("SILVER", "Old-ConferenceRM", 545, 48, 4, 28, 465),
    ("SILVER", "CCN-Social", 526, 31, 4, 19, 472), ("SILVER", "CP-ReceptionArea", 481, 4, 3, 21, 453),
    ("GOLD", "DCP-admin", 370, 8, 2, 16, 344), ("GOLD", "Crime-Stop", 247, 19, 0, 12, 216),
    ("GOLD", "CCN-Studio-AP", 226, 12, 1, 7, 206), ("GOLD", "Property-Mgnt-Blackwood", 188, 3, 1, 10, 174),
    ("GOLD", "DocuShare-AP", 183, 35, 0, 5, 143), ("GOLD", "My-HRPlus", 141, 27, 0, 3, 111),
    ("GOLD", "CourtOfEnquiry", 109, 4, 0, 13, 92), ("GOLD", "CP-GenOffice", 41, 6, 1, 4, 29),
    ("GOLD", "ICTD-MainArea", 24, 2, 0, 2, 20),
]


@pytest.mark.parametrize("tier,ap,neg,rej,dto,auth,de", PDF_ROWS)
def test_event_tiers_match_action_report(tier, ap, neg, rej, dto, auth, de):
    got, triggers = H.event_tier({"reject": rej, "dhcp_timeout": dto, "auth": auth, "deauth": de,
                                  "arm": neg - rej - dto - auth - de, "negative": neg})
    assert got == tier, (ap, triggers)
    assert (triggers == []) == (tier == "GOLD")


def test_report_tier_counts_match_pdf():
    tiers = [H.event_tier({"reject": r, "dhcp_timeout": d, "auth": a, "deauth": de, "arm": n - r - d - a - de,
                           "negative": n})[0] for _, _, n, r, d, a, de in PDF_ROWS]
    assert (tiers.count("GOLD"), tiers.count("SILVER"), tiers.count("BRONZE")) == (9, 6, 6)


@pytest.mark.parametrize("text,cat", [
    ("Station e0:06:e6:31:55:4b deauthenticated from AP CP-Registry", H.DEAUTH),
    ("Client disassociated: reason inactivity", H.DEAUTH),
    ("Association reject for client 11:22:33:44:55:66: AP resource constrained", H.REJECT),
    ("Reassociation request denied", H.REJECT),
    ("DHCP timeout for client aa:bb:cc:dd:ee:ff on VLAN 20", H.DHCP_TIMEOUT),
    ("DHCP ACK received for client", H.DHCP_ACK),
    ("802.1X authentication failed for user", H.AUTH),
    ("ARM channel change from 36 to 44", H.ARM),
    ("AP rebooted", H.OTHER),
])
def test_classifier(text, cat):
    assert H.classify(text) == cat


def test_build_rows_combines_events_and_condition_and_never_defaults_gold():
    ev = H.aggregate([{"ap": "AP1", "text": "Association reject", "client": "aa:bb:cc:dd:ee:01", "ssid": "GUEST",
                       "band": "5GHz", "hour": 13, "count": 400}])
    rows = {r["ap"]: r for r in H.build_rows(ev, {
        "AP1": {"state": "ONLINE", "util_5g_percent": 20.0},
        "AP2": {"state": "ONLINE", "util_5g_percent": 85.0},   # condition breach only
        "AP3": {"state": "UNKNOWN"},                           # nothing measured
        "AP4": {"state": "OFFLINE"},
        "AP5": {"state": "ONLINE"},
        "AP6": {"state": "ONLINE", "util_5g_percent": 20.0,
                "client_experience": {"tier": "BRONZE", "detail": "Client experience 40/100"}},
    })}
    assert rows["AP1"]["tier"] == "BRONZE" and rows["AP1"]["top_reject_ssid_band"] == "GUEST / 5GHz"
    assert rows["AP2"]["tier"] == "BRONZE" and "5 GHz busy" in rows["AP2"]["trigger"]
    assert rows["AP3"]["tier"] == "UNRATED"
    assert rows["AP4"]["tier"] == "BRONZE"
    assert rows["AP5"]["tier"] == "UNRATED"
    assert rows["AP6"]["tier"] == "BRONZE" and "Client experience 40/100" in rows["AP6"]["trigger"]
    mv = H.movement([{"ap": "AP1", "tier": "SILVER", "events": {}}], [{"ap": "AP1", "tier": "GOLD", "events": {}}])
    assert mv == [{"ap": "AP1", "from": "SILVER", "to": "GOLD", "direction": "improved"}]
    mv = H.movement([{"ap": "AP1", "tier": "BRONZE", "events": {"reject": 400}}], [{"ap": "AP1", "tier": "GOLD", "events": None}])
    assert mv[0]["direction"] == "not comparable"


# ------------------------------------------------------------------ API (demo dataset: simulated clusters)
def _poll(client, admin):
    r = client.post("/api/sites/poll-all", headers=admin)
    if r.status_code == 404:
        from backend.database.session import session_scope
        from backend.monitoring.collector import poll_site, visible_sites
        from backend.infrastructure.persistence.inventory import sync_inventory
        with session_scope() as db:
            for s in visible_sites(db):
                poll_site(db, s)
            sync_inventory(db)


def _seed(client, admin):
    from backend.database.session import session_scope
    from backend.monitoring.collector import ensure_demo_sites, poll_site, visible_sites
    from backend.infrastructure.persistence.inventory import sync_inventory
    with session_scope() as db:
        ensure_demo_sites(db)
        for s in visible_sites(db):
            poll_site(db, s)
        sync_inventory(db)


def test_fleet_is_grouped_by_cluster_with_both_bands(client, admin):
    _seed(client, admin)
    r = client.get("/api/v1/wireless/fleet", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["clusters"] and body["aps"]
    keys = {c["key"] for c in body["clusters"]}
    assert all(a["cluster_key"] in keys for a in body["aps"])
    assert any(a["radio_2g"]["channel"] for a in body["aps"]), "2.4 GHz radio must be surfaced"
    assert any(a["radio_5g"]["channel"] for a in body["aps"])
    for a in body["aps"]:
        assert a["guidance"] and a["tier"] in ("GOLD", "SILVER", "BRONZE", "UNRATED")
    measured = [a for a in body["aps"] if a["client_experience"]]
    assert measured
    assert all(H.TIER_RANK[a["tier"]] <= H.TIER_RANK[a["client_experience"]["tier"]] for a in measured)
    assert body["summary"]["attention"] == sum(v for k, v in body["summary"]["states"].items() if k != "ONLINE")


def test_health_report_condition_only_and_snapshot_compare(client, admin):
    _seed(client, admin)
    r = client.get("/api/v1/wireless/health-report?hours=24", headers=admin)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert set(rep["tiers"]) == {"GOLD", "SILVER", "BRONZE", "UNRATED"} and rep["rows"]
    assert rep["method"]["bronze"]["Rejects"] == 300
    s1 = client.post("/api/v1/wireless/health-report/snapshots", headers=admin, json={"label": "before"}).json()
    s2 = client.post("/api/v1/wireless/health-report/snapshots", headers=admin, json={"label": "after"}).json()
    got = client.get(f"/api/v1/wireless/health-report/snapshots/{s2['id']}?compare_to={s1['id']}", headers=admin)
    assert got.status_code == 200 and "movement" in got.json()
    assert len(client.get("/api/v1/wireless/health-report/snapshots", headers=admin).json()) == 2


def test_health_report_import_aruba_csv(client, admin):
    _seed(client, admin)
    lines = ["Occurred On,AP Name,Client MAC,SSID,Band,Description"]
    lines += [f"2026-04-24 13:{i % 60:02d}:00,CP-Conference Room,e0:06:e6:31:55:{i % 99:02d},JCFGUEST_WIFI,5GHz,"
              "Association reject: AP resource constrained" for i in range(350)]
    lines += ["2026-04-24 09:00:00,CP-GenOffice,aa:bb:cc:00:00:01,JCF,5GHz,DHCP ACK"]
    r = client.post("/api/v1/wireless/health-report/import", headers=admin,
                    files={"file": ("export.csv", "\n".join(lines).encode(), "text/csv")}, data={"label": "Apr 24"})
    assert r.status_code == 200, r.text
    rows = {x["ap"]: x for x in r.json()["rows"]}
    conf = rows["CP-Conference Room"]
    assert conf["tier"] == "BRONZE" and conf["events"]["reject"] == 350
    assert conf["top_reject_ssid_band"] == "JCFGUEST_WIFI / 5GHz" and conf["reject_hours"][0]["hour"] == 13
    assert rows["CP-GenOffice"]["tier"] == "GOLD"
    bad = client.post("/api/v1/wireless/health-report/import", headers=admin,
                      files={"file": ("x.csv", b"a,b\n1,2", "text/csv")})
    assert bad.status_code == 422


def test_client_connectivity_and_journey(client, admin):
    _seed(client, admin)
    r = client.get("/api/v1/wireless/clients?hours=24", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["clients_seen"] == len(body["clients"]) and body["by_cluster"]
    if body["clients"]:
        cid = body["clients"][0]["id"]
        key = body["clients"][0]["cluster_key"]
        filtered = client.get(f"/api/v1/wireless/clients?cluster={key}", headers=admin).json()
        assert all(c["cluster_key"] == key for c in filtered["clients"])
        j = client.get(f"/api/v1/wireless/clients/{cid}/journey", headers=admin)
        assert j.status_code == 200 and "score" in j.json()


def test_viewer_sees_masked_client_macs(client, admin):
    _seed(client, admin)
    h = make_user(client, admin, "viewer1", "viewer")
    body = client.get("/api/v1/wireless/clients", headers=h)
    assert body.status_code == 200
    assert all(c["hostname"] is None and c["ip"] is None for c in body.json()["clients"])
    full = client.get("/api/v1/wireless/clients", headers=admin).json()["clients"]
    if full:
        assert body.json()["clients"][0]["mac"] != full[0]["mac"]


def test_dashboard_reports_inventory_coverage_and_both_bands(client, admin):
    _seed(client, admin)
    s = client.get("/api/dashboard/summary", headers=admin).json()
    assert s["coverage"]["inventory_aps"] >= s["coverage"]["monitored_aps"] >= 1
    assert "avg_util_2g_percent" in s and "avg_util_5g_percent" in s


# ------------------------------------------------------------------ inventory adoption (duplicate fix)
def test_cluster_poll_adopts_imported_ap_instead_of_duplicating(client, admin):
    from sqlalchemy import select
    from backend.database.models import AccessPoint, Site
    from backend.database.models_inm import InmDevice, InmSite
    from backend.database.session import session_scope
    from backend.infrastructure.device_state import canonical_inventory
    from backend.infrastructure.persistence.inventory import sync_inventory
    with session_scope() as db:
        phys = InmSite(name="ST. ANN DIVISION HQ", source_type="REAL")
        db.add(phys)
        db.flush()
        imported = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="access_point",
                             serial_number="CNPCLJ51T4", hostname="34:3a:20:cd:10:26", adapter="manual_snapshot",
                             site_id=phys.id, mgmt_addresses=["10.1.1.5"])
        stale_dup = InmDevice(source_type="REAL", environment="lab", vendor="aruba", family="access_point",
                              serial_number="cnpclj51t4", adapter="aruba_instant_8_10")
        db.add_all([imported, stale_dup])
        site = Site(name="Instant StAnn", vc_host="10.1.1.5", simulated=False)
        db.add(site)
        db.flush()
        db.add(AccessPoint(site_id=site.id, name="StAnn-AP1", mac="34:3a:20:cd:10:26", ip="10.1.1.5",
                           serial="CNPCLJ51T4", model="AP-505", status="up"))
        db.flush()
        sync_inventory(db)
        devs = list(db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL")))
        live = [d for d in canonical_inventory(devs)]
        assert len(live) == 1
        assert live[0].id == imported.id and live[0].legacy_ap_id is not None
        assert live[0].site_id == phys.id, "imported physical site must be kept"
        assert stale_dup.lifecycle == "merged"


# ------------------------------------------------------------------ auto-onboarding (mocked device I/O)
def test_autodiscovery_status_recovers_abandoned_run(app_env):
    from backend.database.session import create_schema, session_scope
    from backend.infrastructure import ap_autodiscovery as AD

    create_schema()
    saved = {"state": "RUNNING", "done": 15, "total": 247}
    with session_scope() as db:
        assert AD.job_status(db) == {"state": "NEVER_RUN"}
        AD._put(db, AD.JOB_KEY, saved, "operator")

    with session_scope() as db:
        with AD._LOCK:
            assert AD.job_status(db) == saved
        recovered = AD.job_status(db)
        assert recovered["state"] == "FAILED"
        assert (recovered["done"], recovered["total"]) == (15, 247)
        assert "Start a new run" in recovered["error"]


def test_autodiscovery_registers_cluster_once_and_links_members(app_env):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.9.9.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    from backend.database.session import create_schema, session_scope
    create_schema()
    from backend.database.models import AccessPoint, Site
    from backend.database.models_inm import InmCluster, InmDevice
    from backend.database.models_onboarding import OnboardingCredential
    from backend.infrastructure import ap_autodiscovery as AD
    from backend.security.crypto import encrypt_secret
    members = [{"name": "AP-A", "ip": "10.9.9.10", "is_master": True, "serial": "S1", "mac": "aa:aa:aa:aa:aa:01"},
               {"name": "AP-B", "ip": "10.9.9.11", "serial": "S2", "mac": "aa:aa:aa:aa:aa:02"},
               {"name": "AP-C", "ip": "10.9.9.12", "serial": "S3", "mac": "aa:aa:aa:aa:aa:03"}]
    calls = {"rest": 0, "ssh": 0, "poll": 0}

    def rest(ip, u, p):
        calls["rest"] += 1
        from backend.aruba.errors import ArubaRestDisabledError
        raise ArubaRestDisabledError("off")

    def ssh(ip, u, p, fp):
        calls["ssh"] += 1
        return members, {"transport": "ssh_cli", "ssh_host": ip, "fingerprint": fp}

    def poll(db, site):
        calls["poll"] += 1
        site.last_poll_ok = True
        for member in members:
            db.add(AccessPoint(site_id=site.id, name=member["name"], mac=member["mac"],
                               ip=member["ip"], serial=member["serial"]))
        return True

    saved = []

    def progress(update):
        if update.get("clusters_saved"):
            with session_scope() as reader:
                saved.append((reader.query(InmCluster).count(), reader.query(InmDevice)
                              .filter(InmDevice.cluster_id.is_not(None)).count()))

    with session_scope() as db:
        db.add(OnboardingCredential(name="ap-admin", device_family="access_point", protocol="aruba_instant_rest",
                                    username="admin", password_enc=encrypt_secret("pw"), created_by="t"))
        for i, ip in ((2, "10.9.9.11"), (1, "10.9.9.10"), (3, "10.9.9.12")):
            db.add(InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="access_point",
                             serial_number=f"S{i}", hostname=f"AP-{'ABC'[i-1]}", adapter="manual_snapshot",
                             mgmt_addresses=[ip], created_at=datetime.now(timezone.utc) + timedelta(seconds=i)))
        db.flush()
        AD._put(db, "ssh-hostkey:10.9.9.11", {"fingerprint": "SHA256:x"}, "t")
        out = AD.discover(db, "tester", rest_members=rest, ssh_members=ssh, poll=poll, progress=progress)
        assert out["counts"].get("CLUSTER_REGISTERED") == 1
        assert out["counts"].get("LINKED") == 2
        assert calls["ssh"] == 1 and calls["poll"] == 1 and calls["rest"] <= 2
        site = db.query(Site).one()
        assert site.vc_host == "10.9.9.10"
        assert AD.site_transport(db, site.id)["transport"] == "ssh_cli"
        assert saved == [(1, 3)]


def test_autodiscovery_requires_hostkey_approval_before_sending_credentials(app_env):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.9.8.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    from backend.database.session import create_schema, session_scope
    create_schema()
    from backend.database.models_inm import InmDevice
    from backend.database.models_onboarding import OnboardingCredential
    from backend.infrastructure import ap_autodiscovery as AD
    from backend.security.crypto import encrypt_secret
    from backend.aruba.errors import ArubaRestDisabledError

    def rest(ip, u, p):
        raise ArubaRestDisabledError("off")

    def ssh(*a):
        raise AssertionError("SSH must not be attempted without an approved fingerprint")

    with session_scope() as db:
        db.add(OnboardingCredential(name="ap", device_family="access_point", protocol="aruba_instant_rest",
                                    username="admin", password_enc=encrypt_secret("pw"), created_by="t"))
        db.add(InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="access_point",
                         serial_number="Z1", hostname="Z1", adapter="manual_snapshot", mgmt_addresses=["10.9.8.5"]))
        db.flush()
        out = AD.discover(db, "t", rest_members=rest, ssh_members=ssh, poll=lambda db, s: True)
        assert out["counts"] == {"NEEDS_HOSTKEY_APPROVAL": 1}
        res = AD.scan_host_keys(db, ["10.9.8.5"], "t", scanner=lambda ip: {"fingerprint": "SHA256:abc", "key_type": "rsa"})
        assert res[0]["fingerprint"] == "SHA256:abc"
        bad = AD.approve_fingerprints(db, [{"ip": "10.9.8.5", "fingerprint": "SHA256:evil"}], "t")
        assert bad[0]["approved"] is False and AD.pinned_fingerprint(db, "10.9.8.5") is None
        ok = AD.approve_fingerprints(db, [{"ip": "10.9.8.5", "fingerprint": "SHA256:abc"}], "t")
        assert ok[0]["approved"] and AD.pinned_fingerprint(db, "10.9.8.5") == "SHA256:abc"


def test_ssh_show_client_refuses_non_allowlisted_and_targeted_commands():
    from backend.aruba.errors import ArubaCommandError, ArubaSshError, ArubaUnsupportedError
    from backend.aruba.ssh import SshShowClient
    c = SshShowClient("10.0.0.1", "u", "p", "")
    with pytest.raises(ArubaUnsupportedError):
        c.show("configure terminal")
    with pytest.raises(ArubaCommandError):
        c.show("show ap debug system-status", iap_ip="10.0.0.2")
    with pytest.raises(ArubaSshError):
        c.login()  # no approved fingerprint → nothing sent


def test_ssh_show_client_reads_output_and_handles_paging(monkeypatch):
    from backend.aruba import ssh

    class Chan:
        def __init__(self):
            self.sent, self.queue = [], []

        def get_pty(self, **k): pass

        def invoke_shell(self): self.queue.append("banner\nAP-A# ")

        def recv_ready(self): return bool(self.queue)

        def recv(self, n): return self.queue.pop(0).encode()

        def send(self, s):
            self.sent.append(s)
            if s.startswith("show aps"):
                self.queue.append("show aps\n1 Access Points\n---------------\nName  IP Address\n--More--")
            elif s == " ":  # pager line is overwritten in place
                self.queue.append("\rAP-A  10.0.0.1*\n\nAP-A# ")

    class Key:
        def asbytes(self): return b"k"

        def get_name(self): return "ssh-rsa"

    class T:
        def __init__(self): self.chan = Chan()

        def get_remote_server_key(self): return Key()

        def auth_password(self, u, p): pass

        def open_session(self): return self.chan

        def close(self): pass

    t = T()
    monkeypatch.setattr(ssh, "_open_transport", lambda h, p, to: t)
    fp = ssh.fingerprint(Key())
    with ssh.SshShowClient("10.0.0.1", "admin", "pw", fp, timeout=2) as c:
        out = c.show("show aps")
    assert "10.0.0.1*" in out["Command output"] and " " in t.chan.sent
    from backend.aruba import parsers
    aps = parsers.normalize_aps(parsers.extract_rows(out, "show aps"))
    assert aps and aps[0]["is_master"] is True


# ------------------------------------------------------------------ incidents
_N = [0]


def _alert(state="OPEN"):
    _N[0] += 1
    from backend.database.models_inm import InmDevice
    from backend.database.models_phases import InmAlert
    from backend.database.session import session_scope
    with session_scope() as db:
        d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch", serial_number=f"SW{_N[0]}",
                      hostname="AllmanTownPS-SW-1", adapter="aruba_cx")
        db.add(d)
        db.flush()
        a = InmAlert(device_id=d.id, rule_id="DEVICE_UNREACHABLE", severity="critical",
                     message="AllmanTownPS-SW-1: Device not answering polls: 3 (threshold 3)", state=state)
        db.add(a)
        db.flush()
        return a.id, d.id


def test_incident_assign_ticket_resolve_and_reopen(client, admin):
    aid, _ = _alert()
    make_user(client, admin, "noc1", "operator")
    assert {"username": "noc1", "role": "operator"} in client.get("/api/v1/incidents/assignees", headers=admin).json()
    r = client.post(f"/api/v1/incidents/{aid}/assign", headers=admin, json={"assignee": "noc1"})
    assert r.status_code == 200 and r.json()["assignee"] == "noc1" and r.json()["state"] == "ACKNOWLEDGED"
    assert client.post(f"/api/v1/incidents/{aid}/assign", headers=admin, json={"assignee": "nobody"}).status_code == 422
    r = client.post(f"/api/v1/incidents/{aid}/ticket", headers=admin, json={"system": "ServiceNow", "reference": "INC0012345"})
    assert r.json()["ticket"]["reference"] == "INC0012345"
    # cannot close as "no ticket" while a ticket is open
    r = client.post(f"/api/v1/incidents/{aid}/resolve", headers=admin,
                    json={"resolution": "NO_TICKET_REQUIRED", "note": "fixed"})
    assert r.status_code == 409
    r = client.post(f"/api/v1/incidents/{aid}/resolve", headers=admin,
                    json={"resolution": "TICKET_RESOLVED", "note": "Replaced patch cable"})
    body = r.json()
    assert body["state"] == "RESOLVED" and body["resolution"]["type"] == "TICKET_RESOLVED" and body["ticket"]["closed"]
    kinds = [e["kind"] for e in body["timeline"]]
    assert kinds == ["ASSIGNED", "TICKET_LINKED", "RESOLVED"]
    r = client.post(f"/api/v1/incidents/{aid}/reopen", headers=admin, json={"text": "flapping again"})
    assert r.json()["state"] == "ACKNOWLEDGED" and r.json()["resolution"] is None
    mine = client.get("/api/v1/incidents?assignee=noc1", headers=admin).json()
    assert [x["id"] for x in mine] == [aid]


def test_incident_resolve_without_ticket_and_auto_recovery(client, admin):
    aid, did = _alert()
    r = client.post(f"/api/v1/incidents/{aid}/resolve", headers=admin,
                    json={"resolution": "NO_TICKET_REQUIRED", "note": "Power restored on site"})
    assert r.json()["resolution"]["type"] == "NO_TICKET_REQUIRED"
    # self-healing: the alert engine closes the alert when the metric is measured healthy again
    aid2, did2 = _alert()
    client.post(f"/api/v1/incidents/{aid2}/ticket", headers=admin, json={"reference": "T-9"})
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    from backend.infrastructure import alert_engine
    from backend.domain import alert_rules as R
    rule = next(r for r in R.CATALOGUE if r.id == "DEVICE_UNREACHABLE")
    with session_scope() as db:
        alert_engine.apply(db, db.get(InmDevice, did2), {rule.metric: 0.0})
    inc = client.get(f"/api/v1/incidents/{aid2}", headers=admin).json()
    assert inc["state"] == "RESOLVED" and inc["resolution"]["type"] == "AUTO_RECOVERED"
    assert inc["resolution"]["ticket_needs_closure"] is True
    s = client.get("/api/v1/incidents/summary", headers=admin).json()
    assert s["auto_recovered"] == 1 and s["tickets_to_close"] == 1


def test_incident_writes_need_operator(client, admin):
    aid, _ = _alert()
    h = make_user(client, admin, "viewer2", "viewer")
    assert client.post(f"/api/v1/incidents/{aid}/resolve", headers=h,
                       json={"resolution": "NO_TICKET_REQUIRED", "note": "x" * 5}).status_code == 403


def test_device_insight(client, admin):
    _seed(client, admin)
    fl = client.get("/api/v1/wireless/fleet", headers=admin).json()
    did = fl["aps"][0]["device_id"]
    r = client.get(f"/api/v1/wireless/devices/{did}/insight", headers=admin)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ap"]["device_id"] == did and "inputs" in b["health"] and "config" in b
    measured = b["ap"]["client_experience"]
    assert b["health"]["score"] == (measured["score"] if measured else None)
    displayed = [m for m in b["metrics"] if m["metric"] == "exp_score"]
    assert len(displayed) == (1 if measured else 0)


# ------------------------------------------------------------------ diagnosis
def test_diagnose_ap_finds_cochannel_congestion_and_proposes_least_used_channel():
    from backend.domain import wifi_diagnosis as W
    ap = {"device_id": "a", "name": "CP-Conf", "state": "ONLINE", "clients": 45,
          "radio_5g": {"channel": "36", "util_percent": 85, "noise_floor_dbm": -92},
          "radio_2g": {"channel": "3", "util_percent": 20, "noise_floor_dbm": -95}}
    nb = [{"device_id": "b", "name": "N1", "radio_5g": {"channel": "36"}, "radio_2g": {"channel": "1"}},
          {"device_id": "c", "name": "N2", "radio_5g": {"channel": "40"}, "radio_2g": {"channel": "6"}}]
    clients = [{"snr_db": 10, "band": "5GHz"}] * 4 + [{"snr_db": 30, "band": "5GHz"}] * 2
    d = W.diagnose_ap(ap, nb, clients, {"events": {"reject": 400, "deauth": 0}, "top_reject_ssid_band": "GUEST / 5GHz"})
    ids = [f["id"] for f in d["findings"]]
    assert {"util_5", "density", "coverage", "rejects", "ch_2g_overlap"} <= set(ids)
    util = next(f for f in d["findings"] if f["id"] == "util_5")
    assert "N1" in util["why"] and util["proposal"]["to"] == 44
    assert all(p["to"] not in (None,) for p in d["proposals"])


def test_diagnose_client_sticky_and_band():
    from backend.domain import wifi_diagnosis as W
    d = W.diagnose_client({"ap": "X", "snr_db": 9, "band": "5GHz", "disconnects": 6, "roams": 0}, None, 5)
    assert [f["id"] for f in d["findings"]][:2] == ["unstable", "weak_signal"] or "weak_signal" in d["headline"].lower() \
        or d["findings"][0]["severity"] == "high"
    assert "sticky" in next(f for f in d["findings"] if f["id"] == "weak_signal")["why"]
    d2 = W.diagnose_client({"snr_db": 35, "band": "2.4GHz", "speed_mbps": 11}, None, 1)
    assert {"band", "low_rate"} <= {f["id"] for f in d2["findings"]}


def test_diagnose_endpoints_and_cluster_optimization(client, admin):
    _seed(client, admin)
    fl = client.get("/api/v1/wireless/fleet", headers=admin).json()
    ap = next(a for a in fl["aps"] if a["cluster_key"] != "unlinked")
    r = client.get(f"/api/v1/wireless/diagnose/ap/{ap['device_id']}?ai=true", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["headline"] and r.json()["ai"]["available"] is False  # AI disabled in tests → rule-based only
    clients = client.get("/api/v1/wireless/clients", headers=admin).json()["clients"]
    if clients:
        rc = client.get(f"/api/v1/wireless/diagnose/client/{clients[0]['id']}", headers=admin)
        assert rc.status_code == 200 and "findings" in rc.json()
    co = client.get(f"/api/v1/wireless/optimization/cluster/{ap['cluster_key']}", headers=admin)
    assert co.status_code == 200, co.text
    body = co.json()
    assert body["platform_blockers"] and all("blockers" in x and "headline" in x for x in body["aps"])


# ------------------------------------------------------------------ guided workflows
@pytest.mark.parametrize("key", ["monitoring", "insights", "learning", "assistant", "optimization", "incidents"])
def test_guides_compute_live_steps(client, admin, key):
    r = client.get(f"/api/v1/guide/{key}", headers=admin)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["steps"] and all(s["status"] in ("DONE", "TODO", "BLOCKED", "INFO") for s in g["steps"])
    assert g["sample"]["api"].startswith("/v1/demo-data/")


def test_bulk_switch_plans_only_for_credentialed_devices(client, admin):
    from backend.database.models_inm import InmDevice
    from backend.database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
    from backend.database.session import session_scope
    from backend.security.crypto import encrypt_secret
    with session_scope() as db:
        p = OnboardingCredential(name="cx", device_family="switch", protocol="aruba_cx_rest", username="admin",
                                 password_enc=encrypt_secret("x"), created_by="t")
        db.add(p)
        db.flush()
        for i in range(3):
            d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch",
                          serial_number=f"CX{i}", hostname=f"SW{i}", adapter="aruba_cx", mgmt_addresses=[f"10.2.0.{i + 1}"])
            db.add(d)
            db.flush()
            if i < 2:
                db.add(OnboardingDeviceCredential(device_id=d.id, credential_id=p.id, bound_by="t"))
    r = client.post("/api/v1/guide/actions/auto-plans", headers=admin, json={"family": "switch"})
    assert r.status_code == 200 and r.json()["created"] == 2 and r.json()["skipped"]["no_credential"] == 1
    again = client.post("/api/v1/guide/actions/auto-plans", headers=admin, json={"family": "switch"}).json()
    assert again["created"] == 0 and again["skipped"]["has_plan"] == 2


def test_loki_count_by_parses_instant_vector():
    import httpx
    from backend.infrastructure.logs.loki import LokiLogStore

    def handler(req):
        assert req.url.path == "/loki/api/v1/query" and "count_over_time" in req.url.params["query"]
        return httpx.Response(200, json={"data": {"result": [
            {"metric": {"host": "CP-Registry"}, "value": [0, "3085"]},
            {"metric": {"host": "10.0.0.5"}, "value": [0, "12"]}]}})
    s = LokiLogStore("http://loki", transport=httpx.MockTransport(handler))
    got = s.count_by('sum by (host) (count_over_time({source="syslog"} |~ `deauth` [24h]))')
    assert got == {"CP-Registry": 3085.0, "10.0.0.5": 12.0}


# ------------------------------------------------------------------ discovery rules (subnet + credentials joined)
def test_discovery_rule_joins_subnet_and_credentials_with_fallback(client, admin, app_env):
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.50.0.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    from backend.database.session import session_scope
    from backend.database.models_onboarding import OnboardingCredential
    from backend.security.crypto import encrypt_secret
    with session_scope() as db:
        for n in ("cx-old", "cx-new"):
            db.add(OnboardingCredential(name=n, device_family="switch", protocol="aruba_cx_rest", username="admin",
                                        password_enc=encrypt_secret(n), created_by="t"))
    creds = {c["name"]: c["id"] for c in client.get("/api/v1/onboarding/credentials", headers=admin).json()}
    bad = client.post("/api/v1/discovery/subnets", headers=admin, json={"name": "x", "cidr": "8.8.8.0/24", "site_name": "S"})
    assert bad.status_code == 422
    big = client.post("/api/v1/discovery/subnets", headers=admin, json={"name": "x", "cidr": "10.0.0.0/16", "site_name": "S"})
    assert big.status_code == 422
    sub = client.post("/api/v1/discovery/subnets", headers=admin,
                      json={"name": "HQ switches", "cidr": "10.50.0.0/29", "site_name": "HQ"}).json()
    rule = client.post("/api/v1/discovery/rules", headers=admin, json={
        "name": "HQ CX", "subnet_ids": [sub["id"]], "credential_ids": [creds["cx-old"], creds["cx-new"]],
        "schedule": "daily"}).json()
    assert rule["next_run_at"]
    assert client.delete(f"/api/v1/discovery/subnets/{sub['id']}", headers=admin).status_code == 409

    from backend.api import onboarding_workflow as OW

    def fake_probe(profile, ip):
        if str(ip) == "10.50.0.2" and profile.name == "cx-new":
            return {"serial_number": "SG0001", "hostname": "HQ-SW-1", "model": "6100", "firmware": "10.10"}
        if str(ip) == "10.50.0.2":
            raise RuntimeError("AUTH_REJECTED: login refused")
        raise OSError("no route")

    def fake_identify(pid, ip):
        from backend.database.session import session_scope as ss
        with ss() as db2:
            p = db2.get(OnboardingCredential, pid)
            try:
                o = fake_probe(p, ip)
            except Exception:
                return {"management_ip": ip, "status": "FAILED", "enrollable": False}
            return {"management_ip": ip, "status": "FOUND", "hostname": o["hostname"], "model": o["model"],
                    "serial_number": o["serial_number"], "firmware": o["firmware"], "enrollable": True}

    app_env.setattr(OW, "_probe", fake_probe)
    app_env.setattr(OW, "_identify", fake_identify)
    r = client.post(f"/api/v1/discovery/rules/{rule['id']}/run", headers=admin)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["state"] == "COMPLETED", run
    assert run["summary"]["registered"] == 1 and run["summary"]["retries"] >= 1
    devs = client.get("/api/v1/inventory/devices", headers=admin)
    from backend.database.models_inm import InmDevice
    from backend.database.models_onboarding import OnboardingDeviceCredential
    from backend.database.models_phases import CollectionPlan
    with session_scope() as db:
        d = db.query(InmDevice).filter(InmDevice.serial_number == "SG0001").one()
        assert db.get(OnboardingDeviceCredential, d.id).credential_id == creds["cx-new"]
        assert db.query(CollectionPlan).filter(CollectionPlan.device_id == d.id).count() == 1
    rules = client.get("/api/v1/discovery/rules", headers=admin).json()
    assert rules[0]["last_run"]["state"] == "COMPLETED" and rules[0]["subnets"][0]["name"] == "HQ switches"
