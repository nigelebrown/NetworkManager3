#!/usr/bin/env bash
# One command: initialise the test environment, run every automated suite and write an evidence report.
#
#   sudo bash scripts/run_full_test_suite.sh [--quick] [--no-browser] [--no-fullstack] [--no-load]
#
# Suites (each recorded PASS / FAIL / SKIPPED with counts and log in evidence/test-run-<UTC>/):
#   1  python unit + contract tests on SQLite                       tests/ backend/tests
#   2  the same contract suite on a real PostgreSQL 16 management DB  (INM_TEST_PG_ADMIN_URL, enforced types/lengths)
#   3  frontend type check + production build, frontend->backend API route audit, npm audit (production deps)
#   4  PostgreSQL migration chain 0004..head, expand-only checks, pg_dump/pg_restore drill
#   5  full distributed stack: PostgreSQL + TimescaleDB, NATS JetStream (shipped ACLs), Loki, Alloy, API role,
#      worker role, HTTPS CX emulators -> tests/fullstack (cross-service contracts, outages, restarts)
#   6  upgrade / backup / restore on the stack                       tests/fullstack/test_upgrade_backup_restore.py
#   6b native seven-role stack incl. the isolated AI service          tests/integration (dev/native_stack.sh)
#   7  browser suites (Chrome): gui_workflow + live_operations (full stack), demo GUI, ops workflows, routing,
#      group import, integrations/ML, printer GUI, intelligence GUI, navigation/overlap audit
#   8  mixed load: API + separate worker on PostgreSQL/TimescaleDB, N users, latency / errors / RSS
#
# Every device is EMULATED. Nothing here is hardware certification. Needs root (PostgreSQL as the postgres user,
# loopback aliases for the emulated switches) and the packages listed in tests/fullstack/stack.sh.
# Without Docker this runs natively; .github/workflows/full-test-suite.yml runs the same script on CI.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
QUICK=0 BROWSER=1 FULLSTACK=1 LOAD=1
for a in "$@"; do case $a in --quick) QUICK=1 ;; --no-browser) BROWSER=0 ;; --no-fullstack) FULLSTACK=0 ;;
  --no-load) LOAD=0 ;; *) echo "unknown option $a"; exit 2 ;; esac; done
export INM_PY=${INM_PY:-$ROOT/.venv/bin/python}
PY=$INM_PY
export PATH="$(dirname "$PY"):$PATH"  # shell-based verifier checks must use the same locked environment
RUN="test-run-$(date -u +%Y%m%dT%H%M%SZ)"
EV="$ROOT/evidence/$RUN"
mkdir -p "$EV"
SUMMARY="$EV/summary.tsv"
: > "$SUMMARY"
export SIMS_DISABLE_SCHEDULER=true INM_E2E_CHROME=${INM_E2E_CHROME:-/usr/bin/google-chrome}

record() {  # name status counts log
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$SUMMARY"
  printf '%-34s %-8s %s\n' "$1" "$2" "$3"
}
pytest_suite() {  # name log args...
  local name=$1 log=$2; shift 2
  "$PY" -m pytest -q -p no:cacheprovider -W ignore -rs --junitxml="$EV/$name.xml" "$@" > "$EV/$log" 2>&1
  local rc=$? line; line=$(grep -E "^[0-9]+ (passed|failed)|[0-9]+ (passed|failed|skipped|error)" "$EV/$log" | tail -1)
  if [[ $rc -eq 0 ]] && grep -qE "[0-9]+ passed" <<<"$line"; then record "$name" PASS "$line" "$log"
  elif [[ $rc -eq 5 ]] || { [[ $rc -eq 0 ]] && ! grep -qE "[0-9]+ passed" <<<"$line"; }; then record "$name" SKIPPED "${line:-no tests ran}" "$log"
  else record "$name" FAIL "$line" "$log"; fi
}
wait_url() { for _ in $(seq 1 "${2:-120}"); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; return 1; }

echo "== environment"
{ echo "run: $RUN"; echo "commit: $(git rev-parse HEAD 2>/dev/null || echo n/a)"; echo "version: $(cat VERSION)"
  uname -a; "$PY" --version; node --version 2>/dev/null; /usr/lib/postgresql/16/bin/postgres --version 2>/dev/null
  "$INM_E2E_CHROME" --version 2>/dev/null; } > "$EV/environment.txt"

# ---------------------------------------------------------------------------- 1. unit + contract (SQLite)
echo "== 1 unit + contract (SQLite)"
pytest_suite unit_contract_sqlite unit_contract_sqlite.log tests/unit tests/contract
pytest_suite legacy_regression legacy_regression.log backend/tests

