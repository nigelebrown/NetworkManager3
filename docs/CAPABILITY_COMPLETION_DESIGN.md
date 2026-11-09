# Intelligent Network Manager — capability completion design baseline

**Document class:** implementation specification and acceptance contract (not evidence of completed software)
**Revision:** Design Addendum 1, 2026-11-08
**Repository:** `nigelebrown/NetworkManager3`
**Owners:** product sponsor approves scope; technical lead approves contracts; independent QA approves gates
**Technology remains unchanged:** Python/FastAPI + React/TypeScript, Onion/Clean Architecture, PostgreSQL, TimescaleDB, NATS JetStream, Loki/Alloy, isolated AI service, Docker Compose on CentOS Stream 9; distribute logical roles onto independent VMs later.

> **Source boundary:** The external ChatGPT sharing URL `https://chatgpt.com/s/t_6af10e333d148191a950fa2d5b43a258` could not be retrieved. This addendum therefore translates *confirmed outstanding requirements in the existing repository and project discussion* into executable design. It **does not assert** that it covers undisclosed contents of that link. Reconcile the link item-by-item if its contents become available.
>
> **Release-state boundary:** All newly specified contracts below are `DESIGNED_NOT_IMPLEMENTED` unless a row explicitly identifies an existing foundation. Real AP/CX hardware support remains subject to certification. `release.json` currently declares management schema `0006`; a design addition is **not** authorization to alter that manifest, apply migrations, expose privileged endpoints, or permit device writes.

## A. Application principles and delivery rules

1. **Process-first UX:** Present a guided path, not unrelated menu pages. Each operation has a step indicator, preconditions, validation, back/continue, saved draft/resume, review/confirm and a final evidence/receipt screen.
2. **Demo, manual, observed, verified, inferred and synthetic are distinguishable** in the API, database, UI, chart tooltip and export. A manually entered AP/switch reading is never described as live polling. Missing values remain unknown.
3. **Start useful with one VM and add modules:** retain the functioning real-device onboarding, encrypted reusable credential profiles, bounded discovery, partial redacted baseline, draft configuration and manual/CSV options. Do not rewrite already working code or remove original SIMS routes.
4. **Default read-only:** Hardware writes, credential rotation on devices, REST enablement, RF adjustments and firmware operations remain locked unless the independent Phase 4–6 gates have passed for the specific model/firmware. AI never obtains device credentials or a device-network route.
5. **Audit every workflow:** Include initiator, named identity, operation, target, UTC timestamps, original and final state, source, result, error category, change/approval ID, code release SHA and evidence pointers. Never claim the operator of an external change without supporting AAA evidence.
6. **One-click Git release:** Deployment and updates pull from GitHub; installer performs preflight → backup → compatible migration → deploy → health/e2e verification → signed receipt/rollback. No workstation ZIP copy, unmanaged hotfix or destructive Docker cleanup. Keep the temporary `admin / demo123!` in the *explicitly approved and subnet-restricted* demo only; never copy that setting to production.
7. **Test before proceeding:** Every work package includes tests against demo fixtures and a negative/security case; hardware-dependent acceptance is visibly `NOT_RUN` until evidence exists.

### Status vocabulary

- `AVAILABLE_DEMO`: tested with synthetic fixtures; no real-hardware conclusion.
- `AVAILABLE_READ_ONLY`: path exists but only supported/observed hardware is trustworthy.
- `PARTIAL`: one or more operations exist but end-to-end acceptance is incomplete.
- `DESIGNED_NOT_IMPLEMENTED`: contract added here; not available in runtime.
- `HARDWARE_UNVERIFIED`: adapter behavior has not been certified on the exact model/firmware.
- `WRITE_LOCKED`: no authorization to change network-device configuration.
- `PRODUCTION_GATED`: independent controls or recovery evidence outstanding.

## B. Capability gap register and build order

