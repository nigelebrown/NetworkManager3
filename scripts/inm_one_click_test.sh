#!/usr/bin/env bash
# Intelligent Network Manager: one-command CentOS software + controlled Aruba hardware checks.
# Git checkout and running containers must already be installed; no deployment, migration,
# hardware configuration write, or global TLS bypass is performed by this runner.
#
#   sudo bash scripts/inm_one_click_test.sh
#   sudo bash scripts/inm_one_click_test.sh --software-only
#   sudo bash scripts/inm_one_click_test.sh --hardware-only
#   sudo bash scripts/inm_one_click_test.sh --with-regressions  # prepared TEST VM only
#
# First hardware run prompts once for device IPs/usernames and saves ONLY this inventory
# in /etc/inm/hardware-test-targets.tsv (0600). Passwords are prompted each run.
set -Eeuo pipefail
umask 077

ROOT="${INM_ROOT:-/root/NetworkManager3}"
TARGETS="${INM_HARDWARE_TARGETS:-/etc/inm/hardware-test-targets.tsv}"
LOG_DIR="${INM_LOG_DIR:-/var/log/inm}"
MODE=all
REGRESSIONS=0
while (($#)); do
  case "$1" in
    --software-only) MODE=software; shift ;;
    --hardware-only) MODE=hardware; shift ;;
    --with-regressions) REGRESSIONS=1; shift ;;
    --targets-file) [[ $# -ge 2 ]] || { echo 'Missing --targets-file value' >&2; exit 2; }; TARGETS="$2"; shift 2 ;;
    --help|-h)
      echo 'Usage: sudo bash scripts/inm_one_click_test.sh [--software-only|--hardware-only] [--with-regressions] [--targets-file ROOT_OWNED_TSV]'
      exit 0 ;;
    *) printf '[INM] Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo '[INM] Run with sudo/root.' >&2; exit 2; }
[[ -d "$ROOT/.git" && -f "$ROOT/compose.yaml" ]] || {
  echo "[INM] Git-managed INM installation not found: $ROOT" >&2; exit 2;
}
[[ -f /etc/inm/inm.env ]] || { echo '[INM] /etc/inm/inm.env not found.' >&2; exit 2; }
for script in inm_full_server_test.sh inm_hardware_readonly_test.sh; do
  [[ -f "$ROOT/scripts/$script" ]] || {
    echo "[INM] Missing scripts/$script. Pull the latest GitHub main first." >&2; exit 2;
  }
done
for tool in python3 git docker; do
  command -v "$tool" >/dev/null || { echo "[INM] Missing $tool" >&2; exit 2; }
done
install -d -m 0700 "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/one-click-acceptance-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
printf '\nINM ONE-CLICK ACCEPTANCE | %s | mode=%s | commit=%s\n' \
  "$STAMP" "$MODE" "$(git -C "$ROOT" rev-parse HEAD)"
echo '[INM] No config writes, no automatic RF changes and no Docker volume deletion.'

valid_private_ip() {
  python3 -I - "$1" <<'PY'
import ipaddress, sys
try:
    ip = ipaddress.IPv4Address(sys.argv[1])
    assert ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)
except (ValueError, AssertionError):
    sys.exit(1)
PY
}

# Inventory contains addresses and usernames only, NEVER credentials. No network scans.
create_targets_once() {
  [[ -f "$TARGETS" ]] && return 0
  if [[ ! -r /dev/tty ]]; then
    echo "[INM] Hardware inventory is missing: $TARGETS (interactive setup required)."
    return 1
  fi
  echo '[INM] First-run hardware inventory setup (press Enter to skip a model).'
  echo '[INM] Use only designated lab devices and their actual management IPv4 addresses.'
  local dir tmp model ip user count=0
  dir="$(dirname "$TARGETS")"
  [[ -d "$dir" && ! -L "$dir" ]] || { echo "[INM] Targets directory is missing or a symlink: $dir" >&2; return 1; }
  tmp="$(mktemp "$dir/.inm-hardware-targets.XXXXXXXX")"
  chmod 0600 "$tmp"
  for model in ap-505 ap-535 cx-6100 cx-6200; do
    while true; do
      printf '%s management IP (Enter to skip): ' "$model" >/dev/tty
      IFS= read -r ip </dev/tty || { rm -f "$tmp"; return 1; }
      [[ -z "$ip" ]] && break
      if valid_private_ip "$ip"; then break; fi
      echo '[INM] Valid private management IPv4 required.' >/dev/tty
    done
    [[ -z "$ip" ]] && continue
    printf '%s username [admin]: ' "$model" >/dev/tty
    IFS= read -r user </dev/tty || { rm -f "$tmp"; return 1; }
    user="${user:-admin}"
    if [[ ! "$user" =~ ^[a-zA-Z0-9_.@-]{1,64}$ ]]; then
      echo "[INM] Invalid device username for $model" >&2
      rm -f "$tmp"; return 1
    fi
    printf '%s\t%s\t%s\n' "$model" "$ip" "$user" >> "$tmp"
    count=$((count+1))
  done
  if ((count==0)); then rm -f "$tmp"; echo '[INM] No hardware targets supplied.'; return 1; fi
  if [[ -e "$TARGETS" || -L "$TARGETS" ]]; then
    echo '[INM] Inventory appeared during setup; refusing to overwrite it.' >&2
    rm -f "$tmp"; return 1
  fi
  mv -- "$tmp" "$TARGETS"
  chmod 0600 "$TARGETS"
  printf '[INM] Saved %d target(s) to %s (no passwords stored).\n' "$count" "$TARGETS"
  echo '[INM] Each device must already be explicitly permitted by INM_READONLY_DEVICE_CIDRS.'
}

