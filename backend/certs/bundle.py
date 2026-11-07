"""Validate a PEM certificate bundle (certificate chain + passphrase-protected private key) before it goes anywhere near an AP."""
from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

MAX_BUNDLE_BYTES = 64 * 1024
# The passphrase is typed into the AP CLI, so only characters that can never be interpreted by it are allowed.
_PASSPHRASE = re.compile(r"^[A-Za-z0-9!@#%^*_+=.,:\-]{1,64}$")
_KEY_BLOCK = re.compile(rb"-----BEGIN ((?:ENCRYPTED |RSA |EC )?PRIVATE KEY)-----.*?-----END \1-----", re.S)


class BundleError(ValueError):
    """Shown to the admin as-is. Never contains the passphrase or key material."""


def check_passphrase(passphrase: str) -> None:
    if not _PASSPHRASE.match(passphrase or ""):
        raise BundleError("The passphrase must be 1-64 characters from letters, digits and ! @ # % ^ * _ + = . , : - "
                          "(it is typed into the AP's command line, so spaces, quotes and other symbols are not allowed).")


def _cn(name: x509.Name) -> str | None:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def inspect_bundle(pem: bytes, passphrase: str, *, host: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Return display-safe facts about the bundle, or raise BundleError."""
    check_passphrase(passphrase)
    if not pem or len(pem) > MAX_BUNDLE_BYTES:
        raise BundleError(f"The file must be a PEM text file under {MAX_BUNDLE_BYTES // 1024} KB.")
    try:
        certs = x509.load_pem_x509_certificates(pem)
    except ValueError:
        raise BundleError("No readable PEM certificate found. Upload one PEM file containing the certificate "
                          "(and any intermediates) followed by the private key.") from None
    key_match = _KEY_BLOCK.search(pem)
    if not key_match:
        raise BundleError("The file has no private key. The AP needs the certificate and its private key in one PEM file.")
    try:
        key = serialization.load_pem_private_key(key_match.group(0), password=passphrase.encode())
    except TypeError:
        raise BundleError("The private key is not passphrase-protected. It would travel to the AP over TFTP in the clear, "
                          "so SIMS requires an encrypted key. Encrypt it first (openssl pkcs8 -topk8 or openssl rsa -aes256) "
                          "and use that passphrase.") from None
    except ValueError:
        raise BundleError("That passphrase does not decrypt the private key (or the key is damaged).") from None

    if isinstance(key, rsa.RSAPrivateKey):
        if key.key_size < 2048:
            raise BundleError("RSA keys shorter than 2048 bits are not accepted.")
        key_desc = f"RSA {key.key_size}"
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        key_desc = f"EC {key.curve.name}"
    else:
        raise BundleError("Only RSA and EC private keys are supported.")

    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    leaf = next((c for c in certs if c.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == pub), None)
    if leaf is None:
        raise BundleError("The private key does not belong to any certificate in the file.")

    now = now or datetime.now(timezone.utc)
    not_before, not_after = leaf.not_valid_before_utc, leaf.not_valid_after_utc
    if now > not_after:
        raise BundleError(f"The certificate expired on {not_after:%Y-%m-%d}.")
    if now < not_before:
        raise BundleError(f"The certificate is not valid until {not_before:%Y-%m-%d}.")

    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns, ips = list(san.get_values_for_type(x509.DNSName)), [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        dns, ips = [], []
    warnings: list[str] = []
    days_left = (not_after - now).days
    if days_left < 30:
        warnings.append(f"The certificate expires in {days_left} days.")
    if host and not _covers(host, _cn(leaf.subject), dns, ips):
        warnings.append(f"The certificate does not name '{host}' (CN/SAN). SIMS verifies TLS by name, so connections to "
                        "that address will fail verification.")
    return {
        "subject_cn": _cn(leaf.subject), "issuer_cn": _cn(leaf.issuer), "san_dns": dns[:10], "san_ip": ips[:10],
        "not_before": not_before.isoformat(), "not_after": not_after.isoformat(), "days_left": days_left,
        "key": key_desc, "chain_length": len(certs),
        "sha256": hashlib.sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest(),
        "warnings": warnings,
    }


def _covers(host: str, cn: str | None, dns: list[str], ips: list[str]) -> bool:
    try:
        ipaddress.ip_address(host)
        return host in ips
    except ValueError:
        pass
    h = host.lower()
    for n in dns + ([cn] if cn else []):
        n = n.lower()
        if n == h or (n.startswith("*.") and "." in h and h.split(".", 1)[1] == n[2:]):
            return True
    return False
