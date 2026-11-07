# Immediate CentOS demo bootstrap when GitHub has docs only

**Status:** Temporary Phase 1 demonstration. The source code has **not** yet been committed to GitHub. This workflow installs a locally transferred, SHA256-verified source archive. It does not make `git pull` deliver application updates.

## 1. Download source ZIP to your workstation

Use the separately supplied `NetworkManager3_Phase1_Source_To_Push.zip` archive.

SHA256 (must match exactly):

```
27db3567d87029232638564ba31e71ce0dd0e9e08167283edbc353bc5bc1af1b
```

## 2. Transfer the ZIP to the CentOS server

Use WinSCP/SFTP or PowerShell SSH:

```powershell
scp "C:\\Users\\<you>\\Downloads\\NetworkManager3_Phase1_Source_To_Push.zip" <ssh-user>@<server-ip>:/tmp/
```

If root SSH login is disabled, send it to your normal SSH user's home directory; then run `sudo cp` to move the archive to `/root`.

Never enter GitHub passwords or personal-access tokens to install the ZIP.

## 3. Pull the updated bootstrap script

```bash
cd /root/NetworkManager3
git pull origin main
ls -l scripts/bootstrap_phase1_zip.sh
```

## 4. Install demo with one command

If the ZIP is at `/root/NetworkManager3_Phase1_Source_To_Push.zip`:

```bash
cd /root/NetworkManager3
sudo bash scripts/bootstrap_phase1_zip.sh /root/NetworkManager3_Phase1_Source_To_Push.zip --install-demo
```

The bootstrap verifies the archive hash, rejects malformed paths, checks that the manifest declares device writes LOCKED, extracts to `/opt/inm/releases/1.0.0-phase1`, runs system preflight and then executes the archive's bundled `install.sh --mode demo` and `verify.sh`.

To inspect preflight without installing: omit `--install-demo`.

**Stop and read the terminal report if any check fails.** On failure, do not bypass prerequisites by changing the release metadata or disabling controls.

## 5. Subsequent checks

```bash
cd /opt/inm/releases/1.0.0-phase1
sudo bash deployment/centos9/status.sh
sudo bash deployment/centos9/verify.sh
```

## 6. Important limitations

- This is **not** a completed GitHub source import. The source ZIP still needs to be committed from an authorized developer workstation using `scripts/import_phase1_source.ps1`.
- The installer depends on Docker registry access and resources sized for the pilot; the clean CentOS run has not been independently verified.
- The staged release is not a Git checkout; `git_commit` will be reported as `unknown`, so no formal release lineage/rollback certification is possible from this ZIP-based stage alone.
- Do not run the ZIP's `update.sh --ref` or `rollback.sh` until the source is committed to GitHub and a tagged release has passed acceptance.
- The device write lock must remain enabled.
- All services are Phase 1 demonstration / laboratory use, not production.

For formal release promotion, see `PHASE1_REVIEW_AND_RELEASE_GATE.md` and `SERVER_DEPLOYMENT_RUNBOOK.md`.
