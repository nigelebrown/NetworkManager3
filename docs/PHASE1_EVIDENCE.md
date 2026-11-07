# Phase 1 evidence pack — INM 1.0.0-phase1

Built 2026-11-07 by Claude (build agent). Per CLAUDE.md, the build agent cannot sign off its own evidence: an
independent tester must re-run the NOT_RUN and PARTIAL items on a clean CentOS Stream 9 VM.

## Where this was tested

The build environment had no CentOS VM and **no access to any container registry** (Docker Hub, ghcr.io, quay.io
were all blocked by policy), so the Docker images could not be pulled or built here. To still exercise every role
for real, the same seven roles ran as native processes (`dev/native_stack.sh`): PostgreSQL 16.15, TimescaleDB
2.22.1 (built from source, Apache licence), NATS 2.11.9 JetStream with the shipped auth config, Loki 3.5.5,
Alloy 1.11.0 with the shipped syslog config, the AI service, the API and the network worker, using the same code
and configuration files the containers use. The Docker layer (images, compose, scripts) was validated statically
(`docker compose config`, topology inspection, `bash -n`, shellcheck) and its preflight logic was executed.

## Results

| Suite | Result | Log |
|---|---|---|
| SIMS baseline, before any change (INTEG-P1-01) | 169 passed, 0 failed, 0 skipped; frontend build OK | `evidence/baseline/` |
| SIMS suite on integrated code | 169 passed | `evidence/tests/legacy_after_integration.log` |
| INM unit + contract | 48 passed | `evidence/tests/unit_contract.log` |
| Integration against real services | 13 passed | `evidence/tests/integration_native_stack.log` |
| Browser E2E (Chrome) | 5 passed, 12 screenshots | `evidence/e2e/` |
| `backend.cli verify` (14 checks incl. full event round-trip and syslog) | all passed | `evidence/native/verify_native_stack.log` |
| verify with management DB stopped | exit 1, names `role:management_db` etc. | `evidence/native/verify_mgmt_db_down.log` |
| Installer preflight with ports in use | stopped at PRECHECK, exit 3, nothing created | `evidence/deployment/preflight_port_conflict.log` |
| Secrets: idempotent, 0600/0700, compose topology | as required | `evidence/deployment/secrets_and_compose_validation.log` |
| shellcheck / secret scan | warnings only (reviewed) / 0 matches | `evidence/deployment/` |

Acceptance register (41 Phase 1 tests): **23 PASS, 13 PARTIAL, 5 NOT_RUN, 0 FAIL** — see
`docs/Unified_Acceptance_Register_v4.csv` (status, evidence and artifact link per row) and
`docs/Feature_Integration_Traceability_v4.csv`.

## Defects found and fixed during verification

1. Event envelope hash computed before dataclass defaults → every event dead-lettered. Fixed + regression test.
2. App-wide rate-limit dependency broke every WebSocket handshake (HTTP 500). Fixed (`HTTPConnection`).
3. `/change-password` route evaluated the session once at app start → login loop. Fixed.
4. Event-bus health probe reconnected forever when NATS was down → health page/verify hung. Probes and CLI now fail fast.
5. Worker shutdown could hang on a blocking thread. 15 s watchdog added (Compose grace period is 20 s).
6. Device-write guard returned 500 when the audit DB was down. Now always 403 (fail closed) and logs the audit gap.

## Must be run by the tester on a clean CentOS Stream 9 VM before the gate

- `P1-01`, `P1-DEP-01`, `P1-DEP-13`, `INTEG-P1-09`: `sudo bash deployment/centos9/install.sh --mode demo`, then
  re-run it, then `verify.sh`.
- `P1-DEP-02/03/05`, `P1-09`, `P1-02`: re-run, `ss -lntup`, stop `management-db` and run `verify.sh`, recreate
  containers keeping volumes, `docker compose stop <role>` one at a time.
- `P1-10`: `backup.sh` then `restore.sh --archive <file> --target test`.
- `INTEG-P1-10`: tag a release, update to it, then attempt `rollback.sh` to an incompatible tag.
- `INTEG-P1-04`, `P1-08`, `P1-DEP-15`: confirm with a packet capture that the worker container sends nothing off-host.
- `INTEG-P1-07`: enable the `ollama` profile with a small model and use the Assistant.
- `P1-11`: add image scanning / SBOM in your CI (not included in this build).

## Known risks / open items

- Image tags in `release.json` were not resolvable from the build environment; the first install resolves and
  freezes digests in `/var/lib/inm/images.lock`. If a tag is missing, set `INM_IMAGE_*` in `/etc/inm/inm.env`.
- Background retention for telemetry uses Timescale's policy (community licence in the official image); on an
  Apache-only build the worker prunes with `drop_chunks` instead.
- Approver role and site grants are groundwork; four-eyes approval is Phase 4/6.
- Real device support (AP-505/535, CX 6100/6200) is Phase 2 and remains read-only.
