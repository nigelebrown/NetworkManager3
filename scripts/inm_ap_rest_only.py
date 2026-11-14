#!/usr/bin/env python3
"""CentOS Python 3.9+ utility: check or enable REST on approved Aruba Instant AP-505/AP-535.

Uses Paramiko, NOT global /etc/ssh/ssh_config. Normal algorithms first; retries
only ssh-rsa + diffie-hellman-group14-sha1 when the server requires it.
The exact device IP must already be host-approved (/32) by NetworkManager.
No wildcard 10.x.x.x SSH rules, arbitrary CLI, switches or configuration writes.

Run with a dedicated Python virtualenv containing paramiko; no secrets passed
in command-line arguments, written to disk, or returned in output. This utility
is intentionally independent of the Python 3.13 backend and runs on CentOS 9.
"""
import argparse
import base64
import datetime
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

ALLOWED_AP_MODELS = ("AP-505", "AP-535")
PRIVATE_NETS = tuple(ipaddress.ip_network(x) for x in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
APPROVAL_FILE = Path("/var/lib/inm/approvals/applied.json")
AUDIT_FILE = Path("/var/log/inm/ap-rest-only.jsonl")
COMMANDS = ("configure terminal", "allow-rest-api", "end", "commit apply")
PROMPT = re.compile(r"[#>]\s?$")
CLI_ERRORS = ("% parse error", "parse error", "invalid input", "command not found",
              "unknown command", "% invalid", "permission denied", "not authorized", "incomplete command")
UNSAFE_KEX = ("diffie-hellman-group1-sha1", "diffie-hellman-group-exchange-sha1")


def canonical_private_ip(value):
    ip = ipaddress.IPv4Address(value)
    if str(ip) != value or not any(ip in block for block in PRIVATE_NETS):
        raise ValueError("Only a canonical RFC1918 IPv4 device IP is permitted")
    return str(ip)


def require_host_approved(ip):
    data = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    ips = data.get("ips", []) if isinstance(data, dict) else []
    if not isinstance(ips, list) or ip not in ips:
        raise PermissionError("Target IP is not in INM host-enforced exact /32 approvals")


def key_sha256(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def get_paramiko():
    try:
        import paramiko
    except ImportError:
        raise RuntimeError(
            "Paramiko unavailable. Set up a CentOS Python 3 virtualenv and install paramiko "
            "there; never weaken the system OpenSSH config."
        ) from None
    return paramiko


def transport_to(ip, *, timeout=10.0):
    """Per-target SSH legacy fallback; no global SSH configuration change."""
    p = get_paramiko()
    for legacy in (False, True):
        disabled = {"kex": list(UNSAFE_KEX)}
        if not legacy:
            disabled["keys"] = ["ssh-rsa"]
            disabled["kex"].append("diffie-hellman-group14-sha1")
        try:
            sock = socket.create_connection((ip, 22), timeout=timeout)
        except (ConnectionRefusedError, TimeoutError, OSError):
            raise ConnectionError("SSH TCP/22 unavailable; a successful ping does not permit REST bootstrap") from None
        transport = p.Transport(sock, disabled_algorithms=disabled)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.set_keepalive(30)
        try:
            transport.start_client(timeout=timeout)
            return transport, legacy
        except p.ssh_exception.IncompatiblePeer:
            transport.close()
            if legacy:
                raise RuntimeError("SSH negotiation failed even with ssh-rsa / group14-sha1") from None
        except (p.SSHException, OSError, EOFError):
            transport.close()
            raise RuntimeError("SSH service did not complete negotiation") from None
    raise RuntimeError("SSH negotiation failed")


def read_prompt(chan, timeout):
    result = ""
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if chan.recv_ready():
            result += chan.recv(4096).decode("utf-8", "replace")
            last = result.rstrip("\r\n").split("\n")[-1] if result.strip() else ""
            if PROMPT.search(last):
                return result
        else:
            time.sleep(.05)
    raise TimeoutError("Timed out waiting for Instant AP CLI prompt")


def check_ports(ip):
    for port in (22, 4343):
        try:
            with socket.create_connection((ip, port), timeout=3):
                print("TCP {}: reachable".format(port))
        except OSError:
            print("TCP {}: unavailable".format(port))
    ping = shutil.which("ping")
    if ping:
        result = subprocess.run([ping, "-n", "-c", "1", "-W", "2", ip],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, timeout=5)
        print("ICMP ping:", "replied" if result.returncode == 0 else "no reply")
    else:
        print("ICMP ping: tool unavailable (not required for REST/SSH)")


def audit(ip, model, action, success, reason):
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    entry = json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "uid": os.geteuid(), "target": ip, "model": model,
                        "action": action, "success": bool(success), "reason": reason}) + "\n"
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(AUDIT_FILE), flags, 0o600)
    try:
        os.write(fd, entry.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="CentOS Aruba Instant AP-505/535 REST-only bootstrap")
    parser.add_argument("action", choices=("check", "enable"))
    parser.add_argument("--ip", required=True, help="Approved private IP, not a CIDR")
    parser.add_argument("--model", required=True, choices=ALLOWED_AP_MODELS)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--fingerprint", help="Pinned SSH SHA256 fingerprint, mandatory for enable")
    args = parser.parse_args(argv)
    ip = canonical_private_ip(args.ip)
    require_host_approved(ip)
    if os.geteuid() != 0:
        raise PermissionError("Run this host utility as root to access the trusted approval ledger")
    if args.action == "check":
        check_ports(ip)
        transport, legacy = transport_to(ip)
        try:
            print("SSH host fingerprint: {}".format(key_sha256(transport.get_remote_server_key())))
            print("SSH algorithms: {}".format("legacy ssh-rsa + group14-sha1" if legacy else "modern"))
            print("This is only an identity scan. Confirm the key independently before enabling REST.")
        finally:
            transport.close()
        return
    if not args.fingerprint or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", args.fingerprint):
        raise ValueError("Provide the independently verified SSH --fingerprint (SHA256:...)")
    phrase = "ENABLE REST API {}".format(ip)
    if input("Type exactly {}: ".format(phrase)).strip() != phrase:
        raise PermissionError("Operator declined the exact-device REST-only change")
    password = getpass.getpass("AP admin password (not echoed): ")
    if not password:
        raise ValueError("Missing administrator password")
    transport, legacy = transport_to(ip)
    try:
        if key_sha256(transport.get_remote_server_key()) != args.fingerprint:
            raise PermissionError("SSH host fingerprint changed; refused before authentication")
        p = get_paramiko()
        try:
            transport.auth_password(args.username, password)
        except p.AuthenticationException:
            raise PermissionError("SSH password rejected") from None
        chan = transport.open_session()
        chan.get_pty()
        chan.invoke_shell()
        read_prompt(chan, 20)
        audit(ip, args.model, "rest_enable_attempt", False, "operator-approved")
        for cmd in COMMANDS:
            chan.send(cmd + "\n")
            response = read_prompt(chan, 20)
            output_lines = response.replace("\r", "").split("\n")[1:]
            if any(any(error in line.lower() for error in CLI_ERRORS) for line in output_lines):
                raise RuntimeError("AP rejected fixed CLI command {}; stopping".format(cmd))
        audit(ip, args.model, "rest_enable_sent", True, "fixed-command-sequence")
        print("AP accepted fixed REST enable commands. Verify HTTPS port 4343 and REST login in the INM GUI.")
        print("SSH legacy fallback:", "used" if legacy else "not needed")
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, PermissionError, OSError, RuntimeError, ConnectionError, TimeoutError) as exc:
        print("INM AP REST: " + str(exc), file=sys.stderr)
        sys.exit(1)