| ID | Capability to complete | Existing foundation | New contract/work package | Phase / status |
|---|---|---|---|---|
| GAP-01 | Continuous real AP/CX collection | One-time read-only probe; generic worker; TimescaleDB | Per-site schedules, credential bindings, poll leases, backoff, gap flags | 2–3 / DESIGNED_NOT_IMPLEMENTED |
| GAP-02 | Genuine user/RF experience analytics | SIMS scores; client/RF parsers | Time-window KPIs, quality weighting, retry/roam/latency tests, service baseline | 3 / PARTIAL |
| GAP-03 | Structured syslog and accounting correlation | Alloy/Loki synthetic pipeline; audit chain | Per-device source identity, parser versions, AAA session correlation | 3–4 / DESIGNED_NOT_IMPLEMENTED |
| GAP-04 | Actual last-changed-by evidence | Named application users; audit | AAA/RADIUS/TACACS+ evidence, drift timeline and uncertainty labels | 4 / DESIGNED_NOT_IMPLEMENTED |
| GAP-05 | Full-fidelity encrypted configuration vault | Redacted read-only snapshot | Immutable encrypted restorable object, key management, integrity and restore proof | 5 / DESIGNED_NOT_IMPLEMENTED |
| GAP-06 | Configuration inheritance engine | Per-device drafts, partial site grouping | Global → vendor/family → model/port-count → site → device deterministic merge | 6 / DESIGNED_NOT_IMPLEMENTED |
| GAP-07 | Controlled hardware configuration | Draft configuration; global write lock | Typed plan/diff, independent approval, pre/post snapshots, readback/rollback | 6 / WRITE_LOCKED |
| GAP-08 | AP RF optimization constraints | Rule recommendations; native Instant ARM | ClientMatch/ARM/power/DFS guards, width only 20/40 MHz, channel exclusions | 6–9 / DESIGNED_NOT_IMPLEMENTED |
| GAP-09 | Switch configuration capability profiles | CX identity/interface REST read | VLAN/access/trunk, PoE, LACP, LLDP and model/firmware support matrix | 6 / WRITE_LOCKED |
| GAP-10 | Device replacement workflow | Inventory and redacted snapshots | Same/compatible model validation, serial remap, staged restore, validation | 7 / DESIGNED_NOT_IMPLEMENTED |
| GAP-11 | Visible ML learning history | Rule-based Analyze Now; advisory service | Datasets, feature versions, baseline comparison, trained model registry, feedback | 8 / DESIGNED_NOT_IMPLEMENTED |
| GAP-12 | Measured recommendation effectiveness | Rule recommendation objects | Before/after observation windows, counterfactual/seasonality flags, promotion gate | 8–9 / DESIGNED_NOT_IMPLEMENTED |
| GAP-13 | Per-device opt-in automatic optimization | No device write permissions | Default-off device enrollment, explicit device IDs, bounded approved actions, cooldown, expiry, emergency stop; no inherited site-wide auto-enable | 9 / WRITE_LOCKED |
| GAP-14 | Cisco/UniFi and generic devices | Vendor-neutral adapter boundary | SNMPv3/HTTPS/NETCONF/SSH capability adapters; no false support | 10 / DESIGNED_NOT_IMPLEMENTED |
| GAP-15 | Distributed multi-VM production | One Compose VM, seven roles | Role separation, TLS/mTLS, HA/DR, migration, durability and recovery tests | 11 / DESIGNED_NOT_IMPLEMENTED |
| GAP-16 | Auditable reports and executive views | Basic charts; audit/list screens | Site/model/user experience, changes, compliance, signed exports | 11 / DESIGNED_NOT_IMPLEMENTED |
| GAP-17 | Isolated local AI code reviewer | Isolated AI advisory boundary | Read-only application source + sanitized build logs; no network/secret/device access | Cross-cutting / DESIGNED_NOT_IMPLEMENTED |
| GAP-18 | Printer intelligence completion | Manual printer inventory, SNMPv3 test and rules | Model-specific MIB packs, scheduled telemetry, service rules, predictive model gate | Separate printer module / PARTIAL |

**No duplicate build:** existing `backend/api/onboarding_workflow.py`, `frontend/src/pages/GuidedOnboarding.tsx`, `DeviceIntake.tsx`, `DeviceConfig.tsx`, `backend/api/discovery_ranges.py` and schema `0006` are starting assets. Expand behind their contracts rather than implementing a second onboarding stack.

## C. Detailed workflows and core data contracts

### C1 — Inventory → live telemetry (GAP-01, GAP-02)

**Workflow:** physical site → model/device → saved credential profile → verify TLS/capabilities → choose read-only metrics → poll preview → approve schedule → review active collection and health → inspect graphs/logs → pause/resume.

- Schedule scope is per site, adapter and device; honor device rate limit, concurrency budget and maintenance window. Store the schedule and next due time, last success, last failure category, last observation watermark, credential profile ID, API capability-set version, firmware, polling mode and source quality.
- Persist a poll job through NATS with dedupe ID `(device_id, metric_family, scheduled_window)`. Worker takes a bounded lease, retries transient errors with exponential backoff and jitter, sends permanent failures to DLQ, and never converts failed collection into a successful zero.
- Separate Instant Virtual Controller cluster identity from physical site and AP identity. A cluster query must not accidentally duplicate every AP member; normalize by immutable serial and timestamp.
- Every `Observation` includes `device_id, physical_site_id, cluster_id?, metric_name, value?, unit, source_type, source_protocol, quality, observed_at_utc, collected_at_utc, source_event_id, adapter_version, firmware, sample_window_seconds?, reason_if_missing`.
- Experience measurements include measured sample size and input coverage, client association/roam/disconnect/retry outcomes where supported, channel utilization, airtime fairness and RF noise where obtainable, plus explicit caveats for unmeasured latency or loss.
- Surface 1m/5m/1h/24h history without forcing page reload; use WebSocket deltas, replay cursor, REST reconciliation and a clearly visible **last updated** watermark.

