#!/usr/bin/env bash
# NetworkManager3 / Intelligent Network Manager: CentOS Stream 9 Docker installer.
# One command for a NEW installation. Optional --reset-inm explicitly removes
# known INM/SIMS application data before installing.
#
# THIS DOES NOT REINSTALL CENTOS, DELETE OTHER APPLICATIONS, OR RESET IP/SSH.
# A true CentOS Minimal reimage MUST be done from VM/IPMI console.
#
# Usage:
#   sudo bash inm_fresh_docker_centos9.sh
#   sudo bash inm_fresh_docker_centos9.sh --reset-inm --ack-data-loss
#   sudo bash inm_fresh_docker_centos9.sh --demo
set -Eeuo pipefail
umask 077

MODE=live
RESET=0
ACK=0
for arg in "$@"; do
  case "$arg" in
    --demo) MODE=demo ;;
    --reset-inm) RESET=1 ;;
    --ack-data-loss) ACK=1 ;;
    -h|--help)
      echo "Usage: sudo bash $0 [--demo] [--reset-inm --ack-data-loss]"
      exit 0 ;;
    *) echo "ERROR: Unknown option: $arg" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'ERROR: Run as sudo/root.' >&2; exit 2; }
[[ "$RESET" == "$ACK" ]] || {
  echo 'ERROR: --reset-inm must be paired with --ack-data-loss.' >&2; exit 2;
}
[[ -r /etc/os-release ]] || { echo 'ERROR: /etc/os-release missing' >&2; exit 2; }
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == centos && "${VERSION_ID:-}" == 9* ]] || {
  echo "ERROR: CentOS Stream 9 required; detected ${PRETTY_NAME:-unknown}" >&2; exit 2;
}
LOG="/root/inm-fresh-docker-$(date -u +%Y%m%dT%H%M%SZ).log"
touch "$LOG"
chmod 0600 "$LOG"
exec > >(tee -a "$LOG") 2>&1
say(){ printf '[INM FRESH] %s\n' "$*"; }
die(){ say "ERROR: $*" >&2; exit 1; }
say "Mode=$MODE, reset known INM/SIMS data=$RESET, log=$LOG"
say 'NetworkManager, host IP/route, SSH, sshd, and base OS settings are NOT modified by this script.'
if [[ "$RESET" == 1 ]]; then
  say 'WARNING: --reset-inm permanently erases identified INM/SIMS Docker volumes, /etc/inm secrets, backups and INM source checkouts.'
  say 'WARNING: Previously encrypted records cannot be recovered after their keys are erased.'
fi

for t in dnf systemctl rpm awk grep find df; do
  command -v "$t" >/dev/null || die "Missing required host command: $t"
done
[[ ! -L /etc/inm && ! -L /var/lib/inm && ! -L /opt/NetworkManager3 ]] ||
  die 'Symlink found in application destination/state; refusing installation.'

# Gather IP and SSH facts without changing them. Never print private key material.
say 'Current host management interface and route:'
if command -v ip >/dev/null; then
  ip -br -4 addr show || true
  ip -4 route show default || true
else
  say 'iproute command unavailable (will install prerequisite later).'
fi
if systemctl is-active -q sshd; then say 'sshd: ACTIVE'; else say 'WARNING: sshd not reported active. Continuing without changing it.'; fi

# Do not silently overwrite credentials or attach new secrets to old data.
if [[ "$RESET" == 0 ]]; then
  for p in /etc/inm/inm.env /etc/inm/backup.key /var/lib/inm/receipts.jsonl /opt/NetworkManager3 /root/NetworkManager3; do
    [[ ! -e "$p" ]] || die "Existing INM state $p. Use documented recovery, or explicitly rerun with --reset-inm --ack-data-loss after off-host backup."
  done
  if [[ -d /var/lib/docker/volumes ]] &&
     find /var/lib/docker/volumes -maxdepth 1 -type d -name 'inm_*' -print -quit | grep -q .; then
    die 'Old INM Docker volume directory present; new credentials may not decrypt or connect to old data.'
  fi
fi

# CentOS Stream 9 Docker CE installation: no host OS upgrade/downgrade.
say 'Installing prerequisite tools and Docker Engine + Compose plugin where needed.'
dnf -y install dnf-plugins-core ca-certificates curl git openssl python3 iproute
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
  dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker info >/dev/null || die 'Docker daemon could not be started.'
docker compose version >/dev/null || die 'Docker Compose plugin unavailable after installation.'

# Confirm that other Compose applications aren't inadvertently treated as INM.
say 'Inspecting existing Docker resources before any application cleanup.'
docker ps -a --format '{{.Names}} | project={{.Label "com.docker.compose.project"}}'
docker volume ls --format '{{.Name}}'

