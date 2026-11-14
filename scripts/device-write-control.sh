#!/usr/bin/env bash
# Explicit, reversible operator control for hardware configuration writes.
#
# sudo bash scripts/device-write-control.sh status
# sudo bash scripts/device-write-control.sh enable --ack-lab-writes
# sudo bash scripts/device-write-control.sh disable
#
# Enables the existing engine; does NOT certify a model, approve a plan, turn on
# automatic optimization, bypass the device allowlist, or contact any hardware.
# Never invoke the development pull/update script after enabling: that script
# deliberately resets both flags to read-only.
set -Eeuo pipefail
umask 077

source "$(dirname "$0")/../deployment/centos9/common.sh"
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed: $ENV_FILE" 2
[[ -f "$ROOT/compose.yaml" ]] || die "Compose file not found" 2

ACTION="${1:-}"
ACK="${2:-}"
case "$ACTION" in
  status)
    echo "Installation mode: $(env_get INM_MODE)"
    echo "Profile: $(env_get INM_PROFILE)"
    echo "Hardware write lock: $(env_get INM_DEVICE_WRITE_LOCK)"
    echo "Manual changes permitted: $(env_get INM_ALLOW_CONFIG_CHANGES)"
    echo "Automatic optimization: $(env_get INM_AUTOMATIC_OPTIMIZATION)"
    echo "Emulated certification does not confer physical-device certification."
    exit 0 ;;
  enable)
    [[ "$ACK" == "--ack-lab-writes" && $# -eq 2 ]] ||
      die "Usage: sudo bash scripts/device-write-control.sh enable --ack-lab-writes (LAB hardware only)" 2
    [[ "$(env_get INM_MODE)" == live && "$(env_get INM_PROFILE)" == lab-readonly ]] ||
      die "Only an existing live/lab-readonly installation may enable hardware writes. Migrate demo to live securely first." 2
    [[ "$(env_get INM_DEMO_SIMPLE_LOGIN)" != true ]] ||
      die "Weak demo-login mode cannot enable hardware writes." 2
    [[ "$(env_get SIMS_ADMIN_PASSWORD)" != demo123! ]] ||
      die "Demo password prohibited for live hardware writes." 2
    # Prevent accidentally enabling writes on an unscoped or public-management
    # installation. Target authorization is ALSO enforced inside writer_for().
    python3 - "$ENV_FILE" <<'PY'
import ipaddress, sys
items = {}
for line in open(sys.argv[1], encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.rstrip("\n").split("=", 1)
        items[key.strip()] = value.strip()
ranges = [s.strip() for s in items.get("INM_READONLY_DEVICE_CIDRS", "").split(",") if s.strip()]
if not ranges or len(ranges) > 20:
    raise SystemExit("No approved, narrowly scoped management target ranges configured.")
for text in ranges:
    try:
        cidr = ipaddress.ip_network(text, strict=False)
        if not isinstance(cidr, ipaddress.IPv4Network) or not cidr.is_private or cidr.prefixlen < 24:
            raise ValueError("must be a private IPv4 /24 or narrower")
    except ValueError as exc:
        raise SystemExit(f"Invalid management allowlist entry: {text!r} ({exc})") from exc
PY
    ;;
  disable)
    [[ $# -eq 1 ]] || die "Usage: sudo bash scripts/device-write-control.sh disable" 2 ;;
  *) die "Usage: sudo bash scripts/device-write-control.sh {status|enable --ack-lab-writes|disable}" 2 ;;
esac

command -v docker >/dev/null || die "Docker unavailable" 2
docker compose version >/dev/null || die "Docker Compose unavailable" 2

# Preserve the original root-owned, secret-bearing env file. No secrets are
# printed or copied to a user-readable path.
OLD_ENV="$(mktemp "$ETC/.write-control-rollback.XXXXXXXX")"
cp -p "$ENV_FILE" "$OLD_ENV"
chmod 0600 "$OLD_ENV"
COMMITTED=0
restore() {
  if [[ $COMMITTED -eq 0 ]]; then
    cp -p "$OLD_ENV" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    compose up -d --no-deps --force-recreate --wait --wait-timeout 180 api worker >/dev/null 2>&1 || true
    echo "[inm] Write-control change failed; previous settings restored." >&2
  fi
  rm -f "$OLD_ENV"
}
trap restore EXIT

start_transcript "device-write-$ACTION"
if [[ "$ACTION" == enable ]]; then
  set_env_value INM_DEVICE_WRITE_LOCK false
  set_env_value INM_ALLOW_CONFIG_CHANGES true
  # Automatic optimization remains explicitly disabled. Approval, capability
  # certification and per-device change plans are still enforced by backend.
  set_env_value INM_AUTOMATIC_OPTIMIZATION false
else
  set_env_value INM_DEVICE_WRITE_LOCK true
  set_env_value INM_ALLOW_CONFIG_CHANGES false
  set_env_value INM_AUTOMATIC_OPTIMIZATION false
fi
chmod 0600 "$ENV_FILE"
compose config --quiet
log "Restarting API and worker to load write-control flags; existing databases and volumes preserved"
compose up -d --no-deps --force-recreate --wait --wait-timeout 180 api worker

if [[ "$ACTION" == enable ]]; then
  compose exec -T api python - <<'PY'
from backend.config import get_settings
s = get_settings()
assert s.mode == "live" and s.inm_profile == "lab-readonly"
assert s.allow_config_changes is True and s.device_write_lock is False
assert s.device_writes_permitted is True
assert s.require_independent_approval is True
assert s.demo_simple_login is False
print("PASS: write engine permitted; independent approval enforced; demo login disabled")
PY
else
  compose exec -T api python - <<'PY'
from backend.config import get_settings
s = get_settings()
assert s.device_write_lock is True and s.allow_config_changes is False
assert s.device_writes_permitted is False
print("PASS: hardware writes locked")
PY
fi

COMMITTED=1
if [[ "$ACTION" == enable ]]; then
  log "Write engine enabled for explicitly authorized LAB certification and approved, certified operations."
  log "UNCERTIFIED changes will continue to be DENIED. No hardware operation was run."
  log "Open Configuration > Hardware certification; use a designated spare device and independent approver."
  log "Re-lock with: sudo bash scripts/device-write-control.sh disable"
else
  log "Hardware configuration writes re-locked; automatic optimization disabled."
fi
