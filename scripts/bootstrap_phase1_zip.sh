#!/usr/bin/env bash
# Local Phase 1 bootstrap from the reviewed source ZIP.
# Usage:
#   sudo bash scripts/bootstrap_phase1_zip.sh /root/NetworkManager3_Phase1_Source_To_Push.zip --install-demo
# Or omit --install-demo to stage the release only.
# This is an interim source handoff. Import code into GitHub before a formal release.
set -Eeuo pipefail
umask 027

ZIP_PATH="${1:-}"
MODE="${2:---stage-only}"
TARGET="/opt/inm/releases/1.0.0-phase1"
EXPECTED_SHA256="27db3567d87029232638564ba31e71ce0dd0e9e08167283edbc353bc5bc1af1b"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "Run with sudo or as root."
[[ -n "$ZIP_PATH" && -f "$ZIP_PATH" ]] || die "Provide a local ZIP path; transfer NetworkManager3_Phase1_Source_To_Push.zip to the server first."
[[ "$MODE" == "--stage-only" || "$MODE" == "--install-demo" ]] || die "Second argument must be --stage-only or --install-demo."
command -v python3 >/dev/null || die "python3 is required."
command -v sha256sum >/dev/null || die "sha256sum is required."

GOT_SHA256="$(sha256sum "$ZIP_PATH" | awk '{print $1}')"
[[ "$GOT_SHA256" == "$EXPECTED_SHA256" ]] || die "Source package SHA256 does not match the reviewed archive. Got: $GOT_SHA256"
log "Verified source archive SHA256 $GOT_SHA256"

if [[ ! -d "$TARGET" ]]; then
  install -d -m 0755 /opt/inm/releases
  STAGE="$(mktemp -d -p /opt/inm/releases .inm-phase1.XXXXXXXX)"
  trap 'if [[ -n "${STAGE:-}" && -d "$STAGE" ]]; then rm -rf -- "$STAGE"; fi' EXIT
  python3 - "$ZIP_PATH" "$STAGE" <<'PY'
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from zipfile import ZipFile

zip_path, output_root = sys.argv[1:3]
output_root = Path(output_root)
prefix = "inm-1.0.0-phase1/"
with ZipFile(zip_path) as z:
    infos = z.infolist()
    if not infos:
        raise SystemExit("Empty source ZIP")
    for info in infos:
        name = info.filename
        if not name.startswith(prefix):
            raise SystemExit(f"Unexpected archive path: {name}")
        relative = name[len(prefix):]
        if not relative or name.endswith("/"):
            continue
        path = PurePosixPath(relative)
        if path.is_absolute() or any(p in (".", "..") for p in path.parts) or "\\" in relative:
            raise SystemExit(f"Unsafe archive path: {name}")
        if info.file_size > 5_000_000 or info.compress_size > 5_000_000:
            raise SystemExit(f"Unexpected large archive member: {name}")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xffff):
            raise SystemExit(f"Symlink forbidden in source archive: {name}")
        dest = output_root.joinpath(*path.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as inp, dest.open("wb") as out:
            import shutil
            shutil.copyfileobj(inp, out)
    required = ["release.json", "VERSION", "compose.yaml",
                "deployment/centos9/doctor.sh",
                "deployment/centos9/install.sh",
                "deployment/centos9/verify.sh",
                "backend/main.py", "frontend/package.json"]
    missing = [p for p in required if not (output_root / p).is_file()]
    if missing:
        raise SystemExit(f"Required files missing: {missing}")
    release = json.loads((output_root / "release.json").read_text())
    if release.get("version") != "1.0.0-phase1" or release.get("device_writes") != "LOCKED":
        raise SystemExit("Wrong release or device writes not locked")
print("Archive validated: 1.0.0-phase1 / DEVICE WRITES LOCKED")
PY
  mv -T -- "$STAGE" "$TARGET"
  STAGE=""
  log "Source extracted to $TARGET"
else
  log "Existing staged release preserved at $TARGET"
fi

log "Running diagnostic preflight"
if ! ( cd "$TARGET"; bash deployment/centos9/doctor.sh ); then
  die "Preflight failed. Fix the reported requirements before installing."
fi

if [[ "$MODE" == "--install-demo" ]]; then
  log "Starting Phase 1 DEMO install; no real device writes are permitted"
  cd "$TARGET"
  bash deployment/centos9/install.sh --mode demo
  bash deployment/centos9/verify.sh
  log "Demo install and verification commands completed; review the output for acceptance."
else
  log "Source staged and preflight passed."
  log "Install later with: cd $TARGET && bash deployment/centos9/install.sh --mode demo && bash deployment/centos9/verify.sh"
fi

log "NOTE: This staged ZIP is not a GitHub release. Import full source into GitHub before a tagged update or production acceptance."