**Proposed API contract:** `POST /api/v1/collection/plans/preview`, `POST /api/v1/collection/plans`, `GET /api/v1/collection/jobs`, `POST /api/v1/collection/jobs/{id}/pause`, `GET /api/v1/devices/{id}/experience`. These are proposed, not claimed implemented.

**Acceptance:** real AP-505/AP-535 and CX 6100/6200 show representative counters with supported/unsupported fields; remove network and verify stale/gap flags, no synthetic fallback, bounded retries, worker restart continuity, duplicate suppression and a chart update without browser refresh.

### C2 — Syslog, AAA accounting and technician attribution (GAP-03, GAP-04)

- Ingest device syslog using mutually authenticated secured transport where supported; support restricted syslog/UDP in the pilot with source-IP allowlist and transparent transport-quality flag. Normalize vendor, serial, management source, event code/severity, device and ingest UTC timestamp, parser version, user/session (when supplied), device config revision and source log locator.
- Correlate `ChangeEvidence` across application audit, CX audit/accounting logs, AAA (RADIUS/TACACS+), device-reported configuration commit, previous/current config hashes and syslog events.
- **Attribution levels:** `VERIFIED` (unique named AAA session and matching config event), `CORRELATED` (candidate link with recorded evidence and uncertainty), `UNKNOWN` (no defendable identity). Never infer `last_changed_by` merely because someone last logged into the app.
- Show *application change* vs *direct-device change* as separate event types. Allow investigation notes and authorized correction of correlation only by creating an auditable superseding record, never overwriting raw evidence.

**Proposed contracts:** `DeviceEvent`, `AuthSessionEvidence`, `ConfigDriftEvent`, `ChangeAttribution`; `GET /api/v1/devices/{id}/change-timeline`, `GET /api/v1/drift`.

**Acceptance:** change by named technician has correlated session, UTC timeline and evidence links; out-of-band change with no accounting shows **UNKNOWN**; forged/unrelated session cannot earn VERIFIED; remove log source and show correlation coverage gap; test tamper-evident audit continuity.

### C3 — Configuration vault and replacement (GAP-05, GAP-10)

**Workflow:** discover existing config → inspect completeness/secret classification → obtain encrypted full snapshot through certified adapter → immutable version/hash → schedule backup and retention → restore verification on isolated compatible test target → mark `RESTORE_VERIFIED` → permit replacement wizard later.

- Keep current redacted **PARTIAL/NOT_RESTORABLE** configuration snapshots *separate* from full-fidelity encrypted snapshots; never present one as equivalent to the other.
- Store encrypted object out of ordinary database rows in secured object storage with envelope key/KMS (test VM may use a local provider), immutable hash, signed manifest, vendor/model/firmware, device serial, capture source, config generation, key ID, retention policy, backup job and restore test evidence. Do not log raw credentials or export full configs through ordinary list endpoints.
- Snapshot capture is triggered by schedule, approved change pre/post, external drift, and manual operator action. Perform conflict/idempotency checks; if required pre-change backup fails, deny the write.
- **Replace Device wizard:** failed old device → capture known-good version → same/explicitly compatible model and software → map identity-specific fields (serial, management IP, certificates, uplink topology, PoE budget) → independent approval → lab restore/readback → staged production restore if certified → postflight reachability and service tests → rollback and audit receipt.

**Proposed contracts:** `ConfigArtifact, ConfigVersion, RestoreValidation, ReplacementPlan, BackupTrigger`; `GET /api/v1/devices/{id}/config-versions`, `POST /api/v1/replacements/plan`. No real restore endpoint before lab certification.

**Acceptance:** tampered vault object fails hash/restore, wrong model/firmware is rejected, backup secret is never in logs/exports, compatible replacement verifies against both the expected config hash and functional port/SSID checks, and orphaned device identity remains traceable.

### C4 — Template inheritance and approval-controlled changes (GAP-06–GAP-09)

**User workflow:** create global base → select vendor/family/model/port count → attach one or more physical sites/groups → assign to devices → preview *effective configuration + provenance* → view conflicts/drift → save draft → request approval → approve (different person) → backup → apply only after write certification → readback → close or roll back.

