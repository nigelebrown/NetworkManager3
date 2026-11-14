export SIMS_JWT_SECRET=tttttttttttttttttttttttttttttttttttttttttttttttt
export SIMS_FERNET_KEY=$(cat /tmp/e2e/fernet)
export SIMS_ADMIN_PASSWORD=correct-horse-battery
export SIMS_DATABASE_URL="postgresql+psycopg://inm@/inm?host=/tmp/pg&port=5433"
export INM_TELEMETRY_DATABASE_URL=sqlite:////tmp/e2e/ts.db
export INM_LOKI_URL=http://127.0.0.1:3100
export SIMS_MODE=live SIMS_INM_PROFILE=standalone SIMS_ROLE=standalone SIMS_DEVICE_WRITE_LOCK=true
export SIMS_ARUBA_VERIFY_TLS=false SIMS_POLL_INTERVAL_SECONDS=60
export INM_READONLY_DEVICE_CIDRS=172.17.0.0/24,172.18.0.0/24
cd /home/claude/src/NetworkManager3-main
exec /home/claude/venv/bin/python -m uvicorn backend.main:create_app --factory --port 8000
