# JCF Intelligent Network Management — Printer Intelligence

Source requirements: *Printer Intelligence, Efficiency & Predictive Maintenance Module* (2026-11-07),
supplied by the project sponsor. This document records implementation in the
current **NetworkManager3** application and its incomplete acceptance gates.

## Architectural decision

The uploaded specification originally describes .NET 10 / ASP.NET Core / Blazor /
SQL Server. This repository is **already FastAPI + React + PostgreSQL/TimescaleDB**.
Printer Intelligence is an independent bounded module in that existing stack,
not a second unmanaged software stack. A .NET migration would require a
separate architecture decision, a data migration, and regression/acceptance tests.

- Management: `backend/api/printers.py`, `backend/database/models_printer.py`,
  management PostgreSQL, additive Alembic revision `0005`.
- Analytics: `backend/printers/analytics.py` (pure functions; rule and evidence based).
- SNMPv3: `backend/printers/snmp.py` (SHA-256 + AES-128, bounded single-target read).
- Interface: `frontend/src/pages/Printers.tsx` at **Printers → Printer Intelligence**.
- Containers and persistent storage: inherited from Git-based CentOS installer;
  no second SQL database, no new privileged container or management daemon.
- Source of truth: private GitHub repository; no local PC or ZIP distribution.
- Security: printer hardware writes/firmware/print jobs intentionally unavailable.

## Capability register (do not describe unavailable functions as implemented)

| ID | Module | Implementation status |
|---|---|---|
| PRN-01 | Discovery & onboarding | **Partial P0:** manual single-printer onboarding, location fields, serial deduplication, optional explicit SNMPv3 read-only test. CIDR scan, bulk imports, saved credentials, multi-step resumable wizard still pending |
| PRN-02 | Model recognition & adapters | **Pending:** generic RFC 3805 and SNMP system MIB test is available; verified manufacturer catalogues/MIB downloads and per-model adapters not yet added |
| PRN-03 | Collection engine | **Partial P0:** manual measurements and one-off SNMPv3 probe. Scheduled SNMP/IPPS polling, raw response capture and distributed workers pending |
| PRN-04 | Usage performance | **Partial P0:** separate cumulative impression/sheet/colour/scan counters, history and safe reset detection. Full usage charts and duty-cycle workload ratios pending |
| PRN-05 | Consumable intelligence | **Partial P0:** colour-specific reported levels, low-supply rules, manual refill/cartridge cycles with cost. Automatic replacement identification and stock linkage pending |
| PRN-06 | Actual yield and cost | **Initial operational:** closed-cycle delta, per-1,000-impression cost, declared-yield comparison, measured-ink ml / impression where directly entered; source/coverage warnings |
| PRN-07 | Preventive maintenance | **Partial:** manual fault, replacement, service events. OEM service intervals and work orders pending |
| PRN-08 | AI/ML | **Rules only:** Analyse Now reviews recorded low supplies; anomaly detection, prediction training, approval workflow and confidence calibration pending |
| PRN-09 | Model recommendations | **Exploratory only:** cost-only peer comparison requires >=3 valid cycles; not a procurement ranking. Full TCO/duty-cycle scoring pending |
| PRN-10 | Consumables procurement | **Pending:** stock rooms, barcode issues, suppliers and reorder forecasting |
| PRN-11 | Service desk workflow | **Partial:** service event history. Ticket assignment and service-desk integration pending |
| PRN-12 | Dashboards & reporting | **Initial dashboard:** printer count, status, low supplies, model list. Scheduled Excel/PDF reports and outbound alerts pending |
| PRN-13 | Manufacturer knowledge base | **Pending:** approved MIB and specification catalogue, lifecycle and service bulletins |
| PRN-14 | Security & governance | **Initial:** admin-only access until physical location ACLs exist, explicit private /24–/32 SNMPv3 targets, no device writes, no print contents, shared audit log. Secrets profiles and multi-location permissions pending |

## Mandatory measurement rules

- Counter type is always explicit (`impressions_total`, `sheets_total`,
  `bw_impressions`, etc.). A **sheet** is never silently treated as an
  **impression**. A counter decrease is marked **RESET_OR_ROLLOVER** and
  yields no fabricated increment.
- A consumable cycle starts at an operator-entered cumulative meter and
  closes at a later cumulative meter. For a valid closed cycle, the
  difference is the **device meter delta during that lifecycle**.
- A colour cartridge does not necessarily contribute ink to every printed
  impression. Thus cycle usage/cost is a **calculated proxy**, not certified
  colour-specific page coverage. Users see this qualification.
- Refill millilitres added are **not automatically assumed consumed**.
  `measured_consumed_ml` is a distinct operator-entered fact.
- Reported supply percentages do not imply a measured ink volume; missing
  levels are unknown rather than zero. Thresholds are `LOW <= 15%`,
  `CRITICAL <= 5%` and apply only to validated reported levels.
- No statistical model is called *trained ML* until real maintenance
  labels and calibration evidence are available. No manufacturer yield
  comparison is treated as a laboratory-equivalent ISO test.
- Model cost comparisons require >=3 valid closed cycles **per model**.
  Even then they are cost observations, not procurement recommendations.

## One-off SNMPv3 read

1. Server administrator approves the printer management IP with
   `bash scripts/allow-readonly-device-range.sh 10.103.44.21/32`.
2. Administrator opens **Printers → Printer Intelligence → Test SNMPv3 connection**.
3. SNMPv3 requires a SHA-256 authentication passphrase and AES-128 privacy
   passphrase. No SNMPv1/v2c fallback. Credentials are not persisted.
4. Read only numeric system OIDs and a bounded Printer-MIB marker counter.
   Read the paired `prtMarkerCounterUnit` and only interpret its life
   count as impressions or sheets if the declared unit supports it.
5. Review returned hostname, system description, object ID and optional
   meter reading. Then **register the device separately**. Probes do
   not auto-create inventory entries.

**Limitations:** SNMPv3 support must be certified using actual HP, Epson,
Kyocera and Sharp models. Remote printer data are untrusted, must not be
interpreted as instructions, and must not be allowed to execute code or
download drivers on the management host.

## CentOS deployment and acceptance

This is an **additive 0005 schema migration** and therefore a change to
persistent management data. Existing encrypted backups must be preserved.
The one-command reload must take a new encrypted backup before modifying
the existing stack. Verify migrations, service health, printer CRUD,
meter/supply/cycle workflows, and restoration in a disposable test
environment before approval.

From the existing SSH-authorized GitHub checkout:

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/rebuild-demo.sh
```

The reload is **not** equivalent to a fully approved tagged production
update. No production printer write operations are enabled.

## Next implementation sequence

1. Harden and certify SNMPv3 on four real-brand test devices and add
   IPPS fallback with validated TLS.
2. Introduce encrypted per-site credential profiles, approval-gated
   per-device discovery and bounded distributed poll jobs.
3. Capture model-specific Printer-MIB counters, supplies and fault codes,
   plus source/quality metadata with backoff.
4. Add cartridge-change detection with operator confirmation, model
   catalogue and service rules. Integrate location registry.
5. Only after real cycles/maintenance evidence accumulates, release
   explainable forecasting, work orders and procurement comparisons.

**Acceptance is empirical, not promotional:** manufacturer coverage,
counter quality, realistic ink/toner yields, restoreability and read-only
device safety must be proven before claiming P0 operational deployment.
