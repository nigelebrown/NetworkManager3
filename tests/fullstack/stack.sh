#!/usr/bin/env bash
# Full distributed INM stack, natively (no container registry needed): PostgreSQL (management),
# PostgreSQL + TimescaleDB (telemetry), NATS JetStream with the shipped ACLs, Loki, Alloy (syslog), migrate,
# API role, worker role, and strict CX protocol emulators on real HTTPS sockets. TEST HARNESS.
#   bash tests/fullstack/stack.sh provision|up|down|status|start <comp>|stop <comp>|restart <comp>
# comps: pg nats loki alloy emu api worker
# rc18: `provision` (also run by `up`) installs the pinned NATS / Loki / Alloy release binaries (SHA-256 checked
# against the upstream SHA256SUMS values below) and, if the PostgreSQL 16 server lacks it, builds TimescaleDB
# 2.22.1 from its release tag. Needs: postgresql-16, postgresql-server-dev-16, cmake, gcc, git, curl, unzip,
# iproute2 (loopback aliases for the emulated switches); run as root (test harness).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
S=${INM_STACK_DIR:-/tmp/inm-stack}
BIN=$S/bin
PGBIN=/usr/lib/postgresql/16/bin
PGDATA=$S/pg
PY=${INM_PY:-$ROOT/.venv/bin/python}
mkdir -p "$S/logs" "$S/nats" "$S/alloy" "$S/vault" "$BIN"
NATS_VER=2.11.9 NATS_SHA=3eef83faa048d3879937a6871f59ae5d9c3bbf5900905b7a9ff996a894d755ab
LOKI_VER=3.5.5 LOKI_SHA=eefea8fd3701f2aae77ee7130be10973d51a33d9fa942075b8cefbf7f930868b
ALLOY_VER=1.11.0 ALLOY_SHA=0423f20c7fbfc2cad1a2530eca2bb15ed22b0fedc4cf0d82c65296bf75d58bb5
TSDB_TAG=2.22.1