# ---------------------------------------------------------------------------- PostgreSQL (shared by 2, 4, 5)
if [[ $FULLSTACK -eq 1 ]]; then
  bash tests/fullstack/stack.sh down >/dev/null 2>&1 || true
  rm -rf /tmp/inm-stack/pg /tmp/inm-stack/nats /tmp/inm-stack/loki /tmp/inm-stack/vault
  if bash tests/fullstack/stack.sh up > "$EV/stack_up.log" 2>&1; then record stack_up PASS "all roles running" stack_up.log
  else record stack_up FAIL "see log" stack_up.log; FULLSTACK=0; fi
fi
PG_ADMIN="postgresql+psycopg://postgres@127.0.0.1:56432/postgres"

# ---------------------------------------------------------------------------- 2. contract suite on PostgreSQL
if [[ $FULLSTACK -eq 1 && $QUICK -eq 0 ]]; then
  echo "== 2 contract suite on PostgreSQL"
  INM_TEST_PG_ADMIN_URL=$PG_ADMIN pytest_suite unit_contract_postgresql unit_contract_postgresql.log tests/unit tests/contract
else record unit_contract_postgresql SKIPPED "needs the stack PostgreSQL (--no-fullstack/--quick)" -; fi

# ---------------------------------------------------------------------------- 3. frontend
echo "== 3 frontend"
if (cd frontend && { [[ -d node_modules ]] || npm ci --no-audit --no-fund; } && npm run build) > "$EV/frontend_build.log" 2>&1
then record frontend_typecheck_build PASS "tsc -b + vite build" frontend_build.log
else record frontend_typecheck_build FAIL "see log" frontend_build.log; fi
if "$PY" -I scripts/audit_frontend_api.py "$ROOT" > "$EV/frontend_api_audit.log" 2>&1
then record frontend_api_route_audit PASS "$(tail -1 "$EV/frontend_api_audit.log")" frontend_api_audit.log
else record frontend_api_route_audit FAIL "$(tail -1 "$EV/frontend_api_audit.log")" frontend_api_audit.log; fi
if (cd frontend && npm audit --omit=dev) > "$EV/npm_audit.log" 2>&1
then record npm_audit_production PASS "$(grep -E 'found|vulnerabilit' "$EV/npm_audit.log" | tail -1)" npm_audit.log
else record npm_audit_production FAIL "$(tail -2 "$EV/npm_audit.log" | head -1)" npm_audit.log; fi

# ---------------------------------------------------------------------------- 4. PostgreSQL migrations
if [[ $FULLSTACK -eq 1 ]]; then
  echo "== 4 PostgreSQL migration chain + restore drill"
  if "$PY" scripts/validate_pg_migration_restore.py --admin-url "$PG_ADMIN" > "$EV/pg_migrations.log" 2>&1
  then record pg_migration_restore PASS "$(grep -c '^PASS' "$EV/pg_migrations.log") checks" pg_migrations.log
  else record pg_migration_restore FAIL "see log" pg_migrations.log; fi
fi

# ---------------------------------------------------------------------------- 5/6. full distributed stack
if [[ $FULLSTACK -eq 1 ]]; then
  echo "== 5 full distributed stack"
  INM_FS=1 pytest_suite fullstack_contracts fullstack.log tests/fullstack/test_fullstack.py
  INM_FS=1 pytest_suite fullstack_upgrade_backup_restore fullstack_upgrade.log tests/fullstack/test_upgrade_backup_restore.py
  cp -r evidence/fullstack "$EV/fullstack-evidence" 2>/dev/null || true
  # native seven-role stack incl. the isolated AI service (tests/integration); shares ports with the stack above
  echo "== 5c native seven-role integration"
  bash tests/fullstack/stack.sh down >/dev/null 2>&1 || true
  bash dev/native_stack.sh stop >/dev/null 2>&1 || true
  rm -rf /tmp/inm-native
  if PYTHON="$PY" bash dev/native_stack.sh start > "$EV/native_stack.log" 2>&1; then
    ( set +u; export PYTHON="$PY"   # the tests restart roles through dev/native_stack.sh, which needs the same interpreter
      eval "$(bash dev/native_stack.sh env)"; source /tmp/inm-native/secrets.env
      export MGMT_PW TS_PW INM_IT=1 INM_IT_ADMIN_PASSWORD="$SIMS_ADMIN_PASSWORD"
      pytest_suite integration_native integration_native.log tests/integration )
  else record integration_native FAIL "native stack did not start" native_stack.log; fi
  PYTHON="$PY" bash dev/native_stack.sh stop >/dev/null 2>&1 || true
  bash tests/fullstack/stack.sh up >/dev/null 2>&1 || true            # PostgreSQL for the load test below
