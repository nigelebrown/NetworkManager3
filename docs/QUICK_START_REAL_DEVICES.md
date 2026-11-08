# Quick-start: usable AP and switch demo from GitHub (CentOS Stream 9)

The complete **Intelligent Network Manager** source, Docker Compose files, web interface,
migration scripts and test code live in the private GitHub repository
`nigelebrown/NetworkManager3`. **No PC source transfers or ZIP files are required.**

## A. Single-command reload on the existing CentOS server

The server already has an authorized read-only GitHub SSH deploy key and a checkout
at `/root/NetworkManager3`. Copy and paste:

```bash
cd /root/NetworkManager3 && git pull --ff-only origin main && bash scripts/rebuild-demo.sh
```

If no checkout exists, first clone once:

```bash
git clone github-networkmanager3:nigelebrown/NetworkManager3.git /root/NetworkManager3
```

The reload command will:

1. Refuse a dirty Git worktree or an unexpected repository.
2. Confirm an existing installation is a **loopback-bound DEMO**, never a production deployment.
3. When an existing INM stack is running, make an encrypted **pre-rebuild backup**. Abort without removing anything if it fails.
4. Fetch the latest main commit from GitHub using a fast-forward-only update.
5. Stop and replace **only Compose project `inm` containers**, **never** delete Docker named volumes, other projects, or the global image cache.
6. Check CentOS resources, install Docker/Compose if needed, generate/store secrets outside Git, pull approved base images and rebuild the frontend/backend/AI images from Git.
7. Start management PostgreSQL, TimescaleDB, NATS, Loki, Alloy, API, AI, React/Nginx and worker.
8. Enable **simple demo-only credentials**, recreate API and persist the admin password hash.
9. Execute the built-in checks: network isolation, secret file protection, device-write lock, API, service health, a synthetic syslog round-trip and NATS → worker → databases → UI event round-trip.

It preserves data volumes and records. **It does not reset or delete any Docker volumes.**
If an existing INM database is damaged and the backup fails, stop and investigate instead of wiping it.

## B. Access and log in

With the deliberately weak demo password, the HTTPS web endpoint is **local-only**
(`127.0.0.1:8443`). From an authorized computer with SSH access, open an SSH tunnel:

```bash
ssh -L 8443:127.0.0.1:8443 root@<CENTOS-SERVER-IP>
```

Then open **https://localhost:8443** in that computer's browser. Your pilot uses a
self-signed TLS certificate; verify its fingerprint against the server certificate.
Do not bypass certificate warnings on an untrusted network.

- **Username:** `admin`
- **Demo-only password:** `demo123!`

This weak login is deliberately refused if the web bind is changed from localhost,
if the software is no longer in demo mode, or if device configuration writes are enabled.
It MUST be replaced before exposing the service on the LAN or moving to production.

## C. Add a real AP or switch immediately — no network connection required

1. In the left menu, open **Inventory → Add device**.
2. Select **Access point** (AP-505 / AP-535) or **Switch** (CX 6100 / CX 6200).
3. Choose **Enter device details & readings**.
4. Enter site name, hostname, serial number, model, management IP (optional), firmware and observed metrics from the real device.
5. Click **Save real device and readings**.

Your device is stored in PostgreSQL with `source_type=REAL`, and numeric readings
are stored in TimescaleDB with a `MANUAL_ENTRY` provenance. It is not silently
passed off as an API-verified observation or simulated demo fixture.

Open **Access points** or **Switches** to see the imported details and readings.
Repeat with the same device serial to update its identity and append a new observation.

## D. Optional: read from the device via Aruba REST API

**Only when the AP or switch management address is reachable from the CentOS server**.
The server administrator must explicitly authorize a narrow subnet/individual IP.
For example, to allow just one AP and one switch:

```bash
cd /root/NetworkManager3
bash scripts/allow-readonly-device-range.sh 10.103.5.20/32,10.103.5.21/32
```

Replace those two sample IPs with your actual management addresses.
This helper permits only private IPv4 targets and at most a /24 range;
the default allowlist is empty, so device polling is disabled until configured.

Then visit **Inventory → Add device → Read directly from device (REST API)**.
Supply management IPv4, model, serial, name, site and a read-only device username/password.
Passwords are used for this one authenticated request only and **not stored**.
The application makes only vendor session-login/logout calls and read-only
`show`/GET calls. It never invokes configuration writes.

- **Aruba Instant**: ArubaOS 8.10 REST API on HTTPS port 4343, `show aps`,
  `show clients`, `show version` and supported read-only RF/system commands.
  The Instant REST API must already be enabled; this release will not enable it for you.
- **Aruba CX 6100 / 6200**: AOS-CX REST `/rest/v10.14` authenticated login,
  system identity and interface status reads over HTTPS 443.
  Device firmware/model API support must be checked on the actual hardware.

TLS certificate validation remains enabled by default. Configure a trusted device
certificate/CA where necessary; do not disable TLS validation in a production network.
The current read is a **one-off snapshot**, not continuous collection. Future live
polling, deeper interface statistics and configuration workflows remain separately gated.

## E. Check the installation

```bash
cd /root/NetworkManager3
bash scripts/inm.sh verify
bash scripts/inm.sh status
```

Additional diagnostics (on CentOS):

```bash
docker compose --project-name inm --project-directory /root/NetworkManager3 \
  -f /root/NetworkManager3/compose.yaml --env-file /etc/inm/inm.env ps
```

Log files: `/var/log/inm/install-*.log`. The private app secrets and backup
encryption key live in `/etc/inm`, not Git. Preserve an off-host copy of the backup key.

## F. Before production

Do not expose `demo123!` via a LAN- or internet-bound endpoint.
Require strong passwords or SSO/MFA, individual accounts, TLS trust, site permissions,
separate audit approval, tested backup/restore, reviewed Git release tags and
actual AP/CX hardware certification. Device writes remain disabled.
