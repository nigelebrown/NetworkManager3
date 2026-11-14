# NetworkManager3 — production acceptance and release gate

**Current verdict: NO_GO.** The software CI suite is useful evidence but does not establish physical AP-505,
AP-535, CX 6100 or CX 6200 write/restore compatibility, production HA/DR resilience, external security
assessment, or independent acceptance. The platform is **not** certified production-ready on these grounds.

This gate is intentionally read-only. It cannot enable writes, connect to hardware, deploy code,
complete missing features or forge evidence. Every accepted operation still requires the existing
per-model/firmware certificate, approved change plan, backup, readback and rollback controls.

## What this branch improves

- Prevents replay of AOS-CX configuration POST/PATCH/PUT/DELETE requests after 401 or expired CSRF.
  A transient write failure instead returns an explicit auth error to the durable change recovery
  engine. GET/HEAD/OPTIONS may reauthenticate once.
- Exposes read-only, allowlisted CX optics diagnostics (`show interface transceiver detail` and
  `show interface dom`) from Device 360. The device's AnyCLI catalogue must advertise each command,
  otherwise it is UNSUPPORTED. These are **not** equivalent to live optical telemetry or DOM alerts.
- Adds an independently signed, artifact-hashed release verdict and a CentOS preflight to ensure
  that demo settings, unverified Aruba TLS, missing external evidence and partial acceptance
  cannot be presented as a production PASS.
- Preserves all 20/40 MHz channel-width policy guards. Never approve or configure 80 MHz.

**Vendor limitation:** HPE's AOS-CX 10.14 AnyCLI interface explicitly permits only a predefined
list of read-only show commands and **does not support configuration commands**.
Do not implement 802.1X/MAB or VSF writes through that endpoint; first obtain the exact
model/firmware-specific REST resource schema from `https://<device-ip>/api/v10.xx/`.
Reference: https://arubanetworking.hpe.com/techdocs/AOS-CX/10.14/HTML/rest_v10-0x/Content/Chp_AnyCLI/any-cli.htm

## Evidence required to receive GO

The `scripts/production-readiness.py` evaluator returns exit 0 (GO) **only if all conditions hold**:

1. A recent (<30-day) successful GitHub CI run matching the **exact deployed 40-character commit SHA**.
2. Every row in the updated acceptance CSV has PASS, a nonempty actual result and an artifact link;
   the register must have **at least 220 distinct tests**. Emulator claims cannot replace physical tests.
3. Every one of **11 development phases** is marked PASS with referenced SHA-256 evidence.
4. Every primary API/monitoring/configuration gap in `PRIMARY_GAPS` is closed with evidence,
   including enterprise WLAN policy, CX port authentication/VSF, firmware and hardware diagnostics.
5. One physical AP-505, AP-535, CX 6100 and CX 6200 on **actual deployed firmware** each has
   authenticated read/discovery/monitoring, logs, backups, independently approved writes,
   post-write readback, rollback and physical restoration drill results.
6. **Every typed write operation** exposed by the respective Aruba adapter has specific physical
   certification evidence; emulated write certification is NEVER an acceptable substitute.
7. HA/DR restore, independent penetration testing, representative capacity tests, release
   upgrade/rollback, credentials rotation and audit-integrity checks all have PASS evidence.
8. All supplied artifact files exist under the specified evidence root and match their
   declared SHA-256 values, with no absolute paths, path traversal or symlink files.
9. At least two **different trusted people** (one security, one operations) sign the complete
   acceptance manifest with Ed25519 keys. The code preparer cannot approve their own release.

The module checks evidence integrity and signature validity, **not the truth of a human test report**.
An authorized independent reviewer must inspect actual artifacts before signing. Do not store
signing *private* keys on the INM server, in GitHub, chat or the evidence archive.

## Prepare the documents

Generate a deliberately BLOCKED template in an offline engineering environment:

```bash
python scripts/init-release-attestation.py \
  --output /secure/work/release-attestation.json \
  --commit "$(git rev-parse HEAD)" \
  --prepared-by engineer-account
```

The template has `NOT_RUN` everywhere, blank physical serial/firmware and no signatures.
Each finished field must be supported by externally preserved test evidence. At minimum:

