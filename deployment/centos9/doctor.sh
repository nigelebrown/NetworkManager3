#!/usr/bin/env bash
# Preflight report: OS, CPU, RAM, disk, time sync, SELinux, Docker/Compose, ports, registries, secrets, version.
#   sudo bash deployment/centos9/doctor.sh            report (exit 1 if a hard requirement fails)
# Read-only: changes nothing.
source "$(dirname "$0")/common.sh"

FAIL=0
ok()   { printf '  [ OK ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
note() { printf '  [WARN] %s\n' "$*"; }

preflight() {
  echo "INM preflight — version $(release_version), commit $(git_commit)"
  # shellcheck disable=SC1091
  source /etc/os-release 2>/dev/null || true
  if [[ "${ID:-}" == centos && "${VERSION_ID:-}" == 9* ]]; then ok "OS: ${PRETTY_NAME}"
  elif [[ "${INM_ALLOW_UNSUPPORTED_OS:-}" == 1 ]]; then note "OS: ${PRETTY_NAME:-unknown} (unsupported; INM_ALLOW_UNSUPPORTED_OS=1 is for developer test rigs only)"
  else bad "OS: ${PRETTY_NAME:-unknown} — CentOS Stream 9 required"; fi

  local cpu mem_gb disk_gb
  cpu=$(nproc); mem_gb=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024 / 1024 ))
  local droot; droot="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null | head -1 || true)"
  [[ -n "$droot" && -d "$droot" ]] || droot=/var/lib
  local kb; kb="$(df -Pk "$droot" 2>/dev/null | awk 'NR==2{print $4}')"
  disk_gb=$(( ${kb:-0} / 1024 / 1024 ))
  if (( cpu >= 16 && mem_gb >= 60 )); then ok "CPU/RAM: ${cpu} vCPU, ${mem_gb} GB (meets the recommended 16 vCPU / 64 GB)"
  elif (( cpu >= 8 && mem_gb >= 30 )); then note "CPU/RAM: ${cpu} vCPU, ${mem_gb} GB (meets the restricted-pilot minimum 8 vCPU / 32 GB, below recommended)"
  elif (( cpu >= 4 && mem_gb >= 7 )); then note "CPU/RAM: ${cpu} vCPU, ${mem_gb} GB (below pilot minimum: acceptable for a DEMO evaluation only)"
  else bad "CPU/RAM: ${cpu} vCPU, ${mem_gb} GB — at least 4 vCPU and 8 GB are needed even for DEMO"; fi
  if (( disk_gb >= 300 )); then ok "Docker data disk free: ${disk_gb} GB"
  elif (( disk_gb >= 40 )); then note "Docker data disk free: ${disk_gb} GB (pilot sizing calls for 300-500 GB SSD)"
  else bad "Docker data disk free: ${disk_gb} GB — need at least 40 GB"; fi

  if command -v chronyc >/dev/null && chronyc tracking 2>/dev/null | grep -q 'Leap status *: Normal'; then ok "time sync: chrony synchronised"
  else note "time sync: chrony not confirmed synchronised (audit and log correlation depend on accurate UTC)"; fi
  if command -v getenforce >/dev/null; then ok "SELinux: $(getenforce) (volumes use :Z labels; nothing here disables SELinux)"; fi
  if systemctl is-active -q firewalld 2>/dev/null; then ok "firewalld active (INM opens no firewall ports; web/syslog bind ${INM_WEB_BIND:-127.0.0.1} by default)"; fi

  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    ok "Docker $(docker version -f '{{.Server.Version}}' 2>/dev/null || echo '?'), $(docker compose version --short 2>/dev/null)"
  else note "Docker/Compose not installed yet (install.sh installs them from download.docker.com)"; fi

  local web_port syslog_port
  web_port="${INM_WEB_PORT:-$( [[ -f "$ENV_FILE" ]] && env_get INM_WEB_PORT || echo 8443)}"
  syslog_port="${INM_SYSLOG_PORT:-$( [[ -f "$ENV_FILE" ]] && env_get INM_SYSLOG_PORT || echo 1514)}"
  for p in "$web_port" "$syslog_port"; do
    local o; o="$(port_owner "$p")"
    if [[ -z "$o" ]]; then ok "port $p free"
    elif docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "^${PROJECT}-.*:$p->"; then ok "port $p held by this INM installation"
    else bad "port $p in use by: $o"; fi
  done

  if curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://registry-1.docker.io/v2/ | grep -qE '^(200|401)$'; then ok "Docker Hub registry reachable"
  else note "Docker Hub not reachable: images must be pre-loaded (docker load) before install"; fi

  if [[ -f "$ENV_FILE" ]]; then
    local mode; mode=$(stat -c %a "$ENV_FILE")
    [[ "$mode" == 600 ]] && ok "secrets file $ENV_FILE mode 0600" || bad "secrets file $ENV_FILE has mode $mode (must be 0600)"
  else ok "no existing installation secrets (fresh install)"; fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  require_root
  preflight
  exit $FAIL
fi
