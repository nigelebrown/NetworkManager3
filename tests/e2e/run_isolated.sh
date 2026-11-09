#!/usr/bin/env bash
# Run each browser suite against its own freshly built full stack (PostgreSQL/TimescaleDB, NATS
# JetStream, Loki, Alloy, API, worker, scheduler, emulators). Suites create users and templates
# with fixed names, so they are not designed to share one database.
set -euo pipefail
cd "$(dirname "$0")/../.."
export INM_E2E_URL=http://127.0.0.1:8810 INM_E2E_ADMIN_PASSWORD='FullStack-Admin-2026!'
export INM_E2E_CHROME=${INM_E2E_CHROME:-/usr/bin/google-chrome}
export INM_E2E_RESTART_CMD="bash tests/fullstack/stack.sh restart api"
mkdir -p evidence/e2e-gui
rc=0
for suite in tests/e2e/test_gui_workflow.py tests/e2e/test_live_operations.py; do
  bash tests/fullstack/stack.sh down >/dev/null 2>&1 || true
  rm -rf /tmp/inm-stack/pg /tmp/inm-stack/nats /tmp/inm-e2e/loki /tmp/inm-stack/vault
  bash tests/fullstack/stack.sh up >/dev/null
  name=$(basename "$suite" .py)
  .venv/bin/python -m pytest -q "$suite" -p no:cacheprovider -W ignore -rs 2>&1 | tee "evidence/e2e-gui/$name.log" | tail -3 || rc=1
  grep -qE "[0-9]+ passed" "evidence/e2e-gui/$name.log" && ! grep -qE "[0-9]+ failed|error" "evidence/e2e-gui/$name.log" || rc=1
done
bash tests/fullstack/stack.sh down >/dev/null 2>&1 || true
exit $rc
