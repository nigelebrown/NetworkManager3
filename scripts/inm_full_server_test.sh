#!/usr/bin/env bash
# Intelligent Network Manager - installed-server software acceptance.
# Read-only toward Aruba hardware. Does create synthetic INM telemetry/audit
# evidence through the supported platform verifier; it never resets volumes.
# Usage: sudo bash inm_full_server_test.sh [--with-regressions] [--root /root/NetworkManager3]
set -Eeuo pipefail
umask 077
ROOT=/root/NetworkManager3
SOURCE_TESTS=0
while (( $# )); do
  case "$1" in
    --root) [[ $# -ge 2 ]] || { echo 'Missing --root value' >&2; exit 2; }; ROOT="$2"; shift 2 ;;
    --with-regressions) SOURCE_TESTS=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo/root.' >&2; exit 2; }
for bin in git docker python3 curl; do
  command -v "$bin" >/dev/null || { echo "Missing prerequisite: $bin" >&2; exit 2; }
done
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose plugin unavailable' >&2; exit 2; }
ENV_FILE="${INM_ETC_DIR:-/etc/inm}/inm.env"
[[ -d "$ROOT/.git" && -f "$ROOT/compose.yaml" && -f "$ENV_FILE" ]] || {
  echo 'Existing Git-managed INM installation and /etc/inm/inm.env required.' >&2; exit 2;
}
LOG_DIR="${INM_LOG_DIR:-/var/log/inm}"
install -d -m 0700 "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/software-acceptance-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
cd "$ROOT"
PASSED=0; FAILED=0; SKIPPED=0
pass() { printf '[PASS] %s\n' "$1"; PASSED=$((PASSED+1)); }
fail() { printf '[FAIL] %s\n' "$1" >&2; FAILED=$((FAILED+1)); }
check() {
  local description="$1"; shift
  echo; printf '--- %s ---\n' "$description"
  if "$@"; then pass "$description"; else fail "$description"; fi
}
compose() { docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" --env-file "$ENV_FILE" "$@"; }
getenv() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
HEAD_SHA="$(git rev-parse HEAD)"
printf 'INM SOFTWARE VERIFICATION | UTC %s | git %s\n' "$STAMP" "$HEAD_SHA"
printf 'Mode=%s Profile=%s\n' "$(getenv INM_MODE)" "$(getenv INM_PROFILE)"
printf 'Hardware configuration and automatic RF optimization will NOT be enabled.\n'

preflight() { bash deployment/centos9/doctor.sh; }
write_safety() {
  [[ "$(getenv INM_DEVICE_WRITE_LOCK)" != false ]] &&
  [[ "$(getenv INM_ALLOW_CONFIG_CHANGES)" != true ]] &&
  [[ "$(getenv INM_AUTOMATIC_OPTIMIZATION)" != true ]] || return 1
  for svc in api worker; do
    compose exec -T "$svc" python - <<'PY'
from backend.config import get_settings
s = get_settings()
assert s.device_writes_permitted is False
assert s.allow_config_changes is False
assert s.device_write_lock is True
print('PASS: active container write lock and manual configuration gate closed')
PY
  done
}
revision_match() {
  local svc got
  for svc in api worker; do
    got="$(compose exec -T "$svc" python -c 'import os; print(os.environ.get("INM_GIT_COMMIT", "unknown"))')" || return 1
    [[ "$got" == "$HEAD_SHA" ]] || {
      printf '%s is running %s but checkout is %s\n' "$svc" "$got" "$HEAD_SHA" >&2; return 1;
    }
  done
}
verify_schema() {
  local actual
  actual="$(compose exec -T api python -m backend.cli schema-status)" || return 1
  printf '%s' "$actual" | python3 -c '
import json,sys
from pathlib import Path
current=json.load(sys.stdin)
release=json.loads(Path("release.json").read_text())
assert current["management"] == release["management_schema_head"], current
assert current["telemetry"] == release["telemetry_schema_head"], current
print("Management/telemetry schema at release head:", current)
'
}
api_health() {
  compose exec -T api python - <<'PY'
import httpx
for path,flag in [('/api/health','ok'),('/api/v1/ready','ready')]:
    response=httpx.get('http://127.0.0.1:8000'+path,timeout=12)
    assert response.status_code == 200, (path,response.status_code)
    assert response.json().get(flag) is True, path
    print('PASS',path)
PY
}
https_health() {
  local bind port
  bind="$(getenv INM_WEB_BIND)"; port="$(getenv INM_WEB_PORT)"
  bind="${bind:-127.0.0.1}"; port="${port:-8443}"
  # --insecure is restricted to the local test of INM's own HTTPS endpoint,
  # and never changes or disables TLS checks against management hardware.
  curl --insecure --fail --silent --show-error --max-time 15 \
    -o /dev/null "https://${bind}:${port}/healthz"
  curl --insecure --fail --silent --show-error --max-time 15 \
    -o /dev/null "https://${bind}:${port}/login"
}
source_regressions() {
  [[ -x "$ROOT/.venv/bin/python" ]] || {
    echo 'Missing .venv/bin/python; install isolated source-test dependencies first.' >&2; return 1;
  }
  SIMS_DISABLE_SCHEDULER=true "$ROOT/.venv/bin/python" -m pytest -q \
    tests/unit tests/contract backend/tests
}
frontend_build() {
  command -v npm >/dev/null || { echo 'npm missing' >&2; return 1; }
  [[ -d frontend/node_modules ]] || { echo 'frontend/node_modules missing (use npm ci in test workspace)' >&2; return 1; }
  (cd frontend && npm run build)
}

check 'CentOS host preflight' preflight
check 'Docker Compose validation' compose config --quiet
check 'Installed container commit matches the Git checkout' revision_match
check 'Hardware write lock and optimization remain disabled' write_safety
check 'Container security, isolation, services and synthetic end-to-end pipeline' bash deployment/centos9/verify.sh
check 'Management and telemetry schema revisions' verify_schema
check 'API health and readiness' api_health
check 'Audit-chain integrity' compose exec -T api python -m backend.cli audit-verify
check 'HTTPS endpoint and login page' https_health
if (( SOURCE_TESTS )); then
  check 'Host isolated regression / unit / contract suites' source_regressions
  check 'Frontend production build (existing test dependencies)' frontend_build
else
  echo '[SKIP] Local source regressions and frontend rebuild; run --with-regressions in a prepared test workspace.'
  SKIPPED=$((SKIPPED+2))
fi
printf '\nTest summary: %d passed, %d failed, %d skipped\n' "$PASSED" "$FAILED" "$SKIPPED"
printf 'Log: %s\n' "$LOG"
printf 'Hardware AP/CX REST, physical configuration writes, restore drills, and release certification were NOT performed.\n'
if (( FAILED )); then printf 'RESULT: FAIL\n'; exit 1; fi
printf 'RESULT: SOFTWARE SERVER CHECKS PASS (not production certification)\n'