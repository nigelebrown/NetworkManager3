"""rc18 defect (found by the browser run): two first requests on a fresh database both seeded the built-in AI rules;
the loser hit UNIQUE(rule_id) and answered HTTP 500 (seen as a failed model-training request). Seeding is now
per-rule in a savepoint and a duplicate means another request already seeded it."""
from backend.database.models_intelligence import AiRuleDefinition
from backend.database.session import create_schema, get_engine, session_scope
from backend.infrastructure import rule_store


def test_concurrent_first_seed_does_not_fail(app_env, monkeypatch):
    create_schema()
    original = rule_store._seed
    raced = []

    def racing_seed(db, b):
        if not raced:                                  # another request commits the same rule first
            from sqlalchemy.orm import Session
            with Session(get_engine()) as other:
                original(other, b)
                other.commit()
            raced.append(b["rule_id"])
        original(db, b)
    monkeypatch.setattr(rule_store, "_seed", racing_seed)
    with session_scope() as db:
        rule_store.ensure(db)                          # must not raise
    with session_scope() as db:
        ids = [r.rule_id for r in db.query(AiRuleDefinition)]
        assert raced and len(ids) == len(set(ids)) == len(rule_store.BUILTIN)
        assert rule_store.effective(db)                # usable afterwards
