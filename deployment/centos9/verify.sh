#!/usr/bin/env bash
# Readiness verification (Appendix H.1). Exits non-zero, naming each failed dependency, when any required API,
# worker, storage, messaging, logging, AI, access-control or migration check fails.
#   sudo bash deployment/centos9/verify.sh [--json]
source "$(dirname "$0")/common.sh"
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed ($ENV_FILE missing)" 2

FAILED=()
check() { local name=$1; shift; if "$@"; then echo "[PASS] $name"; else echo "[FAIL] $name"; FAILED+=("$name"); fi; }

containers_running() {
  local missing=0 svc
  for svc in management-db timeseries-db nats loki alloy ai api web worker; do
    if ! compose ps --status running "$svc" 2>/dev/null | grep -q "$svc"; then echo "       container not running: $svc"; missing=1; fi
  done
  return $missing
}

secrets_protected() {
  local f m ok=0
  for f in "$ENV_FILE" "$ETC/initial-admin-password" "$ETC/backup.key" "$ETC/tls/tls.key"; do
    [[ -e "$f" ]] || continue
    m=$(stat -c %a "$f"); [[ "$m" == 600 ]] || { echo "       $f has mode $m (expected 600)"; ok=1; }
  done
  [[ "$(stat -c %a "$ETC")" == 700 ]] || { echo "       $ETC is not mode 700"; ok=1; }
  if git -C "$ROOT" ls-files --error-unmatch .env inm.env >/dev/null 2>&1; then echo "       an env file is tracked by git"; ok=1; fi
  return $ok
}

only_expected_ports() {
  # Only web (HTTPS) and alloy (syslog) may publish ports, and only on the configured bind address (P1-DEP-03).
  local bad=0 line
  while read -r line; do
    [[ -z "$line" ]] && continue
    case "$line" in
      inm-web-*|inm-alloy-*) ;;
      *) echo "       unexpected published port: $line"; bad=1 ;;
    esac
  done < <(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}} {{.Ports}}' | grep -- '->' || true)
  for svc in management-db timeseries-db nats loki api worker ai; do
    if docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$svc" --format '{{.Ports}}' | grep -q -- '->'; then
      echo "       $svc publishes a host port"; bad=1
    fi
  done
  return $bad
}

worker_public_egress_blocked() {
  # Worker needs opt-in private device management egress but never general
  # internet access. Require a host firewall policy AND live socket denial.
  bash "$ROOT/scripts/inm_worker_egress_guard.sh" --check || return 1
  compose exec -T worker python -c "
import socket,sys
s=socket.socket(); s.settimeout(3)
try:
    s.connect(('1.1.1.1',443))
    print('       SECURITY FAILURE: worker reached public 1.1.1.1:443')
    sys.exit(1)
except OSError:
    print('       public internet egress blocked as expected')
    sys.exit(0)"
}

app_checks() {
  compose exec -T api python -m backend.cli verify --api-url http://127.0.0.1:8000 --syslog-host alloy --syslog-port 1514 ${JSON:+--json}
}

JSON=""; [[ "${1:-}" == "--json" ]] && JSON=1
check "containers: all seven roles running" containers_running
check "security: secrets files protected (0600/0700, not in git)" secrets_protected
check "security: only web and syslog publish ports" only_expected_ports
check "safety: worker egress firewall and public internet denied" worker_public_egress_blocked
check "application: health, safety, AI isolation, syslog, end-to-end event" app_checks

echo
if [[ ${#FAILED[@]} -eq 0 ]]; then echo "VERIFY RESULT: PASS"; exit 0; fi
echo "VERIFY RESULT: FAIL — ${FAILED[*]}"
exit 1
