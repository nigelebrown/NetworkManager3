# NetworkManager3 1.3.0-rc17 — developer test results (25 November 2026)

These tests ran in an isolated workspace:

- Debian container
- Python 3.13
- Node/Vite
- Chrome at `/usr/bin/google-chrome`
- PostgreSQL 16.15, without TimescaleDB

There was no Docker, no NATS/Loki stack, no physical device and no live server. "EMULATED" means the
repository's labelled emulators (CX, Instant, SNMP agent, probe), exercised through the same writer and collector code
paths used for hardware. **No result here certifies hardware or the server.**

## 1. Unit and contract suite (`tests/`)

```bash
source /home/claude/venv/bin/activate
python -m pytest -p no:warnings -q -rfEs tests
```

| Run | Started (UTC) | Result | Log |
|---|---|---|---|
| Final (all rc17 changes, docs and version bump included) | 2026-11-25 13:14 | **726 passed, 0 failed, 50 skipped** in 695 s | `evidence/regression/rc17-tests-final.log` |
| Earlier full run (same code, before the docs/version bump) | 2026-11-25 ≈12:31 | 725 passed, **1 failed**, 50 skipped | `evidence/regression/rc17-tests.log` |

The single failure in the earlier run was `tests/contract/test_network_intelligence.py::test_predictive_insufficient_experimental_and_validated_paths` (`sc["scored"] == 0`).

- It passed alone at 12:5x UTC.
- The whole file passed at 13:10 UTC (11/11).
- It passed in the final full run.

Neither the test nor the predictive code changed in rc17. The test's `NOW` is the wall clock at import time, so it is
probably time-dependent. It is recorded as **intermittent and pre-existing**; it needs a fixed clock (handover §4, item 13).

The 50 skips are suites that need their own environment:

- `tests/integration/*` (`INM_IT=1` and a native stack)
- `tests/e2e/*` (`INM_E2E_URL`); these were run separately in §3

Comparison with rc16 (667 passed): rc17 adds 53 new test cases and extends several existing ones.

| File | Cases |
|---|---|
| `test_rc17_instant_wlan_security.py` | 14 |
| `test_rc17_switch_ops.py` | 12 |
| `test_rc17_purchasing_print_jobs.py` | 8 |
| `test_rc17_probes_roaming.py` | 4 |
| `test_ap_reboot_orchestration.py` | 4 |
| `test_rc17_batch1.py` | 2 |
| `test_rc17_cx_firmware.py` | 2 |
| `test_rc17_active_diagnostics.py` | 2 |
| `test_rc17_snmp_adapters.py` | 2 |
| `test_rc17_typed_restore.py` | 2 |
| `test_release_signing.py` | 1 (7 refusal modes) |

Existing tests extended or updated:

- printer heartbeat storage
- demo refusal in live mode
- site batches
- feature-registry states
- lifecycle op sets
- schema 0015

## 2. Legacy backend suite

```bash
python -m pytest -p no:warnings -q backend/tests
```

Result: **181 passed** (`evidence/regression/rc17-legacy-backend.log`).

## 3. Browser suites (Chrome, isolated demo/emulated stacks)

```bash
python -I tests/e2e/ops_stack.py /tmp/inm-nav 8833 &
INM_E2E_URL=http://127.0.0.1:8833 INM_E2E_ADMIN_PASSWORD='Ops-E2E-Admin-2026!' python -m pytest -q tests/e2e/test_ops_workflows_gui.py
python tests/e2e/nav_audit.py http://127.0.0.1:8833 'Ops-E2E-Admin-2026!' evidence/e2e/nav-rc17
python -I tests/e2e/printer_stack.py /tmp/inm-prn 8834 &   ;  INM_E2E_URL=http://127.0.0.1:8834 python -m pytest -q tests/e2e/test_printer_gui.py
python -I tests/e2e/intelligence_stack.py /tmp/inm-int 8835 & ; INM_E2E_URL=http://127.0.0.1:8835 python -m pytest -q tests/e2e/test_intelligence_gui.py
```

