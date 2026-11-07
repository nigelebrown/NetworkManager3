"""Work Order 001 inventory: scan the frozen SIMS baseline and emit parity docs.

Usage: python -I tools/inventory.py <repo-root> <baseline-ref>
Reads files from `git show <ref>:<path>` so the inventory always describes the frozen baseline.
"""
from __future__ import annotations

import ast
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
ref = sys.argv[2] if len(sys.argv) > 2 else "sims-baseline"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


files = git("ls-tree", "-r", "--name-only", ref).split()
src = {f: git("show", f"{ref}:{f}") for f in files if f.endswith((".py", ".tsx", ".ts"))}

# ---- API routes -----------------------------------------------------------------------
routes = []
for f, text in src.items():
    if not f.startswith("backend/api/"):
        continue
    prefix = {}
    for m in re.finditer(r"(\w+)\s*=\s*APIRouter\(([^)]*)\)", text):
        p = re.search(r'prefix="([^"]*)"', m.group(2))
        prefix[m.group(1)] = p.group(1) if p else ""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and isinstance(dec.func.value, ast.Name):
                method = dec.func.attr.upper()
                if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    continue
                path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else ""
                role = "public"
                seg = ast.get_source_segment(text, node) or ""
                rm = re.search(r'require_role\("(\w+)"\)', seg)
                if rm:
                    role = rm.group(1)
                elif "get_current_user" in seg:
                    role = "authenticated"
                routes.append((method, prefix.get(dec.func.value.id, "") + path, f, node.name, role))
routes.sort(key=lambda r: (r[1], r[0]))

# ---- screens ---------------------------------------------------------------------------
screens = sorted(f for f in src if f.startswith("frontend/src/pages/"))
components = sorted(f for f in src if f.startswith("frontend/src/components/") or f.startswith("frontend/src/charts/"))

# ---- ORM entities ----------------------------------------------------------------------
models = re.findall(r'class (\w+)\(Base\):\s*(?:"""[^"]*"""\s*)?__tablename__ = "(\w+)"', src["backend/database/models.py"], re.S)

# ---- tests -------------------------------------------------------------------------------
test_defs = {f: len(re.findall(r"^def test_\w+", t, re.M)) for f, t in src.items() if f.startswith("backend/tests/test_")}

# ---- Aruba operations / safety switches -------------------------------------------------
client = src["backend/aruba/client.py"]
ro_cmds = re.search(r"READ_ONLY_COMMANDS = \((.*?)\)", client, re.S).group(1)
ro_cmds = re.findall(r'"([^"]+)"', ro_cmds)
cfg_eps = re.search(r"CONFIG_ENDPOINTS = (.*)", client)
settings_fields = re.findall(r"^    (\w+): [^=]+ = ", src["backend/config.py"], re.M)
ai_tools = re.findall(r'^    "(\w+)": \(', src["backend/ai/context.py"], re.M)

TARGET = {
    "backend/api/auth.py": ("identity + authorization + audit ports", "F003", "Harden: logout, request IDs, forced initial password change, auditor role"),
    "backend/api/data.py": ("presentation/api legacy facade -> application queries", "F002/F001/F024", "Retain via compatibility facade; site-scope filter added"),
    "backend/api/sites.py": ("presentation/api legacy facade; Phase 2 onboarding", "F020", "Retain; REST-enable write denied by Phase 1 write lock"),
    "backend/api/config.py": ("application/change_orchestration (read/plan only)", "F033", "Plan retained; apply denied + audited by write lock"),
    "backend/api/experience.py": ("domain/experience + application/experience", "F022", "Retain unchanged; parity fixture test"),
    "backend/api/ai.py": ("ai_service/advisory via isolated AI gateway", "F014", "Retain; Ollama reached only via ai_service container"),
    "backend/api/firmware.py": ("modules/firmware", "F037", "Screens retained; upgrade/cert apply denied by write lock"),
    "backend/api/system.py": ("presentation/api_v1/system", "F005", "Retain + /api/v1/system/services"),
}