SOFTWARE_RC=0
SOFTWARE_SKIPPED=0
HARDWARE_FAILED=0
HARDWARE_TOTAL=0
HARDWARE_SKIPPED=0
if [[ "$MODE" != hardware ]]; then
  echo; echo '========== INSTALLED SOFTWARE / PLATFORM =========='
  args=(--root "$ROOT")
  if ((REGRESSIONS)); then
    echo '[INM] Source regression flag was selected. Run only on a prepared TEST VM.'
    args+=(--with-regressions)
  fi
  bash "$ROOT/scripts/inm_full_server_test.sh" "${args[@]}" || SOFTWARE_RC=$?
  if ((SOFTWARE_RC)); then
    echo '[INM] Software verification failed; hardware tests will NOT run automatically.'
  fi
else
  SOFTWARE_SKIPPED=1
fi

if [[ "$MODE" != software && "$SOFTWARE_RC" == 0 ]]; then
  echo; echo '========== PHYSICAL ARUBA READ-ONLY TESTS =========='
  if create_targets_once; then
    [[ -f "$TARGETS" && ! -L "$TARGETS" ]] || { echo '[INM] Targets file invalid.' >&2; exit 2; }
    [[ "$(stat -c %u "$TARGETS")" == 0 ]] || { echo '[INM] Targets file must be owned by root.' >&2; exit 2; }
    [[ "$(stat -c %a "$TARGETS")" == 600 ]] || { echo '[INM] Targets file must have mode 0600.' >&2; exit 2; }
    while IFS=$'\t' read -r model ip username extra || [[ -n "${model:-}" ]]; do
      [[ -z "${model:-}" || "$model" == \#* ]] && continue
      case "$model" in ap-505|ap-535|cx-6100|cx-6200) ;; *) echo "[INM] Invalid model in targets: $model" >&2; HARDWARE_FAILED=$((HARDWARE_FAILED+1)); continue ;; esac
      if [[ -n "${extra:-}" || -z "${username:-}" || ! "$username" =~ ^[a-zA-Z0-9_.@-]{1,64}$ ]] || ! valid_private_ip "$ip"; then
        echo "[INM] Invalid target row for $model; not contacting device." >&2
        HARDWARE_FAILED=$((HARDWARE_FAILED+1)); continue
      fi
      HARDWARE_TOTAL=$((HARDWARE_TOTAL+1))
      echo; printf '[INM] Testing %s at %s (read-only)\n' "$model" "$ip"
      INM_ROOT="$ROOT" bash "$ROOT/scripts/inm_hardware_readonly_test.sh" "$model" "$ip" "$username" || HARDWARE_FAILED=$((HARDWARE_FAILED+1))
    done < "$TARGETS"
    if ((HARDWARE_TOTAL==0)); then HARDWARE_SKIPPED=1; fi
  else
    HARDWARE_SKIPPED=1
  fi
elif [[ "$MODE" != software ]]; then
  HARDWARE_SKIPPED=1
fi

echo; echo '========== FINAL SUMMARY =========='
if ((SOFTWARE_SKIPPED)); then echo 'Software: SKIPPED (--hardware-only)';
elif ((SOFTWARE_RC)); then echo 'Software: FAIL';
else echo 'Software: PASS'; fi
printf 'Hardware devices attempted: %d | failed: %d | skipped: %d\n' \
  "$HARDWARE_TOTAL" "$HARDWARE_FAILED" "$HARDWARE_SKIPPED"
printf 'Combined transcript: %s\n' "$LOG"
echo '[INM] Physical writes, rollback/restore drills, HA/DR, and release certification remain separate.'
if ((SOFTWARE_RC || HARDWARE_FAILED)); then echo 'RESULT: FAIL'; exit 1; fi
if [[ "$MODE" == hardware && "$HARDWARE_TOTAL" == 0 ]] || [[ "$MODE" == all && "$HARDWARE_TOTAL" == 0 ]]; then
  echo 'RESULT: HARDWARE NOT RUN (not full hardware acceptance)'
  exit 3
fi
echo 'RESULT: ALL EXECUTED CHECKS PASSED (not production certification)'