if [[ "$RESET" == 1 ]]; then
  say 'Removing only known INM/SIMS container, network, volume and source state.'
  IDS=()
  while read -r id name project; do
    [[ -n "$id" ]] || continue
    case "$project" in
      inm|sims|sims-wifi-manager|networkmanager3|simwifi) IDS+=("$id"); continue ;;
    esac
    case "$name" in
      inm-*|inm_*|sims-*|sims_*|simswifi-*|networkmanager3-*) IDS+=("$id") ;;
    esac
  done < <(docker ps -a --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.project"}}')
  if ((${#IDS[@]})); then docker rm -f -v "${IDS[@]}"; fi

  while IFS= read -r volume; do
    case "$volume" in
      inm_*|sims_*|sims-wifi-manager_*|networkmanager3_*|simwifi_*)
        say "Removing named INM/SIMS volume: $volume"
        docker volume rm "$volume" || die "Could not remove volume $volume; aborting cleanup."
        ;;
    esac
  done < <(docker volume ls -q)
  while IFS= read -r network; do
    case "$network" in
      inm_*|sims_*|sims-wifi-manager_*|networkmanager3_*|simwifi_*)
        docker network rm "$network" || say "WARNING: could not remove network $network (may be in use)."
        ;;
    esac
  done < <(docker network ls --format '{{.Name}}')

  # Remove known application image tags only; no global image/volume pruning.
  while IFS= read -r tag; do
    case "$tag" in inm/*|sims/*|networkmanager3/*)
      docker image rm "$tag" >/dev/null 2>&1 || say "WARNING: image $tag remains (in use or protected)" ;;
    esac
  done < <(docker images --format '{{.Repository}}:{{.Tag}}')

  for p in /opt/NetworkManager3 /root/NetworkManager3; do
    if [[ -d "$p" ]]; then
      [[ -d "$p/.git" && -f "$p/compose.yaml" ]] ||
        die "Unrecognized directory at $p; manual inspection required, refusing delete."
      origin="$(git -C "$p" remote get-url origin 2>/dev/null || true)"
      case "$origin" in
        https://github.com/nigelebrown/NetworkManager3|https://github.com/nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git|github-networkmanager3:nigelebrown/NetworkManager3.git)
          say "Removing old INM Git checkout $p"
          rm -rf -- "$p" ;;
        *) die "Unrecognized Git origin in $p; refusing delete." ;;
      esac
    fi
  done
  for p in /etc/inm /var/lib/inm /var/log/inm; do
    if [[ -d "$p" ]]; then say "Removing INM state directory: $p"; rm -rf -- "$p"; fi
  done
  say 'Old known INM/SIMS state removed; external data and other applications untouched.'
fi

# Important: refuse a new install while unexpected resources could imply
# a second or legacy installation; don't silently attach to existing data.
if docker ps -a --format '{{.Label "com.docker.compose.project"}}' |
   grep -E '^(inm|sims|sims-wifi-manager|networkmanager3|simwifi)$' >/dev/null; then
  die 'An old INM/SIMS Compose project is still present. Refusing to continue.'
fi
if docker volume ls -q | grep -E '^(inm|sims|sims-wifi-manager|networkmanager3|simwifi)[_-]' >/dev/null; then
  die 'An old INM/SIMS volume remains. Refusing to reuse it.'
fi
for p in /etc/inm/inm.env /etc/inm/backup.key /var/lib/inm/receipts.jsonl; do
  [[ ! -e "$p" ]] || die "Old INM state remains at $p. Refusing to regenerate keys."
done

INSTALL_DIR=/opt/NetworkManager3
[[ ! -e "$INSTALL_DIR" ]] || die "Installation target exists: $INSTALL_DIR"
say "Cloning NetworkManager3 main to $INSTALL_DIR"
git clone --depth 1 --branch main https://github.com/nigelebrown/NetworkManager3.git "$INSTALL_DIR"
cd "$INSTALL_DIR"
COMMIT="$(git rev-parse HEAD)"
say "Commit=$COMMIT"
say 'Installing INM live/read-only Docker stack using the repository deployment installer.'
bash deployment/centos9/install.sh --mode "$MODE"

say 'Running additional one-click software verification (NO hardware access).'
bash scripts/inm_one_click_test.sh --software-only
say 'SUCCESS: INM Docker installation and software verification finished.'
say 'Hardware tests must be authorized and configured separately. Device writes remain locked.'
say 'The web interface binds to 127.0.0.1:8443 by default; use SSH port forwarding for remote access.'
say "Install log: $LOG"
