"""Rollback / update schema compatibility gate (INTEG-P1-10, Appendix H/P).

Usage: python3 -I schema_gate.py <current-schema.json> <target-release.json>
  current-schema.json : {"management": "0004", "telemetry": "t001"} (from `backend.cli schema-status`)
  target-release.json : release.json of the release you want to run
Prints a JSON decision and exits 0 (compatible) or 3 (incompatible: do NOT start that release on this data).

A release declares which schema revisions its code can run against (accepts_*). Automatic binary rollback is
only allowed when the current database revisions are in that list. Otherwise the only safe path is restoring
the pre-update backup, which this gate reports instead of guessing.
"""
from __future__ import annotations

import json
import sys


def decide(current: dict, target: dict) -> dict:
    problems = []
    for key, accept_key in (("management", "accepts_management_schemas"), ("telemetry", "accepts_telemetry_schemas")):
        cur = current.get(key)
        accepted = target.get(accept_key)
        if not isinstance(accepted, list) or not accepted:
            problems.append(f"target release does not declare {accept_key}; refusing to guess compatibility")
        elif cur not in accepted:
            problems.append(f"{key} schema is {cur!r}; target {target.get('version')} accepts only {accepted}")
    return {
        "compatible": not problems,
        "current": current,
        "target_version": target.get("version"),
        "problems": problems,
        "manual_path": None if not problems else
        "Restore the pre-update backup taken by update.sh: sudo bash deployment/centos9/restore.sh --archive "
        "<backup> --target live, after checking out the target release. The database was not changed.",
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        current = json.load(f)
    with open(argv[2]) as f:
        target = json.load(f)
    d = decide(current, target)
    print(json.dumps(d, indent=2))
    return 0 if d["compatible"] else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
