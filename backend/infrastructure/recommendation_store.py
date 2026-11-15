"""Single write path for analysis recommendations (site rules, device insights, predictive).

Deduplication: one OPEN record per (rule, target). Re-detection inside the rule's suppression window updates
``last_seen_at``/``occurrences``/evidence instead of creating a duplicate; re-detection after a record was
DISMISSED stays suppressed for the window; after RESOLVED or once the window passes a new record is opened.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models_inm import RecommendationRecord

OPEN_STATES = ("ADVISORY", "ACKNOWLEDGED")
CLOSED_STATES = ("DISMISSED", "RESOLVED")


def _utc(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def upsert(db: Session, *, rule_id: str, rule_version: str, engine: str, title: str, explanation: str, evidence: dict,
           risk: str, source_type: str, actor: str, device_id: str | None = None, legacy_site_id: int | None = None,
           reason_codes: list[str] | None = None, affected: dict | None = None, input_window: str = "latest",
           suppression_minutes: int = 30, correlation_id: str | None = None, now: datetime | None = None) -> tuple[RecommendationRecord | None, str]:
    """Returns (record, outcome) with outcome CREATED | UPDATED | SUPPRESSED."""
    now = now or datetime.now(timezone.utc)
    target = device_id or (f"site:{legacy_site_id}" if legacy_site_id is not None else "global")
    key = f"{rule_id}|{target}|{source_type}"
    latest = db.scalar(select(RecommendationRecord).where(RecommendationRecord.dedupe_key == key)
                       .order_by(RecommendationRecord.created_at.desc()).limit(1))
    if latest is not None:
        seen = _utc(latest.last_seen_at or latest.created_at)
        if latest.approval_state in OPEN_STATES:
            latest.last_seen_at, latest.occurrences = now, (latest.occurrences or 1) + 1
            latest.evidence, latest.title, latest.explanation, latest.rule_version = evidence, title[:200], explanation, rule_version
            return latest, "UPDATED"
        if latest.approval_state == "DISMISSED" and now - _utc(latest.reviewed_at or seen) < timedelta(minutes=suppression_minutes):
            return None, "SUPPRESSED"
    rec = RecommendationRecord(device_id=device_id, legacy_site_id=legacy_site_id, engine=engine, rule_id=rule_id,
                               rule_version=rule_version, source_type=source_type, risk=risk, title=title[:200],
                               explanation=explanation, evidence=evidence, input_window=input_window[:64],
                               approval_state="ADVISORY", requested_by=actor, correlation_id=correlation_id or str(uuid.uuid4()),
                               dedupe_key=key, last_seen_at=now, occurrences=1, reason_codes=reason_codes or [rule_id],
                               affected=affected or ({"devices": [device_id]} if device_id else {"sites": [legacy_site_id]}),
                               created_at=now)
    db.add(rec)
    db.flush()
    return rec, "CREATED"


TRANSITIONS = {
    "ACKNOWLEDGE": ({"ADVISORY"}, "ACKNOWLEDGED"),
    "DISMISS": ({"ADVISORY", "ACKNOWLEDGED"}, "DISMISSED"),
    "RESOLVE": ({"ADVISORY", "ACKNOWLEDGED"}, "RESOLVED"),
    "REOPEN": ({"DISMISSED", "RESOLVED"}, "ADVISORY"),
}


def triage(rec: RecommendationRecord, action: str, actor: str, note: str, now: datetime | None = None) -> None:
    allowed, target = TRANSITIONS[action]
    if rec.approval_state not in allowed:
        raise ValueError(f"cannot {action.lower()} a recommendation that is {rec.approval_state.lower()}")
    now = now or datetime.now(timezone.utc)
    rec.approval_state, rec.reviewed_by, rec.reviewed_at, rec.review_note = target, actor, now, note[:500]
    rec.resolved_at = now if target == "RESOLVED" else None if target == "ADVISORY" else rec.resolved_at
