#!/usr/bin/env bash
# NetworkManager3: Docker-only recovery diagnosis and isolated software smoke test.
# Does not overwrite or recover old secrets, touch existing INM/SIMS projects,
# contact real Aruba devices, or modify existing named Docker volumes.
# Usage: sudo bash inm_recovery_sandbox_test.sh [--diagnose-only]
set -Eeuo pipefail
umask 077

MODE="${1:-test}"
[[ "$MODE" == test || "$MODE" == --diagnose-only ]] || { echo 'Usage: sudo bash inm_recovery_sandbox_test.sh [--diagnose-only]' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'ERROR: Run as root/sudo.' >&2; exit 2; }

LOG_DIR=/var/log/inm
install -d -m 0700 "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/recovery-docker-test-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
say(){ printf '[INM recovery test] %s\n' "$*"; }
fail(){ say "ERROR: $*" >&2; exit 1; }

say 'Diagnosing source, configuration and Docker state (read-only).'
for t in git docker python3 openssl curl; do
  if command -v "$t" >/dev/null; then say "available: $t"; else say "MISSING: $t"; fi
done
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  say "Docker Server: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
  docker compose version || true
  say 'Docker Compose project inventory:'
  docker compose ls --all || true
  say 'Existing INM, SIMS, Aruba-related containers:'
  docker ps -a --format '{{.Names}} | {{.Image}} | {{.Label "com.docker.compose.project"}}' |
    grep -Ei '(^inm|sims|aruba|wifi|network)' || say 'None matched by name/image.'
  say 'Existing potentially relevant Docker volume NAMES (contents remain untouched):'
  docker volume ls --format '{{.Name}}' | grep -Ei '(inm|sims|aruba|wifi)' || say 'None matched by name.'
else
  say 'Docker daemon unavailable or Docker is not installed.'
fi
if [[ -f /etc/inm/inm.env ]]; then
  say '/etc/inm/inm.env: EXISTS (contents redacted).'
else
  say '/etc/inm/inm.env: MISSING. Old secrets must not be replaced to reconnect existing encrypted data.'
fi
for p in /var/lib/inm/backups /etc/inm/backup.key /root/NetworkManager3 /opt/inm /opt/NetworkManager3 /srv/inm; do
  if [[ -e "$p" ]]; then say "EXISTS: $p"; else say "missing: $p"; fi
done
say "Diagnostics log: $LOG"
if [[ "$MODE" == --diagnose-only ]]; then
  say 'Diagnostic-only mode: no clone, Docker image build, or test deployment.'
  exit 0
fi

command -v docker >/dev/null || fail 'Cannot run sandbox: Docker missing.'
docker info >/dev/null 2>&1 || fail 'Cannot run sandbox: Docker daemon unavailable.'
docker compose version >/dev/null 2>&1 || fail 'Cannot run sandbox: Docker Compose v2 missing.'
for t in git python3 openssl curl; do command -v "$t" >/dev/null || fail "Cannot run sandbox: $t missing."; done

# Avoid overloading production Docker hosts.
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib)"
[[ -d "$DOCKER_ROOT" ]] || DOCKER_ROOT=/var/lib
FREE_KB="$(df -Pk "$DOCKER_ROOT" | awk 'NR==2{print $4}')"
(( FREE_KB >= 12*1024*1024 )) || fail 'Less than 12 GiB free on Docker data filesystem; sandbox not started.'
MEM_KB="$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)"
(( MEM_KB >= 3*1024*1024 )) || fail 'Less than 3 GiB available RAM; sandbox not started.'

# Keep source and secrets strictly separate from /etc/inm and existing repositories.
WORK="$(mktemp -d /var/tmp/inm-test.XXXXXXXX)"
PROJECT="inmtest$(date -u +%H%M%S)$$"
TEST_CREATED=0
SOURCE="$WORK/source"
ENV="$WORK/test.env"
OVERRIDE="$WORK/isolated-images.yaml"
cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$TEST_CREATED" == 1 ]]; then
    say "Removing ONLY temporary Compose project '$PROJECT' and its own test volumes. Existing projects remain untouched."
    docker compose --project-name "$PROJECT" --project-directory "$SOURCE" -f "$SOURCE/compose.yaml" -f "$OVERRIDE" --env-file "$ENV" down -v --remove-orphans >/dev/null 2>&1 || say 'WARNING: sandbox cleanup failed; manually inspect project name above.'
  fi
  rm -rf -- "$WORK"
  if ((rc == 0)); then say "PASS: isolated INM Docker software test. Log: $LOG"; else say "FAIL (exit $rc): isolated INM Docker software test. Log: $LOG"; fi
  exit "$rc"
}
trap cleanup EXIT

say 'Cloning current main to a temporary directory; no installed checkout or configuration is changed.'
git clone --quiet --depth 1 --branch main https://github.com/nigelebrown/NetworkManager3.git "$SOURCE"
VERSION="$(tr -d '[:space:]' < "$SOURCE/VERSION")"
SHA="$(git -C "$SOURCE" rev-parse HEAD)"
SHORT="${SHA:0:12}"
say "Testing Git commit $SHA (version $VERSION), isolated project $PROJECT."

