"""Print-job metadata and document inspection (rc17, owner choice "Metadata + document inspection", 2026-11-25).

Governance:
  * OFF by default. Enabling collection needs a written purpose statement and an approval reference; enabling
    content inspection additionally needs collection enabled. Every policy change is audited.
  * Ingestion only from a registered print-server agent presenting an ingestion token (stored as SHA-256).
  * Document CONTENT is never stored. When inspection is on, the agent may send extracted text; the server runs the
    rules in memory, keeps the SHA-256 of the text, the rule names, counts and a masked token per finding, and
    discards the text. When inspection is off, any submitted text is discarded unread (state SKIPPED).
  * User identities can be pseudonymised (HMAC-SHA256 with a server key) before storage.
  * Job-level records (who printed what) are readable only by admin and auditor and every read is audited;
    operators see per-printer aggregates without user identities.
  * Retention: daily purge of jobs (and their findings) older than the policy's retention_days (7–730).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_printer import PrinterDevice
from ..database.models_rc17 import PrintJob, PrintJobFinding
from ..security.deps import audit

POLICY_KEY = "print_job_policy"
TOKEN_PREFIX = "print-ingest-token:"
DEFAULT_POLICY = {"enabled": False, "inspection_enabled": False, "purpose": "", "approval_reference": "",
                  "retention_days": 90, "pseudonymise_users": True, "store_document_names": False,
                  "keyword_rules": []}
SOURCES = ("CUPS", "WINDOWS_PRINT_SERVER", "IPP", "OTHER")
MAX_TEXT = 1_000_000          # characters of extracted text inspected per job; more is truncated (and flagged)
SEVERITIES = ("info", "minor", "major")


class PrintJobError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- policy
def policy(db: Session) -> dict:
    row = db.get(AppSetting, POLICY_KEY)
    return DEFAULT_POLICY | (row.value if row else {})


def _validate_keyword_rules(rules) -> list[dict]:
    if not isinstance(rules, list) or len(rules) > 50:
        raise PrintJobError("keyword_rules must be a list of at most 50 rules.")
    out = []
    for r in rules:
        name = str(r.get("name", "")).strip().upper()
        words = [str(w).strip() for w in r.get("keywords", []) if str(w).strip()]
        sev = r.get("severity", "minor")
        if not re.fullmatch(r"[A-Z0-9_]{3,40}", name) or not 1 <= len(words) <= 50 or sev not in SEVERITIES \
                or any(len(w) > 80 for w in words):
            raise PrintJobError("Each keyword rule needs NAME_LIKE_THIS (3–40), 1–50 keywords of ≤80 characters and "
                                "a severity of info, minor or major.")
        out.append({"name": name, "keywords": words, "severity": sev})
    return out


def set_policy(db: Session, value: dict, actor: str, request=None) -> dict:
    v = DEFAULT_POLICY | {k: value[k] for k in DEFAULT_POLICY if k in value}
    v["purpose"] = str(v["purpose"] or "").strip()[:1000]
    v["approval_reference"] = str(v["approval_reference"] or "").strip()[:120]
    if not (isinstance(v["retention_days"], int) and 7 <= v["retention_days"] <= 730):
        raise PrintJobError("Retention must be 7–730 days.")
    if v["enabled"] and (len(v["purpose"]) < 20 or not v["approval_reference"]):
        raise PrintJobError("Enabling print-job collection needs a purpose statement (20+ characters) and an "
                            "approval reference.")
    if v["inspection_enabled"] and not v["enabled"]:
        raise PrintJobError("Document inspection needs print-job collection to be enabled.")
    v["keyword_rules"] = _validate_keyword_rules(v["keyword_rules"])
    old = policy(db)
    row = db.get(AppSetting, POLICY_KEY) or AppSetting(key=POLICY_KEY, value={})
    row.value, row.updated_by, row.updated_at = v, actor, _now()
    db.add(row)
    changed = sorted(k for k in v if v[k] != old.get(k))
    audit(db, "print_job_policy_set", username=actor, target=POLICY_KEY, request=request,
          detail=f"enabled={v['enabled']} inspection={v['inspection_enabled']} retention={v['retention_days']}d "
                 f"pseudonymise={v['pseudonymise_users']} approval={v['approval_reference']} changed={changed}")
    return v


# ---------------------------------------------------------------- ingestion tokens
def create_token(db: Session, name: str, actor: str, request=None) -> dict:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{3,60}", name):
        raise PrintJobError("Token name: 3–60 letters, digits, space, dot, underscore or dash.")
    tid = secrets.token_hex(8)
    raw = f"inmpj_{tid}_{secrets.token_urlsafe(32)}"
    db.add(AppSetting(key=TOKEN_PREFIX + tid, value={"name": name, "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                                                       "created_by": actor, "created_at": _now().isoformat(),
                                                       "revoked": False, "last_used_at": None},
                      updated_by=actor, updated_at=_now()))
    audit(db, "print_ingest_token_created", username=actor, target=tid, detail=name, request=request)
    return {"id": tid, "name": name, "token": raw}


def tokens(db: Session) -> list[dict]:
    return [{"id": r.key[len(TOKEN_PREFIX):]} | {k: r.value.get(k) for k in ("name", "created_by", "created_at",
                                                                             "revoked", "last_used_at")}
            for r in db.scalars(select(AppSetting).where(AppSetting.key.startswith(TOKEN_PREFIX)))]


def revoke_token(db: Session, tid: str, actor: str, request=None) -> None:
    row = db.get(AppSetting, TOKEN_PREFIX + tid)
    if row is None:
        raise PrintJobError("Token not found.", 404)
    row.value = row.value | {"revoked": True}
    row.updated_by, row.updated_at = actor, _now()
    audit(db, "print_ingest_token_revoked", username=actor, target=tid, request=request)


def authenticate(db: Session, raw: str | None) -> str | None:
    m = re.fullmatch(r"inmpj_([0-9a-f]{16})_[A-Za-z0-9_-]{20,}", raw or "")
    if not m:
        return None
    row = db.get(AppSetting, TOKEN_PREFIX + m.group(1))
    if row is None or row.value.get("revoked"):
        return None
    if not hmac.compare_digest(row.value.get("sha256", ""), hashlib.sha256(raw.encode()).hexdigest()):
        return None
    row.value = row.value | {"last_used_at": _now().isoformat()}
    return m.group(1)


# ---------------------------------------------------------------- inspection
def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


_CARD = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")
_TRN = re.compile(r"\bTRN\b[^0-9\n]{0,15}([0-9]{3}[- ]?[0-9]{3}[- ]?[0-9]{3})(?![0-9])", re.IGNORECASE)
_MARKING = re.compile(r"\b(TOP SECRET|SECRET|CONFIDENTIAL|RESTRICTED)\b")


def _mask(s: str) -> str:
    digits = re.sub(r"[^0-9]", "", s)
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"


def inspect_text(text: str, keyword_rules: list[dict]) -> list[dict]:
    """Return findings with masked tokens only. Pure function; no I/O; text never leaves this call."""
    findings: list[dict] = []
    cards = [m.group(0) for m in _CARD.finditer(text) if _luhn(re.sub(r"[ -]", "", m.group(0)))]
    if cards:
        findings.append({"rule_name": "PAYMENT_CARD_NUMBER", "severity": "major", "match_count": len(cards),
                         "excerpt": ", ".join(sorted({_mask(c) for c in cards})[:5])})
    trns = [m.group(1) for m in _TRN.finditer(text)]
    if trns:
        findings.append({"rule_name": "JM_TAXPAYER_REGISTRATION_NUMBER", "severity": "major", "match_count": len(trns),
                         "excerpt": ", ".join(sorted({_mask(t) for t in trns})[:5])})
    marks = [m.group(1) for m in _MARKING.finditer(text)]
    if marks:
        findings.append({"rule_name": "CLASSIFICATION_MARKING", "severity": "minor", "match_count": len(marks),
                         "excerpt": ", ".join(sorted(set(marks)))})
    folded = text.casefold()
    for r in keyword_rules:
        hits = {w: folded.count(w.casefold()) for w in r["keywords"]}
        n = sum(hits.values())
        if n:
            findings.append({"rule_name": r["name"], "severity": r["severity"], "match_count": n,
                             "excerpt": ", ".join(sorted(w for w, c in hits.items() if c))[:300]})
    return findings


# ---------------------------------------------------------------- ingestion
def _pseudonym(user: str) -> str:
    from ..config import get_settings
    key = hashlib.sha256(("print-job-pseudonym:" + get_settings().jwt_secret).encode()).digest()
    return "u_" + hmac.new(key, user.casefold().encode(), hashlib.sha256).hexdigest()[:24]


def _match_printer(db: Session, ref: str) -> str | None:
    ref = ref.strip()
    p = db.scalar(select(PrinterDevice.id).where(or_(PrinterDevice.management_ip == ref, PrinterDevice.serial_number == ref,
                                                     func.lower(PrinterDevice.hostname) == ref.lower())).limit(1))
    return p


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def ingest(db: Session, token_id: str, jobs: list[dict]) -> dict:
    pol = policy(db)
    if not pol["enabled"]:
        raise PrintJobError("Print-job collection is disabled by policy.", 403)
    out = {"accepted": 0, "duplicates": 0, "inspected": 0, "findings": 0, "content_discarded_uninspected": 0}
    for j in jobs:
        completed = _aware(j["completed_at"])
        key = hashlib.sha256(f"{j['source']}|{j['source_job_id']}|{j['printer_ref']}|{completed.isoformat()}".encode()
                             ).hexdigest()
        if db.scalar(select(PrintJob.id).where(PrintJob.dedupe_key == key)):
            out["duplicates"] += 1
            continue
        user = j["user"].strip()
        text = j.get("content_text")
        job = PrintJob(source=j["source"], source_job_id=j["source_job_id"], dedupe_key=key,
                       printer_id=_match_printer(db, j["printer_ref"]), printer_ref=j["printer_ref"][:120],
                       user_ref=_pseudonym(user) if pol["pseudonymise_users"] else user[:128],
                       user_pseudonymised=bool(pol["pseudonymise_users"]),
                       document_name=((j.get("document_name") or "")[:255] or None) if pol["store_document_names"] else None,
                       pages=j.get("pages"), copies=j.get("copies") or 1, colour=j.get("colour"), duplex=j.get("duplex"),
                       submitted_at=_aware(j.get("submitted_at")), completed_at=completed)
        findings: list[dict] = []
        if text is not None and pol["inspection_enabled"]:
            truncated = len(text) > MAX_TEXT
            body = text[:MAX_TEXT]
            job.content_sha256 = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
            findings = inspect_text(body, pol["keyword_rules"])
            if truncated:
                findings.append({"rule_name": "INSPECTION_TRUNCATED", "severity": "info", "match_count": 1,
                                 "excerpt": f"only the first {MAX_TEXT} characters were inspected"})
            job.inspection_state = "FINDINGS" if findings else "CLEAN"
            out["inspected"] += 1
        elif text is not None:
            job.inspection_state = "SKIPPED"
            out["content_discarded_uninspected"] += 1
        text = None  # noqa: F841 — drop the only reference before anything else runs
        db.add(job)
        db.flush()
        for f in findings:
            db.add(PrintJobFinding(job_id=job.id, **f))
            out["findings"] += 1
            if f["severity"] == "major":
                from ..infrastructure.notifications import enqueue_event
                enqueue_event(db, kind="print_inspection_finding", severity="major", ref=job.id, event=f["rule_name"],
                              message=f"Print inspection: {f['rule_name']} ({f['match_count']}) on printer "
                                      f"{job.printer_ref}; review in Print jobs",
                              detail={"job_id": job.id, "rule": f["rule_name"], "count": f["match_count"]})
        out["accepted"] += 1
    audit(db, "print_jobs_ingested", username=f"ingest-token:{token_id}", target="print_jobs",
          detail=str(out))
    return out


# ---------------------------------------------------------------- reads, review, retention
def job_dict(j: PrintJob, findings: list[PrintJobFinding] | None = None) -> dict:
    return {"id": j.id, "source": j.source, "source_job_id": j.source_job_id, "printer_id": j.printer_id,
            "printer_ref": j.printer_ref, "user_ref": j.user_ref, "user_pseudonymised": j.user_pseudonymised,
            "document_name": j.document_name, "pages": j.pages, "copies": j.copies, "colour": j.colour,
            "duplex": j.duplex, "completed_at": _aware(j.completed_at).isoformat(),
            "inspection_state": j.inspection_state, "content_sha256": j.content_sha256,
            "findings": [{"id": f.id, "rule_name": f.rule_name, "severity": f.severity, "match_count": f.match_count,
                          "excerpt": f.excerpt, "review_state": f.review_state, "reviewed_by": f.reviewed_by}
                         for f in (findings or [])]}


def review_finding(db: Session, finding_id: str, state: str, actor: str, request=None) -> PrintJobFinding:
    f = db.get(PrintJobFinding, finding_id)
    if f is None:
        raise PrintJobError("Finding not found.", 404)
    if state not in ("DISMISSED", "CONFIRMED"):
        raise PrintJobError("Review state must be DISMISSED or CONFIRMED.")
    f.review_state, f.reviewed_by, f.reviewed_at = state, actor, _now()
    audit(db, "print_inspection_finding_reviewed", username=actor, target=finding_id, detail=state, request=request)
    return f


def summary(db: Session, days: int = 30, printer_ids: set[str] | None = None) -> list[dict]:
    since = _now() - timedelta(days=days)
    q = (select(PrintJob.printer_id, PrintJob.printer_ref, func.count(), func.coalesce(func.sum(PrintJob.pages * PrintJob.copies), 0),
                func.sum(case((PrintJob.colour.is_(True), 1), else_=0)))
         .where(PrintJob.completed_at >= since).group_by(PrintJob.printer_id, PrintJob.printer_ref))
    out = []
    for pid, ref, n, pages, colour in db.execute(q):
        if printer_ids is not None and pid not in printer_ids:
            continue
        out.append({"printer_id": pid, "printer_ref": ref, "jobs": n, "pages": int(pages or 0),
                    "colour_jobs": int(colour or 0)})
    return sorted(out, key=lambda r: -r["pages"])


def purge(db: Session, now: datetime | None = None, actor: str = "print-job-retention") -> dict:
    pol = policy(db)
    cutoff = (now or _now()) - timedelta(days=pol["retention_days"])
    ids = list(db.scalars(select(PrintJob.id).where(PrintJob.completed_at < cutoff).limit(50_000)))
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        db.execute(delete(PrintJobFinding).where(PrintJobFinding.job_id.in_(chunk)))
        db.execute(delete(PrintJob).where(PrintJob.id.in_(chunk)))
    if ids:
        audit(db, "print_jobs_purged", username=actor, target="print_jobs",
              detail=f"{len(ids)} jobs older than {pol['retention_days']} days")
    return {"deleted": len(ids), "retention_days": pol["retention_days"]}
