#!/usr/bin/env bash
# Trusted release-key management on the server (rc18). The owner's PRIVATE key never comes here.
#   sudo bash deployment/centos9/release-trust.sh init   --public owner.pub --fingerprint <sha256 from the owner>
#   sudo bash deployment/centos9/release-trust.sh rotate --statement rotation.json   (signed by a trusted key)
#   sudo bash deployment/centos9/release-trust.sh status
# init also installs the verifier to $VERIFIER and records its hash; install/update/rollback use that copy.
source "$(dirname "$0")/common.sh"
CMD="${1:-}"; shift || true
require_root
case "$CMD" in
  init)
    PUB="" FP=""
    while [[ $# -gt 0 ]]; do case "$1" in --public) PUB="$2"; shift 2 ;; --fingerprint) FP="$2"; shift 2 ;; *) die "unknown option $1" 2 ;; esac; done
    [[ -f "$PUB" && -n "$FP" ]] || die "usage: release-trust.sh init --public owner.pub --fingerprint <sha256>" 2
    install -d -m 0755 "$(dirname "$VERIFIER")"
    install -m 0644 "$ROOT/scripts/release_signing.py" "$VERIFIER"
    python3 -I "$VERIFIER" trust-init --store "$TRUST_DIR" --public "$PUB" --fingerprint "$FP"
    sha256sum "$VERIFIER" | cut -d' ' -f1 > "$TRUST_DIR/verifier.sha256"; chmod 0644 "$TRUST_DIR/verifier.sha256"
    log "release trust initialised in $TRUST_DIR by $(operator_name)" ;;
  rotate)
    [[ "${1:-}" == --statement && -f "${2:-}" ]] || die "usage: release-trust.sh rotate --statement rotation.json" 2
    _verifier_ok || die "verifier missing or modified" 3
    python3 -I "$VERIFIER" rotate --store "$TRUST_DIR" --statement "$2"
    log "release key rotation applied by $(operator_name)" ;;
  status)
    _verifier_ok && log "verifier OK ($VERIFIER)"
    python3 -I -c 'import json,sys;d=json.load(open(sys.argv[1]));[print(k,v["fingerprint"],"REVOKED" if v["revoked"] else "trusted",v["via"]) for k,v in d["keys"].items()]' "$TRUST_DIR/trust.json" ;;
  *) die "usage: release-trust.sh init|rotate|status" 2 ;;
esac
