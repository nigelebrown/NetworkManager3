# SIMS-WiFi-Manager uploaded source → NetworkManager3 acceptance register

**Source inspected:** user-supplied `SIMS-WiFi-Manager-main(1).zip` (112 files, 2026-11-08).  
**Destination:** `nigelebrown/NetworkManager3` private GitHub `main`.  
**Intent:** preserve every useful, existing SIMS feature, add *actual device onboarding* to the current demo, and keep unsafe hardware writes locked.

This is a **capability and acceptance record**, not a declaration that every vendor device
has been certified. A function being present in source or passing mock tests is
different from having been tested against real Aruba AP-505/AP-535 or CX 6100/6200.

## Uploaded legacy SIMS features and status in the combined application

| Uploaded SIMS feature | Where it lives now | Evidence & honest deployment status |
|---|---|---|
| Login, password changes, logout, role checks and audit | `backend/api/auth.py`, `backend/security`, `/login`, `/users`, `/audit` | Retained and exercised by original backend and browser suites. Weak `demo123!` only for restricted demonstration; production must use strong authentication |
| Demo Instant clusters, access points, clients and network dashboard | `backend/monitoring/demo.py`, `backend/monitoring/collector.py`, `/sites`, `/aps`, `/clients`, `/` | Working simulated fixture flow; explicitly labelled DEMO |
| Aruba Instant VC REST login and read-only commands | `backend/aruba/client.py`, `authentication.py`, `monitoring.py`, `parsers.py` | Legacy client/parsers preserved and mock-tested; real hardware compatibility requires per-version testing |
| Live Aruba Instant VC site registration / manual polling | `POST /api/sites`, `POST /api/sites/{site_id}/test`, `.../poll`, `frontend/src/pages/Sites.tsx` | Legacy live-mode capability retained. **Intentionally blocked in the global demo profile**; do not claim that registering a generic AP automatically creates a polling Instant VC cluster |
| AP and client inventory, histories, RF/CPU/utilization statistics | `backend/api/data.py`, `aruba/telemetry.py`, `/aps`, `/clients` | Legacy demo and mock polling work; real Instant telemetry varies by firmware/API availability |
| User experience scoring, scores and explanatory events | `backend/experience/scoring.py`, `backend/api/experience.py`, `/experience` | Preserved, regression-tested and visible in simulated mode. Only available input metrics count toward the scores; no fabricated latency/retry |
| Alert rules, active alerts, acknowledgement and resolution | `backend/monitoring/rules.py`, `backend/api/data.py`, `/alerts` | Preserved; original simulated alert lifecycle and role checks are regression-tested |
| Redacted configuration snapshots, SSID readback, comparisons and change plans | `backend/configmgmt/service.py`, `backend/api/config.py`, `/config` | Retained for read/plan. **Actual device write/apply remains LOCKED**; redacted snapshots are not full restore backups |
| AP rename/move/change planning | `backend/api/config.py`, `backend/api/data.py`, `/aps` | Legacy plan and mock behaviours retained; real configuration writes/physical AP moves locked until certification |
| Firmware compliance, image metadata and upgrade plans | `backend/firmware`, `backend/api/firmware.py`, `/firmware` | Read/plan/staging exists; real firmware deployment not certified or enabled in this demo |
| Certificate planning, HTTPS/TFTP support and REST-onboarding checks | `backend/certs`, `backend/aruba/onboarding.py`, `backend/api/sites.py` | Code and mock tests retained; SSH REST enablement and certificate delivery are **device writes and locked** |
| Optional local Ollama assistant | `backend/ai`, `backend/api/ai.py`, `/assistant` | Existing advisory endpoints/logic retained. Actual model responses require a connected, configured local model |
| Original SIMS UI pages and test suite | `frontend/src/pages/`, `backend/tests/` | All 10 original application page types preserved with routes; original regression suite remains part of GitHub Actions |

## Device onboarding in the combined app (added beyond the uploaded SIMS application)

**Inventory → Add device** supports:

