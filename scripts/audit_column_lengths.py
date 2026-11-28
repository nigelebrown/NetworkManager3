"""Static audit (rc18): string literals passed to a model constructor that exceed the String(n) column.

SQLite ignores VARCHAR lengths; PostgreSQL (production) refuses them. For every `Model(col="literal", ...)` in
backend code the literal is compared with that model's column length (exact table, no guessing across tables).
Runtime-computed values are covered by running the contract suite on PostgreSQL (INM_TEST_PG_ADMIN_URL).
Usage: python -I scripts/audit_column_lengths.py <repo root>   -> exit 1 on findings."""
import ast, os, sys, pathlib, tempfile
root = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(root))
os.environ.setdefault("SIMS_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/a.db")
from backend.database import session  # noqa: F401  registers every model module
from backend.database.models import Base
classes = {m.class_.__name__: m.class_.__table__ for m in Base.registry.mappers}
findings = []
for p in (root / "backend").rglob("*.py"):
    if "migrations" in p.parts or "tests" in p.parts:
        continue
    for node in ast.walk(ast.parse(p.read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
        table = classes.get(name)
        if table is None:
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg in table.c and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                n = getattr(table.c[kw.arg].type, "length", None)
                if isinstance(n, int) and len(kw.value.value) > n:
                    findings.append(f"{p.relative_to(root)}:{node.lineno}: {table.name}.{kw.arg} VARCHAR({n}) "
                                    f"<- {kw.value.value!r} ({len(kw.value.value)})")
for f in sorted(set(findings)):
    print(f)
print(f"{len(set(findings))} over-length literal(s) in model constructors")
sys.exit(1 if findings else 0)
