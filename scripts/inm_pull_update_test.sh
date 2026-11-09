#!/usr/bin/env bash
# NetworkManager3 - development-server update from GitHub main, then smoke tests.
# Designed for an EXISTING CentOS Stream 9 Git/Docker Compose installation.
# Does not connect to, provision, or change any Aruba device.
# Usage: sudo bash inm_pull_update_test.sh [/root/NetworkManager3]
#        sudo bash inm_pull_update_test.sh --verify-only [/root/NetworkManager3]
set -Eeuo pipefail
umask 077

VERIFY_ONLY=0
if [[ "${1:-}" == "--verify-only" ]]; then VERIFY_ONLY=1; shift; fi
ROOT="${1:-/root/NetworkManager3}"
ENV_FILE="${INM_ETC_DIR:-/etc/inm}/inm.env"
LOG_DIR="${INM_LOG_DIR:-/var/log/inm}"
BACKUP=""
OLD_COMMIT=""
NEW_COMMIT=""

[[ "$EUID" -eq 0 ]] || { echo 'ERROR: Run with sudo/root.' >&2; exit 2; }
for c in git docker python3; do command -v "$c" >/dev/null || { echo "ERROR: $c is missing" >&2; exit 2; }; done
docker compose version >/dev/null 2>&1 || { echo 'ERROR: Docker Compose plugin missing' >&2; exit 2; }
[[ -d "$ROOT/.git" ]] || { echo "ERROR: Git clone missing at $ROOT. Do not use a ZIP checkout." >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "ERROR: Existing INM installation not found ($ENV_FILE). For a fresh setup, use deployment/centos9/install.sh --mode live." >&2; exit 2; }
[[ -f "$ROOT/compose.yaml" && -f "$ROOT/release.json" ]] || { echo 'ERROR: Incomplete repository checkout' >&2; exit 2; }

install -d -m 0700 "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/dev-pull-test-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
say() { printf '[NM3] %s\n' "$*"; }
fail() { printf '[NM3] ERROR: %s\n' "$*" >&2; exit 1; }
on_error() {
  local rc=$?
  trap - ERR
  say "FAILED (exit $rc); full output: $LOG"
  [[ -n "$BACKUP" ]] && say "Pre-update encrypted backup: $BACKUP"
  [[ -n "$OLD_COMMIT" ]] && say "Previous Git commit: $OLD_COMMIT"
  say 'Do not simply check out old application code after a schema migration; use the documented backup/restore procedure on a disposable clone first.'
  exit "$rc"
}
trap on_error ERR

compose() { docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" --env-file "$ENV_FILE" "$@"; }
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | head -1; }
set_env() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "$ENV_FILE"; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
  fi
}

cd "$ROOT"
ORIGIN="$(git remote get-url origin)"
case "$ORIGIN" in
  github-networkmanager3:nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git|https://github.com/nigelebrown/NetworkManager3.git|https://github.com/nigelebrown/NetworkManager3) ;;
  *) fail "Unexpected Git origin: $ORIGIN" ;;
esac
OLD_COMMIT="$(git rev-parse HEAD)"
say "Existing source: ${OLD_COMMIT:0:12}; installed mode: $(env_value INM_MODE)"

if [[ "$VERIFY_ONLY" -eq 0 ]]; then
  [[ "$(git branch --show-current)" == main ]] || fail 'Checkout must be on main (no detached HEAD or feature branch).'
  [[ -z "$(git status --porcelain)" ]] || fail 'Uncommitted/untracked files present. Preserve or remove them manually before updating.'

  say '1/6 Backing up installed services BEFORE changing source code...'
  BACKUP_LOG="$LOG_DIR/dev-preupdate-backup-$STAMP.log"
  bash deployment/centos9/backup.sh --label "pre-main-$STAMP" > "$BACKUP_LOG" 2>&1 || { tail -30 "$BACKUP_LOG"; fail 'Encrypted backup failed; nothing has been updated.'; }
  BACKUP="$(tail -n 1 "$BACKUP_LOG")"
  [[ -f "$BACKUP" && -s "$BACKUP" ]] || fail "Backup artifact not found. Check $BACKUP_LOG"
  [[ -f "$BACKUP.sha256" ]] || fail 'Backup checksum missing; refusing update.'
  (cd "$(dirname "$BACKUP")" && sha256sum -c "$(basename "$BACKUP").sha256") || fail 'Backup checksum did not match.'
  say "Backup verified: $BACKUP"

  say '2/6 Fetching GitHub main (fast-forward only; no force, reset, or local commits)...'
  git fetch origin main
  git merge --ff-only origin/main
  NEW_COMMIT="$(git rev-parse HEAD)"
  say "GitHub main now checked out: $NEW_COMMIT"

  say '3/6 Applying development safety settings (hardware writes OFF)...'
  set_env INM_DEVICE_WRITE_LOCK true
  set_env INM_ALLOW_CONFIG_CHANGES false
  set_env INM_AUTOMATIC_OPTIMIZATION false
  set_env SIMS_ARUBA_VERIFY_TLS false
  set_env INM_GIT_COMMIT "$NEW_COMMIT"
  set_env INM_VERSION "$(tr -d '[:space:]' < "$ROOT/VERSION")"
  chmod 0600 "$ENV_FILE"
  if [[ "$(env_value INM_DEMO_SIMPLE_LOGIN)" == true && "$(env_value INM_WEB_BIND)" != '127.0.0.1' ]]; then
    fail 'Insecure simple demo login cannot be exposed outside loopback.'
  fi
  compose config --quiet

  say '4/6 Building current application images...'
  compose build --pull

  say '5/6 Starting dependencies and running database migrations...'
  compose up -d --wait --wait-timeout 600 management-db timeseries-db nats loki alloy ai
  compose run --rm migrate

  say '6/6 Starting application, preserving all named Docker volumes...'
  compose up -d --wait --wait-timeout 600 --remove-orphans management-db timeseries-db nats loki alloy ai api web worker
