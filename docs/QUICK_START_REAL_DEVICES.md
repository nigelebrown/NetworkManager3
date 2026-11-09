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

## B2. Access from PCs on an approved local subnet (same demo password)

This optional operation **does not change** the existing `admin / demo123!`
credential or any database volume. It binds HTTPS to an assigned private
CentOS interface IP, enables the corresponding Nginx IP allowlist and adds a
narrow firewalld rule where available. It refuses arbitrary 0.0.0.0 binding,
public IPs, and broad CIDRs.

On CentOS, after fetching the latest GitHub changes:

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/enable-lan-demo.sh 172.17.71.21 172.17.71.0/24
```

Use the real server IP and **your actual allowed client subnet**; the /24
above is only an example. If the interface is already configured with a
private /24-or-narrower netmask, you can omit the second argument and
let the script detect its subnet:

```bash
bash scripts/enable-lan-demo.sh 172.17.71.21
```

**Existing database schemas:** A rebuild may introduce a newer schema. For
an existing demo at management schema `0004`, this LAN command now backs up
the management database, TimescaleDB, JetStream, Loki and app state into an
encrypted archive, verifies the archive checksum, and applies the additive
`0004 → 0005` migration through the one-shot `migrate` service **before**
restarting the API. It checks the management and telemetry revisions against
`release.json` and refuses unknown revisions. If a backup or migration
fails, do not delete volumes or manually downgrade the schema; inspect the
reported backup path and failure. Verifying an archive checksum is not a
substitute for a separate test-restore.

After the script reports success, a PC in the approved subnet can browse to:

```text
https://172.17.71.21:8443
Username: admin
Password: demo123!
```

The initial self-signed certificate was generated for localhost; a direct
IP address can trigger both a trust error and a hostname/SAN mismatch.
For broader access, deploy a certificate for the correct DNS name signed
by a trusted internal CA. The Nginx allowlist remains enforced even if
Docker's published ports bypass some host firewall chains.

**Security trade-off:** `demo123!` is intentionally weak. Restrict this demo
to an isolated/trusted management VLAN, never expose port 8443 on the
internet, and do not store operational credentials in its demo account.
This permission is temporary and must not be reused for production.

The single-command `scripts/rebuild-demo.sh` now detects an **existing
approved LAN demo** and delegates to `scripts/enable-lan-demo.sh`, which
retains the restricted private IP, CIDR-based Nginx access control and
`admin / demo123!` password. It refuses unrestricted binds and does not
weaken the global device-write lock. It updates the API and React web image
and migrates the management schema if necessary without deleting volumes.

To update an approved existing LAN demo directly from Git:

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/rebuild-demo.sh
```

Once it completes, open `https://<SERVER_LAN_IP>:8443/onboarding` and
select **Inventory → Add device — 7-step wizard**. For manual registration
or a CSV upload, select **Manual / CSV import** in the Inventory menu.

If you see a refusal about an unapproved LAN binding, inspect only the
nonsecret settings with:

```bash
grep -E '^(INM_PROFILE|INM_WEB_BIND|INM_LAN_CLIENT_CIDR|INM_LAN_DEMO_ACK|INM_DEMO_SIMPLE_LOGIN)=' /etc/inm/inm.env
ip -4 -br addr
```

Do **not** set `INM_WEB_BIND=0.0.0.0` or disable the CIDR allowlist just
to keep the temporary demo password.

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
This helper permits only private IPv4 /24–/32 targets and now supports
both localhost demos and existing **approved CIDR-restricted LAN demos**. It
preserves earlier approved target scopes and leaves `admin / demo123!` unchanged.
The default target allowlist is empty, so read-only device access is blocked
until an administrator explicitly approves a target.

For the initial guided AP-505 test at **10.192.57.55**, use:

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/allow-readonly-device-range.sh 10.192.57.55/32
```

Then refresh **Inventory → Add device — 7-step wizard** and in Step 4
choose **Test credentials (read only)** followed by **Discover test IP only**.
The selected discovery target becomes `10.192.57.55/32`.

To approve other APs in that subnet for subsequent small-batch testing,
an administrator may approve `10.192.57.0/24`, but **the active scan is
still limited to eight IP addresses per request**. Enter /29 or narrower
target ranges; a full /24 sweep is not implemented. The system does not
turn on live background polling and never performs device configuration writes.

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
