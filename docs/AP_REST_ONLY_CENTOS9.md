# Aruba Instant AP-505 / AP-535 — REST API enablement (CentOS 9)

This is a restricted REST-only management operation, NOT a general configuration unlock.
The working CentOS deployment remains authoritative; merging to GitHub does not modify it.

## GUI

1. Register the real AP-505 or AP-535, choose its management IP, and assign a saved AP credential.
2. Click **Test & Approve All** or **Approve IP & Test**. Only individual /32 IPs are approved.
3. If REST shows DISABLED or UNREACHABLE, click **Check SSH / Enable REST**.
4. NetworkManager checks SSH TCP/22 and offers device-specific legacy ssh-rsa and
   diffie-hellman-group14-sha1 only when modern negotiation fails.
5. Independently verify the SHA256 SSH host-key fingerprint, then type the required
   ENABLE REST API <device-IP> confirmation.
6. For an eligible AP with explicit /32 host approval, the fixed commands are
   configure terminal; allow-rest-api; end; commit apply. REST login is rechecked.
7. After successful REST login, **Onboard Successful** captures a read-only baseline
   and creates a read-only collection plan where supported.

**Ping alone cannot enable REST.** If TCP/22 is unavailable, the SSH bootstrap cannot
work. Restore SSH through the device console or authorized management interface and retry.
For an Instant cluster, REST is served by the master/Virtual Controller, not each member.

## Standalone CentOS Python 3 tool

The source script is scripts/inm_ap_rest_only.py. It supports CentOS Python 3.9+,
is limited to AP-505/535, requires a host-enforced /32 approval in
/var/lib/inm/approvals/applied.json, prompts for passwords, and audits actions to
/var/log/inm/ap-rest-only.jsonl.

Install Paramiko in a separate dedicated venv, not system Python:

    sudo dnf install -y python3 python3-pip
    sudo python3 -m venv /opt/inm-ap-rest-venv
    sudo /opt/inm-ap-rest-venv/bin/pip install 'paramiko==3.5.1'

Read-only port checks and SSH fingerprint discovery:

    sudo /opt/inm-ap-rest-venv/bin/python /opt/NetworkManager3/scripts/inm_ap_rest_only.py check --ip 10.190.97.211 --model AP-505

After independently checking the key fingerprint, explicitly enable REST:

    sudo /opt/inm-ap-rest-venv/bin/python /opt/NetworkManager3/scripts/inm_ap_rest_only.py enable --ip 10.190.97.211 --model AP-505 --username admin --fingerprint SHA256:REPLACE_WITH_TRUSTED_FINGERPRINT

The script prompts for the full confirmation phrase and administrator password.
It sends no arbitrary CLI input or other configuration commands.

## Compatibility and host SSH config

NetworkManager uses Paramiko, which does not read /etc/ssh/ssh_config.
Do not add legacy algorithms to every 10.x.x.x address (the entire 10.0.0.0/8 network).
For manual OpenSSH troubleshooting only, the following is an example restricted to
one approved target IP. Keep it in a separate controlled SSH client configuration:

    Host 10.190.97.211
        HostKeyAlgorithms +ssh-rsa
        PubkeyAcceptedAlgorithms +ssh-rsa
        KexAlgorithms +diffie-hellman-group14-sha1
        ServerAliveInterval 30
        ServerAliveCountMax 3

The PubkeyAcceptedAlgorithms setting is relevant to public-key login; saved INM
profiles currently use a password. Prefer modern SSH algorithms if supported.

## Test acceptance

- SSH host key verified before password use
- Device inventory model and saved profile match AP-505/535
- Host-enforced exact /32 approval is required
- Only four fixed REST-enable commands; no generic write API unlocked
- REST login on TCP 4343 must verify success after SSH changes
- Failure with unavailable SSH remains a failure, regardless of ICMP replies
- Real hardware tests are still required after software CI
