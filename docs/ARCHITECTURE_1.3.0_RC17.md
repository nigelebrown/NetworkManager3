# NetworkManager3 1.3.0-rc17 — architecture (delta over rc16)

The platform shape is unchanged; see `docs/ARCHITECTURE*.md` and `CLAUDE.md` for the base:

- FastAPI API
- network worker
- PostgreSQL management DB (Alembic) + TimescaleDB telemetry
- NATS outbox
- Loki/Alloy logs
- React/Vite SPA
- Docker Compose on CentOS Stream 9

This document covers what rc17 adds and where it lives.

```
            ┌──────────── React SPA (route-split chunks; one menu entry per purpose, related screens = view tabs)
            │
 API (FastAPI) ── api/printer_purchasing.py ── printers/purchasing.py ──┐
            ├─── api/print_jobs.py ────────── printers/print_jobs.py ───┤── management DB (0015 tables)
            ├─── api/cx_firmware.py ───────── firmware/cx_firmware.py ──┤   + AppSetting keys (policies, plans,
            ├─── api/firmware.py (+reboot) ── firmware/ap_reboot.py ────┤     tokens, probe/clock state)
            ├─── api/vault.py (+typed restore) domain/instant_reconcile ┤
            ├─── api/production_ops.py (probe, diagnostics, analytics, device-log retention)
            └─── api/collection.py (SNMP profile binding)
 Worker ─── scheduled_jobs: device_probes (5 min, opt-in) · printer_purchasing (daily) · print_job_retention (daily)
            · device_log_retention (daily) · collection_runner → adapters/snmp_switch.COLLECTORS (READ-ONLY)
 Writers ── writers/aruba_cx.py (ACL/route/QoS/VSF + firmware upload/boot)  writers/aruba_instant.py (role/portal/
            multi-auth)  ── both behind change_control.validate_operation → plan → approval → certification →
            checkpoint → apply → readback → rollback (unchanged governance)
 Emulators (tests/demo only) ── writers/emulator.py (CX + Instant), adapters/snmp_switch.EMULATORS,
            device_probes.EMULATED, EmulatedPrinterAgent — selected only by explicit *_EMULATION flags or DEMO devices
```

## Data model (migration 0015, expand-only)

| Table | Purpose | Key constraints |
|---|---|---|
| `printer_purchase_requests` | Internal supply requests and their lifecycle | Index `(dedupe_key, status)`; at most one open request per printer supply is enforced in the service |
| `print_jobs` | Job metadata from print-server agents | Unique `dedupe_key` (source, job id, printer reference, completion time); optional pseudonymous `user_ref`; `content_sha256` only |
| `print_job_findings` | Inspection rule matches | Masked token / rule name / count; cascade delete with the job |

AppSetting keys:

| Key | Holds |
|---|---|
| `printer_purchasing_policy` | Automatic purchase-request policy |
| `print_job_policy` | Print-job collection and inspection policy |
| `print-ingest-token:<id>` | Ingestion token: SHA-256 only |
| `device-probe:<device>` | Latest active-probe result |
| `device-clock:<device>` | Latest clock-offset measurement |
| `device_log_retention` | Device-log retention policy |
| `opt-batch:<id>` | Optimisation site batch |
| `cx-fw-image:<sha>` | Stored CX firmware image |
| `cx-fw-plan:<id>` | CX firmware plan |

## Security and safety properties added in rc17

**Printers**

- Print jobs are off by default and need a purpose and an approval reference.
- Ingestion uses a bearer-style header token stored hashed and compared in constant time; tokens can be revoked.
- Document text never reaches storage or logs.
- Job-level reads are restricted to admin and auditor, and every read is audited.
- Operators see aggregates only.

**Firmware and reboots**

- CX boot and AP reboot persist their "sent" state and commit before any device contact.
- A repeated request is refused instead of being resent.
- Verification reads version and serial (CX) or every planned member (AP).

**Configuration writes**

- New write operations are typed and validated, use an allowlisted CLI grammar (Instant) or fixed REST bodies (CX), are readback-checked, and roll back by inverse operation or checkpoint.
- They are **UNCERTIFIED** until hardware evidence exists, and the execution gate refuses uncertified operations.
- 80 MHz is impossible by validation, by render (`no 80mhz-support`) and by reconciliation.

**Active probes**

- Probes touch only registered REAL devices at allow-listed addresses, with bounded concurrency.
- They never fill a missing counter.

**Other**

- Demo data in live mode needs an explicit environment opt-in, and the refusal is audited.
- AI/assistant paths gained no credential access and no write path.

## Frontend structure

- `components/Layout.tsx` `GROUPS` is the single navigation registry. Each item has:
  - `purpose`
  - optional `views` (sibling screens rendered as tabs)
  - optional `match` (drill-down routes)
- `ownerOf(path)` resolves the single owning entry.
- `App.tsx` lazy-loads every page except login and password change; Suspense boundaries sit around the router and the layout outlet.
- Printer workspaces keep their own sub-navigation (`PrinterNav`) with the new Supply purchasing and Print jobs workspaces.
