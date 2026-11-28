# NetworkManager3 1.3.0-rc16 — test results

Environment: isolated Linux workspace, Python 3.13.18, Node 22.23.5 / npm 10.9.9, Google Chrome + Playwright 1.63.0,
PostgreSQL 16.15 (no TimescaleDB), no Docker/NATS/Loki, no physical devices, no access to the live server.
Date: 25 November 2026. These are this workspace's observed results; CI on the exact Git SHA must be re-run after commit.

## Results

| Suite | Command | Baseline rc15 (this workspace) | rc16 |
|---|---|---|---|
| Unit + contract | `SIMS_DISABLE_SCHEDULER=true python -m pytest -q tests/unit tests/contract` | 655 passed (577.8 s) | **667 passed, 0 failed** (638.6 s) |
| Original Wi-Fi regression | `SIMS_DISABLE_SCHEDULER=true python -m pytest -q backend/tests` | 181 passed | **181 passed, 0 failed** |
| Python compile | `python -m compileall -q backend workers ai_service tests scripts` | — | rc 0 |
| Frontend build | `cd frontend && npm ci && npm run build` | passed | **passed** (existing > 500 kB chunk advisory); CI grep for `data-inm-approval-action` passes |
| Browser (Chrome) — rc16 workflows | see below | — | **3 passed** (import mapping/partial/export; port groups multi-select/history/viewer/delete; SNMPv3 profile). Zero page errors, zero HTTP 5xx. Screenshots in `evidence/e2e/ops-rc16/` |
| PostgreSQL migration + restore | `python scripts/validate_pg_migration_restore.py --admin-url postgresql+psycopg://postgres@127.0.0.1:55432/postgres` | — | **3/3 PASS** (`evidence/migration/rc16-pg16.txt`) |
| Mixed load (harness) | see below | — | **PASS** against provisional targets (`evidence/load/`) |
| Python dependency audit | `pip-audit -r requirements.lock` | 6 findings (pyasn1 ×4, paramiko ×2) | paramiko only — accepted risk (`evidence/security/rc16-pip-audit.txt`) |
| Frontend dependency audit | `cd frontend && npm audit` | 4 (vite/esbuild dev-only; react-router) | unchanged — risk assessed (`evidence/security/rc16-npm-audit.txt`) |

New automated tests in rc16 (12): `tests/contract/test_port_monitoring_groups.py` (3), `tests/unit/test_alert_hysteresis.py` (4),
`tests/unit/test_printer_srs_units.py` (+3), `tests/contract/test_printer_intelligence.py` (+1; 3 parametrised
cases updated for the additive v3 fields), `tests/contract/test_bulk_device_import.py` (+1).

Not run here (need the full stack or hardware): `tests/e2e/test_gui_workflow.py`, `test_live_operations.py`,
`test_demo_gui.py`, `test_printer_gui.py`, `test_intelligence_gui.py`, `tests/fullstack/`, `tests/integration/`.

## Browser run

```bash
(cd frontend && npm ci && npm run build)
python -I tests/e2e/ops_stack.py /tmp/ops-e2e 8830 &          # demo data + CX emulator, SQLite; never contacts a network
INM_E2E_URL=http://127.0.0.1:8830 python -m pytest -q tests/e2e/test_ops_workflows_gui.py
```

Note: the login limiter is 8 attempts/min/IP. Restart the stack before re-running the suite repeatedly.

## Capacity harness

Provisional targets (to be confirmed by the owner, A-OPS-03): 25 concurrent NOC/engineering users with ~2 s think time;
API p95 < 1 s; zero 5xx/transport errors; worker keeps up with configured polling; no API blocking by background jobs.

```bash
createdb inm_load      # disposable PostgreSQL 16
python scripts/load_test.py serve  --workdir /tmp/inm-load --port 8840 --mgmt-url postgresql+psycopg://postgres@127.0.0.1:55432/inm_load &
python scripts/load_test.py worker --workdir /tmp/inm-load --mgmt-url postgresql+psycopg://postgres@127.0.0.1:55432/inm_load &
python scripts/load_test.py load   --url http://127.0.0.1:8840 --users 25 --seconds 120 --think 2 --out evidence/load/result.json
```

Scenario: one API process (as the production image), one separate worker process, PostgreSQL management DB, SQLite
telemetry; 600 manual inventory devices, demo dataset with 4 CX-emulator switch plans (polled continuously), 500
emulated printers at a 60 s interval (15× more frequent than the 900 s default), 25 users (viewer/operator mix) over
ten dashboard/inventory/monitoring endpoints.

| Measure | Before printer-list fix | After (rc16) |
|---|---|---|
| Requests in 120 s | 1,191 | 1,445 |
| Throughput | 9.6 req/s | 11.8 req/s |
| Errors (5xx/transport) | 0 | 0 |
| Latency p50 / p95 / p99 / max | 81 / 4,446 / 6,971 / 8,146 ms | **24 / 226 / 562 / 963 ms** |
| `/api/printers` p95 (500 rows) | 7,264 ms | 390 ms |
| `/api/v1/noc/overview` p95 | 896 ms | 262 ms |
| API RSS peak | 251 MB | 213 MB |
| PostgreSQL connections | 7 | 7 |
| Worker: switch cycle (4 plans) median / max | — | 0.01 / 0.47 s |
| Worker: printer throughput while polling | — | ≈ 26 polls/s (1,696 polls in 65.5 s busy time), 0 worker errors |
| Management DB growth | — | ≈ 12.4 KB per printer poll (58,004 samples, 22.3 MB incl. indexes, ≈ 1,797 polls) |

`/api/printers/dashboard` returned 403 to viewer/operator users by design (admin-only); counted separately, not as errors.

### What this does and does not show

- Shows: the API stays responsive while the worker polls; the N+1 query defect is fixed; no request errors at the
  provisional user load; storage growth per printer poll.
- Does **not** show: TimescaleDB ingestion rates, NATS/Loki overhead, real SNMP/REST round-trip times and timeouts,
  network loss, multi-day behaviour, or the target VM's CPU/memory. Emulated printers answer in-process, so real
  polling throughput will be lower and dominated by device latency and timeouts. Re-run ENV-02 on the target VM.
- No performance guarantee is made from these figures.
