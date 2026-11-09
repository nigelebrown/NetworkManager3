"""WP-05 / GAP-05 — encrypted, immutable configuration vault (pure crypto + compatibility rules).

Envelope encryption: each artifact gets a fresh data key (Fernet); the data key is wrapped by
the vault master key. The manifest (hash of plaintext, hash of ciphertext, identity) is
HMAC-signed so tampering with the object OR the manifest fails verification.

A redacted onboarding snapshot (PARTIAL / NOT_RESTORABLE) is NEVER stored or shown as a
full-fidelity artifact.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

FIDELITY_FULL = "FULL_FIDELITY"
FIDELITY_REDACTED = "REDACTED_NOT_RESTORABLE"


class VaultIntegrityError(Exception):
    pass


@dataclass(frozen=True)
class SealedArtifact:
    ciphertext: bytes
    wrapped_key: str
    key_id: str
    plaintext_sha256: str
    ciphertext_sha256: str
    manifest: dict
    signature: str


def _sign(master_key: bytes, manifest: dict) -> str:
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(hashlib.sha256(master_key).digest(), body, hashlib.sha256).hexdigest()


def key_id_of(master_key: bytes) -> str:
    return "local-" + hashlib.sha256(b"kid:" + master_key).hexdigest()[:12]


def seal(plaintext: bytes, *, master_key: bytes, identity: dict) -> SealedArtifact:
    """identity: device_id, serial, vendor, model, firmware, capture_source, fidelity."""
    if identity.get("fidelity") not in (FIDELITY_FULL, FIDELITY_REDACTED):
        raise ValueError("fidelity must be declared")
    data_key = Fernet.generate_key()
    ct = Fernet(data_key).encrypt(plaintext)
    wrapped = Fernet(master_key).encrypt(data_key).decode()
    p_hash = hashlib.sha256(plaintext).hexdigest()
    c_hash = hashlib.sha256(ct).hexdigest()
    manifest = dict(identity, plaintext_sha256=p_hash, ciphertext_sha256=c_hash,
                    key_id=key_id_of(master_key), size=len(plaintext), format_version=1)
    return SealedArtifact(ct, wrapped, manifest["key_id"], p_hash, c_hash, manifest,
                          _sign(master_key, manifest))


def open_sealed(ciphertext: bytes, *, wrapped_key: str, manifest: dict, signature: str,
                master_key: bytes) -> bytes:
    """Verify signature, ciphertext hash, decrypt, verify plaintext hash. Any mismatch -> error."""
    if not hmac.compare_digest(_sign(master_key, manifest), signature):
        raise VaultIntegrityError("Manifest signature mismatch (manifest tampered or wrong key).")
    if hashlib.sha256(ciphertext).hexdigest() != manifest["ciphertext_sha256"]:
        raise VaultIntegrityError("Encrypted object hash mismatch (object tampered).")
    try:
        data_key = Fernet(master_key).decrypt(wrapped_key.encode())
        plain = Fernet(data_key).decrypt(ciphertext)
    except InvalidToken as exc:
        raise VaultIntegrityError("Decryption failed (wrong key or corrupted object).") from exc
    if hashlib.sha256(plain).hexdigest() != manifest["plaintext_sha256"]:
        raise VaultIntegrityError("Plaintext hash mismatch.")
    return plain


def derive_master_key(secret: str) -> bytes:
    """Deterministic local-provider master key from the deployment secret (test VM provider)."""
    return base64.urlsafe_b64encode(hashlib.sha256(b"inm-vault-v1:" + secret.encode()).digest())


def _major_minor(fw: str | None) -> tuple[str, ...]:
    if not fw:
        return ()
    parts = [p for p in fw.replace("-", ".").split(".") if p]
    return tuple(parts[:3])


def restore_compatibility(*, artifact: dict, target: dict) -> dict:
    """Decide if an artifact may be restored to a target (lab or replacement). Pure; no writes."""
    reasons = []
    if artifact.get("fidelity") != FIDELITY_FULL:
        reasons.append("Artifact is redacted / not restorable.")
    if (artifact.get("vendor") or "").casefold() != (target.get("vendor") or "").casefold():
        reasons.append("Vendor differs.")
    if (artifact.get("model") or "").casefold() != (target.get("model") or "").casefold():
        compatible = {m.casefold() for m in target.get("compatible_models", [])}
        if (artifact.get("model") or "").casefold() not in compatible:
            reasons.append(f"Model {artifact.get('model')} is not compatible with {target.get('model')}.")
    a, t = _major_minor(artifact.get("firmware")), _major_minor(target.get("firmware"))
    if not a or not t:
        reasons.append("Firmware unknown on artifact or target.")
    elif a[:2] != t[:2]:
        reasons.append(f"Firmware train {'.'.join(a[:2])} differs from target {'.'.join(t[:2])}.")
    return {"compatible": not reasons, "reasons": reasons}


def redact_for_listing(manifest: dict) -> dict:
    """List endpoints expose identity/hash metadata only — never content or keys."""
    keep = ("device_id", "serial", "vendor", "model", "firmware", "capture_source", "fidelity",
            "plaintext_sha256", "ciphertext_sha256", "key_id", "size", "format_version")
    return {k: manifest.get(k) for k in keep}
