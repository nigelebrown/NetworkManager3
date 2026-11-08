#!/usr/bin/env bash
# Safe expand-only 0004/0005 -> 0006 schema upgrade for existing INM demos.
# Called by enable-lan-demo.sh after building the current backend image.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="/etc/inm/inm.env"
compose() {
  docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" --env-file "$ENV" "$@"
}
fail() { echo "[inm-schema] ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 && -f "$ENV" ]] || fail "Root and an existing installation are required."
[[ "$(sed -n 's/^INM_PROFILE=//p' "$ENV" | tail -1)" == demo ]] || fail "Only a demo may be upgraded by this helper."
[[ "$(sed -n 's/^SIMS_ADMIN_PASSWORD=//p' "$ENV" | tail -1)" == 'demo123!' ]] || fail "Demo password changed; refusing."
status() {
  # Use a new one-off image, not the running API, which rejects older schemas.
  compose run -T --rm --no-deps migrate python -m backend.cli schema-status | tail -n 1
}
before="$(status)" || fail "Cannot inspect existing management and telemetry schema."
echo "[inm-schema] Existing revisions: $before"
decision="$(python3 -I - "$before" "$ROOT/release.json" <<'PY'
import json,sys
try:
    cur=json.loads(sys.argv[1]); r=json.load(open(sys.argv[2]))
    if r.get('device_writes')!='LOCKED' or cur.get('telemetry')!=r['telemetry_schema_head']:
        raise ValueError('unexpected telemetry schema or device-write policy')
    if cur.get('management')==r['management_schema_head']: print('CURRENT')
    elif (cur.get('management') in ('0004','0005') and r['management_schema_head']=='0006'
          and r.get('migrations_expand_only') is True): print('MIGRATE')
    else: raise ValueError('unsupported management revision')
except (ValueError,KeyError,TypeError,OSError) as e:
    sys.exit('UNSAFE SCHEMA: '+str(e))
PY
)" || fail "Unknown schema. Nothing was migrated."
if [[ "$decision" == MIGRATE ]]; then
  echo "[inm-schema] Creating encrypted backup before management schema migration."
  tmp="$(mktemp /etc/inm/.schema-backup.XXXXXXXX)"
  if ! bash "$ROOT/deployment/centos9/backup.sh" --label pre-lan-schema-upgrade >"$tmp" 2>&1; then
    cat "$tmp" >&2; rm -f "$tmp"; fail "Encrypted backup failed; no migration attempted."
  fi
  archive="$(tail -n 1 "$tmp")"
  cat "$tmp"; rm -f "$tmp"
  [[ -f "$archive" && -f "$archive.sha256" ]] || fail "Backup archive/checksum missing."
  (cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256") \
    || fail "Backup checksum mismatch."
  echo "[inm-schema] Backup verified: $archive"
  echo "[inm-schema] Applying expand-only schema migration through one-shot backend."
  compose run -T --rm --no-deps migrate \
    || fail "Migration failed; do not downgrade manually. Backup: $archive"
else
  echo "[inm-schema] Already at target revision; preserving existing database."
fi
after="$(status)" || fail "Unable to inspect schema after migration."
python3 -I - "$after" "$ROOT/release.json" <<'PY' || fail "Unexpected schema after migration."
import json,sys
cur=json.loads(sys.argv[1]); r=json.load(open(sys.argv[2]))
if cur.get('management')!=r['management_schema_head'] or cur.get('telemetry')!=r['telemetry_schema_head']:
    raise SystemExit('Unexpected management or telemetry revision.')
print('[inm-schema] PASS: current schema matches backend release.')
PY