- Configuration precedence must be explicit and deterministic: `organization global → vendor/family → model/port-layout profile → site/group → device override`. Each effective field must show its source, baseline revision and override reason.
- One logical template can target multiple APs or switches. Port-layout rules for 24/48-port models may override common VLANs without overwriting unrelated per-site interfaces. Support global common VLAN sets (illustrative examples: 100–500), specialized model VLAN sets and site-specific SSIDs/VLAN mappings.
- Typed configuration schema: `NetworkTemplate(id,version,scope,parent_id,device_filter,settings)`; `EffectiveConfig(device_id,source_versions,diff,conflicts,capabilities)`; `ChangePlan(id,requester,reviewer,approval_policy,preconditions,expected_hash,operations,rollback_plan)`.
- Aruba AP configuration surface: SSID ↔ VLAN, 2.4/5 GHz band choices, band steering, ClientMatch, airtime fairness, ARM, transmit-power min/max, DFS and region-specific channels. **Only 20 or 40 MHz. Reject 80 MHz and previously excluded channels** until an approved site compatibility profile explicitly permits those other *channels* (never 80 MHz). Do not claim vendor support until firmware API evidence exists.
- Aruba CX surface (capability-gated): VLAN definition, port access/trunk, LACP, PoE/LLDP, administrative status, relevant uplink safety restrictions and interface readback. A model missing an operation must report `UNSUPPORTED`, not silently skip it.
- No two-person bypass: requester cannot self-approve; approvals expire after config drift, device firmware change, backup expiry or change window expiration.
- AI advice produces a **draft plan only**, never a direct device command. Writes require `write_lock_off`, certified vendor adapter, approved change, authenticated executor, verified encrypted pre-change backup and locked device lease.

**Acceptance:** merge engine gives same effective output regardless of request order; missing template inheritance and contradictory port/VLAN intent fail visibly; unauthorized edits and self-approvals fail; failed pre-backup performs zero device writes; 80 MHz or unsupported channels fail before reaching adapter; two concurrent approvers cannot double-apply.

### C5 — Explainable AI learning and outcome tracking (GAP-11–GAP-13)

**AI is part of the base product from day one, but learned models must be earned by evidence.**

**Workflow:** telemetry collected → data-quality gate → baseline and rules recommendations → Analyze Now / hourly evaluation → operator accepts/rejects with reason → approved action if eligible → compare pre/post observations → outcome recorded → candidate dataset/model trained in isolation → version/evaluate/calibrate → independent model promotion or rejection → timeline shows decision changes.

- Distinguish `RULE_BASED`, `STATISTICAL` and `TRAINED_ML` on every recommendation. An LLM explanation does not qualify as a trained RF optimizer.
- Record `Recommendation(id,device/site,created_at,analyzer_version,model_id?,training_data_revision?,policy_version,input_window,feature_coverage,reason_codes,confidence?,expected_effect,proposed_action,status)`.
- Record `DecisionFeedback(recommendation_id,operator,decision,reason,decision_time)` and `OutcomeEvaluation(recommendation_id,action_id?,before_window,after_window,metrics,confounders,quality_delta,success?,evidence)`.
- ML datasets must have schema, provenance, retained labels, explicit missingness and temporal train/validation/holdout separation; no leakage from future measurements. Store model version, algorithm/hyperparameters, evaluation metrics, calibration, validation environment and promotion approval (e.g. MLflow behind an interface, optional until later).
- Show **Learning Timeline**: recommended action, why, data window, feedback, whether a change occurred, measured outcome, model/rule version, confidence and what recommendation changed since last week. Do not claim that model quality improved without held-out and post-action evidence.
- Optimization policy: approved site/maintenance windows, allowlisted operations, max change frequency, rollback threshold, cooldown/hysteresis, strict write audit, global emergency stop and shadow/no-write mode. Compare proposed action to native Aruba ARM/ClientMatch behaviour rather than fighting it.

**Proposed API contracts:** `GET /api/v1/ai/learning-history`, `GET /api/v1/ai/models`, `POST /api/v1/ai/feedback`, `GET /api/v1/ai/outcomes`, `POST /api/v1/ai/evaluation/shadow`; keep existing read-only Analyze Now endpoint.

**Acceptance:** recommendation with insufficient data shows `INSUFFICIENT_EVIDENCE`; every recommendation explains input quality, actor feedback and version; test model leakage/drift; deterioration triggers stop/rollback proposal, not oscillating writes; no ML or AI service has egress to device management network.

### C5a — Per-device automatic optimization policy (GAP-13 — normative)

