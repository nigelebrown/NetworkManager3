#!/usr/bin/env bash
# Enable HTTPS access to an existing write-locked demo from an approved local subnet.
# No password change, no database reset, no Docker-wide prune.
# Usage: bash scripts/enable-lan-demo.sh 172.17.71.21 [172.17.71.0/24]
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="/etc/inm/inm.env"
ACL="/etc/inm/nginx-access.conf"
PROJECT=inm
die() { echo "[inm-lan] ERROR: $*" >&2; exit 1; }
say() { echo "[inm-lan] $*"; }
[[ $EUID -eq 0 ]] || die "Run as root on the CentOS VM."
# Environment exports take precedence over --env-file in Docker Compose; force
# this operation to use its validated, persisted source of truth.
unset INM_WEB_BIND INM_LAN_CLIENT_CIDR INM_LAN_DEMO_ACK INM_NGINX_ACCESS_FILE
[[ $# -ge 1 && $# -le 2 ]] || die "Usage: bash scripts/enable-lan-demo.sh <SERVER_LAN_IP> [ALLOWED_PRIVATE_CIDR]"
[[ -f "$ENV" && -f "$ROOT/compose.yaml" && -d "$ROOT/.git" ]] || die "Install INM from Git before enabling LAN access."
command -v python3 >/dev/null || die "python3 is required"
command -v ip >/dev/null || die "iproute tools are required"
command -v docker >/dev/null || die "Docker is not installed"
command -v flock >/dev/null || die "flock is required"
exec 9>/run/inm-lan-demo.lock
flock -n 9 || die "Another LAN exposure change is already running."
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || die "Git has modified tracked files. Refusing local source changes."
getenv() { sed -n "s/^$1=//p" "$ENV" | tail -n1; }
[[ "$(getenv INM_PROFILE)" == demo ]] || die "Only write-locked demo deployments are supported."
[[ "$(getenv SIMS_ADMIN_PASSWORD)" == 'demo123!' ]] || die "This script never changes passwords; expected existing demo123! credential is not set."
[[ "$(getenv INM_DEMO_SIMPLE_LOGIN)" == true ]] || die "Simple demo login must already be enabled."
[[ "$(getenv INM_WEB_BIND)" == 127.0.0.1 || "$(getenv INM_WEB_BIND)" == "$1" ]] \
  || die "Existing web bind is not localhost or requested IP. Refusing unexpected rebinding."
[[ "$(getenv INM_VERSION)" == "$(tr -d '[:space:]' < "$ROOT/VERSION")" ]] || die "Version mismatch: use approved Git update procedure."
[[ "$(python3 -I - "$ROOT/release.json" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get('device_writes', ''))
except Exception: print('')
PY
)" == LOCKED ]] || die "Device-write lock is not declared by this release."
SERVER_IP="$1"
PROPOSED_CIDR="${2:-}"
CIDR="$(python3 -I - "$SERVER_IP" "$PROPOSED_CIDR" <<'PY'
import ipaddress, subprocess, sys
try:
    ip = ipaddress.IPv4Address(sys.argv[1])
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError("server address must be a private non-loopback IPv4")
    result = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, check=True)
    addresses = [word for line in result.stdout.splitlines() for word in line.split() if "/" in word]
    matches = [ipaddress.IPv4Interface(word) for word in addresses if word.split("/")[0] == str(ip)]
    if len(matches) != 1:
        raise ValueError("requested server IP is not assigned to exactly one local network interface")
    network = ipaddress.IPv4Network(sys.argv[2], strict=False) if sys.argv[2] else matches[0].network
    if not network.is_private or network.prefixlen < 24 or ip not in network:
        raise ValueError("client CIDR must be private, include server IP, and be /24 or narrower")
    print(network)
except (ValueError, OSError, subprocess.CalledProcessError) as exc:
    sys.exit(str(exc) + "; pass an explicit allowed /24 (or narrower) as second argument")
PY
)" || die "LAN address/subnet validation failed."
say "Approving LAN access to $SERVER_IP:8443 from $CIDR only."
say "Keeping existing demo administrator password unchanged."
acl_existed=0
aclbackup="$(mktemp /etc/inm/nginx-access.before-lan.XXXXXXXX)"
if [[ -f "$ACL" ]]; then cp -p "$ACL" "$aclbackup"; acl_existed=1; fi
backup="$(mktemp /etc/inm/inm.env.before-lan.XXXXXXXX)"
cp -p "$ENV" "$backup"
setvar() {
  local key="$1" value="$2"
  if grep -q "^$key=" "$ENV"; then sed -i "s|^$key=.*|$key=$value|" "$ENV"
  else printf '%s=%s\n' "$key" "$value" >> "$ENV"; fi
}
rollback() {
  local rc=$?
  trap - EXIT
  if (( rc == 0 )); then return 0; fi
  say "LAN change failed (exit $rc): restoring original environment and access controls."
  cp -p "$backup" "$ENV" || true
  if (( acl_existed )); then cp -p "$aclbackup" "$ACL" || true; else rm -f "$ACL"; fi
  rm -f "$backup" "$aclbackup"
  # Recreate the previous API/web binding using the original env; never prune volumes.
  docker compose --project-name "$PROJECT" --project-directory "$ROOT" \
    --env-file "$ENV" -f "$ROOT/compose.yaml" up -d --no-deps --force-recreate api web || true
  exit "$rc"
}
trap rollback EXIT
# Bring the updated API guard and Nginx ACL into the runtime. No change to
# the admin password or PostgreSQL/TimescaleDB volume contents.
say "Building current Git backend and web images without changing data volumes."
docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" config --quiet
# The API service has image: but no build: declaration. The one-shot 'migrate'
# service owns the backend Dockerfile and produces the SAME inm/backend:<version>
# image consumed by API and worker. 'build api web' silently builds web only,
# leaving API running an old image that rejects LAN mode.
docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" build migrate web
# The API refuses to boot against an older management schema. A fresh backend
# image may require an additive migration; backup and apply it first.
bash "$ROOT/scripts/ensure-lan-schema.sh"
# Change LAN exposure only AFTER the pre-change encrypted backup, migration,
# and exact version verification have succeeded.
printf '# Authorized demo LAN clients only; compiled by enable-lan-demo.sh\nallow 127.0.0.1;\nallow %s;\ndeny all;\n' "$CIDR" > "$ACL"
chmod 0644 "$ACL"
setvar INM_NGINX_ACCESS_FILE "$ACL"
setvar INM_WEB_BIND "$SERVER_IP"
setvar INM_LAN_CLIENT_CIDR "$CIDR"
setvar INM_LAN_DEMO_ACK true
setvar INM_GIT_COMMIT "$(git -C "$ROOT" rev-parse HEAD)"
chmod 0600 "$ENV"
docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" config --quiet
say "Recreating API and HTTPS web containers; other services and all data volumes remain intact."
# Start API separately. The web service has a service_healthy dependency on
# API; starting both at once can report only a misleading dependency error.
if ! docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" up -d --no-deps --force-recreate --wait --wait-timeout 120 api; then
  echo "[inm-lan] API did not become healthy. Last 80 lines (credentials should not appear):" >&2
  docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
    -f "$ROOT/compose.yaml" logs --no-color --tail 80 api >&2 || true
  die "API startup failed: see the redacted logs above; rollback will restore the previous binding."
fi
docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" up -d --no-deps --force-recreate --wait --wait-timeout 120 web
# Test Nginx from inside its container. Host-to-published-port NAT often
# changes the source to a Docker bridge gateway, which the LAN CIDR ACL
# correctly blocks even though remote LAN clients are permitted.
docker compose --project-name "$PROJECT" --project-directory "$ROOT" --env-file "$ENV" \
  -f "$ROOT/compose.yaml" exec -T web \
    wget -q --no-check-certificate -O /dev/null https://127.0.0.1:8443/healthz \
  || die "The internal Nginx HTTPS health endpoint did not respond."
say "Verifying application and synthetic telemetry data pipeline."
bash "$ROOT/deployment/centos9/verify.sh"
# Best-effort firewalld restriction. Docker-published ports may bypass regular
# INPUT rules; the Nginx allow/deny is the primary inbound guard.
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  iface="$(ip -o -4 addr show | awk -v x="$SERVER_IP" '{ if ($4 ~ "^" x "/") { print $2; exit } }')"
  zone="$(firewall-cmd --get-zone-of-interface="$iface" 2>/dev/null || true)"
  [[ -n "$zone" && "$zone" != no ]] || zone="$(firewall-cmd --get-default-zone)"
  rule="rule family=\"ipv4\" source address=\"$CIDR\" port protocol=\"tcp\" port=\"8443\" accept"
  firewall-cmd --permanent --zone="$zone" --add-rich-rule="$rule" >/dev/null \
    && firewall-cmd --reload >/dev/null \
    && say "firewalld: allowed approved subnet on port 8443 (zone $zone)." \
    || say "WARNING: firewalld could not be updated; inspect your host firewall if clients time out."
else
  say "firewalld unavailable: Nginx CIDR ACL is active; check host/network firewall separately if remote clients time out."
fi
trap - EXIT
rm -f "$backup" "$aclbackup"
cat <<EOF

INM HTTPS is now bound to:  https://$SERVER_IP:8443
Approved client subnet:   $CIDR
Demo login:               admin
Demo password:            demo123! (UNCHANGED)
Device configuration:     WRITES LOCKED
Existing Docker volumes:  PRESERVED

The current self-signed TLS certificate may produce a trust/hostname warning.
Use a certificate trusted by your network clients for reliable authenticated access.
Do NOT expose TCP 8443 on the internet or outside the approved client subnet.
EOF
