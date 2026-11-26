#!/usr/bin/env bash
# Restore a backup made by backup.sh.
#   sudo bash deployment/centos9/restore.sh --archive <file.tar.enc> [--target test]      (default, safe)
#   sudo bash deployment/centos9/restore.sh --archive <file.tar.enc> --target live --confirm-overwrite-live-data
# --target test (P1-10): restores into DISPOSABLE volumes of a separate compose project ("inm-restoretest"),
#   verifies checksums, row counts, schema revisions and the audit hash chain, then deletes the disposable
#   volumes. The running installation is not touched.
# --target live: takes a fresh backup of current data first, stops the application roles, replaces the data
#   of every stateful role from the archive, then starts and verifies. Requires the explicit confirmation flag.
source "$(dirname "$0")/common.sh"
ARCHIVE="" TARGET=test CONFIRM=0 KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --confirm-overwrite-live-data) CONFIRM=1; shift ;;
    --keep) KEEP=1; shift ;;
    *) die "unknown option $1" 2 ;;
  esac
done
require_root
[[ -f "$ARCHIVE" ]] || die "--archive <file.tar.enc> is required" 2
[[ "$TARGET" == test || "$TARGET" == live ]] || die "--target must be test or live" 2
[[ -f "$ETC/backup.key" ]] || die "$ETC/backup.key missing (restore needs the key that made the backup)" 2
start_transcript "restore-$TARGET"

