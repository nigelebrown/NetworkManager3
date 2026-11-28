#!/usr/bin/env bash
# Offline installation / update from a release package (rc18). The package is extracted to a staging directory,
# verified with the INSTALLED verifier and trust store, and only then executed.
#   sudo bash deployment/centos9/offline-install.sh --package NetworkManager3-<ver>.zip|.tar.gz --mode live|demo [--target DIR]
source "$(dirname "$0")/common.sh"
PKG="" MODE="" TARGET="${INM_INSTALL_ROOT:-/opt/networkmanager3}"
while [[ $# -gt 0 ]]; do case "$1" in --package) PKG="$2"; shift 2 ;; --mode) MODE="$2"; shift 2 ;; --target) TARGET="$2"; shift 2 ;;
  *) die "unknown option $1" 2 ;; esac; done
[[ -f "$PKG" && ( "$MODE" == live || "$MODE" == demo ) ]] || die "usage: offline-install.sh --package FILE --mode live|demo" 2
require_root
STAGE="$(mktemp -d /var/tmp/inm-stage.XXXXXX)"; trap 'rm -rf "$STAGE"' EXIT
case "$PKG" in
  *.zip) python3 -I -c 'import sys,zipfile,pathlib
z=zipfile.ZipFile(sys.argv[1]); d=pathlib.Path(sys.argv[2]).resolve()
for n in z.namelist():
    p=(d/n).resolve()
    if d not in p.parents and p!=d: sys.exit("unsafe path in package: "+n)
z.extractall(d)' "$PKG" "$STAGE" ;;
  *.tar.gz|*.tgz) python3 -I -c 'import sys,tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], filter="data")' "$PKG" "$STAGE" ;;
  *) die "unsupported package type" 2 ;;
esac
REL="$STAGE"; [[ $(find "$STAGE" -mindepth 1 -maxdepth 1 | wc -l) -eq 1 && -d "$(find "$STAGE" -mindepth 1 -maxdepth 1)" ]] && REL="$(find "$STAGE" -mindepth 1 -maxdepth 1)"
INM_MODE="$MODE" verify_release_dir "$REL" || die "PACKAGE REFUSED: signature or integrity verification failed. Nothing was installed." 3
log "package verified; installing to $TARGET"
[[ -d "$TARGET/.git" ]] && die "$TARGET is a Git checkout: use update.sh --ref <tag> instead" 2
install -d -m 0755 "$TARGET"
cp -a "$REL"/. "$TARGET"/
INM_MODE="$MODE" exec bash "$TARGET/deployment/centos9/install.sh" --mode "$MODE"
