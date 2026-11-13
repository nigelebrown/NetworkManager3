#!/usr/bin/env bash
# Intelligent Network Manager: all application source comes from GitHub.
# GitHub SSH deploy key for nigelebrown/NetworkManager3 is read-only.
set -Eeuo pipefail
umask 027

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${INM_ETC_DIR:-/etc/inm}/inm.env"
APP="$ROOT/deployment/centos9"
REMOTE_REPO="nigelebrown/NetworkManager3"
CHANNEL_FILE="deployment/releases/approved-demo-tag"

say() { printf '[inm-git] %s\n' "$*"; }
fail() { printf '[inm-git] ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
Git-managed Intelligent Network Manager — Phase 1 demo (all device writes locked).

  bash scripts/inm.sh install [--mode live|demo]  Fetch GitHub main; clean install or same-commit repair
  bash scripts/inm.sh update              Install the approved release tag in GitHub's demo channel
  bash scripts/inm.sh update --ref TAG    Update to a specific approved, immutable Git tag
  bash scripts/inm.sh doctor              CentOS preflight report
  bash scripts/inm.sh verify              Health and synthetic end-to-end checks
  bash scripts/inm.sh status              Health, containers, release receipts
  bash scripts/inm.sh backup              Encrypted state backup
  bash scripts/inm.sh rollback --to TAG --acknowledge-schema-compatible

First server clone (authorized read-only GitHub SSH deploy key):
  git clone github-networkmanager3:nigelebrown/NetworkManager3.git /root/NetworkManager3
  cd /root/NetworkManager3 && bash scripts/inm.sh install

No Windows PC, ZIP, USB, source transfer or copied installation packages are required.
Git is the application source; approved base images and build dependencies come from
their respective package registries during installation.
HELP
}

COMMAND="${1:---help}"
shift || true
if [[ "$COMMAND" == "-h" || "$COMMAND" == "--help" || "$COMMAND" == help ]]; then usage; exit 0; fi
case "$COMMAND" in
  install|update|doctor|verify|status|backup|rollback) ;;
  *) usage >&2; fail "unknown command: $COMMAND" ;;
esac
[[ $EUID -eq 0 ]] || fail "run as root, or with sudo using the authorized GitHub deploy key"
command -v git >/dev/null || fail "Git missing: on CentOS use dnf -y install git"
[[ -d "$ROOT/.git" ]] || fail "use a Git clone of $REMOTE_REPO; ZIP copies are unsupported"
[[ -f "$ROOT/compose.yaml" && -f "$ROOT/release.json" ]] || fail "incomplete Git source checkout"
[[ -f "$APP/install.sh" && -f "$APP/verify.sh" && -f "$APP/update.sh" ]] || fail "deployment scripts absent in Git checkout"
ORIGIN="$(git -C "$ROOT" remote get-url origin 2>/dev/null)" || fail "Git origin missing"
case "$ORIGIN" in
  github-networkmanager3:"$REMOTE_REPO".git|git@github.com:"$REMOTE_REPO".git|https://github.com/"$REMOTE_REPO".git|https://github.com/"$REMOTE_REPO") ;;
  *) fail "unexpected origin '$ORIGIN', expected $REMOTE_REPO" ;;
esac
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || fail "local tracked files modified: deploy only reviewed Git source"

INSTALLED=""
if [[ -f "$ENV_FILE" ]]; then
  INSTALLED="$(sed -n 's/^INM_GIT_COMMIT=//p' "$ENV_FILE" | head -1)"
  [[ -n "$INSTALLED" && "$INSTALLED" != unknown ]] || fail "existing installation has no reliable INM_GIT_COMMIT: inspect $ENV_FILE"
fi

case "$COMMAND" in
  install)
    if [[ -z "$INSTALLED" ]]; then
      say "Fresh install: fetching latest main directly from GitHub"
      [[ "$(git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == main ]] \
        || fail "fresh install must be on main, not a detached/unreviewed commit"
      git -C "$ROOT" fetch --quiet origin main || fail "cannot fetch GitHub main"
      git -C "$ROOT" merge --ff-only origin/main || fail "cannot safely fast-forward local checkout"
    else
      [[ "$(git -C "$ROOT" rev-parse HEAD)" == "$INSTALLED" ]] \
        || fail "checkout changed since install (installed $INSTALLED); use tagged update"
      say "Re-running same Git commit idempotently; secrets and named data volumes preserved"
    fi
    MODE="${INM_INSTALL_MODE:-}"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --mode) [[ $# -ge 2 ]] || fail "--mode requires live or demo"; MODE="$2"; shift 2 ;;
        *) fail "unknown install argument $1" ;;
      esac
    done
    if [[ -z "$MODE" && -f "$ENV_FILE" ]]; then MODE="$(sed -n 's/^INM_MODE=//p' "$ENV_FILE" | tail -1)"; fi
    MODE="${MODE:-demo}"
    [[ "$MODE" == live || "$MODE" == demo ]] || fail "--mode must be live or demo"
    say "Installing $(cat "$ROOT/VERSION") at $(git -C "$ROOT" rev-parse --short HEAD) in $MODE mode"
    exec bash "$APP/install.sh" --mode "$MODE"
    ;;
  update)
    [[ -n "$INSTALLED" ]] || fail "not installed; use install first"
    [[ "$(git -C "$ROOT" rev-parse HEAD)" == "$INSTALLED" ]] \
      || fail "checkout differs from installed commit: manual reconciliation required"
    REF=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --ref) [[ $# -ge 2 ]] || fail "--ref requires a tag"; REF="$2"; shift 2 ;;
        *) fail "unknown update argument $1" ;;
      esac
    done
    say "Fetching approved release metadata and tags from GitHub"
    git -C "$ROOT" fetch --quiet origin main --tags || fail "fetch failed; update refused"
    if [[ -z "$REF" ]]; then
      REF="$(git -C "$ROOT" show "refs/remotes/origin/main:$CHANNEL_FILE" 2>/dev/null | sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' | head -1)" \
        || fail "approved release channel cannot be read"
      [[ -n "$REF" ]] || fail "no approved demo tag published in $CHANNEL_FILE; update refused"
    fi
    [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "invalid Git tag name $REF"
    git -C "$ROOT" rev-parse -q --verify "refs/tags/$REF^{commit}" >/dev/null \
      || fail "Git tag $REF unavailable; publisher must tag and approve a release"
    say "Updating from approved Git tag $REF (backup, schema gate and verification required)"
    exec bash "$APP/update.sh" --ref "$REF"
    ;;
  doctor|verify|status|backup)
    [[ $# -eq 0 ]] || fail "unexpected arguments for $COMMAND"
    [[ -f "$APP/$COMMAND.sh" ]] || fail "missing $APP/$COMMAND.sh"
    exec bash "$APP/$COMMAND.sh"
    ;;
  rollback)
    [[ -n "$INSTALLED" ]] || fail "not installed"
    exec bash "$APP/rollback.sh" "$@"
    ;;
esac
