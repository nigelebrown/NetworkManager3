#!/usr/bin/env bash
# Production release acceptance. READ-ONLY and FAIL-CLOSED.
# Existing verify.sh is a SOFTWARE health check; this script additionally
# requires physical-device acceptance, recovery/security/load evidence and
# cryptographic signoff by independent security + operations reviewers.
#
# sudo bash deployment/centos9/verify-production.sh \
#   --manifest /etc/inm/release-attestation.json \
#   --keyring /etc/inm/trusted-reviewers.json \
#   --evidence-root /var/lib/inm/release-evidence
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
require_root

MANIFEST=""
KEYRING=""
EVIDENCE=""
while (($#)); do
  case "$1" in
    --manifest)  [[ $# -ge 2 ]] || die "Missing --manifest value" 2; MANIFEST="$2"; shift 2 ;;
    --keyring)   [[ $# -ge 2 ]] || die "Missing --keyring value" 2; KEYRING="$2"; shift 2 ;;
    --evidence-root) [[ $# -ge 2 ]] || die "Missing --evidence-root value" 2; EVIDENCE="$2"; shift 2 ;;
    *) die "Unknown release verification argument: $1" 2 ;;
  esac
done
[[ -n "$MANIFEST" && -n "$KEYRING" && -n "$EVIDENCE" ]] ||
  die "Usage: sudo bash deployment/centos9/verify-production.sh --manifest FILE --keyring FILE --evidence-root DIR" 2
[[ -f "$ENV_FILE" && -f "$MANIFEST" && -f "$KEYRING" && -d "$EVIDENCE" ]] ||
  die "Missing installation env, signed manifest, trusted reviewer keyring or evidence folder" 3
[[ "$(stat -c %a "$KEYRING")" == 600 ]] ||
  die "Trusted reviewer keyring must be root-protected (mode 0600)" 3
[[ "$(env_get INM_MODE)" == live ]] ||
  die "Production acceptance requires INM_MODE=live; demo evidence is not production" 3
[[ "$(env_get INM_PROFILE)" == lab-readonly ]] ||
  die "Live acceptance requires the current lab-readonly installation profile" 3
[[ "$(env_get INM_DEMO_SIMPLE_LOGIN)" != true ]] ||
  die "Weak demo login is prohibited for production" 3
[[ "$(env_get INM_ARUBA_VERIFY_TLS)" == true ]] ||
  die "INM_ARUBA_VERIFY_TLS=true is mandatory for production hardware management" 3
[[ "$(env_get INM_AUTOMATIC_OPTIMIZATION)" != true ]] ||
  die "Autonomous RF writes must remain disabled until device-specific acceptance" 3
[[ "$(env_get INM_DEVICE_WRITE_LOCK)" != false ]] ||
  die "Run production acceptance with hardware write-lock engaged; approved operation unlocking is separate" 3
[[ "$(env_get INM_ALLOW_CONFIG_CHANGES)" != true ]] ||
  die "Run production acceptance with manual hardware writes disabled" 3

COMMIT="$(git_commit)"
[[ "$COMMIT" =~ ^[[:xdigit:]]{40}$ ]] ||
  die "Production acceptance requires a full Git commit SHA" 3
if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
  die "Uncommitted working-tree changes prevent reproducible production acceptance" 3
fi

log "Checking CentOS preflight and running application integration health checks"
bash "$(dirname "$0")/doctor.sh" || die "CentOS preflight failed" 3
bash "$(dirname "$0")/verify.sh" || die "Integration health checks failed" 3

# Runtime checks are authoritative: reading /etc/inm env alone is insufficient.
compose exec -T api python - <<'PY'
from backend.config import get_settings
s = get_settings()
assert s.mode == "live" and s.inm_profile == "lab-readonly"
assert s.aruba_verify_tls is True
assert s.require_independent_approval is True
assert s.device_writes_permitted is False
assert s.demo_simple_login is False
print("PASS: running API has verified Aruba TLS, independent approval, and a closed write gate")
PY
compose exec -T worker python - <<'PY'
from backend.config import get_settings
s = get_settings()
assert s.mode == "live" and s.aruba_verify_tls is True
assert s.device_writes_permitted is False
print("PASS: network worker has verified Aruba TLS and a closed write gate")
PY

command -v python3 >/dev/null || die "Python 3 is required for signed release validation" 3
python3 -c "import cryptography" >/dev/null 2>&1 ||
  die "Host python3-cryptography required for signed release validation (dnf install python3-cryptography)" 3

# The signing keys are NOT available to this verifier; only independent
# reviewer public keys are stored at the trusted root-owned keyring.
python3 "$ROOT/scripts/production-readiness.py" \
  --manifest "$MANIFEST" --keyring "$KEYRING" \
  --evidence-root "$EVIDENCE" --expected-commit "$COMMIT"
log "Production acceptance evidence verified: GO. No device write was enabled or executed."
