"""Regression: PostgreSQL serializes the audit head in the database, not a competing process mutex."""
from types import SimpleNamespace
import pytest
from backend.security import deps
from backend.database.models import AuditChainHead,AuditLog

@pytest.mark.parametrize("dialect",["postgresql","sqlite"])
def test_audit_uses_database_transaction_lock_on_postgres(monkeypatch,dialect):
    locks=[]
    class Lock:
        def __enter__(self):
            assert dialect!="postgresql","A process mutex can deadlock two database sessions"
            locks.append(True)
        def __exit__(self,*args): pass
    monkeypatch.setattr(deps,"_chain_lock",Lock())
    class DB:
        def __init__(self):
            self.head=AuditChainHead(id=1,last_hash=deps.GENESIS_HASH)
            self.rows=[]
        def get_bind(self): return SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        def scalar(self,statement):
            assert statement.get_execution_options().get("populate_existing")
            return self.head
        def add(self,row):
            self.rows.append(row)
        def flush(self):
            for i,row in enumerate(self.rows,1): row.id=i
    db=DB()
    first=deps.audit(db,"first",username="test")
    second=deps.audit(db,"second",username="test")
    assert second.prev_hash==first.event_hash
    assert bool(locks)==(dialect=="sqlite")