if [[ -f "$ARCHIVE.sha256" ]]; then
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" ) || die "archive checksum mismatch" 3
fi
WORK="$(mktemp -d /var/tmp/inm-restore-XXXX)"; chmod 0700 "$WORK"
RT=inm-restoretest
cleanup() {
  rm -rf "$WORK"
  if [[ "$TARGET" == test && $KEEP -eq 0 ]]; then
    docker rm -f "$RT-mgmt" "$RT-ts" >/dev/null 2>&1 || true
    docker volume rm "${RT}_mgmt" "${RT}_ts" "${RT}_nats" "${RT}_loki" >/dev/null 2>&1 || true
    docker network rm "$RT" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$ETC/backup.key" -in "$ARCHIVE" | tar -C "$WORK" -xf - \
  || die "cannot decrypt/unpack the archive (wrong backup.key?)" 3
( cd "$WORK" && sha256sum -c SHA256SUMS >/dev/null ) || die "component checksum mismatch inside the archive" 3
log "archive decrypted; all component checksums match ($(python3 -I -c 'import json,sys;m=json.load(open(sys.argv[1]));print(m["version"], m["created_utc"], m["schema"])' "$WORK/manifest.json"))"

PG_IMG="$(grep -E '^INM_IMAGE_POSTGRES=' "$ENV_FILE" | cut -d= -f2- || true)"; PG_IMG="${PG_IMG:-postgres:17-alpine}"
TS_IMG="$(grep -E '^INM_IMAGE_TIMESCALEDB=' "$ENV_FILE" | cut -d= -f2- || true)"; TS_IMG="${TS_IMG:-timescale/timescaledb:2.22.1-pg17}"

if [[ "$TARGET" == test ]]; then
  log "restoring into disposable project '$RT' (current data is not touched)"
  docker network create --internal "$RT" >/dev/null
  PW="$(rand 32)"
  docker run -d --name "$RT-mgmt" --network "$RT" -e POSTGRES_USER=inm_app -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=inm_management \
    -v "${RT}_mgmt:/var/lib/postgresql/data" "$PG_IMG" >/dev/null
  docker run -d --name "$RT-ts" --network "$RT" -e POSTGRES_USER=inm_metrics -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=inm_telemetry \
    -e TIMESCALEDB_TELEMETRY=off -v "${RT}_ts:/var/lib/postgresql/data" "$TS_IMG" >/dev/null
  for c in "$RT-mgmt" "$RT-ts"; do
    for _ in $(seq 1 60); do docker exec "$c" pg_isready -q && break; sleep 2; done
  done
  docker exec -i "$RT-mgmt" pg_restore -U inm_app -d inm_management --no-owner --exit-on-error < "$WORK/management.dump"
  docker exec "$RT-ts" psql -q -U inm_metrics -d inm_telemetry -c "SELECT timescaledb_pre_restore();" >/dev/null
  docker exec -i "$RT-ts" pg_restore -U inm_metrics -d inm_telemetry --no-owner < "$WORK/telemetry.dump" || warn "pg_restore reported warnings (TimescaleDB catalog notices are expected)"
  docker exec "$RT-ts" psql -q -U inm_metrics -d inm_telemetry -c "SELECT timescaledb_post_restore();" >/dev/null
  for v in nats loki; do
    docker volume create "${RT}_$v" >/dev/null
    docker run --rm --network none -v "${RT}_$v:/dst" -v "$WORK:/in:ro" "$PG_IMG" sh -c "tar -xzf /in/$( [[ $v == nats ]] && echo jetstream || echo loki ).tgz -C /dst && ls /dst | head -3" >/dev/null
  done
  log "verifying restored data with the INM CLI (schema, row counts, audit hash chain)"
  docker run --rm --network "$RT" \
    -e SIMS_DATABASE_URL="postgresql+psycopg://inm_app:$PW@$RT-mgmt:5432/inm_management" \
    -e INM_TELEMETRY_DATABASE_URL="postgresql+psycopg://inm_metrics:$PW@$RT-ts:5432/inm_telemetry" \
    -e SIMS_JWT_SECRET="$(rand 48)" -e SIMS_FERNET_KEY="$(env_get SIMS_FERNET_KEY)" -e SIMS_ROLE=api \
    -i "inm/backend:$(env_get INM_VERSION)" python - <<'PY'
import json
from sqlalchemy import create_engine, text
import os
from backend.cli import _init, schema_status
from backend.database.session import session_scope
from backend.security.deps import verify_audit_chain
_init()
mg = create_engine(os.environ["SIMS_DATABASE_URL"]); ts = create_engine(os.environ["INM_TELEMETRY_DATABASE_URL"])
with mg.connect() as c:
    counts = {t: c.execute(text(f"select count(*) from {t}")).scalar() for t in
              ("users", "sites", "access_points", "audit_log", "event_records", "inm_devices", "release_events")}
with ts.connect() as c:
    counts["telemetry_points"] = c.execute(text("select count(*) from telemetry_points")).scalar()
with session_scope() as db:
    chain = verify_audit_chain(db)
result = {"schema": schema_status(), "row_counts": counts, "audit_chain": chain}
print(json.dumps(result, indent=2))
assert chain["ok"], "audit chain broken in restored copy"
assert counts["users"] >= 1 and counts["event_records"] >= 1, "restored database is empty"
PY
  log "RESTORE TEST PASSED: backup is restorable; disposable volumes will now be removed (use --keep to inspect)"
  record_release restore success "test restore of $(basename "$ARCHIVE")"
  exit 0
fi

# ---------------------------------------------------------------- live restore
[[ $CONFIRM -eq 1 ]] || die "live restore replaces ALL current data. Re-run with --confirm-overwrite-live-data" 4
log "taking a safety backup of the current data first"
bash "$ROOT/deployment/centos9/backup.sh" --label pre-restore
log "stopping application roles"
compose stop web api worker
log "restoring management and telemetry databases"
compose exec -T management-db sh -c 'dropdb -U inm_app --if-exists inm_management_restore; createdb -U inm_app inm_management_restore'
compose exec -T management-db pg_restore -U inm_app -d inm_management_restore --no-owner --exit-on-error < "$WORK/management.dump"
compose exec -T management-db sh -c 'psql -q -U inm_app -d postgres -c "ALTER DATABASE inm_management RENAME TO inm_management_replaced_'"$(date -u +%Y%m%d%H%M%S)"'" -c "ALTER DATABASE inm_management_restore RENAME TO inm_management"'
compose exec -T timeseries-db sh -c 'dropdb -U inm_metrics --if-exists inm_telemetry_restore; createdb -U inm_metrics inm_telemetry_restore'
compose exec -T timeseries-db psql -q -U inm_metrics -d inm_telemetry_restore -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();"
compose exec -T timeseries-db pg_restore -U inm_metrics -d inm_telemetry_restore --no-owner < "$WORK/telemetry.dump" || warn "pg_restore warnings (TimescaleDB notices are expected)"
compose exec -T timeseries-db psql -q -U inm_metrics -d inm_telemetry_restore -c "SELECT timescaledb_post_restore();"
compose exec -T timeseries-db sh -c 'psql -q -U inm_metrics -d postgres -c "ALTER DATABASE inm_telemetry RENAME TO inm_telemetry_replaced_'"$(date -u +%Y%m%d%H%M%S)"'" -c "ALTER DATABASE inm_telemetry_restore RENAME TO inm_telemetry"'
log "restoring JetStream and Loki stores"
compose stop nats loki
for pair in "nats-data:jetstream.tgz" "loki-data:loki.tgz" "app-data:appdata.tgz"; do
  vol="${pair%%:*}"; file="${pair##*:}"
  docker run --rm --network none -v "${PROJECT}_$vol:/dst" -v "$WORK:/in:ro" "$PG_IMG" sh -c "rm -rf /dst/* && tar -xzf /in/$file -C /dst"
done
compose up -d --wait --wait-timeout 600
bash "$ROOT/deployment/centos9/verify.sh" || die "verification after live restore failed. The replaced databases are kept as *_replaced_<timestamp>." 8
record_release restore success "live restore of $(basename "$ARCHIVE")"
log "LIVE RESTORE COMPLETE. Previous databases kept as inm_*_replaced_<timestamp> until you drop them."
