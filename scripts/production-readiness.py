#!/usr/bin/env python3
"""Read-only NetworkManager3 production GO/NO_GO gate.

Example:
 python scripts/production-readiness.py \
   --manifest /etc/inm/release-attestation.json \
   --keyring /etc/inm/trusted-reviewers.json \
   --evidence-root /var/lib/inm/release-evidence \
   --expected-commit "$(git rev-parse HEAD)" --json

Exit 0 = GO, exit 3 = NO_GO. This script never enables device writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.domain.release_readiness import evaluate  # noqa: E402


def _load(path: Path, max_bytes: int) -> object:
    if path.stat().st_size > max_bytes:
        raise ValueError("Input document exceeds maximum size")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent evidence-backed production acceptance gate")
    parser.add_argument("--manifest", type=Path, required=True, help="Signed release attestation JSON")
    parser.add_argument("--keyring", type=Path, required=True, help="Trusted reviewer Ed25519 public keys (outside git)")
    parser.add_argument("--evidence-root", type=Path, required=True, help="Stored, unredacted test evidence folder")
    parser.add_argument("--expected-commit", required=True, help="Exact full SHA deployed, not a branch name")
    parser.add_argument("--json", action="store_true", help="Print machine-readable verdict")
    args = parser.parse_args(argv)
    try:
        manifest = _load(args.manifest, 128_000)
        keyring = _load(args.keyring, 32_000)
        verdict = evaluate(manifest, evidence_root=args.evidence_root,
                           trusted_keyring=keyring, expected_commit=args.expected_commit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        verdict = {"decision": "NO_GO", "failures": [f"cannot read acceptance inputs: {type(exc).__name__}"],
                   "verified_artifacts": 0}
    if args.json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        print(f"PRODUCTION RELEASE: {verdict['decision']}")
        print(f"Verified evidence artifacts: {verdict.get('verified_artifacts', 0)}")
        for finding in verdict["failures"]:
            print(f"  [BLOCKED] {finding}")
        if verdict["decision"] == "GO":
            print("All declared release acceptance evidence is present and independently signed.")
    return 0 if verdict["decision"] == "GO" else 3


if __name__ == "__main__":
    raise SystemExit(main())
