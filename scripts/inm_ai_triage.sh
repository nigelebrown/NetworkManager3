#!/usr/bin/env bash
# Read-only diagnostic for Docker INM AI service health failure.
# Prints AI and Docker status (NO secrets, no environment variables).
# Usage: sudo bash scripts/inm_ai_triage.sh
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root/sudo.' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Docker missing.' >&2; exit 2; }
echo '========== INM AI STARTUP DIAGNOSTICS =========='
date -u +'%Y-%m-%dT%H:%M:%SZ'
echo
echo '=== Docker service status ==='
docker ps -a --filter label=com.docker.compose.project=inm \
  --format 'NAME={{.Names}} | SERVICE={{.Label "com.docker.compose.service"}} | STATUS={{.Status}}' || true
echo
echo '=== AI status, health and OOM flag (redacted metadata only) ==='
if ! docker container inspect inm-ai-1 >/dev/null 2>&1; then
  echo 'Container inm-ai-1 not found. Was Compose deployed with a different project name?'
  exit 2
fi
docker inspect inm-ai-1 --format 'State={{.State.Status}} | ExitCode={{.State.ExitCode}} | OOMKilled={{.State.OOMKilled}} | RestartCount={{.RestartCount}} | Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
echo
echo '=== AI application logs (last 100 lines) ==='
docker logs --tail 100 inm-ai-1 2>&1 || true
echo
echo '=== AI healthcheck results (last 3 attempts) ==='
docker inspect inm-ai-1 --format '{{if .State.Health}}{{range .State.Health.Log}}{{.Start}} ExitCode={{.ExitCode}} Output={{.Output}}{{println}}{{end}}{{else}}No Docker healthcheck history available{{end}}' 2>/dev/null | tail -n 18 || true
echo
echo '=== Host memory and disk ==='
free -h || true
df -h /var/lib/docker /opt /etc/inm 2>/dev/null || true
echo
echo '=== Service process and direct health endpoint (only if running) ==='
if [[ "$(docker inspect inm-ai-1 -f '{{.State.Running}}' 2>/dev/null || true)" == true ]]; then
  docker exec inm-ai-1 python -I - <<'PY' || true
import urllib.request
try:
    r=urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=6)
    print('Direct local AI health endpoint: HTTP',r.status)
except Exception as exc:
    print('Direct local AI health endpoint:',type(exc).__name__,str(exc)[:300])
PY
fi
echo
echo '=== End of diagnostics ==='
echo 'The script made no configuration changes, no container restarts, and no Docker volume changes.'