fetch() {  # url sha256 dest
  local tmp; tmp="$(mktemp "$S/.dl.XXXXXX")"
  curl -fsSL --retry 3 -o "$tmp" "$1"
  echo "$2  $tmp" | sha256sum -c --quiet - || { rm -f "$tmp"; echo "checksum mismatch for $1" >&2; exit 1; }
  mv "$tmp" "$3"
}
provision() {
  [[ -x $BIN/nats-server ]] || { fetch "https://github.com/nats-io/nats-server/releases/download/v$NATS_VER/nats-server-v$NATS_VER-linux-amd64.tar.gz" \
      "$NATS_SHA" "$S/nats.tgz"; tar -xzf "$S/nats.tgz" -C "$S" && mv "$S/nats-server-v$NATS_VER-linux-amd64/nats-server" "$BIN/"; }
  [[ -x $BIN/loki-linux-amd64 ]] || { fetch "https://github.com/grafana/loki/releases/download/v$LOKI_VER/loki-linux-amd64.zip" \
      "$LOKI_SHA" "$S/loki.zip"; unzip -oq "$S/loki.zip" -d "$BIN"; chmod +x "$BIN/loki-linux-amd64"; }
  [[ -x $BIN/alloy-linux-amd64 ]] || { fetch "https://github.com/grafana/alloy/releases/download/v$ALLOY_VER/alloy-linux-amd64.zip" \
      "$ALLOY_SHA" "$S/alloy.zip"; unzip -oq "$S/alloy.zip" -d "$BIN"; chmod +x "$BIN/alloy-linux-amd64"; }
  if ! ls /usr/lib/postgresql/16/lib/timescaledb-"$TSDB_TAG".so >/dev/null 2>&1; then
    rm -rf "$S/tsdb-src"
    git clone -q --depth 1 --branch "$TSDB_TAG" https://github.com/timescale/timescaledb.git "$S/tsdb-src"
    (cd "$S/tsdb-src" && ./bootstrap -DREGRESS_CHECKS=OFF -DTAP_CHECKS=OFF -DWARNINGS_AS_ERRORS=OFF \
       -DCMAKE_BUILD_TYPE=Release >"$S/logs/tsdb-build.log" 2>&1 && cd build && make -j"$(nproc)" >>"$S/logs/tsdb-build.log" 2>&1 \
       && make install >>"$S/logs/tsdb-build.log" 2>&1) || { tail -30 "$S/logs/tsdb-build.log"; exit 1; }
  fi
  # Loki: the shipped config with local paths
  sed "s#/loki#$S/loki#g" "$ROOT/configs/loki/config.yaml" > "$S/loki.yaml"
}

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
export INM_NOTIFICATION_ALLOWED_HOSTS=127.0.0.1   # on-prem receiver explicitly allow-listed (as an operator would)
export INM_ALLOW_DEMO_DATA_IN_LIVE=${INM_ALLOW_DEMO_DATA_IN_LIVE:-false}   # explicit, audited opt-in (live_operations suite only)
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
  install -d -o postgres "$S/pglog"                 # the server runs as postgres: its log dir must be writable
  su postgres -c "$PGBIN/pg_ctl -D $PGDATA status" >/dev/null 2>&1 ||
    su postgres -c "$PGBIN/pg_ctl -D $PGDATA -l $S/pglog/pg.log -o '-p 56432 -k /tmp' -w start" >/dev/null
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
start_loki() { mkdir -p "$S/loki"; launch loki "$BIN/loki-linux-amd64" -config.file="$S/loki.yaml"; wait_http http://127.0.0.1:3100/ready; }
start_alloy() {
  LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push launch alloy "$BIN/alloy-linux-amd64" run \
    --server.http.listen-addr=127.0.0.1:12345 --storage.path="$S/alloy" "$ROOT/configs/alloy/config.alloy"
  wait_http http://127.0.0.1:12345/-/ready
}
start_emu() {
  # the emulated switches listen on their own management addresses (loopback aliases; test harness only)
  for a in 10.250.0.24 10.250.0.25; do
    ip -o addr show dev lo | grep -q " $a/" || ip addr add "$a/32" dev lo
  done
  [[ -f $S/emu.crt ]] || openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=emulated-cx" \
    -keyout "$S/emu.key" -out "$S/emu.crt" >/dev/null 2>&1
  (cd "$ROOT" && launch emu "$PY" -m tests.fullstack.emulator_server \
    10.250.0.24:443:24:PL.10.14.1000:EMU-FS-24 10.250.0.25:443:48:ML.10.13.1020:EMU-FS-48 \
    --cert "$S/emu.crt" --key "$S/emu.key")
  wait_http http://127.0.0.1:18099/10.250.0.24/writes
}
start_api() {
  (cd "$ROOT" && SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD=$INM_NATS_API_PASSWORD \
    launch api "$PY" -m uvicorn backend.main:create_app --factory --host 127.0.0.1 --port 8810)
  wait_http http://127.0.0.1:8810/api/health
}
start_worker() {
  (cd "$ROOT" && SIMS_ROLE=worker INM_NATS_USER=inm_worker INM_NATS_PASSWORD=$INM_NATS_WORKER_PASSWORD \
    INSTANCE=fs-worker HOSTNAME=fs-worker launch worker "$PY" -m workers.network_worker.main)
  sleep 3; running worker || { tail -20 "$S/logs/worker.log"; exit 1; }
}
migrate() { (cd "$ROOT" && SIMS_ROLE=api INM_NATS_USER=inm_api INM_NATS_PASSWORD=$INM_NATS_API_PASSWORD \
  "$PY" -m backend.cli migrate > "$S/logs/migrate.log" 2>&1) || { tail -30 "$S/logs/migrate.log"; exit 1; }; }

ready() {  # readiness, not a fixed sleep: API reports DB + event bus connected and the worker heartbeat is fresh
  for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:8810/api/v1/ready 2>/dev/null | grep -q '"ready": *true' &&
       grep -q "connected to NATS JetStream" "$S/logs/worker.log" 2>/dev/null; then sleep 5; return 0; fi
    sleep 1
  done
  echo "stack not ready"; return 1
}
stop() {
  case $1 in
    pg) stop_pg ;;
    *) if running "$1"; then kill "$(cat "$(pidf "$1")")" 2>/dev/null || true; for _ in $(seq 1 40); do running "$1" || break; sleep 0.5; done
         running "$1" && kill -9 "$(cat "$(pidf "$1")")" 2>/dev/null || true; fi; rm -f "$(pidf "$1")" ;;
  esac
}
start() { "start_$1"; }

case ${1:-} in
  provision) provision ;;
  up) provision; start_pg; start_nats; start_loki; start_alloy; start_emu; migrate; start_api; start_worker; ready
      echo "stack up: http://127.0.0.1:8810" ;;
  down) for c in worker api emu alloy loki nats; do stop $c; done; stop_pg ;;
  stop) stop "$2" ;;
  start) start "$2" ;;
  restart) stop "$2"; start "$2" ;;
  migrate) migrate ;;
  status) for c in nats loki alloy emu api worker; do printf '%-7s %s\n' $c "$(running $c && echo running || echo stopped)"; done ;;
  *) echo "usage: $0 provision|up|down|status|start|stop|restart <comp>"; exit 2 ;;
esac
