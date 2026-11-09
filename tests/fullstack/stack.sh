#!/usr/bin/env bash
# Full distributed INM stack, natively (no container registry needed): PostgreSQL (management),
# PostgreSQL + TimescaleDB (telemetry), NATS JetStream with the shipped ACLs, Loki, Alloy (syslog), migrate,
# API role, worker role, and strict CX protocol emulators on real HTTPS sockets. TEST HARNESS.
#   bash tests/fullstack/stack.sh up|down|status|start <comp>|stop <comp>|restart <comp>
# comps: pg nats loki alloy emu api worker
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
S=/tmp/inm-stack
BIN=$S/bin
PGBIN=/usr/lib/postgresql/16/bin
PGDATA=$S/pg
mkdir -p "$S/logs" "$S/nats" "$S/alloy" "$S/vault"

cat > "$S/env.sh" <<ENV
export SIMS_MODE=live SIMS_INM_PROFILE=lab-readonly SIMS_DEVICE_WRITE_LOCK=true SIMS_ALLOW_CONFIG_CHANGES=false
export SIMS_DATABASE_URL="postgresql+psycopg://inm_app:fs-mgmt-pass@127.0.0.1:56432/inm_management"
export INM_TELEMETRY_DATABASE_URL="postgresql+psycopg://inm_metrics:fs-ts-pass@127.0.0.1:56432/inm_telemetry"
export INM_NATS_URL=nats://127.0.0.1:14222 INM_LOKI_URL=http://127.0.0.1:3100
export SIMS_JWT_SECRET=fullstack-jwt-secret-0123456789abcdef0123456789abcdef SIMS_FERNET_KEY=YnrqD8FOem3M1fp-1j3Z9KrG8qX3tUEEm081Olaqdng=
export SIMS_ADMIN_USERNAME=admin SIMS_ADMIN_PASSWORD='FullStack-Admin-2026!' SIMS_FORCE_INITIAL_PASSWORD_CHANGE=false
export SIMS_AUTO_MIGRATE=false INM_VAULT_DIR=$S/vault SIMS_DATA_DIR=$S/data INM_GIT_COMMIT=\$(git -C "$ROOT" rev-parse HEAD)
export INM_READONLY_DEVICE_CIDRS=10.250.0.0/24 SIMS_ARUBA_VERIFY_TLS=false INM_SCHEDULED_COLLECTION=true
export INM_NATS_API_PASSWORD=fs-nats-api INM_NATS_WORKER_PASSWORD=fs-nats-worker
ENV
# shellcheck disable=SC1091
source "$S/env.sh"

pidf() { echo "$S/$1.pid"; }
running() { [[ -f $(pidf "$1") ]] && kill -0 "$(cat "$(pidf "$1")")" 2>/dev/null; }
launch() { local n=$1; shift; "$@" > "$S/logs/$n.log" 2>&1 & echo $! > "$(pidf "$n")"; }
wait_http() { for _ in $(seq 1 60); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; echo "timeout $1"; return 1; }

start_pg() {
  if [[ ! -d $PGDATA ]]; then
    install -d -o postgres "$PGDATA"
    su postgres -c "$PGBIN/initdb -D $PGDATA -A trust -U postgres" > "$S/logs/initdb.log"
    echo "shared_preload_libraries = 'timescaledb'" >> "$PGDATA/postgresql.conf"
    echo "timescaledb.telemetry_level = off" >> "$PGDATA/postgresql.conf"
    echo "listen_addresses = '127.0.0.1'" >> "$PGDATA/postgresql.conf"
  fi
  su postgres -c "$PGBIN/pg_ctl -D $PGDATA -l $S/logs/pg.log -o '-p 56432 -k /tmp' -w start" >/dev/null
  psql -h /tmp -p 56432 -U postgres -tAc "select 1 from pg_roles where rolname='inm_app'" | grep -q 1 || {
    psql -h /tmp -p 56432 -U postgres -qc "create role inm_app login password 'fs-mgmt-pass'" \
      -c "create role inm_metrics login superuser password 'fs-ts-pass'" \
      -c "create database inm_management owner inm_app" -c "create database inm_telemetry owner inm_metrics"
    psql -h /tmp -p 56432 -U postgres -d inm_management -qc "grant all on schema public to inm_app"; }
}
stop_pg() { su postgres -c "$PGBIN/pg_ctl -D $PGDATA -m fast stop" >/dev/null 2>&1 || true; }

