#!/usr/bin/env bash
# SERVER FIRST: never replace local server commits with remote main.
# On divergence, preserve local main in a verifiable bundle + backup ref then STOP.
# Does not touch Docker containers, persistent volumes, credentials, IP or SSH.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo '[INM SYNC] root/sudo required' >&2; exit 2; }
ROOT="${1:-/opt/NetworkManager3}"
[[ $# -le 1 && -d "$ROOT/.git" ]] || { echo '[INM SYNC] Expected Git checkout path' >&2; exit 2; }
say(){ printf '[INM SYNC] %s\n' "$*"; }
die(){ say "ERROR: $*" >&2; exit 1; }
cd "$ROOT"
ORIGIN="$(git remote get-url origin)"
case "$ORIGIN" in
  https://github.com/nigelebrown/NetworkManager3|https://github.com/nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git) ;;
  *) die "Unrecognized Git origin '$ORIGIN'. No changes made." ;;
esac
[[ "$(git symbolic-ref --quiet --short HEAD)" == main ]] ||
  die 'Checkout must be on local main. No changes made.'
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  die 'Uncommitted changes or untracked files exist. Preserve them before syncing; no source files were changed.'
say 'Fetching GitHub main without merging.'
git fetch --no-tags origin main
BEFORE="$(git rev-parse --verify refs/heads/main)"
TARGET="$(git rev-parse --verify refs/remotes/origin/main)"
[[ -n "$BEFORE" && -n "$TARGET" ]] || die 'Cannot determine source and target commits.'
if [[ "$BEFORE" == "$TARGET" ]]; then
  say "Already synchronized at $TARGET"
elif git merge-base --is-ancestor "$BEFORE" "$TARGET"; then
  say "Fast-forwarding $BEFORE to $TARGET"
  git merge --ff-only "$TARGET"
else
  DIR=/var/lib/inm/git-main-backups
  install -d -m 0700 "$DIR"
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  REF="refs/heads/inm-local-backups/$STAMP"
  BUNDLE="$DIR/main-before-sync-$STAMP.bundle"
  TMP="$(mktemp "$DIR/.main-before-sync.XXXXXXXX.bundle")"
  trap 'rm -f -- "$TMP"' EXIT
  say "Local main diverged; preserving server commit $BEFORE"
  git bundle create "$TMP" refs/heads/main
  git bundle verify "$TMP" >/dev/null || die 'Git bundle failed verification; branch unchanged.'
  chmod 0600 "$TMP"
  [[ ! -e "$BUNDLE" ]] || die 'Backup filename collision; branch unchanged.'
  mv -- "$TMP" "$BUNDLE"
  git update-ref "$REF" "$BEFORE" || die 'Could not preserve backup ref; branch unchanged.'
  [[ "$(git rev-parse --verify "$REF")" == "$BEFORE" && -s "$BUNDLE" ]] ||
    die 'Backup ref/bundle verification failed; branch unchanged.'
  say "Preserved old branch: $REF"
  say "Preserved offline Git bundle: $BUNDLE"
  say "SERVER FIRST: local main $BEFORE is authoritative; remote main $TARGET will NOT be deployed."
  die "Diverged history preserved in $BUNDLE. Review server commits and reconcile into GitHub via a separate branch/PR; deployment stopped with the working server checkout unchanged."
fi
[[ "$(git rev-parse HEAD)" == "$TARGET" ]] ||
  die 'Checkout did not reach origin/main. Deployment must not continue.'
say "PASS: local main matches origin/main at $TARGET"
