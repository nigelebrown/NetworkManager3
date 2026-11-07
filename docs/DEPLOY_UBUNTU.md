# Deploying on Ubuntu (22.04 / 24.04)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nodejs npm nginx
sudo useradd -r -m -d /opt/sims sims
sudo -u sims mkdir -p /opt/sims && sudo cp -r sims-wifi-manager /opt/sims/ && sudo chown -R sims: /opt/sims
cd /opt/sims/sims-wifi-manager
sudo -u sims python3 -m venv .venv
sudo -u sims .venv/bin/pip install -r requirements.txt
(cd frontend && sudo -u sims npm ci && sudo -u sims npm run build)
sudo -u sims cp .env.example .env && sudo chmod 600 .env
```

Edit `.env`: set `SIMS_JWT_SECRET` (`python3 -c "import secrets;print(secrets.token_urlsafe(64))"`), `SIMS_FERNET_KEY`
(`.venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`),
`SIMS_ADMIN_PASSWORD`, `SIMS_MODE=live`, and `SIMS_DATABASE_URL` (PostgreSQL recommended in production).
Back up `.env`, because the Fernet key is required to read stored credentials.

`/etc/systemd/system/sims.service`:

```ini
[Unit]
Description=SIMS Wi-Fi Manager
After=network-online.target

[Service]
User=sims
WorkingDirectory=/opt/sims/sims-wifi-manager
ExecStart=/opt/sims/sims-wifi-manager/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

`sudo systemctl daemon-reload && sudo systemctl enable --now sims`

Run a single worker. The scheduler and rate limiter are in-process. nginx terminates TLS (internal CA or your
own cert) and proxies to `127.0.0.1:8000`:

```nginx
server {
  listen 443 ssl;
  server_name sims.internal.example;
  ssl_certificate     /etc/ssl/sims/fullchain.pem;
  ssl_certificate_key /etc/ssl/sims/key.pem;
  location / { proxy_pass http://127.0.0.1:8000; proxy_set_header X-Forwarded-For $remote_addr; }
}
```

Restrict access to management networks with a firewall (`ufw allow from <mgmt-subnet> to any port 443`).
Allow outbound TCP 4343 to each site's Virtual Controller over the Fortinet SD-WAN. Do not expose this to
the internet.
