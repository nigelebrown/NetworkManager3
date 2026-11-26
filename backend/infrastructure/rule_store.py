"""Persistence for the AI rule catalogue (two-person change workflow). Definitions live in domain.rule_catalog."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models_intelligence import AiRuleDefinition, AiRuleVersion
from ..domain.rule_catalog import BUILTIN, PRED_METRIC, SCHEMA, validate_params  # noqa: F401  (re-exported)


def ensure(db: Session) -> None:
    """Seed missing built-in rules. Concurrent first requests on a fresh database may both try to seed (rc18 defect:
    the loser raised IntegrityError -> HTTP 500); each rule is inserted in its own savepoint and a duplicate means
    another request seeded it first."""
    from sqlalchemy.exc import IntegrityError
    have = {r for (r,) in db.execute(select(AiRuleDefinition.rule_id))}
    for b in BUILTIN:
        if b["rule_id"] in have:
            continue
        try:
            with db.begin_nested():
                _seed(db, b)
        except IntegrityError:
            continue                                 # seeded concurrently by another request: nothing to do
    db.flush()


def _seed(db: Session, b: dict) -> None:
    db.add(AiRuleDefinition(rule_id=b["rule_id"], engine=b["engine"], title=b["title"], description=b["description"],
                            inputs=b["inputs"], condition=b["condition"], output=b["output"], risk=b["risk"],
                            precedence=b["precedence"], default_params=dict(b["params"]), params=dict(b["params"]),
                            enabled=True, suppression_minutes=b["suppression"], version=1, updated_by="system"))
    db.add(AiRuleVersion(rule_id=b["rule_id"], version=1, params=dict(b["params"]), enabled=True,
                         suppression_minutes=b["suppression"], proposed_by="system", approved_by="system",
                         note="built-in default"))
    db.flush()


def effective(db: Session, engine: str | None = None) -> dict[str, dict]:
    """rule_id -> {enabled, params, suppression_minutes, version, risk, title}. Safe defaults if unseeded."""
    ensure(db)
    q = select(AiRuleDefinition)
    if engine:
        q = q.where(AiRuleDefinition.engine == engine)
    return {r.rule_id: {"enabled": r.enabled, "params": dict(r.params), "suppression_minutes": r.suppression_minutes,
                        "version": r.version, "risk": r.risk, "title": r.title, "precedence": r.precedence}
            for r in db.scalars(q)}


def propose(db: Session, rule_id: str, *, params: dict | None, enabled: bool | None, suppression_minutes: int | None,
            actor: str, note: str) -> AiRuleDefinition:
    r = db.get(AiRuleDefinition, rule_id)
    if r is None:
        raise LookupError("rule not found")
    if r.pending:
        raise PermissionError("a change is already awaiting review for this rule")
    merged = dict(r.params) | validate_params(rule_id, params or {})
    if suppression_minutes is not None and not 0 <= suppression_minutes <= 10080:
        raise ValueError("suppression_minutes must be between 0 and 10080")
    change = {"params": merged, "enabled": r.enabled if enabled is None else enabled,
              "suppression_minutes": r.suppression_minutes if suppression_minutes is None else suppression_minutes}
    if change == {"params": r.params, "enabled": r.enabled, "suppression_minutes": r.suppression_minutes}:
        raise ValueError("proposal does not change the rule")
    r.pending, r.pending_by, r.pending_note, r.pending_at = change, actor, note, datetime.now(timezone.utc)
    db.flush()
    return r


def decide(db: Session, rule_id: str, *, approve: bool, actor: str, note: str) -> AiRuleDefinition:
    r = db.get(AiRuleDefinition, rule_id)
    if r is None:
        raise LookupError("rule not found")
    if not r.pending:
        raise ValueError("no pending change")
    if (r.pending_by or "").casefold() == actor.casefold():
        raise PermissionError("rule changes require an independent approver")
    if approve:
        r.params, r.enabled, r.suppression_minutes = r.pending["params"], r.pending["enabled"], r.pending["suppression_minutes"]
        r.version += 1
        r.updated_by, r.updated_at = actor, datetime.now(timezone.utc)
        db.add(AiRuleVersion(rule_id=r.rule_id, version=r.version, params=r.params, enabled=r.enabled,
                             suppression_minutes=r.suppression_minutes, proposed_by=r.pending_by or "?", approved_by=actor,
                             note=f"{r.pending_note or ''} | approved: {note}"[:500]))
    r.pending = r.pending_by = r.pending_note = r.pending_at = None
    db.flush()
    return r
