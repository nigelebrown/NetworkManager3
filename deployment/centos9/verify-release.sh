#!/usr/bin/env bash
# Verify an extracted release package or a Git ref with the INSTALLED verifier and the trusted key store.
#   bash deployment/centos9/verify-release.sh --dir <extracted-release-dir>
#   bash deployment/centos9/verify-release.sh --ref <tag>
# Exit 0 only for a signature by a trusted, non-revoked key with every file matching and nothing unlisted.
source "$(dirname "$0")/common.sh"
DIR="" REF=""
while [[ $# -gt 0 ]]; do case "$1" in --dir) DIR="${2:-}"; shift 2 ;; --ref) REF="${2:-}"; shift 2 ;;
  *) die "unknown option $1" 2 ;; esac; done
export INM_MODE="${INM_MODE:-live}"          # verification is never skipped by this script
if [[ -n "$REF" ]]; then verify_release_ref "$REF"; elif [[ -d "$DIR" ]]; then verify_release_dir "$DIR"; else
  die "usage: verify-release.sh --dir DIR | --ref TAG" 2; fi
