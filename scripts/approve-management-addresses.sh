#!/usr/bin/env bash
# Apply an Inventory export of explicitly reviewed, exact management addresses.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 && $# == 1 && -f "$1" ]] || {
  echo "Usage: sudo bash scripts/approve-management-addresses.sh APPROVALS.txt" >&2
  exit 2
}
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
envfile=/etc/inm/inm.env
guard="$root/scripts/inm_worker_egress_guard.sh"
exec 9>/run/inm-readonly-approval.lock
flock -n 9
[[ -f "$envfile" ]]
compose() { docker compose -p inm --project-directory "$root" -f "$root/compose.yaml" --env-file "$envfile" "$@"; }
unset INM_APPROVED_DEVICE_IPS
grep -qx INM_PROFILE=lab-readonly "$envfile"
old="$(sed -n 's/^INM_APPROVED_DEVICE_IPS=//p' "$envfile" | tail -1)"
approved="$(python3 -I - "$1" "$old" <<'PY'
import ipaddress
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if path.stat().st_size > 20000:
    sys.exit("Approval file is too large.")
new = path.read_text(encoding="utf-8-sig").splitlines()
if not any(line.strip() for line in new):
    sys.exit("Approval file is empty.")
private = [ipaddress.IPv4Network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
addresses = set()
for value in [*sys.argv[2].split(","), *new]:
    if value.strip():
        address = ipaddress.IPv4Address(value.strip())
        if not any(address in net for net in private):
            sys.exit("Approvals must be exact RFC1918 IPv4 addresses.")
        addresses.add(address)
if len(addresses) > 1000:
    sys.exit("At most 1000 exact addresses may be approved.")
print(",".join(map(str, sorted(addresses))))
PY
)"
backup="$(mktemp /etc/inm/inm.env.approval.XXXXXXXX)"
cp -p "$envfile" "$backup"
finish() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 )); then
    cp -p "$backup" "$envfile"
    bash "$guard" --apply || true
    compose up -d --no-deps --no-build --pull never --wait api worker || true
  fi
  rm -f "$backup"
  exit "$rc"
}
trap finish EXIT
sed -i '/^INM_APPROVED_DEVICE_IPS=/d' "$envfile"
printf 'INM_APPROVED_DEVICE_IPS=%s\n' "$approved" >> "$envfile"
chmod 0600 "$envfile"
compose config --quiet
bash "$guard" --install
compose up -d --no-deps --no-build --pull never --wait --wait-timeout 180 api worker
for service in api worker; do
  [[ "$(compose exec -T "$service" printenv INM_APPROVED_DEVICE_IPS)" == "$approved" ]]
done
echo "Exact management approvals applied."