**Owner requirement: AUTOMATIC OPTIMIZATION MUST BE OFF BY DEFAULT.** Every device may receive monitoring, ML analysis, and non-executing recommendations. Automatic configuration adjustments are permissible only after an administrator deliberately opts in **that specific device**, in addition to completion of the real-device write-certification and audit/backup gates. A site, model, global template, discovered-device batch, newly cloned/replaced device, or future vendor adapter **must not automatically inherit an enabled state**.

#### User workflow — explicit per-device enrollment

1. Open **Devices → Device details → Optimization**, default mode **Observe and recommend (no changes)**. A prominent badge shows `AUTO OFF` for every device, including devices onboarded later.
2. A separate administrator action **Configure automatic optimization for this device** opens a wizard. Show immutable device ID, serial, site, model, firmware, API capability certification and current configuration hash.
3. Choose exactly which **operations** may be automated (examples: specified radio channel or power bounds where supported); define permitted values, channel exclusions, maximum 20/40 MHz width, hours/change windows, cooldown, rate budget, quality baseline, adverse-effect stop threshold and policy expiry. **No implicit "all settings" or "all devices" choice.**
4. Preview a dry run and affected devices; require a named independent policy approver, recorded backup/restore readiness, the version of applicable configuration baseline, and a human-readable scope summary. Only an explicit approval can change status from `OFF` to `ARMED` for the selected device. A vendor capability certification check and global hardware-write lock still apply.
5. Show `ARMED` or `ACTIVE` on that specific device, expiry date, approver, most recent action and direct **Disable automatic optimization** control. Disabling revokes queued authorization immediately and does not require a second approval.
6. Optional **Select multiple devices** is an administrative *bulk proposal* only: enumerate every exact device identity in the preview, record a separate device enrollment and approval record per device, and reject unreviewed/undiscovered devices. It never enables future devices by group membership.
7. On expiry, replacement, device firmware change, credential/certification loss, site transfer, configuration drift, policy edit, quality degradation, backup failure, emergency stop, failed readback or lost audit integrity, pause or revoke automatic execution until the specific device is reviewed again.

#### Authorization & execution boundary

- Separate **three modes** per device: `RECOMMEND_ONLY` (default), `SHADOW` (calculate/compare but do not apply), and `DEVICE_OPT_IN` (eligible for gated automated actions). `DEVICE_OPT_IN` is an intent, not a bypass of the central write lock.
- Enforce **global emergency stop and `device_write_lock` first**; when either blocks writes, ALL devices remain effectively read-only regardless of recorded enrollment. The GUI must display `Configured for future automation — currently locked` rather than misleadingly claiming active control.
- Evaluate authorizations on the server **at execution time**, not just when a job is scheduled: exact device UUID/serial and firmware/certification, unexpired enrollment + approver, permitted operation and limits, source/quality freshness, schedule, concurrency lease, expected config hash, required pre-change full-fidelity backup, and safe rollback. Any missing evidence = `DENY`.
- The isolated AI service proposes a typed change plan and evidence, **never holds device credentials or writes directly**. Only the separately authorized executor may write. A standing *policy approval* may allow individual bounded actions without separate click-through approval only after a dedicated production acceptance gate; until then, require explicit approval for each proposed change. Every action remains audited with before/after, measured result and rollback.
- Maintain native Aruba ARM/ClientMatch compatibility: do not continuously counteract native automatic RF behavior; use a conflict policy, cool-down and hysteresis. Reject 80 MHz under all optimizer modes. Other vendors require separate certification, not inheritance of Aruba permissions.
- Distinguish user-configured automation from vendor **native** optimization (for example Aruba ARM). A management-software `AUTO OFF` state does not itself disable native radio optimization on the equipment.

#### Proposed persisted contracts (not yet implemented)

```text
DeviceOptimizationEnrollment(
    id, device_id, immutable_serial, vendor_model, firmware_bound,
    mode=RECOMMEND_ONLY, policy_version, allowed_operation_types[],
    value_constraints, permitted_windows, min_observation_quality,
    baseline_config_hash, cooldown_seconds, max_actions_per_day,
    impact_stop_threshold, valid_from_utc, expires_at_utc,
    requester_id, independent_approver_id, approved_at_utc,
    status, last_action_id, revoked_at_utc, revoked_by
)
OptimizationExecutionDecision(
    id, enrollment_id, device_id, recommendation_id, proposed_diff_hash,
    evaluated_at_utc, global_lock_state, guard_evaluation[],
    allow_or_deny, denial_reason, backup_id?, audit_event_id
)
```

