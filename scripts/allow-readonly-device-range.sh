#!/usr/bin/env bash
# Authorize only specified private IPv4 management targets for one-off read-only REST probes.
# Usage: bash scripts/allow-readonly-device-range.sh 10.103.5.20/32,10.103.5.21/32
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="/etc/inm/inm.env"
die() { echo "[inm] ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run as root or sudo"
[[ -f "$ENV" ]] || die "Install INM from Git before configuring device reads"
CIDRS="${1:-}"
[[ -n "$CIDRS" && $# -eq 1 ]] || die "usage: bash scripts/allow-readonly-device-range.sh 10.103.5.20/32,10.103.5.21/32"
[[ "$(sed -n 's/^INM_PROFILE=//p' "$ENV" | head -1)" == demo ]] || die "this helper is for the local demo only"
# A weak-password demo may authorize device reads when served over the approved,
# CIDR-restricted LAN mode. Never allow unrestricted web exposure.
getenv() { sed -n "s/^$1=//p" "$ENV" | tail -n1; }
BIND="$(getenv INM_WEB_BIND)"
if [[ "$BIND" != 127.0.0.1 ]]; then
  [[ "$(getenv INM_LAN_DEMO_ACK)" == true &&
     "$(getenv INM_DEMO_SIMPLE_LOGIN)" == true &&
     "$(getenv SIMS_ADMIN_PASSWORD)" == 'demo123!' ]] \
    || die "Non-loopback demo requires the approved LAN-demo access policy."
  python3 -I - "$BIND" "$(getenv INM_LAN_CLIENT_CIDR)" "$(getenv INM_NGINX_ACCESS_FILE)" <<'PY' \
    || die "LAN demo access policy is missing or broader than the approved private subnet."
import ipaddress,pathlib,sys
try:
    ip=ipaddress.IPv4Address(sys.argv[1])
    net=ipaddress.IPv4Network(sys.argv[2],strict=False)
    acl=pathlib.Path(sys.argv[3])
    if not (ip.is_private and not ip.is_loopback and not ip.is_link_local and
            net.is_private and net.prefixlen>=24 and ip in net and
            acl == pathlib.Path("/etc/inm/nginx-access.conf") and acl.is_file()):
        raise ValueError("LAN binding or ACL path is not approved")
    lines=[line.strip() for line in acl.read_text().splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    if set(lines)!={"allow 127.0.0.1;",f"allow {net};","deny all;"}:
        raise ValueError("Nginx ACL must allow only localhost and one approved private subnet")
except (OSError,ValueError) as exc:
    sys.exit(str(exc))
PY
fi
[[ "$(python3 -I - "$ROOT/release.json" <<'PY'
import json,sys
try: print(json.load(open(sys.argv[1])).get("device_writes",""))
except (OSError,ValueError): print("")
PY
)" == LOCKED ]] || die "Refusing device access: release does not declare hardware writes locked"
# Add to existing approved ranges without silently revoking other devices.
CIDRS="$(python3 -I - "$(getenv INM_READONLY_DEVICE_CIDRS)" "$CIDRS" <<'PY'
import ipaddress,sys
out=[]
for raw in sys.argv[1:]:
    for token in raw.split(","):
        token=token.strip()
        if not token: continue
        net=ipaddress.ip_network(token,strict=False)
        if not isinstance(net,ipaddress.IPv4Network) or not net.is_private or net.prefixlen<24:
            raise SystemExit("Only private IPv4 /24-/32 management targets can be approved")
        if str(net) not in out: out.append(str(net))
if len(out)>20: raise SystemExit("At most 20 approved address ranges")
print(",".join(out))
PY
)" || die "Invalid management targets; no settings modified"
command -v docker >/dev/null || die "Docker is required"
command -v flock >/dev/null || die "flock is required"
exec 9>/run/inm-readonly-approval.lock
flock -n 9 || die "Another approval is running"
# Environment exports override the persisted Compose env-file: reject overrides.
unset INM_READONLY_DEVICE_CIDRS INM_WEB_BIND INM_LAN_DEMO_ACK INM_LAN_CLIENT_CIDR INM_NGINX_ACCESS_FILE
backup="$(mktemp /etc/inm/inm.env.readonly.XXXXXXXX)"
cp -p "$ENV" "$backup"
restore_on_failure() {
  local rc=$?
  trap - EXIT
  if (( rc == 0 )); then rm -f "$backup"; return; fi
  cp -p "$backup" "$ENV" || true
  rm -f "$backup"
  echo "[inm] Restored previous allowlist after API restart failure." >&2
  docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
    --env-file "$ENV" up -d --no-deps --force-recreate api >/dev/null 2>&1 || true
  exit "$rc"
}
trap restore_on_failure EXIT
if grep -q '^INM_READONLY_DEVICE_CIDRS=' "$ENV"; then
  sed -i "s|^INM_READONLY_DEVICE_CIDRS=.*|INM_READONLY_DEVICE_CIDRS=$CIDRS|" "$ENV"
else
  printf 'INM_READONLY_DEVICE_CIDRS=%s\n' "$CIDRS" >> "$ENV"
fi
chmod 0600 "$ENV"
docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
  --env-file "$ENV" config --quiet || die "Compose validation failed"
docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
  --env-file "$ENV" up -d --no-deps --force-recreate --wait --wait-timeout 120 api \
  || die "API failed its health check; the old allowlist will be restored"
trap - EXIT
rm -f "$backup"
echo "Read-only API targets approved: $CIDRS"
echo "No device configuration changes are enabled. Full /24 sweeps remain prohibited."
