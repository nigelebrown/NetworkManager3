# Whole-application review — 1.3.0-rc18 (directive section 5)

This review searched the whole repository (backend, workers, frontend, scripts) for unfinished work, using the
directive's search list. The table shows what remains after rc18 and why each remaining hit is not unfinished work.
Counts exclude test code.

| Search | Remaining | Classification |
|---|---|---|
| `TODO` | 4 backend, 2 frontend | Not work items. 3 + 2 are the **status value** `TODO` of guided-setup steps (`api/guides.py`, `GuidePanel.tsx`). The fourth (`printers/mib.py`, vendor printer OIDs) was rewritten to point at the hardware rows that confirm them |
| `FIXME`, `NotImplementedError`, `CONCEPT` | 0 | — |
| `NOT_IMPLEMENTED` | 7 backend, 1 frontend | Truthful per-operation capability states of the **read-only** non-Aruba adapters (Cisco SNMP, UniFi, generic SNMP): writes are not offered. The unused `_not_implemented()` 501 helper was removed in rc18. No adapter in the registry has `implemented=False` |
| `placeholder` | 4 backend, 109 frontend | Frontend: HTML input placeholder text and CSS. Backend: a comment about the retired Phase 1 routes (now 410 MOVED to the live route), the scaffold-adapter flag (none in use), and the Instant guard that refuses to send a masked/placeholder key. The real placeholder-secret fallback was removed in rc18 |
| Empty exception handlers | 0 silent of 466 | 8 `pass` handlers carry an inline reason, for example a best-effort close or a metric that is absent so not reported. All other handlers log, convert or re-raise. rc18 replaced every silent `except: pass` |
| Placeholder responses / hardcoded demo data | 0 in live paths | Demo data lives only in `infrastructure/demo_dataset.py`. In live mode it is refused (403, audited) unless `INM_ALLOW_DEMO_DATA_IN_LIVE=true`. Emulator output is labelled `EMULATED` (transceiver output fixed in rc18) |
| Unconnected frontend | 0 | `scripts/audit_frontend_api.py`: every API path the SPA calls has a backend route (352 paths in the last run). `tests/e2e/nav_audit.py`: every menu entry and view tab loads with no overlap |
| Column lengths | 0 known violations | `scripts/audit_column_lengths.py`. The PostgreSQL contract run found 4 real defects, fixed in 0016 |

## Software gaps found by reviewing the register rather than searching for text

Some unfinished work leaves no tell-tale word in the code. These gaps surfaced only by reading the acceptance register
row by row against the code. All were completed in rc18 (details in `docs/RELEASE_1.3.0_RC18_CHANGELOG.md`):

- P6-10: no cluster-scope impact view and no stronger approval for high-risk deployments.
- RC16-T12: onboarding could drive shared/AAA accounts into lockout.
- P3-01 / INTEG-P3-03 / GAP-T03: device syslog in Loki never reached the device timeline or attribution.
- P4-08: no alert when syslog or AAA evidence stops.
- P10-02: Cisco configuration events were not recognised.
- P10-03: the UniFi adapter had no test and could never be selected.
- P11-DEP-03: no expand/migrate/contract interoperation window.
- Earlier in rc18: site-scoped exports (INTEG-P11-04), the update quiesce gate (P6-DEP-01), emergency access
  (P4-09), legacy sample migration (INTEG-P3-02), and the outbox poison envelope (P3-08).
