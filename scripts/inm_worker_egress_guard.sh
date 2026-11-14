#!/usr/bin/env bash
# INM worker Docker egress guard. Fail-closed IPv4 filter, no privilege or TLS relaxation.
# DOCKER-USER filters FORWARD traffic from the worker's "inm_devices" bridge.
# Allowed outbound destinations: explicit private INM_READONLY_DEVICE_CIDRS only.
# All other forwarded traffic from that bridge is dropped, including public internet.
# Internal backend service traffic remains on the separate internal Docker bridge.
#
# Usage:
#   sudo bash scripts/inm_worker_egress_guard.sh --install
#   sudo bash scripts/inm_worker_egress_guard.sh --apply
#   sudo bash scripts/inm_worker_egress_guard.sh --check
set -Eeuo pipefail
umask 077
MODE="${1:---apply}"
case "$MODE" in --install|--apply|--check) ;; *) echo "Usage: $0 [--install|--apply|--check]" >&2; exit 2 ;; esac
[[ "$EUID" == 0 ]] || { echo '[INM EGRESS] Root required.' >&2; exit 2; }
say(){ printf '[INM EGRESS] %s\n' "$*"; }
die(){ say "ERROR: $*" >&2; exit 1; }
for command in docker iptables ip python3; do
  command -v "$command" >/dev/null || die "Missing command: $command"
done
docker info >/dev/null 2>&1 || die 'Docker daemon unavailable.'
[[ -f /etc/inm/inm.env ]] || die 'Existing INM environment file required.'
[[ "$(docker network inspect inm_devices --format '{{.Driver}}' 2>/dev/null)" == bridge ]] ||
  die 'Docker network inm_devices must exist and use the bridge driver.'
[[ "$(docker network inspect inm_devices --format '{{.EnableIPv6}}' 2>/dev/null)" == false ]] ||
  die 'IPv6 on inm_devices must be disabled or independently filtered (fail closed).'
NETID="$(docker network inspect inm_devices --format '{{.Id}}')"
BRIDGE="$(docker network inspect inm_devices --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || true)"
BRIDGE="${BRIDGE:-br-${NETID:0:12}}"
[[ "$BRIDGE" =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Unexpected Docker bridge name.'
ip link show dev "$BRIDGE" >/dev/null 2>&1 || die "Docker bridge $BRIDGE missing."

# This bridge must be exclusively attached to the INM worker.
NETWORK_MEMBERS="$(docker network inspect inm_devices --format '{{json .Containers}}')"
python3 -I - "$NETWORK_MEMBERS" <<'PY' || die 'Devices bridge contains unrecognized containers.'
import json,sys
members=json.loads(sys.argv[1])
bad=[c.get('Name') for c in (members or {}).values() if c.get('Name')!='inm-worker-1']
if bad: raise SystemExit("Unexpected network members: "+repr(bad))
PY

getenv(){ sed -n "s/^$1=//p" /etc/inm/inm.env | tail -1; }
PROFILE="$(getenv INM_PROFILE)"
[[ "$PROFILE" == demo || "$PROFILE" == lab-readonly ]] ||
  die "Unexpected INM profile $PROFILE."
[[ "$(getenv INM_DEVICE_WRITE_LOCK)" != false &&
   "$(getenv INM_ALLOW_CONFIG_CHANGES)" != true ]] ||
  die 'Configuration-write lock is not engaged; do not apply this acceptance guard.'
CIDRS="$(getenv INM_READONLY_DEVICE_CIDRS)"
# Validate every CIDR before touching firewall state (fail closed).
VALIDATED="$(python3 -I - "$CIDRS" <<'PY'
import ipaddress,sys
raw=[v.strip() for v in sys.argv[1].split(',') if v.strip()]
if len(raw)>20: raise SystemExit('More than 20 CIDRs.')
for value in raw:
    n=ipaddress.ip_network(value,strict=False)
    if n.version!=4 or not n.is_private or n.prefixlen<24 or n.is_loopback or n.is_link_local:
        raise SystemExit('Invalid non-private management CIDR.')
    print(n)
PY
)" || die 'CIDR validation failed; leaving firewall unchanged.'
if [[ "$MODE" == --check ]]; then
  iptables -w -C DOCKER-USER -i "$BRIDGE" -j INM_WKR_EGRESS ||
    die 'Worker egress chain is not installed.'
  iptables -w -C INM_WKR_EGRESS -j DROP ||
    die 'Worker egress chain has no terminal DROP.'
  say "PASS: firewall egress policy installed on $BRIDGE."
  exit 0
fi
iptables -w -L DOCKER-USER -n >/dev/null 2>&1 ||
  die 'DOCKER-USER firewall chain missing; cannot enforce worker egress.'
# Place a temporary DROP in front of any old rule while updating the chain.
# If anything fails, the worker remains blocked rather than falling open.
iptables -w -I DOCKER-USER 1 -i "$BRIDGE" -j DROP
iptables -w -N INM_WKR_EGRESS 2>/dev/null || true
iptables -w -F INM_WKR_EGRESS
while IFS= read -r cidr; do
  [[ -n "$cidr" ]] || continue
  iptables -w -A INM_WKR_EGRESS -d "$cidr" -j RETURN
done <<< "$VALIDATED"
iptables -w -A INM_WKR_EGRESS -j DROP
# Replace any old jump while the temporary DROP is still active.
# The validated chain must be the first non-temporary matching rule.
while iptables -w -C DOCKER-USER -i "$BRIDGE" -j INM_WKR_EGRESS 2>/dev/null; do
  iptables -w -D DOCKER-USER -i "$BRIDGE" -j INM_WKR_EGRESS
done
iptables -w -I DOCKER-USER 2 -i "$BRIDGE" -j INM_WKR_EGRESS
iptables -w -D DOCKER-USER -i "$BRIDGE" -j DROP ||
  die 'Could not remove temporary guard: the egress bridge remains BLOCKED.'
say "Worker egress locked on $BRIDGE; allowed private network count: $(printf '%s\n' "$VALIDATED" | grep -c . || true)."
# The existing verification performs a real 1.1.1.1 connection attempt.
# No firewall acceptance is claimed until that probe fails.

if [[ "$MODE" == --install ]]; then
  UNIT=/etc/systemd/system/inm-worker-egress-guard.service
  if [[ -e "$UNIT" && ! -f "$UNIT" ]]; then die "Unexpected unit type at $UNIT."; fi
  if [[ -f "$UNIT" ]] && ! grep -q '^# Managed by NetworkManager3$' "$UNIT"; then
    die "Existing unit not managed by INM: $UNIT."
  fi
  cat > "$UNIT" <<'UNITFILE'
# Managed by NetworkManager3
[Unit]
Description=INM worker restrictive Docker-device-network egress guard
Requires=docker.service
After=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /opt/NetworkManager3/scripts/inm_worker_egress_guard.sh --apply

[Install]
WantedBy=docker.service
UNITFILE
  chmod 0644 "$UNIT"
  systemctl daemon-reload
  systemctl enable inm-worker-egress-guard.service >/dev/null
  say 'Installed persistent Docker service hook; rerun after any firewalld rule reload.'
fi
