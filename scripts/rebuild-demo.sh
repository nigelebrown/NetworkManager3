#!/usr/bin/env bash
# Git-only, single-command Phase 1 DEMO reload on CentOS Stream 9.
# This stops/removes ONLY Docker containers belonging to Compose project "inm".
# Never runs docker system prune and NEVER deletes named volumes or /etc/inm secrets.
# Intentional exception: demo administrator password becomes demo123! ONLY if
# web binds 127.0.0.1, profile is DEMO, and all device-write paths remain locked.
set -Eeuo pipefail
umask 027

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="/etc/inm/inm.env"
PROJECT=inm
die() { printf '[inm-demo-reload] ERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '[inm-demo-reload] %s\n' "$*"; }

[[ $EUID -eq 0 ]] || die "Run as root (or sudo with root's configured GitHub SSH deploy key)."
[[ -d "$ROOT/.git" ]] || die "Application must come from a GitHub clone, not a PC/ZIP source package."
command -v git >/dev/null || die "Install Git: dnf -y install git"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || die "Uncommitted tracked source changes; refusing to overwrite."
ORIGIN="$(git -C "$ROOT" remote get-url origin)"
case "$ORIGIN" in
  github-networkmanager3:nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git|https://github.com/nigelebrown/NetworkManager3.git) ;;
  *) die "Unexpected repository origin: $ORIGIN" ;;
esac
if [[ -f "$ENV" ]]; then
  read_value() { sed -n "s/^$1=//p" "$ENV" | head -1; }
  [[ "$(read_value INM_PROFILE)" == demo ]] || die "Existing installation is not a demo: refusing to modify it."
  [[ "$(read_value INM_WEB_BIND)" == 127.0.0.1 ]] || die "Weak demo credentials require INM_WEB_BIND=127.0.0.1. Stop: do not expose on LAN."
  [[ "$(read_value INM_VERSION)" == "$(tr -d '[:space:]' < "$ROOT/VERSION")" ]] \
    || die "Installed version differs; use the approved tagged update workflow, not demo rebuild."
  if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    if [[ -n "$(docker ps -q --filter label=com.docker.compose.project=inm)" ]]; then
      say "Existing INM containers detected. Creating ENCRYPTED backup before any replacement."
      (cd "$ROOT" && bash deployment/centos9/backup.sh --label pre-demo-rebuild) \
        || die "Backup failed. No containers have been removed; resolve storage health first."
    fi
  fi
fi

say "Fetching latest application source directly from private GitHub"
[[ "$(git -C "$ROOT" symbolic-ref --quiet --short HEAD || true)" == main ]] \
  || die "Current source is not on main; make a controlled change before the demo reload."
git -C "$ROOT" fetch --quiet origin main || die "Git fetch failed; check deploy key and internet access."
git -C "$ROOT" merge --ff-only origin/main || die "Non-fast-forward update refused."
[[ "$(sed -n 's/.*\"device_writes\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' "$ROOT/release.json" | head -1)" == LOCKED ]] \
  || die "Release does not declare device writes locked."

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  say "Stopping/removing containers for Compose project inm ONLY (retaining all named data volumes)"
  if [[ -f "$ENV" ]]; then
    docker compose --project-name "$PROJECT" --project-directory "$ROOT" \
      -f "$ROOT/compose.yaml" --env-file "$ENV" down --remove-orphans \
      || die "Failed to stop previous INM containers."
  else
    mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=inm")
    if [[ "${#ids[@]}" -gt 0 ]]; then
      docker rm -f "${ids[@]}" || die "Failed to remove stale INM containers."
    fi
  fi
fi

say "Installing from Git source with Docker Compose (new images built locally)"
(cd "$ROOT" && bash deployment/centos9/install.sh --mode demo) \
  || die "Installation failed; read /var/log/inm/install-*.log"

[[ -f "$ENV" ]] || die "Installer did not generate secrets file."
[[ "$(sed -n 's/^INM_WEB_BIND=//p' "$ENV" | head -1)" == 127.0.0.1 ]] \
  || die "Refusing simple demo login on any non-loopback web bind."
[[ "$(sed -n 's/^INM_PROFILE=//p' "$ENV" | head -1)" == demo ]] \
  || die "Refusing demo login outside demo profile."

# Non-secret demo toggles in the 0600 environment; this code NEVER touches
# auth/password settings if the bind address or release safety conditions fail.
set_var() {
  local key="$1" val="$2"
  if grep -q "^$key=" "$ENV"; then
    sed -i "s|^$key=.*|$key=$val|" "$ENV"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
}
set_var SIMS_ADMIN_PASSWORD 'demo123!'
set_var INM_DEMO_SIMPLE_LOGIN 'true'
set_var INM_FORCE_PASSWORD_CHANGE 'false'
chmod 0600 "$ENV"
say "Recreating API with the loopback-only demo authentication profile"
docker compose --project-name "$PROJECT" --project-directory "$ROOT" \
  -f "$ROOT/compose.yaml" --env-file "$ENV" up -d --no-deps --force-recreate api \
  || die "Unable to restart API with demo-only settings."
# The API must have completed startup and migrations before changing the hash.
for attempt in $(seq 1 30); do
  if docker compose --project-name "$PROJECT" --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
      --env-file "$ENV" exec -T api python -m backend.demo_access; then
    break
  fi
  [[ "$attempt" != 30 ]] || die "Demo credential reset failed; check API logs."
  sleep 2
done
say "Verifying all nine containers, authorization, logging, databases and synthetic pipeline"
(cd "$ROOT" && bash deployment/centos9/verify.sh) || die "End-to-end check failed."
cat <<'INFO'

======================================================================
  INTELLIGENT NETWORK MANAGER — DEMO INSTALLED FROM GITHUB
======================================================================
  Browser URL (on the server): https://127.0.0.1:8443
  Secure remote browser: SSH tunnel, then https://localhost:8443
  Demo login:    admin
  Demo password: demo123!
  Add devices:   Inventory → Add device
  Simulated devices: labelled DEMO
  Real snapshots:   labelled REAL · MANUAL
  Optional REST reads: require approved INM_READONLY_DEVICE_CIDRS
  Device configuration changes: DISABLED
  Docker data volumes: PRESERVED
  Other Docker applications: UNTOUCHED

  Next verification: cd /root/NetworkManager3 && bash scripts/inm.sh verify
  Logs: /var/log/inm/install-*.log
======================================================================

INFO
