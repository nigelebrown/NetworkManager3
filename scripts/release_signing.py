#!/usr/bin/env python3
"""Release signing, trusted-key management and verification (rc18, P11-DEP-06). Ed25519 over a SHA-256 manifest.

Owner (offline workstation; the private key never reaches a server):
  keygen   --private PATH --public PATH          new key pair (private key 0600, never printed)
  sign     --private PATH --dir DIR              DIR/SHA256SUMS + DIR/SHA256SUMS.sig ("ed25519 <key-id> <b64>")
  sign-git --private PATH --repo DIR --ref REF --out DIR
                                                 export REF with `git archive`, sign it, write the two files to --out
                                                 so they can be committed at the tree root before tagging
  rotation --private CURRENT --new-public NEW [--revoke-current] --out FILE
                                                 rotation statement signed by a currently trusted key

Server (root; store default /etc/inm/release-keys):
  trust-init --store DIR --public PATH --fingerprint SHA256
                                                 first trust anchor; the fingerprint must be confirmed out of band
  rotate     --store DIR --statement FILE        add a key (and optionally revoke one) ONLY via a statement signed
                                                 by a key that is trusted and not revoked
  verify     --store DIR --dir DIR [--require-signature]
                                                 exit 0 only when the signature is by a trusted, non-revoked key, the
                                                 store is untampered, every listed hash matches and nothing is unlisted
  fingerprint --public PATH                      print the key id / fingerprint
Exit codes: 0 verified · 2 verification failed · 3 trust store problem · 4 usage.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SUMS, SIG = "SHA256SUMS", "SHA256SUMS.sig"
TRUST = "trust.json"
EXCLUDE = {SUMS, SIG}


class TrustError(Exception):
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.is_file() and not (p.parent == d and p.name in EXCLUDE))


def manifest(d: Path) -> bytes:
    return "".join(f"{_sha(p)}  {p.relative_to(d).as_posix()}\n" for p in _files(d)).encode()


def _pub_raw(pub: Ed25519PublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(pub: Ed25519PublicKey) -> str:
    return hashlib.sha256(_pub_raw(pub)).hexdigest()


def key_id(pub: Ed25519PublicKey) -> str:
    return fingerprint(pub)[:16]


def _load_pub(path: Path) -> Ed25519PublicKey:
    k = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(k, Ed25519PublicKey):
        raise TrustError(f"{path} is not an Ed25519 public key")
    return k


def _load_priv(path: Path) -> Ed25519PrivateKey:
    k = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise TrustError("private key is not Ed25519")
    return k


# ------------------------------------------------------------------ owner side
def keygen(private: Path, public: Path) -> str:
    if private.exists():
        raise SystemExit(f"refusing to overwrite {private}")
    k = Ed25519PrivateKey.generate()
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
    public.write_bytes(k.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo))
    return fingerprint(k.public_key())


def sign(private: Path, d: Path, out: Path | None = None) -> str:
    k = _load_priv(private)
    m = manifest(d)
    out = out or d
    (out / SUMS).write_bytes(m)
    (out / SIG).write_text(f"ed25519 {key_id(k.public_key())} {base64.b64encode(k.sign(m)).decode()}\n")
    return key_id(k.public_key())


def export_git(repo: Path, ref: str, dest: Path) -> None:
    """Exactly the tracked tree of REF (no .git, no untracked or ignored files)."""
    proc = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref], check=True, capture_output=True)
    with tempfile.TemporaryFile() as tf:
        tf.write(proc.stdout)
        tf.seek(0)
        with tarfile.open(fileobj=tf) as t:
            for m in t.getmembers():
                if m.issym() or m.islnk() or ".." in Path(m.name).parts or m.name.startswith("/"):
                    raise TrustError(f"unsafe archive member {m.name}")
            t.extractall(dest, filter="data")


def rotation(private: Path, new_public: Path, revoke_current: bool, out: Path) -> None:
    cur = _load_priv(private)
    new = _load_pub(new_public)
    body = {"type": "inm-release-key-rotation/v1", "signed_by": key_id(cur.public_key()),
            "new_key_id": key_id(new), "new_fingerprint": fingerprint(new),
            "new_public_pem": new_public.read_text(), "revoke": [key_id(cur.public_key())] if revoke_current else [],
            "created_at": datetime.now(timezone.utc).isoformat()}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    out.write_text(json.dumps({"statement": body, "signature": base64.b64encode(cur.sign(raw)).decode()}, indent=1))


# ------------------------------------------------------------------ trust store (server side)
def _store_digest(entries: dict) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_store(store: Path) -> dict:
    """Trust store integrity: every key file must match its recorded fingerprint, no unrecorded key file may exist,
    and the history chain must be intact. A swapped or added .pub file is an unauthorized key replacement."""
    path = store / TRUST
    if not path.is_file():
        raise TrustError(f"no trust store at {store}: run trust-init with the owner's public key")
    data = json.loads(path.read_text())
    keys = data.get("keys", {})
    prev = "0" * 64
    for h in data.get("history", []):
        expect = hashlib.sha256((prev + json.dumps(h["event"], sort_keys=True)).encode()).hexdigest()
        if h.get("chain") != expect:
            raise TrustError("trust history chain broken: store edited outside release_signing.py")
        prev = expect
    if data.get("digest") != _store_digest(keys) or data.get("head") != prev:
        raise TrustError("trust store digest mismatch: keys edited outside release_signing.py")
    recorded = set()
    for kid, meta in keys.items():
        f = store / f"{kid}.pub"
        if not f.is_file():
            raise TrustError(f"trusted key file {f.name} missing")
        if fingerprint(_load_pub(f)) != meta["fingerprint"]:
            raise TrustError(f"key file {f.name} does not match its recorded fingerprint (unauthorized replacement)")
        recorded.add(f.name)
    extra = sorted(p.name for p in store.glob("*.pub") if p.name not in recorded)
    if extra:
        raise TrustError(f"unrecorded key files in the trust store: {extra}")
    return data


def _save_store(store: Path, data: dict, event: dict) -> None:
    prev = data.get("head", "0" * 64)
    chain = hashlib.sha256((prev + json.dumps(event, sort_keys=True)).encode()).hexdigest()
    data.setdefault("history", []).append({"event": event, "chain": chain})
    data["head"], data["digest"] = chain, _store_digest(data["keys"])
    tmp = store / (TRUST + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, store / TRUST)


def trust_init(store: Path, public: Path, expected_fp: str) -> str:
    store.mkdir(parents=True, exist_ok=True, mode=0o755)
    if (store / TRUST).exists():
        raise TrustError("trust store already initialised; add keys only with a signed rotation statement")
    pub = _load_pub(public)
    fp = fingerprint(pub)
    if expected_fp.strip().lower().replace(":", "") != fp:
        raise TrustError(f"fingerprint mismatch: file is {fp}; confirm the owner's fingerprint out of band")
    kid = key_id(pub)
    (store / f"{kid}.pub").write_bytes(public.read_bytes())
    data = {"schema": 1, "keys": {kid: {"fingerprint": fp, "added_at": datetime.now(timezone.utc).isoformat(),
                                        "revoked": False, "via": "trust-init"}}}
    _save_store(store, data, {"action": "trust-init", "key_id": kid, "fingerprint": fp})
    return kid


def rotate(store: Path, statement_file: Path) -> dict:
    data = load_store(store)
    doc = json.loads(statement_file.read_text())
    body, sig = doc["statement"], base64.b64decode(doc["signature"], validate=True)
    if body.get("type") != "inm-release-key-rotation/v1":
        raise TrustError("not a rotation statement")
    signer = data["keys"].get(body["signed_by"])
    if signer is None or signer["revoked"]:
        raise TrustError("rotation is not signed by a trusted, non-revoked key")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    try:
        _load_pub(store / f"{body['signed_by']}.pub").verify(sig, raw)
    except InvalidSignature:
        raise TrustError("rotation statement signature is invalid") from None
    new = serialization.load_pem_public_key(body["new_public_pem"].encode())
    if not isinstance(new, Ed25519PublicKey) or fingerprint(new) != body["new_fingerprint"] or \
            key_id(new) != body["new_key_id"]:
        raise TrustError("rotation statement key material does not match its fingerprint")
    kid = body["new_key_id"]
    if kid in data["keys"]:
        raise TrustError("key already trusted")
    (store / f"{kid}.pub").write_text(body["new_public_pem"])
    data["keys"][kid] = {"fingerprint": body["new_fingerprint"], "added_at": datetime.now(timezone.utc).isoformat(),
                         "revoked": False, "via": f"rotation signed by {body['signed_by']}"}
    for r in body.get("revoke", []):
        if r in data["keys"]:
            data["keys"][r]["revoked"] = True
    if not any(not k["revoked"] for k in data["keys"].values()):
        raise TrustError("rotation would leave no trusted key")
    _save_store(store, data, {"action": "rotate", "signed_by": body["signed_by"], "added": kid,
                              "revoked": body.get("revoke", [])})
    return {"added": kid, "revoked": body.get("revoke", [])}


def verify(store: Path, d: Path) -> list[str]:
    """Return problems (empty = verified)."""
    try:
        data = load_store(store)
    except (TrustError, ValueError, KeyError) as exc:
        return [f"trust store: {exc}"]
    if not (d / SUMS).is_file() or not (d / SIG).is_file():
        return ["SHA256SUMS or SHA256SUMS.sig missing: unsigned release"]
    parts = (d / SIG).read_text().split()
    if len(parts) != 3 or parts[0] != "ed25519":
        return ["signature file format not recognised (expected: ed25519 <key-id> <signature>)"]
    kid, b64 = parts[1], parts[2]
    meta = data["keys"].get(kid)
    if meta is None:
        return [f"signed by unknown key {kid}"]
    if meta["revoked"]:
        return [f"signed by revoked key {kid}"]
    listed = (d / SUMS).read_bytes()
    try:
        _load_pub(store / f"{kid}.pub").verify(base64.b64decode(b64, validate=True), listed)
    except (InvalidSignature, ValueError):
        return ["signature does not match SHA256SUMS (tampered manifest or wrong key)"]
    problems, names = [], set()
    for line in listed.decode().splitlines():
        digest, _, name = line.partition("  ")
        names.add(name)
        p = d / name
        if ".." in Path(name).parts or name.startswith("/") or not p.is_file():
            problems.append(f"missing: {name}")
        elif _sha(p) != digest:
            problems.append(f"hash mismatch: {name}")
    problems += [f"unlisted file: {p.relative_to(d).as_posix()}" for p in _files(d)
                 if p.relative_to(d).as_posix() not in names]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_store = Path(os.environ.get("INM_RELEASE_TRUST_DIR", "/etc/inm/release-keys"))
    g = sub.add_parser("keygen"); g.add_argument("--private", type=Path, required=True); g.add_argument("--public", type=Path, required=True)
    s = sub.add_parser("sign"); s.add_argument("--private", type=Path, required=True); s.add_argument("--dir", type=Path, required=True)
    sg = sub.add_parser("sign-git"); sg.add_argument("--private", type=Path, required=True); sg.add_argument("--repo", type=Path, required=True)
    sg.add_argument("--ref", required=True); sg.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("rotation"); r.add_argument("--private", type=Path, required=True); r.add_argument("--new-public", type=Path, required=True)
    r.add_argument("--revoke-current", action="store_true"); r.add_argument("--out", type=Path, required=True)
    ti = sub.add_parser("trust-init"); ti.add_argument("--store", type=Path, default=default_store)
    ti.add_argument("--public", type=Path, required=True); ti.add_argument("--fingerprint", required=True)
    ro = sub.add_parser("rotate"); ro.add_argument("--store", type=Path, default=default_store); ro.add_argument("--statement", type=Path, required=True)
    v = sub.add_parser("verify"); v.add_argument("--store", type=Path, default=default_store); v.add_argument("--dir", type=Path, required=True)
    vg = sub.add_parser("verify-git"); vg.add_argument("--store", type=Path, default=default_store)
    vg.add_argument("--repo", type=Path, required=True); vg.add_argument("--ref", required=True)
    f = sub.add_parser("fingerprint"); f.add_argument("--public", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            print(f"fingerprint: {keygen(a.private, a.public)}"); return 0
        if a.cmd == "sign":
            print(f"signed {len(_files(a.dir))} files with key {sign(a.private, a.dir)}"); return 0
        if a.cmd == "sign-git":
            with tempfile.TemporaryDirectory() as t:
                export_git(a.repo, a.ref, Path(t))
                a.out.mkdir(parents=True, exist_ok=True)
                print(f"signed {len(_files(Path(t)))} tracked files of {a.ref} with key {sign(a.private, Path(t), a.out)}; "
                      f"commit {SUMS} and {SIG} at the repository root, then tag")
            return 0
        if a.cmd == "rotation":
            rotation(a.private, a.new_public, a.revoke_current, a.out); print(f"statement written to {a.out}"); return 0
        if a.cmd == "trust-init":
            print(f"trusted key {trust_init(a.store, a.public, a.fingerprint)}"); return 0
        if a.cmd == "rotate":
            print(json.dumps(rotate(a.store, a.statement))); return 0
        if a.cmd == "fingerprint":
            k = _load_pub(a.public); print(f"key-id {key_id(k)} fingerprint {fingerprint(k)}"); return 0
        if a.cmd == "verify-git":
            with tempfile.TemporaryDirectory() as t:
                export_git(a.repo, a.ref, Path(t))
                problems = verify(a.store, Path(t))
                n = len(_files(Path(t)))
        else:
            problems = verify(a.store, a.dir)
            n = len(_files(a.dir))
    except TrustError as exc:
        print(f"TRUST STORE ERROR — refusing: {exc}", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"git export failed: {exc.stderr.decode()[:200] if exc.stderr else exc}", file=sys.stderr)
        return 2
    if problems:
        print("RELEASE VERIFICATION FAILED — do not install:", *problems, sep="\n  ", file=sys.stderr)
        return 2
    print(f"release verified: {n} files, signature OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
