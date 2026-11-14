#!/usr/bin/env bash
# One-command clean installation (Appendix H.1):
#   sudo bash deployment/centos9/install.sh --mode live    (production: real devices, read-only collection)
#   sudo bash deployment/centos9/install.sh --mode demo    (evaluation: simulated sites only)
# State machine: PRECHECK -> PREPARE -> DEPLOY -> VERIFY -> COMMIT (release receipt). Re-running is safe:
# secrets are never regenerated, data volumes are never touched, and a version change is refused (use update.sh).
# Options:
#   --mode live|demo       required. live = INM_MODE=live, INM_PROFILE=lab-readonly (no simulated sites; demo data
#                          only via the in-app Load/Remove buttons). Device writes stay locked in both modes.
#   --preflight-only       run PRECHECK and stop
#   --skip-docker-install  fail instead of installing Docker CE when it is missing
source "$(dirname "$0")/common.sh"
source "$(dirname "$0")/doctor.sh"

MODE="" PREFLIGHT_ONLY=0 SKIP_DOCKER=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --preflight-only) PREFLIGHT_ONLY=1; shift ;;
    --skip-docker-install) SKIP_DOCKER=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown option $1" 2 ;;
  esac
done
[[ "$MODE" == demo || "$MODE" == live ]] || die "usage: sudo bash deployment/centos9/install.sh --mode live|demo" 2

require_root
start_transcript install
# shellcheck disable=SC2154  # rc is assigned inside the trap body
trap 'rc=$?; if [[ $rc -ne 0 ]]; then warn "install FAILED (exit $rc). Transcript: $TRANSCRIPT"; record_release install failed "exit $rc" || true; fi' EXIT

# ---------------------------------------------------------------- PRECHECK
log "PRECHECK"
FAIL=0
preflight
[[ $FAIL -eq 0 ]] || die "preflight failed: fix the [FAIL] items above. Nothing was installed or changed." 3
if [[ -f "$ENV_FILE" ]]; then
  installed="$(env_get INM_VERSION)"
  if [[ -n "$installed" && "$installed" != "$(release_version)" ]]; then
    die "INM $installed is installed; this checkout is $(release_version). Use update.sh --ref <tag> (it backs up first)." 5
  fi
  log "existing installation of $(release_version) found: re-running install idempotently"
else
  check_ports_free "${INM_WEB_PORT:-8443}" "${INM_SYSLOG_PORT:-1514}"
fi
[[ $PREFLIGHT_ONLY -eq 1 ]] && { log "preflight only: stopping here"; trap - EXIT; exit 0; }

# ---------------------------------------------------------------- PREPARE
log "PREPARE"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  [[ $SKIP_DOCKER -eq 0 ]] || die "Docker CE with the compose plugin is required" 6
  log "installing Docker CE and the Compose plugin from download.docker.com"
  dnf -y install dnf-plugins-core ca-certificates curl git openssl iproute
  dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
  dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker >/dev/null
for t in openssl git ss python3; do command -v "$t" >/dev/null || die "required tool missing: $t" 6; done
install -d -m 0750 "$STATE" "$BACKUP_DIR"
generate_secrets
ensure_tls
# Containerized Loki/NATS/Alloy and Nginx read these PUBLIC, tracked configs as
# non-root users. Root-run git clone under umask 077 otherwise makes them mode
# 0600 with 0700 parent directories and the services fail startup.
# Never relax permissions on /etc/inm, credentials or data volumes.
for directory in "$ROOT/configs" "$ROOT/configs/loki" "$ROOT/configs/nats" "$ROOT/configs/alloy" "$ROOT/configs/nginx"; do
  [[ -d "$directory" && ! -L "$directory" ]] || die "invalid public configuration directory: $directory" 6
  chmod 0755 "$directory"
done
for config in "$ROOT/configs/loki/config.yaml" "$ROOT/configs/nats/nats.conf" \
              "$ROOT/configs/alloy/config.alloy" \
              "$ROOT/configs/nginx/inm.conf" "$ROOT/configs/nginx/inm-access-default.conf"; do
  [[ -f "$config" && ! -L "$config" ]] || die "invalid public config file: $config" 6
  chmod 0644 "$config"
done
log "public container configuration permissions normalized (secrets unchanged)"
set_env_value INM_GIT_COMMIT "$(git_commit)"
if [[ "$MODE" == live ]]; then
  [[ "$(env_get SIMS_ADMIN_PASSWORD)" != 'demo123!' ]] || die "the demo password cannot be used in live mode" 2
  set_env_value INM_MODE live
  set_env_value INM_PROFILE lab-readonly
  set_env_value INM_DEMO_SIMPLE_LOGIN false
else
  set_env_value INM_MODE demo
fi
compose config --quiet || die "compose configuration invalid" 7

# ---------------------------------------------------------------- DEPLOY
log "DEPLOY: pulling pinned service images"
compose pull --quiet management-db timeseries-db nats loki alloy
log "DEPLOY: building application images (inm/backend, inm/ai, inm/web $(release_version))"
INM_GIT_COMMIT="$(git_commit)" compose build --pull
log "DEPLOY: starting roles in dependency order (schema migration runs once, before API and worker)"
compose up -d --wait --wait-timeout 600 --remove-orphans \
  management-db timeseries-db nats loki alloy ai api web worker
images_lock

# Enforce explicit private-management allowlists at the host forwarding layer.
# Docker's devices bridge otherwise NATs to the internet by default, even
# while the application denies unapproved targets at the API layer.
# Must run before readiness verification, and never remove existing volumes.
log "SECURITY: restricting worker Docker egress to approved private management ranges"
if ! command -v iptables >/dev/null; then
  dnf -y install iptables-nft || die "cannot install required host egress firewall support" 8
fi
bash "$ROOT/scripts/inm_worker_egress_guard.sh" --install ||
  die "cannot enforce worker egress security; installation blocked" 8

# ---------------------------------------------------------------- VERIFY
log "VERIFY"
bash "$ROOT/deployment/centos9/verify.sh" || die "verification failed: see the [FAIL] lines above" 8

# ---------------------------------------------------------------- COMMIT
record_release install success "mode=$MODE"
trap - EXIT
cat <<EOF

INM $(release_version) (${MODE^^} profile) is installed and verified.
  Web UI   : https://${INM_WEB_BIND:-$(env_get INM_WEB_BIND)}:$(env_get INM_WEB_PORT)/
             (bound to $(env_get INM_WEB_BIND) — from your workstation: ssh -L 8443:127.0.0.1:$(env_get INM_WEB_PORT) $(operator_name | cut -d' ' -f1)@$(hostname))
  Sign in  : user '$(env_get INM_ADMIN_USERNAME)'; the one-time password is in $ETC/initial-admin-password (root only).
             You must choose a new password at first sign-in, then create a named account for each person.
  Backups  : sudo bash deployment/centos9/backup.sh   (store $ETC/backup.key separately, off this host)
  Transcript: $TRANSCRIPT
EOF
