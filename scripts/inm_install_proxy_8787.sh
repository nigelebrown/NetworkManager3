#!/usr/bin/env bash
# Resume CentOS Stream 9 INM Docker installation behind host Nginx HTTPS reverse proxy.
# Browser: https://172.17.71.20:8787  ->  Docker: https://127.0.0.1:18787
# Never stops the existing Nginx listener on 8443, alters SSH/network IP or deletes Docker volumes.
set -Eeuo pipefail
umask 077
IP=172.17.71.20
PORT=8787
BACKEND=18787
ROOT=/opt/NetworkManager3
ENV=/etc/inm/inm.env
SITE=/etc/nginx/conf.d/inm-networkmanager-8787.conf
CRT=/etc/pki/tls/certs/inm-networkmanager-8787.crt
KEY=/etc/pki/tls/private/inm-networkmanager-8787.key
CA=/etc/pki/tls/certs/inm-networkmanager-upstream.crt
log(){ printf '[INM 8787] %s\n' "$*"; }
die(){ log "ERROR: $*" >&2; exit 1; }
[[ $EUID == 0 ]] || die 'Run as root.'
source /etc/os-release
[[ "$ID" == centos && "$VERSION_ID" == 9* ]] || die 'CentOS Stream 9 required.'
for c in ip ss dnf git python3 openssl systemctl curl firewall-cmd; do
  command -v "$c" >/dev/null || die "Missing $c."
done
IFACE="$(ip -o -4 addr show | awk -v addr="$IP" '{split($4,a,"/");if(a[1]==addr){print $2;exit}}')"
[[ -n "$IFACE" ]] || die "Host IP $IP is not assigned to an interface."
ss -H -lnt "( sport = :$PORT )" | grep -q . && die "Port $PORT already in use."
[[ ! -e "$SITE" && ! -L "$SITE" ]] || die "Proxy config $SITE already exists; refusing overwrite."
[[ -d "$ROOT/.git" && -f "$ROOT/compose.yaml" ]] || die "NetworkManager3 checkout missing at $ROOT."
origin="$(git -C "$ROOT" remote get-url origin || true)"
case "$origin" in
  https://github.com/nigelebrown/NetworkManager3|https://github.com/nigelebrown/NetworkManager3.git|git@github.com:nigelebrown/NetworkManager3.git) ;;
  *) die "Unexpected checkout origin: $origin" ;;
esac
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || die 'Git checkout contains local changes; not pulling or installing.'
if [[ -f "$ENV" ]]; then
  BIND="$(sed -n 's/^INM_WEB_BIND=//p' "$ENV" | tail -1)"
  WEB_PORT="$(sed -n 's/^INM_WEB_PORT=//p' "$ENV" | tail -1)"
  [[ "$BIND" == 127.0.0.1 && "$WEB_PORT" == "$BACKEND" ]] ||
    die "Existing INM binds $BIND:$WEB_PORT; refusing to overwrite its settings."
fi
# A prior install may have started the intended backend before proxy setup.
# Reuse it only if it belongs to this checkout and presents the installed TLS identity.
RESUME_BACKEND=false
if [[ -n "$(ss -H -lnt "( sport = :$BACKEND )")" ]]; then
  [[ -f "$ENV" && "$(sed -n 's/^INM_MODE=//p' "$ENV" | tail -1)" == live &&
     "$(sed -n 's/^INM_PROFILE=//p' "$ENV" | tail -1)" == lab-readonly ]] ||
    die "Upstream port $BACKEND is busy and no matching live INM environment was found."
  web="$(docker compose --project-name inm --project-directory "$ROOT" -f "$ROOT/compose.yaml" \
    --env-file "$ENV" ps --status running -q web)" ||
    die "Unable to identify the backend on port $BACKEND."
  [[ -n "$web" &&
     "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$web")" == "$ROOT" &&
     "$(docker port "$web" 8443/tcp)" == "127.0.0.1:$BACKEND" ]] ||
    die "Upstream port $BACKEND is busy and is not the expected INM web binding."
  curl --fail --silent --show-error --max-time 10 --cacert /etc/inm/tls/tls.crt \
    --resolve "localhost:$BACKEND:127.0.0.1" "https://localhost:$BACKEND/healthz" >/dev/null ||
    die "Existing INM backend failed its HTTPS health check."
  RESUME_BACKEND=true
fi
for f in "$CRT" "$KEY" "$CA"; do
  [[ ! -e "$f" ]] || die "Certificate path already exists: $f; refusing overwrite."
done

LOG="/root/inm-proxy-8787-$(date -u +%Y%m%dT%H%M%SZ).log"
touch "$LOG"; chmod 0600 "$LOG"
exec > >(tee -a "$LOG") 2>&1
log "Preparing https://$IP:$PORT -> https://127.0.0.1:$BACKEND."
log 'Preserving the existing Nginx listener on 8443.'
if ! command -v nginx >/dev/null; then dnf -y install nginx; fi
nginx -t || die 'Existing Nginx configuration invalid.'
grep -Eq 'include[[:space:]]+/etc/nginx/conf.d/\*\.conf;' /etc/nginx/nginx.conf ||
  die 'Nginx is not configured to include /etc/nginx/conf.d/*.conf.'
log 'Updating INM Git checkout with a fast-forward only.'
git -C "$ROOT" fetch origin main
[[ "$(git -C "$ROOT" branch --show-current)" == main ]] || die 'Git checkout is not on main.'
git -C "$ROOT" merge --ff-only origin/main

# Existing partially deployed workers must not retain public internet egress
# during rebuild. A clean install applies the same guard after network creation.
if docker network inspect inm_devices >/dev/null 2>&1; then
  log 'Restricting the existing worker to authorized private management IPs before rebuild.'
  bash "$ROOT/scripts/inm_worker_egress_guard.sh" --apply ||
    die 'Unable to restrict existing Docker worker egress. Refusing to continue.'
