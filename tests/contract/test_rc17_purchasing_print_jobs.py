"""rc17: internal supply purchase requests; print-job metadata + document inspection (policy-gated)."""
from datetime import datetime, timedelta, timezone

from tests.conftest import make_user


def _printer(serial="PRN-1", ip="10.20.0.15", level=8.0, status="LOW"):
    from backend.database.models_printer import PrinterDevice
    from backend.database.models_printer_srs import PrinterConsumable
    from backend.database.session import session_scope
    with session_scope() as db:
        p = PrinterDevice(manufacturer="HP", model="LaserJet M607", serial_number=serial, hostname="prn-hq-1",
                          management_ip=ip)
        db.add(p)
        db.flush()
        db.add(PrinterConsumable(printer_id=p.id, supply_index="1.1", supply_type="toner", colour="black",
                                 description="Black Cartridge HP CF237A", part_number="CF237A", level=8, max_capacity=100,
                                 level_state="VALID", percent_remaining=level, status=status,
                                 last_observed_at=datetime.now(timezone.utc)))
        return p.id


def test_purchase_request_lifecycle_dedupe_independent_approval_and_csv(client, admin):
    pid = _printer()
    op = make_user(client, admin, "supply.op", "operator")
    approver = make_user(client, admin, "supply.approver", "approver")
    viewer = make_user(client, admin, "supply.viewer", "viewer")
    body = {"printer_id": pid, "supply_index": "1.1", "supply_description": "Black Cartridge HP CF237A",
            "quantity": 2, "reason": "Spare for month-end payroll run"}
    assert client.post("/api/printer-purchasing/requests", headers=viewer, json=body).status_code == 403
    r = client.post("/api/printer-purchasing/requests", headers=op, json=body)
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "PENDING_APPROVAL" and req["part_number"] == "CF237A" and req["origin"] == "MANUAL"
    dup = client.post("/api/printer-purchasing/requests", headers=op, json=body)
    assert dup.status_code == 409 and "open request" in dup.json()["detail"]
    # operators cannot approve; an admin who did not request may; the requester never may
    assert client.post(f"/api/printer-purchasing/requests/{req['id']}/approve", headers=op, json={}).status_code == 403
    own = client.post("/api/printer-purchasing/requests", headers=admin,
                      json=body | {"supply_index": None, "supply_description": "Fuser kit"})
    assert own.status_code == 201
    selfapp = client.post(f"/api/printer-purchasing/requests/{own.json()['id']}/approve", headers=admin, json={})
    assert selfapp.status_code == 403 and "independent" in selfapp.json()["detail"]
    ok = client.post(f"/api/printer-purchasing/requests/{req['id']}/approve", headers=approver, json={"note": "ok"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED" and ok.json()["decided_by"] == "supply.approver"
    assert client.post(f"/api/printer-purchasing/requests/{req['id']}/progress/order", headers=op,
                       json={}).status_code == 422                 # PO reference required
    o = client.post(f"/api/printer-purchasing/requests/{req['id']}/progress/order", headers=op,
                    json={"order_reference": "PO-77812"})
    assert o.json()["status"] == "ORDERED" and o.json()["order_reference"] == "PO-77812"
    assert client.post(f"/api/printer-purchasing/requests/{req['id']}/progress/cancel", headers=op,
                       json={}).status_code == 409
    assert client.post(f"/api/printer-purchasing/requests/{req['id']}/progress/receive", headers=op,
                       json={}).json()["status"] == "RECEIVED"
    # closed request no longer blocks a new one for the same supply
    assert client.post("/api/printer-purchasing/requests", headers=op, json=body).status_code == 201
    csv = client.get("/api/printer-purchasing/requests.csv", headers=viewer)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("id,status,printer") and len(lines) == 4 and "PO-77812" in csv.text


def test_automatic_purchase_requests_are_off_by_default_then_measured_and_deduplicated(client, admin):
    pid = _printer(serial="PRN-2", ip="10.20.0.16", level=6.0, status="LOW")
    _printer(serial="PRN-3", ip="10.20.0.17", level=80.0, status="OK")
    assert client.post("/api/printer-purchasing/generate", headers=admin).json() == {"automatic": False, "created": 0}
    assert client.put("/api/printer-purchasing/policy", headers=admin,
                      json={"automatic": True, "lead_time_days": 500, "default_quantity": 1}).status_code == 422
    assert client.put("/api/printer-purchasing/policy", headers=admin,
                      json={"automatic": True, "lead_time_days": 14, "default_quantity": 1}).status_code == 200
    out = client.post("/api/printer-purchasing/generate", headers=admin).json()
    assert out == {"automatic": True, "created": 1, "skipped_open": 0}   # only the measured LOW supply; OK one untouched
    rows = client.get("/api/printer-purchasing/requests", headers=admin).json()
    assert len(rows) == 1 and rows[0]["printer_id"] == pid and rows[0]["origin"] == "AUTOMATIC"
    assert rows[0]["evidence"]["percent_remaining"] == 6.0
    assert client.post("/api/printer-purchasing/generate", headers=admin).json()["skipped_open"] == 1
    # an automatic request may be approved by an administrator (no human requester)
    assert client.post(f"/api/printer-purchasing/requests/{rows[0]['id']}/approve", headers=admin,
                       json={}).json()["status"] == "APPROVED"


def test_purchase_request_queues_notification_on_enabled_channels(client, admin):
    from backend.database.models_remediation import NotificationDelivery
    from backend.database.session import session_scope
    pid = _printer(serial="PRN-4", ip="10.20.0.18")
    ch = client.post("/api/v1/notifications/channels", headers=admin,
                     json={"name": "stores", "kind": "email", "target": "stores@example.org", "min_severity": "info"})
    assert ch.status_code in (200, 201), ch.text
    client.post("/api/printer-purchasing/requests", headers=admin,
                json={"printer_id": pid, "supply_description": "Drum unit", "quantity": 1, "reason": "Drum near end of life"})
    with session_scope() as db:
        d = db.query(NotificationDelivery).filter(NotificationDelivery.kind == "EVENT").all()
        assert len(d) == 1 and d[0].payload["event_kind"] == "printer_purchase_request"
        assert d[0].payload["event"] == "created" and d[0].state == "PENDING"


def _enable(client, admin, **kw):
    body = {"enabled": True, "purpose": "Cost allocation of printing per section, approved by the CIO",
            "approval_reference": "ICT-POL-2026-41", "retention_days": 30} | kw
    r = client.put("/api/print-jobs/policy", headers=admin, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _token(client, admin):
    r = client.post("/api/print-jobs/ingest-tokens", headers=admin, json={"name": "cups-hq"})
    assert r.status_code == 201
    return r.json()


def _job(i, **kw):
    return {"source": "CUPS", "source_job_id": str(i), "printer_ref": "10.20.0.15", "user": "jdoe", "pages": 3,
            "copies": 2, "colour": False, "completed_at": datetime.now(timezone.utc).isoformat()} | kw


def test_print_job_collection_is_off_and_needs_purpose_and_approval(client, admin):
    p = client.get("/api/print-jobs/policy", headers=admin).json()
    assert p["enabled"] is False and p["inspection_enabled"] is False and "NONE" in p["content_storage"]
    assert client.put("/api/print-jobs/policy", headers=admin, json={"enabled": True, "purpose": "x"}).status_code == 422
    assert client.put("/api/print-jobs/policy", headers=admin,
                      json={"enabled": False, "inspection_enabled": True}).status_code == 422
    t = _token(client, admin)
    r = client.post("/api/print-jobs/ingest", headers={"X-INM-Ingest-Token": t["token"]}, json={"jobs": [_job(1)]})
    assert r.status_code == 403                                     # policy off: nothing stored
    assert client.post("/api/print-jobs/ingest", json={"jobs": [_job(1)]}).status_code == 401
    assert client.post("/api/print-jobs/ingest", headers={"X-INM-Ingest-Token": t["token"][:-2] + "zz"},
                       json={"jobs": [_job(1)]}).status_code == 401


def test_metadata_ingest_pseudonymises_dedupes_and_restricts_reads(client, admin):
    pid = _printer()
    _enable(client, admin)
    t = _token(client, admin)
    h = {"X-INM-Ingest-Token": t["token"]}
    job = _job(10, document_name="Payroll Nov.xlsx", content_text="should be discarded")
    out = client.post("/api/print-jobs/ingest", headers=h, json={"jobs": [job, _job(11, user="asmith")]}).json()
    assert out["accepted"] == 2 and out["content_discarded_uninspected"] == 1 and out["inspected"] == 0
    assert client.post("/api/print-jobs/ingest", headers=h, json={"jobs": [job]}).json()["duplicates"] == 1
    op = make_user(client, admin, "print.op", "operator")
    auditor = make_user(client, admin, "print.auditor", "auditor")
    assert client.get("/api/print-jobs", headers=op).status_code == 403        # job-level data is restricted
    s = client.get("/api/print-jobs/summary", headers=op).json()
    assert s["printers"] == [{"printer_id": pid, "printer_ref": "10.20.0.15", "jobs": 2, "pages": 12, "colour_jobs": 0}]
    rows = client.get("/api/print-jobs", headers=auditor).json()
    assert len(rows) == 2 and all(r["user_pseudonymised"] and r["user_ref"].startswith("u_") for r in rows)
    assert all(r["document_name"] is None for r in rows)          # names not stored unless the policy allows
    assert {r["inspection_state"] for r in rows} == {"SKIPPED", "NOT_INSPECTED"}
    assert "jdoe" not in str(rows) and "asmith" not in str(rows)
    from backend.database.models import AuditLog
    from backend.database.session import session_scope
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "print_jobs_viewed",
                                         AuditLog.username == "print.auditor").count() == 1
    # revoke -> refused
    assert client.delete(f"/api/print-jobs/ingest-tokens/{t['id']}", headers=admin).status_code == 204
    assert client.post("/api/print-jobs/ingest", headers=h, json={"jobs": [_job(12)]}).status_code == 401


def test_document_inspection_keeps_only_hash_and_masked_findings(client, admin):
    _printer()
    _enable(client, admin, inspection_enabled=True, pseudonymise_users=False, store_document_names=True,
            keyword_rules=[{"name": "PROJECT_ORION", "keywords": ["Orion"], "severity": "minor"}])
    t = _token(client, admin)
    text = ("CONFIDENTIAL\nCard 4111 1111 1111 1111 exp 12/29. TRN: 123-456-789. Project Orion budget. "
            "Not a card: 1234 5678 9012 3456.")
    out = client.post("/api/print-jobs/ingest", headers={"X-INM-Ingest-Token": t["token"]},
                      json={"jobs": [_job(20, content_text=text, document_name="budget.pdf"),
                                     _job(21, content_text="Ordinary memo about the staff picnic.")]}).json()
    assert out == {"accepted": 2, "duplicates": 0, "inspected": 2, "findings": 4, "content_discarded_uninspected": 0}
    rows = {r["source_job_id"]: r for r in client.get("/api/print-jobs", headers=admin).json()}
    assert rows["21"]["inspection_state"] == "CLEAN" and rows["21"]["findings"] == []
    f = {x["rule_name"]: x for x in rows["20"]["findings"]}
    assert set(f) == {"PAYMENT_CARD_NUMBER", "JM_TAXPAYER_REGISTRATION_NUMBER", "CLASSIFICATION_MARKING", "PROJECT_ORION"}
    assert f["PAYMENT_CARD_NUMBER"]["match_count"] == 1 and f["PAYMENT_CARD_NUMBER"]["excerpt"] == "****1111"
    assert f["JM_TAXPAYER_REGISTRATION_NUMBER"]["excerpt"] == "****6789"
    assert rows["20"]["user_ref"] == "jdoe" and rows["20"]["document_name"] == "budget.pdf"
    assert len(rows["20"]["content_sha256"]) == 64
    blob = str(rows)
    assert "4111 1111" not in blob and "123-456-789" not in blob and "budget" in blob   # no content stored or returned
    from backend.database.models_rc17 import PrintJob, PrintJobFinding
    from backend.database.session import session_scope
    with session_scope() as db:
        stored = " ".join(str(v) for j in db.query(PrintJob) for v in vars(j).values()) + \
                 " ".join(str(v) for x in db.query(PrintJobFinding) for v in vars(x).values())
        assert "4111111111111111" not in stored.replace(" ", "") and "Orion budget" not in stored
    rv = client.post(f"/api/print-jobs/findings/{f['PROJECT_ORION']['id']}/review", headers=admin,
                     json={"state": "DISMISSED"})
    assert rv.json()["review_state"] == "DISMISSED"


def test_print_job_retention_purges_old_jobs_and_findings(client, admin):
    _enable(client, admin, inspection_enabled=True)
    t = _token(client, admin)
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    client.post("/api/print-jobs/ingest", headers={"X-INM-Ingest-Token": t["token"]},
                json={"jobs": [_job(30, completed_at=old, content_text="SECRET plan"), _job(31)]})
    out = client.post("/api/print-jobs/purge", headers=admin).json()
    assert out == {"deleted": 1, "retention_days": 30}
    from backend.database.models_rc17 import PrintJob, PrintJobFinding
    from backend.database.session import session_scope
    with session_scope() as db:
        assert db.query(PrintJob).count() == 1 and db.query(PrintJobFinding).count() == 0


def test_inspection_rules_unit():
    from backend.printers.print_jobs import inspect_text
    assert inspect_text("nothing here 1234", []) == []
    f = inspect_text("5500-0000-0000-0004 and 4111111111111112", [])
    assert f == [{"rule_name": "PAYMENT_CARD_NUMBER", "severity": "major", "match_count": 1, "excerpt": "****0004"}]
    assert inspect_text("employee 123456789 has a TRN", []) == []   # TRN must precede the number
