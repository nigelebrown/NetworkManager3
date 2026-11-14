"""Simulated Aruba Instant clusters for end-to-end testing on real sockets.

Cluster A (REST enabled)  : VC 172.17.0.1, HTTPS :4343 /rest/login + /rest/show-cmd
Cluster B (REST disabled) : VC 172.18.0.1, :4343 serves an HTML page (like Instant with REST off),
                            SSH :22 Instant-like CLI with --More-- paging.
Output formats follow real AP-505 8.10 text tables (see backend/tests/test_parsers.py).
"""
import json
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import paramiko

USER, PW = "admin", "ArubaLab#1"


def aps_table(rows):
    head = "Name                IP Address     Mode    Spectrum  Clients  Type         IPv6 Address  Mesh Role  Zone  Serial #           Radio 0 Channel  Radio 1 Channel"
    under = "----                ----------     ----    --------  -------  ----         ------------  ---------  ----  --------           ---------------  ---------------"
    lines = [f"{len(rows)} Access Points", "----------------", head, under]
    for name, ip, clients, serial, ch0, ch1 in rows:
        lines.append(f"{name:<20}{ip:<15}access  disable   {clients:<9}505(indoor)  --            Portal     N/A   {serial:<19}{ch0:<17}{ch1}")
    return "\n".join(lines) + "\n"


CLUSTER_A = [("CP-Conference Room", "172.17.0.1*", "12", "CNPCLJ51A1", "36", "6"),
             ("CP-Registry", "10.77.1.2", "7", "CNPCLJ51A2", "36", "1"),
             ("CP-Legal-OfficeAP", "10.77.1.3", "4", "CNPCLJ51A3", "149", "11")]
CLUSTER_B = [("ICTD-Admin", "172.18.0.1*", "9", "CNPCLJ51B1", "44", "6"),
             ("ICTD-Technical-AP", "10.77.2.2", "3", "CNPCLJ51B2", "44", "6")]


def clients_table(cluster):
    lines = ["Client List", "-----------",
             "Name       IP Address    MAC Address        OS     ESSID          Access Point        Channel  Type  Role   IPv6 Address  Signal      Speed (mbps)",
             "----       ----------    -----------        --     -----          ------------        -------  ----  ----   ------------  ------      ------------"]
    n = 0
    for name, _ip, cnt, _s, ch0, ch1 in cluster:
        for i in range(int(cnt)):
            n += 1
            snr = 8 if (n % 4 == 0) else 32
            ch = ch1 if i % 3 == 0 else ch0
            lines.append(f"host-{n:<5}10.9.0.{n:<6}e0:06:e6:31:55:{n:02x}  Win10  JCFGUEST_WIFI  {name:<20}{ch:<9}GN    guest  --            {snr}({"good" if snr > 20 else "poor"})    {144 if snr > 20 else 6}")
    return "\n".join(lines) + "\n"


VERSION = "Aruba Operating System Software.\nArubaOS (MODEL: 505), Version 8.10.0.21_94501\n"


def output(cluster, cmd):
    if cmd == "show aps":
        return aps_table(cluster)
    if cmd == "show clients":
        return clients_table(cluster)
    if cmd == "show version":
        return VERSION
    if cmd == "show summary":
        return f"Name :{cluster[0][0]}\nIP Address :{cluster[0][1].rstrip('*')}\n"
    return None


# ------------------------------------------------------------------ REST VC (cluster A)
SIDS = set()


class Rest(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/rest/login":
            if body.get("user") == USER and body.get("passwd") == PW:
                sid = f"sid{len(SIDS) + 1}"
                SIDS.add(sid)
                return self._json(200, {"Status": "Success", "sid": sid})
            return self._json(200, {"Status": "Failed", "Message": "Invalid credentials"})
        if self.path == "/rest/logout":
            return self._json(200, {"Status": "Success"})
        self._json(404, {"Status": "Failed"})

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path != "/rest/show-cmd" or q.get("sid") not in SIDS:
            return self._json(401, {"Status": "Failed", "Message": "invalid sid"})
        if q.get("iap_ip_addr") not in ("172.17.0.1",):
            return self._json(200, {"Status": "Failed", "Status-code": 6, "Message": "target not supported"})
        text = output(CLUSTER_A, q.get("cmd", ""))
        if text is None:
            return self._json(200, {"Status": "Failed", "Status-code": 6, "Message": "unsupported"})
        self._json(200, {"Status": "Success", "Status-code": 0, "Command": q["cmd"], "Command output": text})


class HtmlOnly(BaseHTTPRequestHandler):
    """Instant with REST disabled: the web UI answers, /rest/* is not JSON."""
    def log_message(self, *a):
        pass

    def _html(self):
        b = b"<html><body>Aruba Instant</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    do_GET = do_POST = lambda self: self._html()  # noqa: E731


def tls_server(ip, handler):
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"/tmp/fake_{ip}.key",
                    "-out", f"/tmp/fake_{ip}.crt", "-days", "2", "-subj", f"/CN={ip}"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv = ThreadingHTTPServer((ip, 4343), handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"/tmp/fake_{ip}.crt", f"/tmp/fake_{ip}.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ------------------------------------------------------------------ SSH CLI (cluster B)
HOSTKEY = paramiko.RSAKey.generate(2048)
SSH_LOG = []


class Srv(paramiko.ServerInterface):
    def check_auth_password(self, u, p):
        return paramiko.AUTH_SUCCESSFUL if (u, p) == (USER, PW) else paramiko.AUTH_FAILED

    def get_allowed_auths(self, u):
        return "password"

    def check_channel_request(self, kind, cid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *a):
        return True

    def check_channel_shell_request(self, ch):
        return True


def ssh_session(sock):
    t = paramiko.Transport(sock)
    t.add_server_key(HOSTKEY)
    t.start_server(server=Srv())
    ch = t.accept(20)
    if ch is None:
        return
    prompt = "ICTD-Admin# "
    ch.send("\r\nAruba Instant\r\n" + prompt)
    buf = ""
    try:
        while True:
            data = ch.recv(1024)
            if not data:
                break
            buf += data.decode()
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                cmd = line.strip()
                SSH_LOG.append(cmd)
                out = output(CLUSTER_B, cmd)
                if cmd in ("configure terminal", "allow-rest-api", "end", "commit apply"):
                    out = ""
                if out is None:
                    ch.send(f"{cmd}\r\n% Parse error\r\n{prompt}")
                    continue
                lines = out.replace("\n", "\r\n").split("\r\n")
                if len(lines) > 6:  # page the output like Instant
                    ch.send(cmd + "\r\n" + "\r\n".join(lines[:6]) + "\r\n--More--")
                    while ch.recv(16) != b" ":
                        pass
                    ch.send("\r        \r" + "\r\n".join(lines[6:]) + "\r\n" + prompt)
                else:
                    ch.send(cmd + "\r\n" + "\r\n".join(lines) + "\r\n" + prompt)
    finally:
        t.close()


def ssh_server(ip, port=22):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((ip, port))
    s.listen(10)

    def loop():
        while True:
            c, _ = s.accept()
            threading.Thread(target=ssh_session, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    tls_server("172.17.0.1", Rest)
    tls_server("172.18.0.1", HtmlOnly)
    ssh_server("172.18.0.1", 22)
    import base64
    import hashlib
    print("SSH fingerprint B: SHA256:" + base64.b64encode(hashlib.sha256(HOSTKEY.asbytes()).digest()).decode().rstrip("="), flush=True)
    while True:
        time.sleep(3600)
