#!/usr/bin/env bash
# One-command status: containers, health, version, last receipts.  sudo bash deployment/centos9/status.sh
source "$(dirname "$0")/common.sh"
require_root
[[ -f "$ENV_FILE" ]] || die "INM is not installed" 2
echo "INM $(release_version) ($(git_commit)) — profile $(env_get INM_PROFILE)"
compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'
echo
compose exec -T api python -c "
from backend.application.health import collect
from backend.infrastructure import probes
from backend.database.session import init_engine
init_engine(); r = collect(probes.all_probes()); print('overall', r['overall'])
for s in r['services']: print(f\"  {s['status']:<9} {s['role']}: {s['detail']}\")" || warn "API not reachable"
echo; echo "last receipts:"; tail -n 5 "$STATE/receipts.jsonl" 2>/dev/null || echo "  none"