fi

say 'Running non-hardware smoke tests...'
[[ "$(env_value INM_DEVICE_WRITE_LOCK)" != false ]] || fail 'Hardware write lock is OFF.'
[[ "$(env_value INM_ALLOW_CONFIG_CHANGES)" != true ]] || fail 'Configuration changes are enabled.'
[[ "$(env_value INM_AUTOMATIC_OPTIMIZATION)" != true ]] || fail 'Automatic optimization is enabled.'

for service in management-db timeseries-db nats loki alloy ai api web worker; do
  cid="$(compose ps -q "$service")"
  [[ -n "$cid" ]] || fail "Missing service: $service"
  state="$(docker inspect -f '{{.State.Status}}' "$cid")"
  [[ "$state" == running ]] || fail "Service not running: $service ($state)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")"
  [[ "$health" == healthy || "$health" == none ]] || fail "Unhealthy service: $service ($health)"
  say "PASS container $service ($health)"
done

compose exec -T api python - <<'PY'
import httpx
import importlib
importlib.import_module("backend.aruba.rest_enablement")
from backend.config import get_settings
s = get_settings()
assert s.device_write_lock is True, 'Device writes are unlocked!'
assert s.allow_config_changes is False, 'Config changes are permitted!'
assert s.aruba_verify_tls is False, 'Untrusted Aruba device TLS certificates are not accepted'
for path in ('/api/health', '/api/v1/ready'):
    r = httpx.get('http://127.0.0.1:8000' + path, timeout=15)
    assert r.status_code == 200, f'{path}: HTTP {r.status_code}'
    body = r.json()
    if path == '/api/health':
        assert body.get('ok') is True, body
    else:
        assert body.get('ready') is True, body
    print('PASS API', path)
print('PASS Aruba REST bootstrap module import / write lock / TLS settings')
PY

SCHEMA_JSON="$(compose exec -T api python -m backend.cli schema-status)"
printf '%s\n' "$SCHEMA_JSON" | python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); a=json.load(sys.stdin); assert a["management"]==r["management_schema_head"], ("management",a,r["management_schema_head"]); assert a["telemetry"]==r["telemetry_schema_head"], ("telemetry",a,r["telemetry_schema_head"]); print("PASS schema revisions",a)' "$ROOT/release.json"

say 'Testing synthetic end-to-end NATS → databases → Loki → UI cursor (no device contact)...'
compose exec -T api python - <<'PYTEST'
import asyncio
from backend.cli import _init, _synthetic_round_trip
_init()
ok, detail = asyncio.run(_synthetic_round_trip('http://127.0.0.1:8000'))
print(('PASS' if ok else 'FAIL') + ' pipeline: ' + str(detail))
if not ok:
    raise SystemExit(1)
PYTEST

compose exec -T api python -m backend.cli audit-verify
say 'PASS audit-chain integrity'

WEB_BIND="$(env_value INM_WEB_BIND)"; WEB_PORT="$(env_value INM_WEB_PORT)"
WEB_BIND="${WEB_BIND:-127.0.0.1}"; WEB_PORT="${WEB_PORT:-8443}"
if command -v curl >/dev/null; then
  curl --insecure --fail --silent --show-error --max-time 15 -o /dev/null "https://${WEB_BIND}:${WEB_PORT}/" \
    && say "PASS HTTPS web application at https://${WEB_BIND}:${WEB_PORT}/"
else
  say 'SKIP web HTTPS probe: curl missing'
fi

say 'Current container status:'
compose ps
say "PASS: latest GitHub main deployed and basic software checks passed. Log: $LOG"
say 'Hardware API calls, REST enablement, real-device writes, and browser/full regression suites were NOT performed.'
[[ -n "$BACKUP" ]] && say "Encrypted pre-update backup: $BACKUP"