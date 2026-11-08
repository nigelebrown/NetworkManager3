# NetworkManager3 — guided device onboarding (read-only pilot)

**User journey:** choose physical site + device family → choose or create credentials →
approved management IP/CIDR → test credentials → bounded discovery → review
devices → register selected real identities → capture available configuration →
open device and edit **configuration drafts**. All device writes stay LOCKED.

## Step 1: Choose site and equipment

Open **Inventory → Add device**. Select either **Aruba Instant AP**
(AP-505/AP-535) or **Aruba CX switch** (CX 6100/CX 6200), and enter the
physical location. A physical site is not silently equated with an Aruba
Instant Virtual Controller cluster.

## Step 2: Create or select reusable credentials

At **Credential profile**, select **Select existing credentials** or
**Create credential profile**.

- Profile has a human-friendly name (e.g. `Head Office Aruba CX read-only`),
  device family/protocol, device username and password.
- Profiles are shared: the same profile can be assigned to any number of
  discovered devices in that device family. Separate Instant and CX
  profiles are supported.
- Password is encrypted at rest with the existing Fernet application key.
  The API returns profile ID/name/username/status, NEVER plaintext or ciphertext.
- **Rotate password** modifies that single saved profile; every bound device
  uses the new secret on subsequent reads. Disabled profiles cannot be used.
- Only application administrators can list, create, rotate, test or disable
  profiles; device credentials are never included in audits, discovery JSON,
  configuration drafts or browser responses.
- Back up the server's existing Fernet key securely and off-host. Changing
  it without controlled re-encryption prevents decryption of stored profiles.

## Steps 3–4: Enter authorized IP/range, test and discover

- Enter a single management IPv4 or a **bounded CIDR of at most eight
  addresses per scan request** (`/29` or narrower).
- The server additionally requires an explicit private management IP
  authorization: `INM_READONLY_DEVICE_CIDRS` (the approved helper configures
  only /24 or narrower entries). The server checks **all targets before
  making any request**.
- Example for an approved private /29:
  `bash scripts/allow-readonly-device-range.sh 10.103.10.0/29`
- **Test credentials** checks a chosen individual IP without registering it.
- **Scan approved range** makes Aruba Instant REST/Aruba CX REST read-only
  requests. Errors and missing identity are shown per address.
- A large CIDR such as `128.0.0.0/9` is supported by the *offline discovery
  planner*, but **never used for unrestricted live scanning**. The planner
  paginates /24 scopes. Public scopes cannot be automatically probed.

## Step 5: Review results and register

Review each result's address, hostname, model, firmware, serial number,
authentication result and whether it is **already in inventory**.
Select only devices you approve. Unverified identities and failed probes
are not eligible for registration. Register selected devices; inventory
records are linked to the chosen reusable credential profile. Existing
real devices with the same serial are updated instead of duplicated.

**Resume later:** the discovery result and selected credential reference are
stored in the management database. Copy the displayed discovery ID and use
**Resume an earlier discovery** in the onboarding screen.

## Step 6: Import existing configuration safely

On a newly registered device, choose **Read existing configurations**.

- Aruba Instant: `show running-config` over the documented REST adapter,
  redacted before parsing, with SSID names, SSID VLAN mappings and relevant
  supported fields.
- Aruba CX: read-only REST VLAN/interface summaries (no unsupported device
  configuration assumptions).
- A snapshot is labelled **PARTIAL**. It contains no PSK, secret values, or
  full restorable original configuration.
- Unsupported firmware, TLS errors and unavailable REST calls are reported;
  the device remains registered even if a baseline read fails.

## Step 7: View and edit proposed device configuration

Open **Device details → View imported configuration / edit draft**.

- Review the imported baseline and its completeness note.
- Edit a proposed set of VLANs, SSIDs and approved 20/40 MHz channel
  widths, or Aruba CX interfaces and access VLAN mappings.
- Click **Save configuration draft**. This saves a new, auditable application
  database record with status **DRAFT**; **no configuration is sent to hardware**.
- Actual device application, approval, change windows, backup-before-write,
  device readback and rollback remain subject to future hardware
  certification and explicit change authorization.

## Alternative when a device cannot be reached

Use **Manual or CSV import** from the guided wizard. It retains the
previous one-device form and batch CSV importer, and labels operator-entered
readings **REAL · MANUAL**. This does not imply a successful REST poll or
create a controller cluster.

## Git-only CentOS update

This release adds **management schema 0006** (additive tables for credentials,
device bindings, discovery results, read-only configuration snapshots and drafts).
An encrypted backup and a tested restore path are mandatory before production.

```bash
cd /root/NetworkManager3
git pull --ff-only origin main
bash scripts/rebuild-demo.sh
```

The reload is application-scoped, preserving named data volumes and unrelated
Docker projects. First verify the latest GitHub Actions run is green and
backup restoration has been exercised on a separate test stack. A successful
GitHub test is not evidence that physical Aruba firmware/API support has been
certified or that the CentOS reload already succeeded on your server.
