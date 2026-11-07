#!/usr/bin/env bash
# One-command update to an approved, immutable release tag (Appendix H.1 / P).
#   sudo bash deployment/centos9/update.sh --ref <tag>
# Sequence: preflight -> verified backup -> record old release/schema -> checkout tag (signature checked when
# INM_REQUIRE_SIGNED_TAGS=1) -> build -> migrate (expand-only) -> health-gated start -> verify -> receipt.
# If the new release fails verification, the previous release is restarted automatically ONLY when the schema
# gate confirms the previous code accepts the (possibly migrated) database; otherwise it stops and prints the
# manual restore path. It never reverses a migration by itself.
source "$(dirname "$0")/common.sh"
REF=""
while [[ $# -gt 0 ]]; do case "$1" in --ref) REF="${2:-}"; shift 2 ;; *) die "unknown option $1" 2 ;; esac; done
[[ -n "$REF" ]] || die "usage: sudo bash deployment/centos9/update.sh --ref <approved-tag>" 2
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed: use install.sh" 2
start_transcript update

[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || die "the checkout at $ROOT has local modifications: refusing (no manual source edits on servers)" 3
git -C "$ROOT" fetch --tags --quiet origin 2>/dev/null || warn "could not fetch from origin; using local tags"
git -C "$ROOT" rev-parse -q --verify "refs/tags/$REF" >/dev/null || die "'$REF' is not a tag. Updates must use an immutable release tag." 3
if [[ "${INM_REQUIRE_SIGNED_TAGS:-0}" == 1 ]]; then git -C "$ROOT" verify-tag "$REF" || die "tag signature verification failed" 3; fi

OLD_COMMIT="$(git_commit)"; OLD_VERSION="$(release_version)"
OLD_SCHEMA="$(schema_status)"
log "current: $OLD_VERSION ($OLD_COMMIT) schema $OLD_SCHEMA; target: $REF"
NEW_RELEASE="$(mktemp)"; git -C "$ROOT" show "$REF:release.json" > "$NEW_RELEASE"
python3 -I -c 'import json,sys;r=json.load(open(sys.argv[1]));assert r.get("migrations_expand_only") is True, "target release does not declare expand-only migrations"' "$NEW_RELEASE" \
  || die "target release is not marked migrations_expand_only: requires a planned, manual upgrade" 3

log "BACKUP before any change"
BACKUP="$(bash "$ROOT/deployment/centos9/backup.sh" --label "pre-update-$REF" | tail -1)"
[[ -f "$BACKUP" ]] || die "backup failed: update aborted, nothing changed" 4

rollback_app() {
  warn "new release failed verification; evaluating automatic application rollback to $OLD_VERSION"
  local cur old_rel; cur="$(mktemp)"; old_rel="$(mktemp)"
  schema_status > "$cur" || echo '{}' > "$cur"
  git -C "$ROOT" show "$OLD_COMMIT:release.json" > "$old_rel"
  if python3 -I "$ROOT/deployment/tools/schema_gate.py" "$cur" "$old_rel"; then
    git -C "$ROOT" checkout -q "$OLD_COMMIT"
    set_env_value INM_VERSION "$OLD_VERSION"; set_env_value INM_GIT_COMMIT "$OLD_COMMIT"
    compose up -d --wait --wait-timeout 600 && bash "$ROOT/deployment/centos9/verify.sh" \
      && { record_release rollback success "automatic after failed update to $REF"; die "update to $REF FAILED; previous release $OLD_VERSION restored and verified" 9; }
  fi
  record_release update blocked "failed update to $REF; automatic rollback not safe"
  die "update FAILED and automatic rollback is NOT safe. Data is intact. Manual path: check out $OLD_COMMIT, then restore.sh --archive $BACKUP --target live --confirm-overwrite-live-data" 10
}

git -C "$ROOT" checkout -q "$REF"
set_env_value INM_VERSION "$(release_version)"; set_env_value INM_GIT_COMMIT "$(git_commit)"
log "building $(release_version)"
INM_GIT_COMMIT="$(git_commit)" compose build --pull || rollback_app
compose pull --quiet management-db timeseries-db nats loki alloy || rollback_app
log "migrating and starting (health-gated)"
compose up -d --wait --wait-timeout 600 --remove-orphans management-db timeseries-db nats loki alloy ai api web worker || rollback_app
images_lock
bash "$ROOT/deployment/centos9/verify.sh" || rollback_app
record_release update success "from $OLD_VERSION ($OLD_COMMIT) schema $OLD_SCHEMA; backup $BACKUP"
log "UPDATE COMPLETE: $OLD_VERSION -> $(release_version). Pre-update backup: $BACKUP"
