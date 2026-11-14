# Simulated-hardware end-to-end harness (not collected by pytest)

Real services + simulated Aruba Instant clusters on real sockets, driven through the browser with Playwright.
Paths are those of the build sandbox (`/tmp/pg`, `/tmp/e2e`, `/home/claude/...`); adjust before reuse.

* PostgreSQL 16 — management DB (Alembic migrations run on start). Telemetry uses SQLite here because TimescaleDB
  was not available; production uses TimescaleDB.
* Grafana Loki 3.5.5 + Alloy 1.11.0 with `configs/` from this repo; syslog sent as RFC 3164 on UDP 1515.
* `fake_instant.py` — cluster A: Virtual Controller with Instant REST on 172.17.0.1:4343 (3 APs);
  cluster B: REST disabled (HTML only) and an Instant-like SSH CLI with `--More--` paging on 172.18.0.1:22 (2 APs).
* `seed.py` — imports 6 APs (MAC-named, unlinked, as in production) + 2 switches and creates operator/approver users.
* `e2e.py` — 21 browser steps; `e2e_keychange.py` — rotated SSH host key is refused, flagged and re-approved.
* `api.sh` — API in `live` mode, standalone role, write lock ON, TLS verification off for the simulator only.

Start order: Postgres → Loki → Alloy → fake_instant.py → api.sh → `npx vite` → seed.py → e2e.py.
