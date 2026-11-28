"""Every API path the frontend calls must exist on the backend (rc18 section 5: unconnected frontend).

Usage: python -I scripts/audit_frontend_api.py <repo root>   -> exit 1 when a call has no backend route."""
import os, re, sys, pathlib, tempfile
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
tmp = tempfile.mkdtemp()
os.environ.update({"SIMS_DATABASE_URL": f"sqlite:///{tmp}/m.db", "SIMS_MODE": "demo", "SIMS_ROLE": "api",
                   "SIMS_JWT_SECRET": "x" * 48, "INM_FEATURE_PURCHASING": "true", "INM_FEATURE_PRINT_JOBS": "true"})
from cryptography.fernet import Fernet
os.environ["SIMS_FERNET_KEY"] = Fernet.generate_key().decode()
from backend.main import create_app
app = create_app()
routes = []
os.environ["SIMS_ADMIN_PASSWORD"] = "Audit-Admin-Password-2026!"
paths = list(app.openapi()["paths"]) + ["/api/health", "/api/v1/ready"]
for path in paths:
    if path.startswith("/api"):
        rx = "^" + re.sub(r"\{[^}]+:path\}", ".+", re.sub(r"\{[^}:]+\}", "[^/]+", path)) + "$"
        routes.append((path, re.compile(rx), set()))
calls = set()
for f in (root / "frontend/src").rglob("*.ts*"):
    s = f.read_text()
    consts = dict(re.findall(r'const (\w+) = "(/(?:v1|api)[^"]*)"', s))
    for m in re.finditer(r'[`"](\$\{(\w+)\}|/(?:v1|api|printers|auth|sites|system|config|data|firmware|ai|experience)[^`"]*)([^`"]*)[`"]', s):
        lit = m.group(0)[1:-1]
        if lit.startswith("${"):
            base = consts.get(m.group(2))
            if not base:
                continue
            lit = base + lit[len(m.group(1)):]
        if not lit.startswith("/") or " " in lit or lit.endswith((".csv", ".png")) or lit.startswith("//"):
            continue
        path = lit.split("?")[0]
        path = re.sub(r"\$\{[^}]+\}", "X", path)
        calls.add((path if path.startswith("/api") else "/api" + path, str(f.relative_to(root))))
def seg_match(call: str, template: str) -> bool:
    """X in a call is a runtime value; {param} in a template accepts anything."""
    a, b = call.strip("/").split("/"), template.strip("/").split("/")
    if len(a) != len(b):
        return False
    return all(x == "X" or y.startswith("{") or x == y for x, y in zip(a, b))


missing = []
for path, src in sorted(calls):
    path = path[:-1] if path.endswith("X") and not path.endswith("/X") else path   # `${qs(...)}` query suffix
    path = path + "X" if path.endswith("/") else path                               # id concatenated at runtime
    if not any(rx.match(path) for _, rx, _ in routes) and not any(seg_match(path, t) for t, _, _ in routes) \
            and not any(t.startswith(path + "/") for t, _, _ in routes) \
            and not (path.endswith("/X") and any(seg_match(path, "/".join(t.split("/")[:path.count("/") + 1]))
                                                 for t, _, _ in routes)):          # "..." + id + "/action"
        missing.append((path, src))
# client-side routes (react router) are not API calls: drop paths that are app routes
app_routes = set(re.findall(r'path="([^"]+)"', (root / "frontend/src/App.tsx").read_text()))
missing = [(p, s) for p, s in missing if p.removeprefix("/api/").split("/")[0] not in {r.split("/")[0] for r in app_routes} or p.startswith("/api/v1")]
print("backend API routes:", len(routes))
for p, s in missing:
    print("NO BACKEND ROUTE:", p, "<-", s)
print(f"{len(calls)} frontend API paths checked, {len(missing)} without a backend route")
sys.exit(1 if missing else 0)