Proposed API endpoints (not yet available): `GET /api/v1/devices/{id}/optimization`, `POST /api/v1/devices/{id}/optimization/preview`, `POST /api/v1/devices/{id}/optimization/enroll`, `POST /api/v1/devices/{id}/optimization/disable`, `GET /api/v1/optimization/enrollments`, `GET /api/v1/optimization/decisions`, `POST /api/v1/optimization/emergency-stop`. Endpoints must enforce scoped RBAC, CSRF/session protection where applicable and immutable audit events. Do not add unrestricted bulk enable or a global "auto on" endpoint.

#### Required acceptance cases (all NOT_RUN until implemented and proved)

| ID | Scenario | Pass criterion |
|---|---|---|
| GAP-T17 | Discover/onboard new AP and switch while some other devices are enrolled | Every new device is `RECOMMEND_ONLY`; no inherited automatic write |
| GAP-T18 | Enroll one AP for bounded automation and leave another AP/switch unselected | Only selected ID can ever become eligible; others get recommendations with **zero writes** |
| GAP-T19 | Set site/group/model template to automatic and then add an AP | Template cannot enable auto enrollment; explicit separate device approval required |
| GAP-T20 | Attempt self-approval, expired enrollment or spoofed device ID/serial | Reject and audit every case before adapter access |
| GAP-T21 | Trip global write lock, kill switch, invalid backup, stale metrics or unauthorized operation | No hardware write, queued action canceled/denied and status visible |
| GAP-T22 | Change firmware, replace device or transfer site after enrollment | Authorization revoked or paused; successor device remains OFF |
| GAP-T23 | Permit approved narrow RF operation but request 80 MHz or excluded channel | Deny at policy engine; no driver request |
| GAP-T24 | Verify successful eligible change, degrade measured experience then re-evaluate | Audited pre/post evidence, cooldown and safe rollback/stop without repeated oscillation |

**Implementation gate:** First ship read-only `RECOMMEND_ONLY`/simulation-only `SHADOW` screens and schema with no hardware-write path. Only after hardware-specific write certification, encrypted pre/post backup verification, approval separation, stable measurement, emergency stop and QA acceptance can any per-device `DEVICE_OPT_IN` action be connected to real hardware. The full device write lock is **unchanged in the current release**.

### C6 — Future Cisco, Ubiquiti and other device adapters (GAP-14)

- Define shared adapter interface `probe_identity`, `get_capabilities`, `collect`, `read_config_summary`, `read_audit_logs`, `backup_full_config`, `validate_change`, `apply_change`, `readback`, `rollback`, all returning capability/quality/error evidence; unimplemented operations return structured `UNSUPPORTED`.
- Cisco IOS/IOS-XE: prioritize read-only SNMPv3 counters plus explicitly approved SSH/NETCONF where firmware provides it. Never assume NETCONF exists on older switches.
- UniFi: controller/API-mediated onboarding and polling where available; device-adapter capability records must distinguish a controller-managed AP from direct-host access. Avoid assuming a universal public configuration API.
- Adapter registry key: vendor, product family, model, firmware bounds, protocol, collection coverage, write capability, certification state and last tested evidence.
- No adapter may bypass global authentication, site scoping, credential store, policy or change/audit workflow.

**Acceptance:** unsupported device can still appear in inventory with limited readings; unsupported write rejected centrally; adding a new vendor does not break Aruba adapters or contaminate normalized metric units.

### C7 — Seven logical roles, one-VM demo then multi-VM production (GAP-15)

| Logical role | CentOS demo | Production split contract |
|---|---|---|
| 1. Web/API | React/Nginx, FastAPI containers | Separate stateless frontends/API, shared auth, HTTPS, WebSocket fan-out |
| 2. Management database | PostgreSQL container | Dedicated PostgreSQL service, secured backups, HA |
| 3. Time-series | TimescaleDB container | Dedicated metrics cluster/retention policy and capacity |
| 4. Event bus/workers | NATS JetStream and worker | Durable independently scaled workers, queues, DLQ |
| 5. Logs | Alloy + Loki containers | Central log ingestion and retention, authenticated transport |
| 6. AI analysis | Isolated AI container | Separate no-device-route ML/analysis host with sanitized data |
| 7. Secrets/backup/operations | Host-mounted secured artifacts + deployment scripts | Dedicated vault, object storage, policy control and release automation |

The first installation remains on one VM/Compose project for testing. All cross-role links use URL/TLS/secret configuration, DNS/service discovery and health contracts instead of hard-coded localhost. Replacing a local endpoint with a remote VM must not require rewriting domain/application code. Exercise independent role failure, broker replay, HA database failover, backup restore and cross-VM mTLS before production.

### C8 — Executive dashboard, reporting and local AI development reviewer (GAP-16, GAP-17)