else record fullstack_contracts SKIPPED "--no-fullstack" -; fi

# ---------------------------------------------------------------------------- 7. browser suites
if [[ $BROWSER -eq 1 ]]; then
  echo "== 7 browser suites"
  if [[ $FULLSTACK -eq 1 ]]; then           # suites designed for the full stack, each on a fresh stack
    for suite in test_gui_workflow test_live_operations; do
      bash tests/fullstack/stack.sh down >/dev/null 2>&1 || true
      rm -rf /tmp/inm-stack/pg /tmp/inm-stack/nats /tmp/inm-stack/loki /tmp/inm-stack/vault
      # live_operations exercises "demo data alongside live data", which live mode refuses unless explicitly allowed
      optin=false; [[ $suite == test_live_operations ]] && optin=true
      INM_ALLOW_DEMO_DATA_IN_LIVE=$optin bash tests/fullstack/stack.sh up >/dev/null 2>&1
      INM_E2E_URL=http://127.0.0.1:8810 INM_E2E_ADMIN_PASSWORD='FullStack-Admin-2026!' \
        INM_E2E_RESTART_CMD="bash tests/fullstack/stack.sh restart api" pytest_suite "e2e_${suite#test_}" "e2e_${suite#test_}.log" "tests/e2e/$suite.py"
    done
  fi
  stop_isolated() {  # module workdir — wait until the process is gone, so the next stack never races it
    pkill -f "tests/e2e/$1 $2" 2>/dev/null || true
    for _ in $(seq 1 30); do pgrep -f "tests/e2e/$1 $2" >/dev/null || return 0; sleep 1; done
    pkill -9 -f "tests/e2e/$1 $2" 2>/dev/null || true
  }
  start_isolated() {  # module port workdir — a fresh stack per suite; the health check must come from the NEW process
    stop_isolated "$1" "$3"
    for _ in $(seq 1 30); do curl -fsS -o /dev/null "http://127.0.0.1:$2/api/health" 2>/dev/null || break; sleep 1; done
    rm -rf "$3"
    "$PY" -I "tests/e2e/$1" "$3" "$2" > "$EV/${1%.py}_$2_$(date +%H%M%S).log" 2>&1 &
    local pid=$!
    wait_url "http://127.0.0.1:$2/api/health" && kill -0 "$pid" 2>/dev/null
  }
  # ops stack: one fresh stack per suite (the login limiter allows 8 sign-ins per minute per address)
  for suite in test_ops_workflows_gui test_routing_rc18 test_group_import_gui test_integrations_ml_gui test_deployment_gui; do
    start_isolated ops_stack.py 8830 /tmp/inm-e2e-ops
    INM_E2E_URL=http://127.0.0.1:8830 INM_E2E_DB=/tmp/inm-e2e-ops/mgmt.db pytest_suite "e2e_${suite#test_}" "e2e_${suite#test_}.log" "tests/e2e/$suite.py"
    stop_isolated ops_stack.py /tmp/inm-e2e-ops
  done
  start_isolated ops_stack.py 8830 /tmp/inm-e2e-ops
  if "$PY" tests/e2e/nav_audit.py http://127.0.0.1:8830 'Ops-E2E-Admin-2026!' "$EV/nav-audit" > "$EV/nav_audit.log" 2>&1 &&
     "$PY" - "$EV/nav_audit.log" <<'PYEOF'
import json, re, sys
t = open(sys.argv[1]).read()
r = json.loads(t[t.index("{"):])
sys.exit(1 if (r["overlaps"] or r["multi_active"] or r["errors"]) else 0)
PYEOF
  then record e2e_navigation_audit PASS "$(grep -o '"visited": [0-9]*' "$EV/nav_audit.log") views, 0 defects" nav_audit.log
  else record e2e_navigation_audit FAIL "see log" nav_audit.log; fi
  stop_isolated ops_stack.py /tmp/inm-e2e-ops
  start_isolated printer_stack.py 8831 /tmp/inm-e2e-printer
  INM_E2E_URL=http://127.0.0.1:8831 pytest_suite e2e_printer_gui e2e_printer_gui.log tests/e2e/test_printer_gui.py
  stop_isolated printer_stack.py /tmp/inm-e2e-printer
  start_isolated intelligence_stack.py 8832 /tmp/inm-e2e-intel
  INM_E2E_URL=http://127.0.0.1:8832 INM_E2E_ADMIN_PASSWORD='Intel-E2E-Admin-2026!' pytest_suite e2e_intelligence_gui e2e_intelligence_gui.log tests/e2e/test_intelligence_gui.py
  stop_isolated intelligence_stack.py /tmp/inm-e2e-intel
  if [[ -f tests/e2e/test_demo_gui.py ]]; then
    start_isolated ops_stack.py 8830 /tmp/inm-e2e-ops
    INM_E2E_URL=http://127.0.0.1:8830 INM_E2E_ADMIN_PASSWORD='Ops-E2E-Admin-2026!' pytest_suite e2e_demo_gui e2e_demo_gui.log tests/e2e/test_demo_gui.py
    stop_isolated ops_stack.py /tmp/inm-e2e-ops
  fi