def target_for(path: str) -> tuple[str, str, str]:
    if path in TARGET:
        return TARGET[path]
    if path.startswith("backend/aruba/"):
        return ("infrastructure/adapters/aruba_instant (wrapped behind DeviceReadPort)", "F017/F018", "Retain; wrapped, write paths gated")
    if path.startswith("backend/monitoring/"):
        return ("workers/network_worker + domain alerts", "F009/F024", "Scheduler moved out of API into worker")
    if path.startswith("backend/experience/"):
        return ("domain/experience", "F022", "Retain unchanged")
    if path.startswith("backend/configmgmt/"):
        return ("application/change_orchestration", "F030/F033", "Retain; legacy backups flagged NOT_RESTORABLE")
    if path.startswith(("backend/firmware/", "backend/certs/")):
        return ("modules/firmware, modules/certificates", "F037", "Retain; hardware ops unverified + write-locked")
    if path.startswith("backend/ai/"):
        return ("ai_service/advisory", "F014/F015", "Retain; isolated network path")
    if path.startswith("backend/security/"):
        return ("infrastructure/security + audit port", "F003/F016", "Harden: hash-chained audit, scope contextvar")
    if path.startswith("backend/database/"):
        return ("infrastructure/persistence (PostgreSQL) + Timescale telemetry", "F011/F012", "Migrate; Alembic 0004 adds INM tables")
    if path.startswith("frontend/src/pages/"):
        return ("frontend/src/pages (preserved) ", "F001", "Retain; live WebSocket invalidation")
    if path.startswith("frontend/"):
        return ("frontend/src (preserved)", "F001/F010", "Retain; useLoad gains WS invalidation")
    if path.startswith("backend/tests/"):
        return ("backend/tests (legacy regression suite)", "INTEG-P1-01", "Run unchanged against integrated code")
    return ("(unchanged support file)", "", "Retain")


out_csv = io.StringIO()
w = csv.writer(out_csv)
w.writerow(["source_path", "kind", "target_module", "feature_ids", "treatment"])
for f in files:
    if f.startswith(("backend/", "frontend/src/")) and not f.endswith("__init__.py"):
        kind = "test" if "/tests/" in f else "screen" if "/pages/" in f else "frontend" if f.startswith("frontend") else "backend"
        t = target_for(f)
        w.writerow([f, kind, t[0], t[1], t[2]])
(root / "docs").mkdir(exist_ok=True)
(root / "docs" / "source_to_target_map.csv").write_text(out_csv.getvalue())

md = [f"# Legacy feature-parity inventory (Work Order 001)\n",
      f"Generated by `tools/inventory.py` from git ref `{ref}` (the frozen, unmodified SIMS source). "
      "Baseline test *results* are in `evidence/baseline/` — this file lists what exists, not what passes.\n",
      f"## API routes ({len(routes)})\n", "| Method | Path | Minimum role | Source | Handler |", "|---|---|---|---|---|"]
md += [f"| {m} | `{p}` | {r} | {f} | {h} |" for m, p, f, h, r in routes]
md += ["", f"Plus `GET /api/health` (public) and the SPA fallback in `backend/main.py`.", "",
       f"## React screens ({len(screens)})", ""] + [f"- `{s}`" for s in screens]
md += ["", f"Shared components: {', '.join('`'+Path(c).name+'`' for c in components)}", "",
       f"## ORM entities ({len(models)})", "", "| Class | Table |", "|---|---|"] + [f"| {c} | `{t}` |" for c, t in models]
md += ["", f"## Test definitions ({sum(test_defs.values())} `def test_` functions; parametrised cases expand at run time)", "",
       "| File | Definitions |", "|---|---|"] + [f"| {f} | {n} |" for f, n in sorted(test_defs.items())]
md += ["", "## Aruba Instant operations", "",
       "Read-only `/rest/show-cmd` allowlist: " + ", ".join(f"`{c}`" for c in ro_cmds),
       "", f"Write endpoints (guarded by `SIMS_ALLOW_CONFIG_CHANGES`): `{cfg_eps.group(1).strip() if cfg_eps else 'n/a'}`",
       "", "SSH write paths (`backend/aruba/ssh.py`): enable REST API (`configure terminal`, `allow-rest-api`, `end`, `commit apply`) and TFTP certificate load.",
       "", "## AI assistant fixed read-only lookup tools", ""] + [f"- `{t}`" for t in ai_tools]
md += ["", "## Settings / feature flags (`SIMS_*`)", "", ", ".join(f"`{s}`" for s in settings_fields), "",
       "## Background tasks", "",
       "- `poll_all` — APScheduler interval job started inside the FastAPI lifespan (`backend/main.py`). **Moved to the network worker in INM.**",
       "- `prune_old` — retention pruning, called at the end of each poll.", "",
       "## Parity commitments for Phase 1", "",
       "Every screen above stays reachable in the integrated UI, every legacy `/api` route stays mounted unchanged "
       "(compatibility facade = the original routers), and the full legacy test suite runs against the integrated code. "
       "Mapping per file: `docs/source_to_target_map.csv`. Mapping per feature: `docs/Feature_Integration_Traceability_v4.csv`."]
(root / "docs" / "legacy_feature_parity.md").write_text("\n".join(md) + "\n")
print(f"routes={len(routes)} screens={len(screens)} models={len(models)} test_defs={sum(test_defs.values())} ro_cmds={len(ro_cmds)} ai_tools={len(ai_tools)}")