- Executive views: total reachable/unreachable devices by site, AP and switch model, clients and experience trends, alert severity/ownership/age, poll coverage, log-ingestion gaps, audit and drift exceptions, recommendation outcomes and change/restore compliance. Do not invent KPIs or fill gaps with zeros. Support user-selected time range and site access scope.
- Exports require requester identity, effective site scope, export ID, UTC range, evidence coverage, data classification, checksum, generation SHA and audit trail; secret values and user identities are masked unless specifically authorized.
- An **AI coding/troubleshooting reviewer** is a separate service with read-only access to approved Git source and sanitized build logs. It must not access device networks, inventories containing credentials, production database connections, SQL secrets, SSH private keys or runtime device-changing endpoints. Code suggestions go through normal Git review and CI; no direct merge or deployment privileges.

### C9 — Printer intelligence completion remains a distinct bounded module (GAP-18)

Retain the existing printer P0 foundation under `docs/PRINTER_INTELLIGENCE_INTEGRATION.md`. Expand model-specific Printer-MIB knowledge, read-only scheduled SNMPv3/IPPS collection, counters by impression/sheet/color, cartridge lifecycle and real-measured consumable yield, maintenance/fault events, statistical early warning and service-desk workflow. Do not download executable drivers onto the management VM as an onboarding side effect; approved signed MIB/metadata catalogue updates are separate. Never treat an estimate as manufacturer-certified measured yield. Trained maintenance models require labeled failures and validated holdouts.

## D. Cross-service event / audit schema

