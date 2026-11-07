#!/usr/bin/env bash
# Developer/CI harness: run the seven INM roles as native processes (no Docker) for integration tests.
# NOT a deployment method. Production/pilot installs use deployment/centos9/install.sh (Docker Compose).
#
# Requires: PostgreSQL 16+ binaries with the TimescaleDB extension installed, nats-server, loki, alloy binaries,
# a Python venv with requirements, and a built frontend (frontend/dist).
#   dev/native_stack.sh start|stop|status|env
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${INM_NATIVE_STATE:-/tmp/inm-native}"
BIN="${INM_NATIVE_BIN:-/opt/inmbin}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PY="${PYTHON:-python}"
SECRETS="$STATE/secrets.env"

gen() { "$PY" -c "import secrets;print(secrets.token_urlsafe(${1:-32}))"; }

write_env() {
  mkdir -p "$STATE"
  if [[ ! -f "$SECRETS" ]]; then
    umask 077
    cat > "$SECRETS" <<EOF
MGMT_PW=$(gen)
TS_PW=$(gen)
INM_NATS_API_PASSWORD=$(gen)
INM_NATS_WORKER_PASSWORD=$(gen)
SIMS_JWT_SECRET=$(gen 48)
SIMS_FERNET_KEY=$("$PY" -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')
SIMS_ADMIN_PASSWORD=$(gen 18)
INM_AI_SHARED_TOKEN=$(gen)
EOF
  fi
}

common_env() {
  # shellcheck disable=SC1090
  source "$SECRETS"
  export SIMS_MODE=demo SIMS_INM_PROFILE=demo SIMS_DEVICE_WRITE_LOCK=true SIMS_ALLOW_CONFIG_CHANGES=false
  export SIMS_AUTO_MIGRATE=false SIMS_FORCE_INITIAL_PASSWORD_CHANGE=true SIMS_POLL_INTERVAL_SECONDS="${POLL:-15}"
  export SIMS_JWT_SECRET SIMS_FERNET_KEY SIMS_ADMIN_PASSWORD SIMS_ADMIN_USERNAME=admin
  export SIMS_DATABASE_URL="postgresql+psycopg://inm_app:${MGMT_PW}@127.0.0.1:55432/inm_management"
  export INM_TELEMETRY_DATABASE_URL="postgresql+psycopg://inm_metrics:${TS_PW}@127.0.0.1:55433/inm_telemetry"
  export INM_NATS_URL=nats://127.0.0.1:4222 INM_LOKI_URL=http://127.0.0.1:3100 INM_AI_URL=http://127.0.0.1:58001
  export INM_NATS_API_PASSWORD INM_NATS_WORKER_PASSWORD INM_AI_SHARED_TOKEN INM_GIT_COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  export SIMS_OLLAMA_URL=http://127.0.0.1:58001/ollama SIMS_OLLAMA_ALLOW_REMOTE=true
}

pg_cluster() {  # name port db user pw [preload]
  local dir="$STATE/pg-$1"
  if [[ ! -d "$dir" ]]; then
    mkdir -p "$dir"; chown postgres "$dir" "$STATE"
    ( umask 077; echo "$5" > "$STATE/.pw" ); chown postgres "$STATE/.pw"
    su postgres -c "$PGBIN/initdb -D $dir -A scram-sha-256 --pwfile=$STATE/.pw -U pgadmin >/dev/null"; rm -f "$STATE/.pw"
    { echo "listen_addresses='127.0.0.1'"; echo "port=$2"; echo "unix_socket_directories='$dir'";
      [[ -n "${6:-}" ]] && echo "shared_preload_libraries='$6'" && echo "timescaledb.telemetry_level=off"; } >> "$dir/postgresql.conf"
    su postgres -c "$PGBIN/pg_ctl -D $dir -l $dir/log -w start >/dev/null"
    PGPASSWORD="$5" PGCONNECT_TIMEOUT=5 "$PGBIN/psql" -w -h 127.0.0.1 -p "$2" -U pgadmin -d postgres -qc "CREATE ROLE $4 LOGIN PASSWORD '$5'" -c "CREATE DATABASE $3 OWNER $4"
    if [[ -n "${6:-}" ]]; then
      PGPASSWORD="$5" PGCONNECT_TIMEOUT=5 "$PGBIN/psql" -w -h 127.0.0.1 -p "$2" -U pgadmin -d "$3" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb;"
    fi
  else
    su postgres -c "$PGBIN/pg_ctl -D $dir -l $dir/log -w status >/dev/null" || su postgres -c "$PGBIN/pg_ctl -D $dir -l $dir/log -w start >/dev/null"
  fi
}

bg() {  # name cmd...
  local name=$1; shift
  if [[ -f "$STATE/$name.pid" ]] && kill -0 "$(cat "$STATE/$name.pid")" 2>/dev/null; then return; fi
  cd "$ROOT"
  setsid "$@" > "$STATE/$name.log" 2>&1 < /dev/null &
  echo $! > "$STATE/$name.pid"   # setsid makes this pid a process-group leader: stop kills the whole group
}

start() {
  write_env; common_env
  pg_cluster mgmt 55432 inm_management inm_app "$MGMT_PW"
  pg_cluster ts 55433 inm_telemetry inm_metrics "$TS_PW" timescaledb
  mkdir -p "$STATE/nats" "$STATE/loki"
  sed "s#/data#$STATE/nats#" "$ROOT/configs/nats/nats.conf" > "$STATE/nats.conf"
  bg nats env INM_NATS_API_PASSWORD="$INM_NATS_API_PASSWORD" INM_NATS_WORKER_PASSWORD="$INM_NATS_WORKER_PASSWORD" \
     "$BIN/nats-server-v2.11.9-linux-amd64/nats-server" -c "$STATE/nats.conf"
  sed "s#/loki#$STATE/loki#g" "$ROOT/configs/loki/config.yaml" > "$STATE/loki.yaml"
  bg loki "$BIN/loki-linux-amd64" -config.file="$STATE/loki.yaml"
  bg alloy env LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push "$BIN/alloy-linux-amd64" run \
     --storage.path="$STATE/alloy" --server.http.listen-addr=127.0.0.1:12345 "$ROOT/configs/alloy/config.alloy"
  for i in $(seq 1 60); do curl -sf http://127.0.0.1:3100/ready >/dev/null && break; sleep 1; done
  # migrations run as the installer would: once, before API/worker start
  SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD="$INM_NATS_API_PASSWORD" "$PY" -m backend.cli migrate
  bg ai env -i PATH="$PATH" INM_AI_SHARED_TOKEN="$INM_AI_SHARED_TOKEN" "$PY" -m uvicorn ai_service.app:app --host 127.0.0.1 --port 58001
  bg api env SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD="$INM_NATS_API_PASSWORD" \
     "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 58000
  bg worker env SIMS_ROLE=worker INM_NATS_USER=inm_worker INM_NATS_PASSWORD="$INM_NATS_WORKER_PASSWORD" \
     "$PY" -m workers.network_worker.main
  for i in $(seq 1 60); do curl -sf http://127.0.0.1:58000/api/v1/ready >/dev/null && break; sleep 1; done
  status
}

stop_one() {
  if [[ -f "$STATE/$1.pid" ]]; then
    local pid; pid=$(cat "$STATE/$1.pid")
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    rm -f "$STATE/$1.pid"
  fi
  return 0
}

stop() {
  for n in worker api ai alloy loki nats; do stop_one $n; done
  for c in mgmt ts; do [[ -d "$STATE/pg-$c" ]] && su postgres -c "$PGBIN/pg_ctl -D $STATE/pg-$c -w stop -m fast >/dev/null" || true; done
}

status() {
  for n in nats loki alloy ai api worker; do
    if [[ -f "$STATE/$n.pid" ]] && kill -0 "$(cat "$STATE/$n.pid")" 2>/dev/null; then echo "$n: running"; else echo "$n: STOPPED"; fi
  done
  curl -s http://127.0.0.1:58000/api/v1/ready; echo
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  env) write_env; common_env; env | grep -E '^(SIMS|INM)_' | sed 's/^/export /' ;;
  stop-role)
    case "$2" in
      pg-mgmt|pg-ts) su postgres -c "$PGBIN/pg_ctl -D $STATE/$2 -w stop -m fast >/dev/null" ;;
      *) stop_one "$2" ;;
    esac ;;
  start-role) write_env; common_env; start ;;
  *) echo "usage: $0 start|stop|status|env|stop-role <name>"; exit 2 ;;
esac
