"""rc18 SSH security (paramiko 5): real handshakes against local OpenSSH servers.

* modern server -> paramiko 5 negotiates modern algorithms; pinned key enforced; changed key refused
* legacy-only server (ssh-rsa + diffie-hellman-group14-sha1) -> refused by default and audited
* the same server, explicitly approved and with INM_SSH_LEGACY_COMPAT=true -> OpenSSH legacy transport, host key still
  pinned, use audited; wrong password and expired approval refused
Needs root + OpenSSH (sshd/ssh/ssh-keyscan/ssh-keygen); skipped otherwise (CI installs openssh). No Aruba device: the
login shell is a small CLI that prints an Instant-style prompt.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NEED = all(shutil.which(x) for x in ("sshd", "ssh", "ssh-keyscan", "ssh-keygen", "useradd", "chpasswd"))
pytestmark = pytest.mark.skipif(not NEED or os.geteuid() != 0, reason="needs root and OpenSSH server/client")
USER, PW = "inmsshtest", "Legacy-Test-Pass-2026"
CLI = """#!{py}
import sys
out = sys.stdout
out.write("lab-ap# "); out.flush()
for line in sys.stdin:
    cmd = line.strip()
    if cmd == "exit":
        break
    out.write(cmd + "\\r\\n" + ("PING 10.0.0.1: 3 packets, 0% loss" if cmd.startswith("ping") else "ok") + "\\r\\nlab-ap# ")
    out.flush()
"""


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="inm-ssh-test-"))          # pytest's tmp dirs are 0700: the login user needs x
    cli = d / "fake-cli"
    cli.write_text(CLI.format(py=sys.executable)); cli.chmod(0o755)
    os.chmod(d, 0o755)
    if subprocess.run(["id", USER], capture_output=True).returncode != 0:
        subprocess.run(["useradd", "-M", "-s", str(cli), USER], check=True)
    else:
        subprocess.run(["usermod", "-s", str(cli), USER], check=True)
    subprocess.run(["chpasswd"], input=f"{USER}:{PW}", text=True, check=True)
    for t in ("ed25519", "rsa"):
        subprocess.run(["ssh-keygen", "-q", "-t", t, "-N", "", "-f", str(d / f"host_{t}")], check=True)
    procs, ports = [], {}
    for name, extra in (("modern", [f"HostKey {d}/host_ed25519", f"HostKey {d}/host_rsa"]),
                        ("legacy", [f"HostKey {d}/host_rsa", "HostKeyAlgorithms ssh-rsa",
                                    "KexAlgorithms diffie-hellman-group14-sha1"])):
        port = _port()
        cfg = d / f"sshd_{name}.conf"
        cfg.write_text("\n".join([f"Port {port}", "ListenAddress 127.0.0.1", "PasswordAuthentication yes",
                                  "KbdInteractiveAuthentication no", "UsePAM no", "PermitRootLogin no",
                                  f"PidFile {d}/{name}.pid", f"AllowUsers {USER}", *extra]) + "\n")
        procs.append(subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(cfg)], stderr=subprocess.DEVNULL))
        ports[name] = port
    for port in ports.values():
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close(); break
            except OSError:
                time.sleep(0.1)
    yield ports, d
    for p in procs:
        p.terminate()
    shutil.rmtree(d, ignore_errors=True)


def _fp_of(pub: Path) -> str:
    return subprocess.run(["ssh-keygen", "-lf", str(pub)], capture_output=True, text=True).stdout.split()[1]


def test_modern_server_negotiates_modern_algorithms_and_pins_the_key(servers, client):
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaSshError
    ports, d = servers
    info = ssh.scan_host_key("127.0.0.1", ports["modern"], timeout=8)
    assert info["legacy_algorithms"] is False and info["key_type"] == "ssh-ed25519"
    assert info["fingerprint"] == _fp_of(d / "host_ed25519.pub")
    out = ssh.run_exec_command("127.0.0.1", USER, PW, info["fingerprint"], "ping 10.0.0.1 repetitions 3",
                               port=ports["modern"], timeout=15)
    assert "0% loss" in out
    with pytest.raises(ArubaSshError, match="does not match the approved fingerprint"):
        ssh.run_exec_command("127.0.0.1", USER, PW, _fp_of(d / "host_rsa.pub"), "ping 10.0.0.1",
                             port=ports["modern"], timeout=15)                       # changed / wrong host key


def test_legacy_server_refused_by_default_and_refusal_audited(servers, client, monkeypatch):
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaSshError
    from backend.database.models import AuditLog
    from backend.database.session import session_scope
    ports, _ = servers
    monkeypatch.delenv("INM_SSH_LEGACY_COMPAT", raising=False)
    with pytest.raises(ArubaSshError, match="does not negotiate by default"):
        ssh.scan_host_key("127.0.0.1", ports["legacy"], timeout=8)
    monkeypatch.setenv("INM_SSH_LEGACY_COMPAT", "true")               # enabled, but this device is not approved
    with pytest.raises(ArubaSshError, match="not approved"):
        ssh.scan_host_key("127.0.0.1", ports["legacy"], timeout=8)
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "ssh_legacy_refused").count() >= 2


def test_approved_legacy_device_uses_pinned_openssh_transport_and_is_audited(servers, client, admin, monkeypatch):
    from backend.aruba import ssh, ssh_legacy
    from backend.aruba.errors import ArubaAuthError, ArubaSshError
    from backend.database.models import AppSetting, AuditLog
    from backend.database.session import session_scope
    ports, d = servers
    monkeypatch.setenv("INM_SSH_LEGACY_COMPAT", "true")
    r = client.post("/api/v1/ssh-legacy-approvals", headers=admin,
                    json={"ip": "127.0.0.1", "reason": "Lab AP on 6.x firmware pending upgrade", "days": 7})
    assert r.status_code == 201, r.text
    info = ssh.scan_host_key("127.0.0.1", ports["legacy"], timeout=8)
    assert info["legacy_algorithms"] is True and info["fingerprint"] == _fp_of(d / "host_rsa.pub")
    out = ssh.run_exec_command("127.0.0.1", USER, PW, info["fingerprint"], "ping 10.0.0.1 repetitions 3",
                               port=ports["legacy"], timeout=20)
    assert "0% loss" in out
    with pytest.raises(ArubaSshError, match="does not match the approved fingerprint"):
        ssh.run_exec_command("127.0.0.1", USER, PW, _fp_of(d / "host_ed25519.pub"), "ping 10.0.0.1",
                             port=ports["legacy"], timeout=20)
    with pytest.raises(ArubaAuthError):
        ssh.run_exec_command("127.0.0.1", USER, "wrong-password", info["fingerprint"], "ping 10.0.0.1",
                             port=ports["legacy"], timeout=20)
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "ssh_legacy_used").count() >= 3
        row = db.get(AppSetting, ssh_legacy.KEY + "127.0.0.1")
        row.value = row.value | {"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
    with pytest.raises(ArubaSshError, match="expired"):
        ssh.scan_host_key("127.0.0.1", ports["legacy"], timeout=8)
    assert client.delete("/api/v1/ssh-legacy-approvals/127.0.0.1", headers=admin).status_code == 204
