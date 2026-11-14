#!/usr/bin/env bash
# Targeted, read-only lab acceptance of Aruba Instant AP-505/AP-535 and
# AOS-CX 6100/6200 through INM's installed device libraries.
# Usage: sudo bash inm_hardware_readonly_test.sh ap-505 10.103.1.10 admin
#        sudo bash inm_hardware_readonly_test.sh cx-6200 10.103.1.20 admin
# No VLAN/SSID/port/PoE/RF setting is changed; does not certify writes.
set -Eeuo pipefail
umask 077
MODEL="${1:-}"; IP="${2:-}"; USERNAME="${3:-}"
[[ $# -eq 3 && -n "$USERNAME" ]] || {
  echo 'Usage: sudo bash inm_hardware_readonly_test.sh {ap-505|ap-535|cx-6100|cx-6200} PRIVATE-IP USERNAME' >&2; exit 2;
}
case "$MODEL" in ap-505|ap-535|cx-6100|cx-6200) ;; *) echo 'Unsupported model selector' >&2; exit 2;; esac
[[ $EUID -eq 0 ]] || { echo 'Run with sudo/root.' >&2; exit 2; }
ROOT="${INM_ROOT:-/root/NetworkManager3}"
ENV_FILE="${INM_ETC_DIR:-/etc/inm}/inm.env"
[[ -f "$ENV_FILE" && -f "$ROOT/compose.yaml" ]] || {
  echo 'An installed Git-managed INM instance is required.' >&2; exit 2;
}
cd "$ROOT"
getenv() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
[[ "$(getenv INM_MODE)" == live && "$(getenv INM_PROFILE)" == lab-readonly ]] || {
  echo 'Hardware acceptance requires INM_MODE=live and INM_PROFILE=lab-readonly.' >&2; exit 2;
}
[[ "$(getenv INM_DEVICE_WRITE_LOCK)" == true && "$(getenv INM_ALLOW_CONFIG_CHANGES)" != true &&
   "$(getenv INM_AUTOMATIC_OPTIMIZATION)" != true ]] || {
  echo 'Refusing hardware tests while any configuration write or autonomous optimization gate is open.' >&2; exit 2;
}
[[ "$(getenv INM_ARUBA_VERIFY_TLS)" == true ]] || {
  echo 'Set INM_ARUBA_VERIFY_TLS=true. Use only an explicitly scoped lab-device exception for untrusted certificates.' >&2; exit 2;
}
command -v docker >/dev/null || { echo 'Docker not installed' >&2; exit 2; }
compose() { docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" --env-file "$ENV_FILE" "$@"; }
[[ -n "$(compose ps -q worker)" ]] || { echo 'INM worker container is not running.' >&2; exit 2; }
LOG_DIR="${INM_LOG_DIR:-/var/log/inm}"
install -d -m 0700 "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/hardware-readonly-$MODEL-$STAMP.log"
printf 'Read-only hardware test: %s at %s\n' "$MODEL" "$IP"
# Read the password from a TTY, not from shell history, environment variables,
# process arguments or a file. Only a single stdin line is sent to Python.
[[ -r /dev/tty ]] || { echo 'Interactive terminal required to enter device password.' >&2; exit 2; }
read -r -s -p "Device password for $USERNAME: " PASSWORD </dev/tty
printf '\n'
PY_CODE="$(cat <<'PY'
from __future__ import annotations
import ipaddress
import os
import socket
import sys
from backend.config import get_settings

model, host, username = sys.argv[1:4]
password = sys.stdin.readline().rstrip('\r\n')
failures = 0
warnings = 0

def step(label, fn, required=True):
    global failures, warnings
    try:
        value = fn()
        if value is False or value is None:
            raise ValueError('No usable result')
        print('[PASS]', label, flush=True)
        return value
    except Exception as exc:
        # Never print URLs, credentials, sessions, raw vendor errors, or config.
        print('[FAIL]' if required else '[GAP]', label, type(exc).__name__, flush=True)
        if required: failures += 1
        else: warnings += 1
        return None

settings = get_settings()
assert settings.device_writes_permitted is False, 'Hardware write gate must remain closed'
assert settings.device_write_lock is True and settings.allow_config_changes is False
assert settings.aruba_verify_tls is True, 'Global TLS verification must be enabled'
addr = ipaddress.ip_address(host)
if not isinstance(addr, ipaddress.IPv4Address) or not addr.is_private or addr.is_loopback or addr.is_link_local:
    raise SystemExit('Only private, non-loopback management IPv4 is permitted')
raw_ranges = os.environ.get('INM_READONLY_DEVICE_CIDRS','').strip()
try:
    ranges = [ipaddress.ip_network(x.strip(), strict=False) for x in raw_ranges.split(',') if x.strip()]
    if not ranges or len(ranges)>20 or any(
        not isinstance(net,ipaddress.IPv4Network) or net.prefixlen<24 or not net.is_private
        for net in ranges):
        raise ValueError('Bad allowlist')
except ValueError:
    raise SystemExit('Set a narrow, private /24-/32 INM_READONLY_DEVICE_CIDRS allowlist in the installed INM env')
if not any(addr in net for net in ranges):
    raise SystemExit('Target IP is not in the running worker approved-device allowlist')
if not password:
    raise SystemExit('Empty device password')
verify = settings.tls_verify_for(host)
print('Transport TLS:', 'SCOPED LAB EXCEPTION' if verify is False else 'VERIFIED', flush=True)
port = 4343 if model.startswith('ap-') else 443

def connect():
    with socket.create_connection((host, port), timeout=7):
        return True
if not step(f'TCP connection to management service port {port}', connect):
    raise SystemExit(1)

if model.startswith('ap-'):
    from backend.aruba.client import ArubaClient, ArubaTarget
    from backend.aruba import parsers, configuration, telemetry
    from backend.aruba.errors import ArubaError
    with ArubaClient(ArubaTarget(host,port,username,password),timeout=12,verify=verify) as client:
        if not step('Instant REST login',lambda: (client.login(),True)[1]):
            raise SystemExit(1)
        def get_aps():
            res=client.show('show aps')
            aps=parsers.normalize_aps(parsers.extract_rows(res,'show aps'))
            if not aps: return None
            print('AP records parsed:',len(aps),flush=True)
            return aps
        aps=step('AP inventory read and parser',get_aps)
        if aps:
            expected=model.split('-')[1]
            recognized=any(expected in str(d.get('model','')) for d in aps)
            step('Requested AP model represented in cluster inventory',lambda: recognized)
        def get_version():
            version=parsers.parse_version(client.show('show version'))
            if version: print('Firmware:',version,flush=True)
            return version
        step('Firmware version read',get_version)
        def get_clients():
            rows=parsers.normalize_clients(parsers.extract_rows(client.show('show clients'),'show clients'))
            print('Connected clients counted:',len(rows),flush=True)
            return True  # Zero clients is valid
        step('Connected-client inventory read',get_clients)
        def config_read():
            data=parsers.output_text(client.show('show running-config'))
            if data is None: return None
            # Configuration is processed only in memory; it is never written to logs.
            parsed=configuration.parse_running_config(data)
            print('SSID profiles parsed:',len(parsed.get('ssids',[])),flush=True)
            return True
        step('Running configuration read and SSID/VLAN parser',config_read)
        def rf_summary():
            result=telemetry.parse_rf_summary(parsers.output_text(client.show('show ap arm rf-summary')))
            if result: print('RF radio records:',len(result.get('radios',[])),flush=True)
            return result
        step('ARM RF/channel utilization and transmit power',rf_summary)
        def radio_stats():
            result=telemetry.parse_radio_stats(parsers.output_text(client.show('show ap debug radio-stats')))
            if result: print('Radio counter/gauge entries:',len(result),flush=True)
            return result
        step('Radio utilization/CCA/retry diagnostics',radio_stats)
        def sys_stats():
            result=telemetry.parse_system_status(parsers.output_text(client.show('show ap debug system-status')))
            return result
        step('CPU/memory/uptime diagnostics',sys_stats,required=False)
else:
    from backend.infrastructure.writers.aruba_cx import CxSession
    try:
        with CxSession(host,username,password,timeout=12,verify=verify) as cx:
            step('AOS-CX REST login and API version negotiation',lambda: True)
            def read_json(path,params=None):
                response=cx.request('GET',path,params=params or {'depth':1})
                result=response.json()
                if not isinstance(result,(dict,list)): return None
                return result
            system=step('Switch system/model/firmware read',lambda: read_json('/system',{'attributes':'hostname,platform_name,software_version,serial_number'}))
            if system:
                print('Reported platform:',str(system.get('platform_name','unknown'))[:85],flush=True)
                print('Reported firmware:',str(system.get('software_version','unknown'))[:85],flush=True)
            vlans=step('VLAN inventory read',lambda: read_json('/system/vlans'))
            if vlans is not None: print('VLAN records:',len(vlans),flush=True)
            interfaces=step('Physical/LAG interface inventory read',lambda: read_json('/system/interfaces'))
            if interfaces is not None: print('Interface records:',len(interfaces),flush=True)
            def read_config():
                response=cx.request('GET','/fullconfigs/running-config')
                return response.status_code == 200 and len(response.content)>0
            step('Full running-configuration backup endpoint (read only)',read_config)
    except Exception as exc:
        print('[FAIL] AOS-CX session/transport:',type(exc).__name__,flush=True)
        failures+=1
print('HARDWARE READ-ONLY SUMMARY: required failures=',failures,'optional gaps=',warnings,flush=True)
print('No hardware configuration writes, recovery drills or certification performed.',flush=True)
raise SystemExit(1 if failures else 0)
PY
)"
set +e
printf '%s\n' "$PASSWORD" | compose exec -T worker python -c "$PY_CODE" "$MODEL" "$IP" "$USERNAME" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[1]}
set -e
unset PASSWORD PY_CODE
printf 'Read-only device log: %s\n' "$LOG"
exit "$RC"