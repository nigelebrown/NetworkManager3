#!/usr/bin/env bash
# Single-command INM test release update (CentOS Stream 9 / Docker Compose).
# Preserve Docker volumes, /etc/inm secrets, host IP, SSH and old Nginx 8443.
# If reverse proxy is already in place, reverify it; otherwise install it.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo '[INM] Run as root/sudo.' >&2; exit 2; }
ROOT=/opt/NetworkManager3
SITE=/etc/nginx/conf.d/inm-networkmanager-8787.conf
[[ -d "$ROOT/.git" && -f "$ROOT/compose.yaml" && -f /etc/inm/inm.env ]] || {
  echo '[INM] Existing Git-managed INM Docker installation missing; use the fresh one-click installer.' >&2; exit 2;
}
command -v docker >/dev/null || { echo '[INM] Docker is missing.' >&2; exit 2; }
docker compose version >/dev/null || { echo '[INM] Docker Compose is missing.' >&2; exit 2; }
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] ||
  { echo '[INM] Local source modifications found. Refusing to overwrite them.' >&2; exit 2; }
[[ "$(git -C "$ROOT" branch --show-current)" == main ]] ||
  { echo '[INM] Source checkout must be on main.' >&2; exit 2; }
echo '[INM] Fetching the latest verified main (fast-forward only).'
git -C "$ROOT" fetch origin main
git -C "$ROOT" merge --ff-only origin/main

if [[ -e "$SITE" ]]; then
  [[ -f "$SITE" && ! -L "$SITE" ]] ||
    { echo '[INM] Unexpected proxy configuration path; refusing change.' >&2; exit 2; }
  grep -q '^# INM managed reverse proxy' "$SITE" ||
    { echo '[INM] Existing 8787 proxy is not managed by INM; refusing replacement.' >&2; exit 2; }
  echo '[INM] Existing Nginx reverse proxy found; updating Docker only.'
  bash "$ROOT/deployment/centos9/install.sh" --mode live
  nginx -t
  systemctl reload nginx
else
  echo '[INM] First-time 8787 Nginx reverse proxy setup.'
  bash "$ROOT/scripts/inm_install_proxy_8787.sh"
fi

echo '[INM] Final verification of Docker services, write lock, egress policy and synthetic pipeline.'
bash "$ROOT/deployment/centos9/verify.sh"
echo '[INM] Checking the GUI on the server management IP.'
curl -kfsS --max-time 20 https://172.17.71.20:8787/healthz >/dev/null ||
  { echo '[INM] Application may be healthy but reverse proxy is not reachable at 172.17.71.20:8787.' >&2; exit 1; }
echo '[INM] PASS: https://172.17.71.20:8787/'
echo '[INM] Open Device inventory -> assign an AP-Standard profile -> Test connection.'
echo '[INM] Admin-confirmed exact-IP /32 approvals are reconciled by the host timer; device writes remain locked.'