# Don't reuse another installation's container names or named volumes.
if docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.ID}}' | grep -q .; then
  fail "Sandbox project $PROJECT unexpectedly exists."
fi
if docker volume ls --format '{{.Name}}' | grep -q "^${PROJECT}_"; then
  fail "Sandbox volumes already exist for $PROJECT."
fi

mkdir -m 0700 "$WORK/tls"
python3 -I - "$ENV" "$VERSION" "$SHA" "$WORK/tls" <<'PY'
import base64, os, secrets, sys
from pathlib import Path
path, version, commit, tls = sys.argv[1:]
data = {
    'INM_VERSION':version, 'INM_GIT_COMMIT':commit, 'INM_MODE':'demo','INM_PROFILE':'demo',
    'INM_WEB_BIND':'127.0.0.1','INM_WEB_PORT':'18443',
    'INM_SYSLOG_BIND':'127.0.0.1','INM_SYSLOG_PORT':'11514',
    'INM_TLS_DIR':tls, 'INM_POLL_INTERVAL_SECONDS':'15',
    'INM_NATS_API_PASSWORD':'nats'+secrets.token_hex(24),
    'INM_NATS_WORKER_PASSWORD':'nats'+secrets.token_hex(24),
    'MGMT_DB_PASSWORD':secrets.token_urlsafe(36),'TS_DB_PASSWORD':secrets.token_urlsafe(36),
    'SIMS_JWT_SECRET':secrets.token_urlsafe(48),
    'SIMS_FERNET_KEY':base64.urlsafe_b64encode(os.urandom(32)).decode(),
    'SIMS_ADMIN_PASSWORD':secrets.token_urlsafe(24),
    'INM_AI_SHARED_TOKEN':secrets.token_urlsafe(48),
    'INM_DEVICE_WRITE_LOCK':'true','INM_ALLOW_CONFIG_CHANGES':'false',
    'INM_AUTOMATIC_OPTIMIZATION':'false','INM_ARUBA_VERIFY_TLS':'true',
    'INM_READONLY_DEVICE_CIDRS':'','INM_SCHEDULED_COLLECTION':'false',
    'INM_DEMO_SIMPLE_LOGIN':'false','INM_FORCE_PASSWORD_CHANGE':'true',
}
Path(path).write_text(''.join(f'{k}={v}\n' for k,v in data.items()),encoding='utf8')
Path(path).chmod(0o600)
PY
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 -subj '/CN=localhost' \
  -keyout "$WORK/tls/tls.key" -out "$WORK/tls/tls.crt" >/dev/null 2>&1
chmod 0600 "$WORK/tls/tls.key"

# The source Compose file names images inm/backend:<version>; isolate image
# tags too, so an existing production install's update cannot use test builds.
cat > "$OVERRIDE" <<YAML
services:
  migrate:
    image: inmlab/backend:$SHORT
  api:
    image: inmlab/backend:$SHORT
  worker:
    image: inmlab/backend:$SHORT
  ai:
    image: inmlab/ai:$SHORT
  web:
    image: inmlab/web:$SHORT
YAML

compose(){ docker compose --project-name "$PROJECT" --project-directory "$SOURCE" -f "$SOURCE/compose.yaml" -f "$OVERRIDE" --env-file "$ENV" "$@"; }
# Check localhost port availability before creating any containers.
python3 -I - <<'PY'
import socket
for port, kind in [(18443,socket.SOCK_STREAM),(11514,socket.SOCK_STREAM),(11514,socket.SOCK_DGRAM)]:
    s=socket.socket(socket.AF_INET,kind)
    try: s.bind(('127.0.0.1',port))
    except OSError as e: raise SystemExit(f'Test port {port} unavailable: {e}')
    finally: s.close()
PY
compose config --quiet
say 'Building and starting a NEW isolated Docker Compose stack (no real-device access).'
TEST_CREATED=1
compose up --build -d --wait --wait-timeout 600 management-db timeseries-db nats loki alloy ai api web worker
say 'Checking HTTPS endpoint on server loopback only.'
curl --silent --show-error --fail --insecure --max-time 20 https://127.0.0.1:18443/healthz >/dev/null
say 'Checking Python API readiness, cross-service synthetic telemetry pipeline, access controls.'
compose exec -T api python -m backend.cli verify --api-url http://127.0.0.1:8000 --syslog-host alloy --syslog-port 1514
say 'Checking migration versions and audit integrity.'
compose exec -T api python -m backend.cli schema-status
compose exec -T api python -m backend.cli audit-verify
say 'Checking actual device write lock in both running service roles.'
for role in api worker; do
  compose exec -T "$role" python - <<'PY'
from backend.config import get_settings
s=get_settings()
assert s.device_writes_permitted is False, 'Device writes unexpectedly enabled'
assert s.allow_config_changes is False
assert s.device_write_lock is True
assert s.aruba_verify_tls is True
print('PASS: write lock is engaged, Aruba TLS verification enabled')
PY
done
say 'All sandbox checks succeeded. Physical hardware was NOT contacted.'
