pkill -f "uvicorn backend.main"; sleep 1
psql -q -h /tmp/pg -p 5433 -U inm -d postgres -c "drop database inm with (force);" -c "create database inm;"
rm -f /tmp/e2e/ts.db
(bash /tmp/e2e/api.sh > /tmp/e2e/api.log 2>&1 &)
for i in $(seq 1 30); do curl -s localhost:8000/api/health >/dev/null 2>&1 && break; sleep 1; done; sleep 3
/home/claude/venv/bin/python /home/claude/scripts/seed.py
rm -rf /tmp/e2e/shots; mkdir -p /tmp/e2e/shots