Add these **proposed** versioned event envelopes, compatible with existing JetStream topics and deduplication:

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "event_type": "device.telemetry.observed",
  "occurred_at_utc": "2026-11-08T12:00:00Z",
  "emitted_at_utc": "2026-11-08T12:00:01Z",
  "actor": {"kind": "service", "id": "network-worker"},
  "subject": {"device_id": "device-uuid", "site_id": "site-uuid"},
  "correlation_id": "workflow-uuid",
  "causation_id": "job-uuid",
  "source_type": "REAL",
  "quality": "OBSERVED",
  "payload_reference": "restricted-timeseries-record-id",
  "release_commit": "git-sha",
  "idempotency_key": "device-uuid:metric:utc-window"
}
```

Other event types: `device.discovery.started`, `device.discovery.completed`, `device.poll.failed`, `device.config.drift_detected`, `config.snapshot.created`, `config.change.approved`, `config.change.applied`, `config.change.rollback_requested`, `ai.recommendation.created`, `ai.feedback.recorded`, `ai.outcome.evaluated`, `system.backup.verified`. Use encrypted storage references, not raw configs or secrets, in event bodies. Version all payloads and reject unknown mandatory fields safely.

## E. Developer work packages, dependencies and test-first completion

| Package | Implement/test next | Dependencies | Positive acceptance | Required negative / failure test |
|---|---|---|---|---|
| WP-01 | Current platform acceptance + 0006 migrations | Git-only CentOS pilot | Clean/repeat install, schema health, local/subnet HTTPS, volume persistence | Backup fails → no migration; unknown schema → stop; no other Docker project removed |
| WP-02 | Real scheduled read-only AP/CX collection | Existing guided onboarding + credential profiles | AP-505/AP-535 and CX 6100/6200 show real counters/history | REST disabled/TLS error/unreachable → explicit stale/unknown and bounded retries |
| WP-03 | Normalized syslog and AAA evidence | WP-02 | Timeline correlates logs to stable serial/site/session | No AAA data → UNKNOWN, never invented last technician |
| WP-04 | Effective configuration inheritance (planning-only) | Baselines/drafts | Global+model+site+device merge, preview, provenance and deterministic diff | Port model conflict/80 MHz/unsupported capability rejected |
| WP-05 | Vault and drift history (read-only) | WP-03/04, storage/encryption provider | Encrypted snapshot, hash, retention, isolated restore to test | Corruption/wrong firmware/secret exposure blocked |
| WP-06 | Approval and certified change executor | WP-03/04/05, hardware certification | Named requester≠approver; pre/post backup/readback; lock/rollback | Bad backup, changed hash, stale approval, dual execution → zero unauthorized writes |
| WP-07 | Compatible device replacement | WP-05/06 | Same-model restore/readback and identity mapping pass | Incorrect model/serial/firmware fails without writes |
| WP-08 | AI learning timeline + outcome loop | WP-02/03 | From rule recommendation → operator feedback → evidence-based outcome | No training labels → not trained ML; no improvement claims |
| WP-09 | **Per-device opt-in only**: default-off recommendation/shadow views, explicit enrollment/expiry, gated execution | WP-06/08 | Exactly selected devices can become eligible following policy approval and certification; other devices remain recommendation-only | Group inheritance, stale approval, unknown device, global lock, 80 MHz and adverse outcome → **zero unauthorized writes**, stop and audit |
| WP-10 | Cisco/UniFi adapter scaffolds | WP-02 adapter contracts | Unsupported operations gracefully represented | Unauthorized cross-vendor write always denied |
| WP-11 | Multi-VM scale out + reporting | Stable earlier contracts | Independent service endpoints, HA recovery and auditable exports | Isolated role/network failure and secret leakage tests |
| WP-12 | Printer model/maintenance intelligence | Printer P0 | Per-model read-only counters and service evidence | Counter rollover and unmeasured cartridge usage not fabricated |

Every package should be small enough for one AI-assisted development branch: **contract → migration (if required) → API → worker/event → React flow → unit/contract/integration/browser tests → evidence → review**. Keep a named Git branch and PR; preserve source SHA. No merge into the accepted demo until the new module passes its own gate. Keep all future pages discoverable in navigation with truthful `PLANNED / READ ONLY / CERTIFICATION REQUIRED` status, avoiding misleading clickable controls.

## F. Test-case registry (new additions)

| Case | Prerequisite | Test | Expected evidence |
|---|---|---|---|
| GAP-T01 | Real approved Aruba devices | Enroll AP-505/AP-535 and CX 6100/6200, restart collector | Saved credentials reused safely; serial dedupe; real timestamps and quality |
| GAP-T02 | Collector active | Pull network cable / reject REST auth | Failed poll and stale widget; no fabricated counters; bounded retries |
| GAP-T03 | Syslog/AAA test fixture | Direct console configuration change without accounting | Drift detected; actor UNKNOWN; source records retained |
| GAP-T04 | AAA enabled lab | Change with distinct authorized technician | VERIFIED only on defensible matching session evidence |
| GAP-T05 | Config template draft | Create common VLAN template, model and site overrides | Effective diff and field provenance deterministic |
| GAP-T06 | Wi-Fi policy profile | Submit 80 MHz and excluded channels | Refused before any adapter call; policy audit emitted |
| GAP-T07 | Vault enabled | Tamper with encrypted config object | Hash mismatch, restore blocked, alert/audit |
| GAP-T08 | Replace wizard | Restore matching backup onto incompatible device | Rejected with compatibility reason; no writes |
| GAP-T09 | Recommendation v1 | Analyze Now with incomplete/synthetic readings | RULE_BASED and missing-data disclosure; no trained ML claim |
| GAP-T10 | Labeled dataset/model | Train/evaluate temporal holdout | Dataset/model/metrics registered; model promotion approval |
| GAP-T11 | Optimization shadow mode | Simulate repeated action and adverse outcome | Cooldown and emergency stop; no automatic device writes |
| GAP-T12 | New vendor adapter | Register older Cisco or UniFi device with missing feature | Normalized inventory; unsupported operations explicit |
| GAP-T13 | Multi-VM trial | Move TimescaleDB, NATS and Loki to dedicated hosts | Config-only re-pointing; health, history and replay retained |
| GAP-T14 | Production report scope | Export site-level compliance/audit report | Scope preserved, secrets redacted, checksum and audit present |
| GAP-T15 | AI reviewer | Attempt access to Aruba IP, config secret or SQL DB | Denied by network/container mounts; audited |
| GAP-T16 | Printer model fixture | Counter reset, toner lifecycle and maintenance outcome | Unknown vs observed values preserved; no false prediction |

**GAP-T17–GAP-T24 extend GAP-T11 for explicit per-device opt-in.** They are required in WP-09 and have not been run.

**All GAP-T cases are currently `NOT_RUN` unless a separate exact-case evidence record is attached.** Unit/mocked tests are not hardware certification. Log actual environment, tester, version, build SHA, input sample, expected versus observed, screenshot/log and independent sign-off into the existing acceptance register.

## G. Definition of done and roadmap control

A feature is eligible for a phase gate only when a developer submits: design diff, schema contract and reversible upgrade path; complete API/worker/UI; positive and negative tests; signed build/source revision; audit and permission tests; cross-service telemetry evidence; on-server install/upgrade/rollback; QA sign-off; and hardware/model evidence if relevant.

**Release posture:** Existing guided read-only device onboarding and demo platform are the near-term usable base. Next practical increment is **WP-02 scheduled read-only collection**, followed by **WP-03 real logs/AAA normalization**; WP-04 inheritance can develop in parallel as a **draft-only** engine. AI learning/evaluation schemas should be established now, while real-device writes remain locked until independent Phase 4–6 acceptance.

**Open source-content blocker:** Once the external ChatGPT share text is available, append any genuinely new requirement as an additional GAP ID with source quote/section, owner, phase, test and explicit status; do not silently assume it has already been covered.