else record browser_suites SKIPPED "--no-browser" -; fi

# ---------------------------------------------------------------------------- 8. mixed load
if [[ $LOAD -eq 1 && $FULLSTACK -eq 1 && $QUICK -eq 0 ]]; then
  echo "== 8 mixed load"
  psql -h /tmp -p 56432 -U postgres -qc "drop database if exists inm_load" -c "create database inm_load" >/dev/null
  psql -h /tmp -p 56432 -U postgres -qc "drop database if exists inm_load_ts" -c "create database inm_load_ts" >/dev/null
  psql -h /tmp -p 56432 -U postgres -d inm_load_ts -qc "create extension if not exists timescaledb" >/dev/null
  LW=/tmp/inm-load; rm -rf "$LW"; mkdir -p "$LW"
  MURL="postgresql+psycopg://postgres@127.0.0.1:56432/inm_load" TURL="postgresql+psycopg://postgres@127.0.0.1:56432/inm_load_ts"
  "$PY" scripts/load_test.py serve --workdir "$LW" --port 8840 --mgmt-url "$MURL" --ts-url "$TURL" --printers "${INM_LOAD_PRINTERS:-1000}" > "$EV/load_serve.log" 2>&1 &
  wait_url http://127.0.0.1:8840/api/health 1200                    # seeding 1,000 emulated printers takes a while
  "$PY" scripts/load_test.py worker --workdir "$LW" --mgmt-url "$MURL" --ts-url "$TURL" --printers "${INM_LOAD_PRINTERS:-1000}" > "$EV/load_worker.log" 2>&1 &
  if "$PY" scripts/load_test.py load --url http://127.0.0.1:8840 --users "${INM_LOAD_USERS:-25}" --seconds "${INM_LOAD_SECONDS:-120}" \
       --out "$EV/load_result.json" > "$EV/load.log" 2>&1
  then record mixed_load PASS "$("$PY" -c "import json,sys;r=json.load(open(sys.argv[1]));w=r.get('worker',{});print('users',r['users'],'requests',r['requests'],'errors',r['error_count'],'p95_ms',r['latency_ms']['p95'],'printers_seeded',sys.argv[2],'printer_polls_sampled',sum(w.get('printer_polls_last_cycles',[])))" "$EV/load_result.json" "${INM_LOAD_PRINTERS:-1000}" 2>/dev/null)" load.log
  else record mixed_load FAIL "see log" load.log; fi
  pkill -f "scripts/load_test.py" 2>/dev/null || true
else record mixed_load SKIPPED "needs the full stack (--no-load/--no-fullstack/--quick)" -; fi

[[ $FULLSTACK -eq 1 ]] && bash tests/fullstack/stack.sh down >/dev/null 2>&1

# ---------------------------------------------------------------------------- report
"$PY" - "$EV" <<'PYEOF'
import sys, pathlib, datetime
ev = pathlib.Path(sys.argv[1])
rows = [l.split("\t") for l in (ev / "summary.tsv").read_text().splitlines() if l.strip()]
env = (ev / "environment.txt").read_text()
fails = [r for r in rows if r[1] == "FAIL"]
lines = ["# Automated test evidence report", "", f"Generated {datetime.datetime.now(datetime.timezone.utc).isoformat()}", "",
         "```", env.strip(), "```", "", "| Suite | Result | Counts / detail | Log |", "|---|---|---|---|"]
lines += [f"| {r[0]} | **{r[1]}** | {r[2]} | {r[3]} |" for r in rows]
lines += ["", f"Overall: **{'FAIL' if fails else 'PASS'}** ({len(rows)} suites, {len(fails)} failed, "
          f"{sum(r[1] == 'SKIPPED' for r in rows)} skipped).", "",
          "All devices are EMULATED (CX protocol emulator, SNMP printer emulator, Instant emulator). These results are "
          "software evidence only and are not hardware certification."]
(ev / "TEST_EVIDENCE_REPORT.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines[-3:]))
sys.exit(1 if fails else 0)
PYEOF
rc=$?
echo "evidence: $EV/TEST_EVIDENCE_REPORT.md"
exit $rc