fi

if [[ "$RESUME_BACKEND" == true ]]; then
  log "Reusing the running INM web backend on 127.0.0.1:$BACKEND."
else
  log "Deploying Docker web on 127.0.0.1:$BACKEND."
  export INM_WEB_BIND=127.0.0.1 INM_WEB_PORT="$BACKEND"
  if ! bash "$ROOT/deployment/centos9/install.sh" --mode live; then
    log 'Docker INM installation failed; showing recent AI and Loki logs for diagnosis.'
    docker logs --tail 35 inm-ai-1 2>&1 || true
    docker logs --tail 35 inm-loki-1 2>&1 || true
    die 'Docker INM installation failed; proxy was NOT changed. Databases and volumes were preserved.'
  fi
fi
[[ -f /etc/inm/tls/tls.crt ]] || die 'INM local TLS certificate was not created.'

if [[ "$(getenforce 2>/dev/null || true)" == Enforcing ]]; then
  dnf -y install policycoreutils-python-utils || die 'SELinux tooling install failed.'
  if ! semanage port -l | awk '$1=="http_port_t" && $2=="tcp" {print $0}' |
       grep -E '(^|[ ,])8787([, -]|$)' >/dev/null; then
    if semanage port -l -C | awk '$2=="tcp" {print $0}' | grep -Eq '(^|[ ,])8787([, -]|$)'; then
      die 'TCP 8787 already has a local SELinux port assignment; review it before retrying.'
    fi
    if semanage port -l | awk '$1=="jboss_debug_port_t" && $2=="tcp" {print $0}' |
         grep -E '(^|[ ,])8787([, ]|$)' >/dev/null; then
      semanage port -m -t http_port_t -p tcp "$PORT" ||
        die 'Unable to change the default SELinux JBoss debug port assignment.'
    else
      semanage port -a -t http_port_t -p tcp "$PORT" ||
        die 'SELinux port 8787 cannot be assigned to HTTP; check any existing label.'
    fi
  fi
  setsebool -P httpd_can_network_connect on ||
    die 'Nginx SELinux loopback connectivity could not be enabled.'
fi

install -d -m 0755 /etc/pki/tls/certs /etc/pki/tls/private
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 397 \
  -subj "/CN=$IP" -addext "subjectAltName=IP:$IP" \
  -keyout "$KEY" -out "$CRT" >/dev/null 2>&1
chmod 0600 "$KEY"; chmod 0644 "$CRT"
cp /etc/inm/tls/tls.crt "$CA"; chmod 0644 "$CA"

cat > "$SITE" <<NGINX
# INM managed reverse proxy. Existing server blocks and 8443 are untouched.
server {
    listen $IP:$PORT ssl;
    server_name $IP;
    ssl_certificate $CRT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    client_max_body_size 450m;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    location / {
        proxy_pass https://127.0.0.1:$BACKEND;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_request_buffering off;
        proxy_ssl_server_name on;
        proxy_ssl_name localhost;
        proxy_ssl_trusted_certificate $CA;
        proxy_ssl_verify on;
        proxy_ssl_verify_depth 2;
    }
}
NGINX
chmod 0644 "$SITE"
if ! nginx -t; then
  rm -f "$SITE"
  die 'New Nginx proxy site failed configuration validation; site removed.'
fi
log 'Reloading existing Nginx config; not restarting its 8443 service.'
if systemctl is-active -q nginx; then
  systemctl reload nginx || die 'Could not reload Nginx.'
else
  systemctl enable --now nginx || die 'Could not start Nginx.'
fi
if systemctl is-active -q firewalld; then
  ZONE="$(firewall-cmd --get-zone-of-interface="$IFACE" 2>/dev/null | head -1 || true)"
  [[ -n "$ZONE" && "$ZONE" != "no zone" ]] || ZONE="$(firewall-cmd --get-default-zone)"
  # Apply runtime and permanent rules separately, without a global firewalld
  # reload that can disrupt Docker's dynamic DOCKER-USER / NAT chains.
  firewall-cmd --permanent --zone="$ZONE" --add-port="$PORT/tcp" || die 'Permanent firewall rule failed.'
  firewall-cmd --zone="$ZONE" --add-port="$PORT/tcp" || die 'Runtime firewall rule failed.'
  log "Opened TCP $PORT on firewall zone $ZONE without reloading Docker firewall chains."
fi
# firewalld reloads can remove runtime Docker forwarding filter entries.
# Reinstate the fail-closed worker policy and rerun full on-host acceptance.
log 'Reapplying worker egress firewall after host firewall changes.'
bash "$ROOT/scripts/inm_worker_egress_guard.sh" --install ||
  die 'Could not enforce and persist safe Docker worker egress policy.'
log 'Rechecking Docker services, device lock, public egress denial and synthetic pipeline.'
bash "$ROOT/deployment/centos9/verify.sh" ||
  die 'Post-proxy Docker verification failed; inspect the named failing check.'

curl --fail --silent --show-error --cacert "$CRT" \
  "https://$IP:$PORT/healthz" >/dev/null || die 'Proxy health check failed.'
curl --fail --silent --show-error --cacert "$CRT" \
  "https://$IP:$PORT/login" >/dev/null || die 'Proxy login page check failed.'
log "PASS: https://$IP:$PORT/"
log 'Self-signed IP certificate: browser trust warning expected until replaced by trusted cert.'
log 'Initial admin password (root only): /etc/inm/initial-admin-password'
log 'Device configuration writes remain locked. Live mode is read-only until separately certified.'
log "Log: $LOG"
