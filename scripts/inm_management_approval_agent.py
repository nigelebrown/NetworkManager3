#!/usr/bin/env python3
"""Root-owned, host-only reconciler of admin-confirmed management IP approvals.

No Docker socket or writable host mounts are provided to the API. The API
writes audited requests to PostgreSQL; this timer safely translates only exact
RFC1918 IPs into Docker worker firewall rules. No hardware configuration writes.
"""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path("/opt/NetworkManager3")
ENV = Path("/etc/inm/inm.env")
DIR = Path("/var/lib/inm/approvals")
FILE = DIR / "applied.json"
GUARD = ROOT / "scripts/inm_worker_egress_guard.sh"
PRIVATE = tuple(ipaddress.IPv4Network(s) for s in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
MAX_IPS = 1000


def exact_ips(values: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_IPS * 2:
        raise ValueError("Invalid or excessive management approval entries")
    result = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("Nonstring management approval")
        ip = ipaddress.IPv4Address(raw)
        if str(ip) != raw or not any(ip in block for block in PRIVATE):
            raise ValueError("Only exact RFC1918 management IPs may be approved")
        result.add(str(ip))
    if len(result) > MAX_IPS:
        raise ValueError("Too many approved management IPs")
    return sorted(result, key=lambda value: int(ipaddress.IPv4Address(value)))


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def env_fields() -> tuple[str, dict[str, str]]:
    raw = ENV.read_text(encoding="utf-8")
    data = dict(line.split("=", 1) for line in raw.splitlines()
                if "=" in line and not line.lstrip().startswith("#"))
    if (data.get("INM_MODE"), data.get("INM_PROFILE")) != ("live", "lab-readonly"):
        raise ValueError("Management approval agent requires locked live/lab-readonly mode")
    if data.get("INM_DEVICE_WRITE_LOCK") != "true" or data.get("INM_ALLOW_CONFIG_CHANGES") == "true":
        raise ValueError("Management approval agent requires the device-write lock")
    return raw, data


def atomic_file(path: Path, data: str, mode: int) -> None:
    fd, filename = tempfile.mkstemp(prefix=".inm-approval-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def install() -> None:
    DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    DIR.chmod(0o755)
    systemd = Path("/etc/systemd/system")
    defs = {
        "inm-management-approval.service": """# Managed by NetworkManager3
[Unit]
Description=Apply audited exact-IP management approvals to Docker worker
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
NoNewPrivileges=true
ExecStart=/usr/bin/python3 -I /opt/NetworkManager3/scripts/inm_management_approval_agent.py --apply
""",
        "inm-management-approval.timer": """# Managed by NetworkManager3
[Unit]
Description=Reconcile INM device management approvals every five seconds

[Timer]
OnBootSec=30s
OnUnitInactiveSec=5s
AccuracySec=1s
Unit=inm-management-approval.service

[Install]
WantedBy=timers.target
""",
    }
    for name, content in defs.items():
        file = systemd / name
        if file.exists() and not file.read_text().startswith("# Managed by NetworkManager3"):
            raise RuntimeError("Refusing to overwrite an unmanaged systemd unit: " + name)
        atomic_file(file, content, 0o644)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", "--now", "inm-management-approval.timer"])
    print("[INM approvals] Host-only admin approval reconciliation installed")


def apply() -> None:
    DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    DIR.chmod(0o755)
    with open("/run/inm-readonly-approval.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old, env = env_fields()
        query = "SELECT value::text FROM app_settings WHERE key='inm.approved_device_ips';"
        raw = command(["docker", "exec", "inm-management-db-1", "psql", "-X", "-U",
                       "inm_app", "-d", "inm_management", "-t", "-A", "-c", query])
        data = json.loads(raw) if raw else {"ips": []}
        if not isinstance(data, dict) or not isinstance(data.get("ips"), list):
            raise ValueError("Malformed approval request in DB")
        existing = [part.strip() for part in env.get("INM_APPROVED_DEVICE_IPS", "").split(",") if part.strip()]
        wanted = exact_ips(existing + data["ips"])
        prior = set()
        try:
            prior = set(exact_ips(json.loads(FILE.read_text()).get("ips", [])))
        except (OSError, ValueError, TypeError):
            pass  # no readable previous approval file: start from an empty set
        guard = ["bash", str(GUARD)]
        if prior == set(wanted):
            try:
                command(guard + ["--check"])
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass  # guard check failed: fall through and rewrite the configuration
        replacement = "\n".join(line for line in old.splitlines()
                                if not line.startswith("INM_APPROVED_DEVICE_IPS="))
        replacement += "\nINM_APPROVED_DEVICE_IPS=" + ",".join(wanted) + "\n"
        atomic_file(ENV, replacement, 0o600)
        try:
            command(guard + ["--apply"])
            payload = json.dumps({"ips": wanted, "applied_at": datetime.now(timezone.utc).isoformat()})
            atomic_file(FILE, payload + "\n", 0o644)
        except Exception:
            atomic_file(ENV, old, 0o600)
            try:
                command(guard + ["--apply"])
            except Exception as _exc:
                # Fail closed: the firewall's temporary DROP remains in place.
                print(f"warning: rollback re-apply failed; firewall DROP stays in place (fail closed): {type(_exc).__name__}", file=__import__("sys").stderr)
            raise
        print("[INM approvals] Host firewall now enforces", len(wanted), "exact /32 IPs")


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            raise PermissionError("Host approval agent requires root")
        if len(sys.argv) != 2 or sys.argv[1] not in ("--apply", "--install"):
            raise ValueError("Usage: agent.py --apply|--install")
        if not ENV.exists() or not GUARD.is_file():
            raise FileNotFoundError("INM installation is incomplete")
        install() if sys.argv[1] == "--install" else apply()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        # Only approved static errors may be reported verbatim. Arbitrary
        # exception text can include database values, credentials or CLI args.
        safe_errors = {
            "Management approval agent requires locked live/lab-readonly mode",
            "Management approval agent requires the device-write lock",
            "Malformed approval request in DB",
            "Invalid or excessive management approval entries",
            "Nonstring management approval",
            "Only exact RFC1918 management IPs may be approved",
            "Too many approved management IPs",
        }
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in safe_errors else type(exc).__name__
        print("[INM approvals] FAIL:", reason, file=sys.stderr)
        sys.exit(1)
