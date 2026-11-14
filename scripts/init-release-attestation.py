#!/usr/bin/env python3
"""Create an unsigned NO_GO acceptance template; cannot certify any device."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.domain import release_readiness as R  # noqa: E402
from backend.domain.adapters import INSTANT_TYPED_OPS, CX_TYPED_OPS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate a safely blocked INM production acceptance template")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--commit", required=True, help="Full expected source commit SHA")
    p.add_argument("--prepared-by", required=True, help="Named engineer; cannot also approve")
    args = p.parse_args(argv)
    if not R.SHA_RE.fullmatch(args.commit) or not args.prepared_by.strip():
        p.error("A full lowercase source SHA and named preparer are required")
    blocked = lambda: {"status": "NOT_RUN", "evidence": []}
    manifest = {
        "schema_version": 1, "release_commit": args.commit,
        "deployment_profile": "live", "prepared_by": args.prepared_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ci": {"commit": args.commit, "conclusion": "NOT_RUN",
               "run_url": "", "completed_at": ""},
        "acceptance_register": "",
        "artifacts": {},
        "phases": {p: blocked() for p in R.PHASES},
        "deployment_gates": {g: blocked() for g in R.DEPLOYMENT_GATES},
        "gap_closure": {g: blocked() for g in R.PRIMARY_GAPS},
        "device_certifications": [{
            "model": model, "serial_number": "", "firmware": "",
            "environment": "UNVERIFIED",
            "checks": {check: blocked() for check in R.DEVICE_CHECKS},
            "certified_operations": {
                op: blocked() for op in (
                    INSTANT_TYPED_OPS if model.startswith("AP-") else CX_TYPED_OPS
                )
            },
        } for model in R.MODELS],
        "signatures": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive 0600 creation: do not overwrite any reviewed/signed acceptance.
    with args.output.open("x", encoding="utf-8") as fh:
        import os
        os.fchmod(fh.fileno(), 0o600)
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"Created unsigned NO_GO template: {args.output}")
    print("No test, operation or hardware evidence is marked PASS. Independent physical testing and approval required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
