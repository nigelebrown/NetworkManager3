#!/usr/bin/env bash
# Operator-initiated application rollback to an earlier approved tag (Appendix H.1).
#   sudo bash deployment/centos9/rollback.sh --to <tag> --acknowledge-schema-compatible
# The schema gate compares the CURRENT database revisions with the revisions the target release declares it
# accepts. If they are not compatible the script stops with exit 3 and changes nothing (INTEG-P1-10): the only
# safe path is restoring the pre-update backup, which it prints. It never downgrades a schema.
source "$(dirname "$0")/common.sh"
REF="" ACK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to|--ref) REF="${2:-}"; shift 2 ;;
    --acknowledge-schema-compatible) ACK=1; shift ;;
    *) die "unknown option $1" 2 ;;
  esac
done
[[ -n "$REF" ]] || die "usage: sudo bash deployment/centos9/rollback.sh --to <tag> --acknowledge-schema-compatible" 2
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed" 2
start_transcript rollback
git -C "$ROOT" rev-parse -q --verify "refs/tags/$REF" >/dev/null || die "'$REF' is not a tag" 2

CUR="$(mktemp)"; TGT="$(mktemp)"
schema_status > "$CUR" || die "cannot read the current schema revisions (is the API running?)" 4
git -C "$ROOT" show "$REF:release.json" > "$TGT" 2>/dev/null || die "$REF has no release.json: it predates the gated release format; use restore.sh" 3
if ! python3 -I "$ROOT/deployment/tools/schema_gate.py" "$CUR" "$TGT"; then
  record_release rollback blocked "target $REF incompatible with schema $(cat "$CUR")"
  die "ROLLBACK REFUSED: $REF cannot run on the current schema. Nothing was changed. See manual_path above." 3
fi
[[ $ACK -eq 1 ]] || die "schema gate passed. Re-run with --acknowledge-schema-compatible to proceed." 2

PREV="$(git_commit)"
git -C "$ROOT" checkout -q "$REF"
set_env_value INM_VERSION "$(release_version)"; set_env_value INM_GIT_COMMIT "$(git_commit)"
INM_GIT_COMMIT="$(git_commit)" compose build
compose up -d --wait --wait-timeout 600 management-db timeseries-db nats loki alloy ai api web worker
if bash "$ROOT/deployment/centos9/verify.sh"; then
  images_lock
  record_release rollback success "from $PREV to $REF"
  log "ROLLBACK COMPLETE: now running $(release_version)"
else
  record_release rollback failed "from $PREV to $REF"
  die "rollback verification failed; previous commit was $PREV" 8
fi
