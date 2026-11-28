#!/usr/bin/env bash
# Encrypted, checksummed backup of every stateful role (Appendix H.1 / P).
#   sudo bash deployment/centos9/backup.sh [--label <text>]
# Contents: management DB (pg_dump -Fc), telemetry DB (pg_dump -Fc), JetStream store, Loki store, app data
# (uploaded firmware), and the secrets env file. Everything is encrypted with AES-256 (openssl, PBKDF2) using
# /etc/inm/backup.key; keep that key OFF this host. A plaintext manifest (no secrets) carries versions, schema
# revisions and SHA-256 of every component. Restore is proven separately: restore.sh --target test.
source "$(dirname "$0")/common.sh"
LABEL="manual"
while [[ $# -gt 0 ]]; do case "$1" in --label) LABEL="${2:-manual}"; shift 2 ;; *) die "unknown option $1" 2 ;; esac; done
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed" 2
[[ -f "$ETC/backup.key" ]] || die "$ETC/backup.key missing" 2
start_transcript backup

TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d "$BACKUP_DIR/.work-$TS-XXXX")"
chmod 0700 "$WORK"
trap 'rm -rf "$WORK"; compose unpause nats loki >/dev/null 2>&1 || true' EXIT

log "dumping management database"
compose exec -T management-db pg_dump -U inm_app -d inm_management -Fc > "$WORK/management.dump"
log "dumping telemetry database"
compose exec -T timeseries-db pg_dump -U inm_metrics -d inm_telemetry -Fc > "$WORK/telemetry.dump"

SCHEMA="$(schema_status || echo '{}')"
NATS_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q nats)")"

volume_tar() {  # $1 compose volume  $2 output name  — uses the (busybox) nats image for tar
  docker run --rm --network none -v "${PROJECT}_$1:/src:ro" -v "$WORK:/out" "$NATS_IMAGE" \
    tar -czf "/out/$2" -C /src .
}
log "snapshotting JetStream and Loki stores (services paused for a consistent copy; producers retry)"
compose pause nats loki >/dev/null
volume_tar nats-data jetstream.tgz
volume_tar loki-data loki.tgz
compose unpause nats loki >/dev/null
volume_tar app-data appdata.tgz
cp "$ENV_FILE" "$WORK/inm.env"

( cd "$WORK" && sha256sum management.dump telemetry.dump jetstream.tgz loki.tgz appdata.tgz inm.env > SHA256SUMS )
python3 -I - "$WORK/manifest.json" "$TS" "$LABEL" "$(release_version)" "$(git_commit)" "$(operator_name)" "$SCHEMA" "$WORK/SHA256SUMS" <<'PY'
import json, sys
out, ts, label, version, commit, operator, schema, sums = sys.argv[1:]
files = {l.split()[1]: l.split()[0] for l in open(sums) if l.strip()}
json.dump({"format": "inm-backup/1", "created_utc": ts, "label": label, "version": version, "git_commit": commit,
           "operator": operator, "schema": json.loads(schema or "{}"), "components": files,
           "encryption": "openssl aes-256-cbc pbkdf2 iter=200000, key /etc/inm/backup.key",
           "contains_secrets": "inm.env (encrypted)"}, open(out, "w"), indent=2)
PY

NAME="inm-backup-$TS-$LABEL"
tar -C "$WORK" -cf - management.dump telemetry.dump jetstream.tgz loki.tgz appdata.tgz inm.env SHA256SUMS manifest.json \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$ETC/backup.key" -out "$BACKUP_DIR/$NAME.tar.enc"
cp "$WORK/manifest.json" "$BACKUP_DIR/$NAME.manifest.json"
( cd "$BACKUP_DIR" && sha256sum "$NAME.tar.enc" > "$NAME.tar.enc.sha256" )
chmod 0600 "$BACKUP_DIR/$NAME".*

log "backup written: $BACKUP_DIR/$NAME.tar.enc ($(du -h "$BACKUP_DIR/$NAME.tar.enc" | cut -f1))"
log "prove it restores: sudo bash deployment/centos9/restore.sh --archive $BACKUP_DIR/$NAME.tar.enc --target test"
record_release backup success "$NAME"
echo "$BACKUP_DIR/$NAME.tar.enc"
