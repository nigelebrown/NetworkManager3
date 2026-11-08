#!/usr/bin/env bash
# Authorize only specified private IPv4 management targets for one-off read-only REST probes.
# Usage: bash scripts/allow-readonly-device-range.sh 10.103.5.20/32,10.103.5.21/32
set -Eeuo pipefail
umask 027
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="/etc/inm/inm.env"
die() { echo "[inm] ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run as root or sudo"
[[ -f "$ENV" ]] || die "Install INM from Git before configuring device reads"
CIDRS="${1:-}"
[[ -n "$CIDRS" && $# -eq 1 ]] || die "usage: bash scripts/allow-readonly-device-range.sh 10.103.5.20/32,10.103.5.21/32"
[[ "$(sed -n 's/^INM_PROFILE=//p' "$ENV" | head -1)" == demo ]] || die "this helper is for the local demo only"
[[ "$(sed -n 's/^INM_WEB_BIND=//p' "$ENV" | head -1)" == 127.0.0.1 ]] || die "demo web service is not bound to loopback"
CIDRS="$(python3 - "$CIDRS" <<'PY'
import ipaddress,sys
out=[]
for token in sys.argv[1].split(','):
    net=ipaddress.ip_network(token.strip(), strict=False)
    if not isinstance(net, ipaddress.IPv4Network) or not net.is_private:
        raise SystemExit("Only private IPv4 management targets may be authorized")
    if net.prefixlen < 24:
        raise SystemExit("For demo, authorize individual /32 addresses or at most a /24 management subnet")
    out.append(str(net))
if len(out)>20: raise SystemExit("At most 20 approved address ranges")
print(','.join(sorted(set(out))))
PY
)" || die "invalid approved management CIDR(s)"
if grep -q '^INM_READONLY_DEVICE_CIDRS=' "$ENV"; then
  sed -i "s|^INM_READONLY_DEVICE_CIDRS=.*|INM_READONLY_DEVICE_CIDRS=$CIDRS|" "$ENV"
else
  printf 'INM_READONLY_DEVICE_CIDRS=%s\n' "$CIDRS" >> "$ENV"
fi
chmod 0600 "$ENV"
docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
  --env-file "$ENV" up -d --no-deps --force-recreate api
docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
  --env-file "$ENV" ps api
echo "Read-only API targets approved: $CIDRS"
echo "Only administrator-initiated HTTPS login + GET observation calls are supported; writes remain locked."