start_nats() {
  sed "s#store_dir: \"/data\"#store_dir: \"$S/nats\"#; s/^port: 4222/port: 14222/; s/^http_port: 8222/http_port: 18222/" \
    "$ROOT/configs/nats/nats.conf" > "$S/nats.conf"
  launch nats "$BIN/nats-server" -c "$S/nats.conf"
  wait_http http://127.0.0.1:18222/healthz
}
start_loki() { mkdir -p /tmp/inm-e2e/loki; launch loki /tmp/inm-e2e/bin/loki-linux-amd64 -config.file=/tmp/inm-e2e/loki.yaml; wait_http http://127.0.0.1:3100/ready; }
start_alloy() {
  LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push launch alloy "$BIN/alloy-linux-amd64" run \
    --server.http.listen-addr=127.0.0.1:12345 --storage.path="$S/alloy" "$ROOT/configs/alloy/config.alloy"
  wait_http http://127.0.0.1:12345/-/ready
}
start_emu() {
  [[ -f $S/emu.crt ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=emulated-cx" \
    -keyout "$S/emu.key" -out "$S/emu.crt" >/dev/null 2>&1
  (cd "$ROOT" && launch emu .venv/bin/python -m tests.fullstack.emulator_server \
    10.250.0.24:443:24:PL.10.14.1000:EMU-FS-24 10.250.0.25:443:48:ML.10.13.1020:EMU-FS-48 \
    --cert "$S/emu.crt" --key "$S/emu.key")
  wait_http http://127.0.0.1:18099/10.250.0.24/writes
}
start_api() {
  (cd "$ROOT" && SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD=$INM_NATS_API_PASSWORD \
    launch api .venv/bin/python -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8810)
  wait_http http://127.0.0.1:8810/api/health
}
start_worker() {
  (cd "$ROOT" && SIMS_ROLE=worker INM_NATS_USER=inm_worker INM_NATS_PASSWORD=$INM_NATS_WORKER_PASSWORD \
    INSTANCE=fs-worker HOSTNAME=fs-worker launch worker .venv/bin/python -m workers.network_worker.main)
  sleep 3; running worker || { tail -20 "$S/logs/worker.log"; exit 1; }
}
migrate() { (cd "$ROOT" && SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD=$INM_NATS_API_PASSWORD \
  .venv/bin/python -m backend.cli migrate > "$S/logs/migrate.log" 2>&1) || { tail -30 "$S/logs/migrate.log"; exit 1; }; }

stop() {
  case $1 in
    pg) stop_pg ;;
    *) if running "$1"; then kill "$(cat "$(pidf "$1")")" 2>/dev/null || true; for _ in $(seq 1 40); do running "$1" || break; sleep 0.5; done
         running "$1" && kill -9 "$(cat "$(pidf "$1")")" 2>/dev/null || true; fi; rm -f "$(pidf "$1")" ;;
  esac
}
start() { "start_$1"; }

case ${1:-} in
  up) start_pg; start_nats; start_loki; start_alloy; start_emu; migrate; start_api; start_worker; echo "stack up: http://127.0.0.1:8810" ;;
  down) for c in worker api emu alloy loki nats; do stop $c; done; stop_pg ;;
  stop) stop "$2" ;;
  start) start "$2" ;;
  restart) stop "$2"; start "$2" ;;
  migrate) migrate ;;
  status) for c in nats loki alloy emu api worker; do printf '%-7s %s\n' $c "$(running $c && echo running || echo stopped)"; done ;;
  *) echo "usage: $0 up|down|status|start|stop|restart <comp>"; exit 2 ;;
esac