1. Choose **Aruba Instant AP-505/AP-535** or **Aruba CX 6100/6200**.
2. **Add individually:** physical location, hostname, serial, model, firmware, management IP; optional AP RF/CPU/client or CX port/PoE statistics.
3. **Edit an existing real device:** choose **Edit details**. A case-insensitive serial number resolves the existing identity instead of duplicating it.
4. **Bulk CSV:** download a template, select a local CSV file, preview up to 100 entries, then confirm. Required columns: `family,site_name,hostname,serial_number,model`; optional `management_ip,firmware,status`. Duplicate serials and cross-family conflicts are rejected before changes are saved.
5. **One-time read-only Aruba REST:** requires an explicitly authorized private device management address, working REST credentials, and trusted TLS. Passwords are not persisted by the quick probe. Real hardware API support still requires certification.
6. Browse **Access points**, **Switches** or **View details**. Actual-device data are marked `REAL · MANUAL` or `REAL · REST READ`, not `DEMO`.

Bulk imports intentionally record **inventory only** to preserve a single management-DB transaction; add individual metrics afterwards. No bulk IP scanning or writing to devices occurs. A CIDR `/9` is usable for **planning**, not automatic scanning (especially public `128.0.0.0/9`).

**Original SIMS concept distinction:** a *legacy Instant site* is a Virtual Controller cluster; an *INM physical site* is a physical location. These are separate records and must not be silently merged.

## Acceptance and how to test

| Layer | Automation | What it actually proves |
|---|---|---|
| Original backend regressions | `python -m pytest -q backend/tests` | Existing Aruba parsers, API, demo fixtures, scoring, firmware plans, auth, redacted configuration, mocked telemetry |
| Legacy parity routes/screens | `python -m pytest -q tests/contract/test_legacy_sims_feature_parity.py` | Original routes and React pages still mounted; demo sites/dashboard and write-blocking constraints |
| New real-device intake | `python -m pytest -q tests/contract/test_quick_device_intake.py tests/contract/test_bulk_device_import.py` | Add/update/validate AP and CX inventory, metrics, role restrictions, duplicate safety |
| Full Phase 1 tests | `python -m pytest -q tests/unit tests/contract` | Platform schemas, security controls, event contracts and CRUD workflows |
| Frontend | `cd frontend && npm ci && npm run build` | React/TypeScript compiles |
| Docker and Chromium | GitHub Actions `.github/workflows/phase1-ci.yml` | Nine-container startup, API+bus+DB+logs event round trip, browser navigation, single-device and bulk onboarding |
| Physical acceptance | **Not completed by GitHub CI** | Must test real AP-505/AP-535 and CX 6100/6200 network access, TLS trust, supported firmware, backup restoration and CentOS SELinux |

## Git-only CentOS reload (preserves named volumes)

Only after the GitHub Actions run reports success:

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/rebuild-demo.sh
```

This validates release and system prerequisites before replacing **only INM**
containers, attempts an encrypted pre-reload backup and runs service verification.
Do not use `docker system prune` or `docker compose down -v`.

The restricted demo web interface is accessible via a locally established SSH tunnel
at `https://localhost:8443`; login `admin` / `demo123!` only while the weak-password
demo safeguards are satisfied. Replace that password before network exposure or production.

## Important unresolved gaps

- No blanket claim that configuration writes, firmware upgrades, REST enablement
  or certificate deployment are certified; those operations remain locked.
- The global demo profile does not register continuously polled real legacy
  Instant VC clusters. The quick REST snapshot is not scheduled continuous polling.
- No evidence that every vendor/firmware variation is supported; hardware test
  evidence and approved device IPs are necessary.
- Site access grants and centrally managed credential profiles for all physical
  devices, source-quality validation, and automatic network discovery require
  separate release gates. Bulk import is offline manual data entry.
- A clean CentOS install, tested encrypted restore, failover, and update/rollback
  still require an acceptance record from the actual server.

**Deployment sign-off must be based on successful CI plus the real CentOS and
hardware acceptance tests**, not merely on the existence of code.