```json
{
  "schema_version": 1,
  "release_commit": "40-character-lowercase-git-commit",
  "deployment_profile": "live",
  "prepared_by": "engineering-operator",
  "ci": {"commit": "same-sha", "conclusion": "success",
         "run_url": "https://github.com/owner/repo/actions/runs/123",
         "completed_at": "2026-11-13T12:00:00Z"},
  "acceptance_register": "acceptance/Unified_Acceptance_Register.csv",
  "artifacts": {
    "acceptance/Unified_Acceptance_Register.csv": "file-sha256",
    "lab/AP-505/approved-readback.json": "file-sha256"
  },
  "phases": {
    "1": {"status": "PASS", "evidence": ["acceptance/Unified_Acceptance_Register.csv"]}
  },
  "signatures": []
}
```

The example above is **incomplete**; all 11 phases, gap closures, six deployment gates and all four
per-operation device certifications are mandatory. Every referenced artifact must appear in `artifacts`.
Copy the completed attestation and test files to root-controlled storage, e.g.:

- `/etc/inm/release-attestation.json` (mode 0600).
- `/etc/inm/trusted-reviewers.json` (mode 0600; **public** keys only).
- `/var/lib/inm/release-evidence/` (actual test artifacts, retained privately).

The trusted keyring schema is:

```json
{
  "reviewers": {
    "security-officer-id": {
      "role": "security",
      "ed25519_public_key": "base64-of-32-byte-raw-public-key"
    },
    "operations-officer-id": {
      "role": "operations",
      "ed25519_public_key": "base64-of-32-byte-raw-public-key"
    }
  }
}
```

**Independent signing:** Generate Ed25519 keypairs offline. Each reviewer inspects the artifacts,
individually signs the canonical JSON bytes returned by
`backend.domain.release_readiness.signed_payload(manifest)`, then adds

```json
{"reviewer": "trusted-reviewer-id", "signature": "base64-of-64-byte-Ed25519-signature"}
```

to the `signatures` array. Signatures cover everything except the array itself; any later manifest
change invalidates both signatures. Do not invent reviewer identities, evidence, certificates or
acceptance statuses.

## Run the gate on CentOS Stream 9

Before acceptance, the installed profile must be `INM_MODE=live`,
`INM_PROFILE=lab-readonly`, with both hardware write flags OFF and autonomous optimization OFF.
For production hardware management, `INM_ARUBA_VERIFY_TLS=true` must be in
`/etc/inm/inm.env` and the API and worker must trust the AP/CX certificate issuers
(system trust or a provisioned **in-container** CA bundle; `INM_ARUBA_CA_BUNDLE`).
`SIMS_ARUBA_VERIFY_TLS` in the containers is configured through `compose.yaml`.

Install the system `python3-cryptography` package on the host and do not disable TLS verification
to make the acceptance check pass. After backing up, using an approved release candidate
and running the standard healthy deployment checks, invoke:

```bash
sudo bash deployment/centos9/verify-production.sh \
  --manifest /etc/inm/release-attestation.json \
  --keyring /etc/inm/trusted-reviewers.json \
  --evidence-root /var/lib/inm/release-evidence
```

The command fails with a nonzero code if any check is missing. It has **no write path**.
It checks Docker health, service isolation, active application posture, Git commit,
file integrity, acceptance coverage, physical device certification, and reviewer signatures.

The underlying evaluator can run separately without Docker:

```bash
python scripts/production-readiness.py \
  --manifest /secure/work/release-attestation.json \
  --keyring /secure/work/trusted-reviewers.json \
  --evidence-root /secure/work/evidence \
  --expected-commit "<exact 40-character SHA>" --json
```

Do not mark the product production-ready from a passing emulator run, a screen rendering, or a
locally fabricated acceptance template. **NO_GO is the expected result until real test evidence exists.**

## Remaining external blockers

- Hardware access and controlled certification on AP-505, AP-535, CX 6100 and CX 6200.
- Reliable AP complete restore on supported firmware or approved verified recovery process.
- Missing first-party configuration functions: complete WLAN roles/captive portal/RADIUS lifecycle,
  CX 802.1X/MAB, VSF provisioning, advanced switch ACL/routing and staged firmware orchestration.
- Continuous log ingest, roaming/interference analytics, scheduled optics monitoring and full
  data-integrity/reconciliation tests.
- Independent security review, capacity planning, HA/DR failover, restored backup drills
  and signed 220-check acceptance.

See `docs/DEVICE_API_GAP_REGISTER.md` and `docs/PRODUCTION_READWRITE_PHASE_ACCEPTANCE_DIRECTIVE.md`
for the full capability and phase requirements. Work must remain in a dedicated review branch until
the owner approves merging; do not treat the existence of this gate as acceptance itself.