| Suite | Result | Notes |
|---|---|---|
| Navigation audit (`nav_audit.py`) | **0 overlaps, 0 duplicate targets, 0 multi-active**. 74 entry visits (37 entries × 1440/1180 px) and 66 view tabs; mobile sidebar hidden | `evidence/e2e/nav-rc17/nav-audit.json` and screenshots |
| `test_ops_workflows_gui.py` | **3 passed** on a fresh stack | A first attempt on a stack already used by the audit failed at login: the login rate limiter (8/min) had been hit, so it was a harness issue, not a product one. |
| `test_printer_gui.py` | **9 passed** | The first run had 1 failure. It exposed a **pre-existing bug**: the discovery IP list was split on the characters `\`/`s`, so "a, b" sent " b" and got a 422. The bug was fixed, then 9/9 passed. |
| `test_intelligence_gui.py` | **8 passed** (twice) | One earlier attempt (2 failed, 1 error) started while the stack was still seeding. The two subsequent runs passed. |
| `test_gui_workflow.py`, `test_live_operations.py`, `test_demo_gui.py` | **NOT RUN** | They need the Docker full stack (`tests/fullstack/stack.sh`) or the CI TLS demo stack. |

## 4. Frontend

```bash
cd frontend && npm run build      # tsc -b && vite build
```

The build passes. Route-level code-splitting brought the main chunk from 1,311 KB to 214 KB (68.6 KB gzip) and the
build now emits 76 chunks (`evidence/regression/rc17-frontend-build.txt`).

## 5. PostgreSQL migration and restore drill

```bash
python scripts/validate_pg_migration_restore.py --admin-url postgresql+psycopg://postgres@127.0.0.1:55432/postgres \
  --pg-bin /usr/lib/postgresql/16/bin
```

| Check | Result |
|---|---|
| 0004 → 0015 | PASS: existing data preserved |
| 0013 → 0014 | PASS: certification evidence preserved |
| 0014 → 0015 | PASS: three tables created; downgrade refused |
| `pg_dump -Fc` / `pg_restore` of an application-seeded head DB | PASS: application boots, login works, records identical, audit chain valid |

Log: `evidence/migration/rc17-pg16.txt`. TimescaleDB and the installer's encrypted archive were **not** exercised.

## 6. Printer storage measurement (RC16 finding)

```bash
python scripts/printer_storage_measure.py
```

Setup: 96 polls at 900 s (24 h) of one emulated printer whose page counter changes every poll and whose toner
drops every 8 polls.

| Measure | rc16 | rc17 |
|---|---|---|
| Samples per poll | 32.3 | 2.09 after the first poll |
| Samples per printer per day | 3,099 | 232 (−92.5 %) |
| Derived bytes per printer per day | ≈ 1.19 MB | ≈ 89 KB |
| Derived bytes per year, 1,000 printers | ≈ 435 GB | ≈ 32.6 GB |

Bytes are **derived** from the rc16 PostgreSQL measurement (22.3 MB / 58,004 samples including indexes) and were not
re-measured on PostgreSQL. Evidence: `evidence/load/rc17-printer-storage.json`. The mixed-load latency harness
(`scripts/load_test.py`) was **not** re-run for rc17.

## 7. Dependency audit

| Tool | Result | Disposition |
|---|---|---|
| `pip-audit -r requirements.lock` | paramiko 3.5.1, PYSEC-2026-2858 (fix 5.0.0) — same as rc16 | **Not upgraded.** paramiko ≥ 4 removes ssh-rsa/SHA-1, which older Instant APs need (pinned in `requirements.txt`). Needs a hardware check of rsa-sha2 support on AP-505/535 8.10, then an upgrade. |
| `npm audit --omit=dev` | 2 moderate (react-router 6) | The fix requires React Router 7 (breaking); not done. |

Evidence: `evidence/security/rc17-pip-audit.txt`, `rc17-npm-audit.txt`.

## 8. Not executed (and therefore not claimed)

- Any physical Aruba, Cisco or UniFi device; any printer or print server
- The live server
- CentOS install, update or rollback scripts
- The Docker full stack
- TimescaleDB
- NATS/Loki integration
- Load and latency after rc17
- Release signing with the owner's key (rc17 is unsigned)
