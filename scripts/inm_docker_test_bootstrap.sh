#!/usr/bin/env bash
# Single-command entrypoint for an EXISTING Docker-managed INM installation.
# Bootstrap is read-only to devices, databases, and Docker volumes.
# It discovers a checkout, verifies the Docker runtime, updates source FF-only,
# and runs the installed software + authorized read-only hardware acceptance.
# Usage: sudo bash inm_docker_test_bootstrap.sh [--software-only|--hardware-only]
set -Eeuo pipefail
umask 077

say() { printf '[INM Docker Test] %s\n' "$*"; }
die() { printf '[INM Docker Test] ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" == 0 ]] || die "Run this script with sudo/root."
command -v docker >/dev/null 2>&1 || die "Docker is not installed. No INM testing performed."
docker info >/dev/null 2>&1 || die "Docker daemon not available. Check systemctl status docker."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 plugin missing."

say "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unavailable) / $(docker compose version --short 2>/dev/null || echo unavailable)"
say "Installed Compose projects:"
docker compose ls --all 2>/dev/null || true
say "Relevant Docker containers (name / image / project):"
docker ps -a --format '{{.Names}} | {{.Image}} | {{.Label "com.docker.compose.project"}}' |
  grep -Ei '(^inm|sims|aruba|wifi|network)' || say "No container names matching INM/SIMS/Aruba found."

is_repo() {
  local dir="$1" origin
  [[ -d "$dir/.git" && -f "$dir/compose.yaml" ]] || return 1
  origin="$(git -C "$dir" remote get-url origin 2>/dev/null)" || return 1
  case "$origin" in
    https://github.com/nigelebrown/NetworkManager3|https://github.com/nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git|github-networkmanager3:nigelebrown/NetworkManager3.git) return 0 ;;
    *) return 1 ;;
  esac
}

ROOT=""
if [[ -n "${INM_ROOT:-}" ]]; then
  is_repo "$INM_ROOT" || die "INM_ROOT=$INM_ROOT is not a valid NetworkManager3 Git checkout."
  ROOT="$INM_ROOT"
fi
if [[ -z "$ROOT" ]]; then
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    if is_repo "$dir"; then ROOT="$dir"; break; fi
  done < <(
    {
      printf '%s\n' /root/NetworkManager3 /opt/NetworkManager3 /opt/inm /srv/NetworkManager3 /srv/inm /usr/local/src/NetworkManager3
      docker ps -a --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null || true
      find /root /opt /srv /home /usr/local/src -maxdepth 5 -type f -name compose.yaml -print 2>/dev/null |
        while IFS= read -r file; do dirname "$file"; done
    } | awk 'NF && !seen[$0]++'
  )
fi

if [[ -z "$ROOT" ]]; then
  say "Could not locate a Git-managed NetworkManager3 checkout in Docker project paths or common installation directories."
  say "No clone, install, migration, container restart, or device access was attempted."
  say "If this is an older SIMS WiFi Manager installation, do not replace it automatically."
  say "Run: docker compose ls --all ; docker ps -a"
  say "To set up a SEPARATE clean INM installation later, review https://github.com/nigelebrown/NetworkManager3/blob/main/deployment/centos9/install.sh"
  exit 3
fi

say "Located INM source checkout: $ROOT"
ENV_FILE="${INM_ETC_DIR:-/etc/inm}/inm.env"
[[ -f "$ENV_FILE" ]] || {
  say "Missing $ENV_FILE; this is not a confirmed current INM Docker deployment."
  say "Refusing to treat any legacy SIMS containers as the new INM application."
  exit 3
}
command -v git >/dev/null || die "Git not installed."
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || die "Git checkout has local/untracked changes. No pull or tests were run."
[[ "$(git -C "$ROOT" branch --show-current)" == main ]] || die "Checkout must be on main; currently $(git -C "$ROOT" branch --show-current)."
say "Fetching latest source (fast-forward only; does NOT rebuild containers or modify databases)."
git -C "$ROOT" fetch origin main
git -C "$ROOT" merge --ff-only origin/main

# The underlying software test compares the checked-out SHA with running API
# and worker images and fails if they differ, rather than hiding a stale deployment.
say "Running installed Docker stack checks followed by allowlisted physical read-only hardware tests."
say "Configuration writes stay locked. No Docker volumes will be deleted."
export INM_ROOT="$ROOT"
bash "$ROOT/scripts/inm_one_click_test.sh" "$@"
