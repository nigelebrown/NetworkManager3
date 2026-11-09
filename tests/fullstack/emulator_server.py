"""Serve the strict AOS-CX protocol emulator on a real HTTPS socket so separate processes (API, worker) reach it
exactly as they would reach a switch. TEST HARNESS ONLY — results are EMULATED evidence, never hardware.

usage: python -m tests.fullstack.emulator_server 10.250.0.24:443:24:PL.10.14.1000:EMU-FS-24 [...more] --cert C --key K
Control endpoint (plain HTTP on 127.0.0.1:18099): POST /<ip>/expire, /<ip>/down, /<ip>/up, /<ip>/password/<pw>,
GET /<ip>/state, /<ip>/violations  — used by the full-stack test to inject faults.
"""
from __future__ import annotations

import json
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from backend.infrastructure.writers.emulator import CxEmulator

EMUS: dict[str, CxEmulator] = {}
DOWN: set[str] = set()


def make_handler(ip: str):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _do(self):
            if ip in DOWN:
                self.connection.close()
                return
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            req = httpx.Request(self.command, f"https://{ip}{self.path}", headers=dict(self.headers.items()), content=body)
            resp = EMUS[ip].handle(req)
            data = resp.content or b""
            self.send_response(resp.status_code)
            for k, v in resp.headers.multi_items():
                if k.lower() not in ("content-length", "transfer-encoding"):
                    self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _do
    return H


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        data = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _, ip, what = self.path.split("/", 2)
        e = EMUS[ip]
        self._reply({"state": e.state, "violations": e.violations, "writes": e.writes, "logins": e.logins}
                    .get(what, {"error": what}))

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        ip, what = parts[0], parts[1]
        e = EMUS[ip]
        if what == "expire":
            e.expire_sessions()
        elif what == "down":
            DOWN.add(ip)
        elif what == "up":
            DOWN.discard(ip)
        elif what == "password":
            e.password = parts[2]
        elif what == "errors":          # /<ip>/errors/<port-last-digit>/<count>
            e.state["interfaces"][f"1/1/{parts[2]}"]["statistics"]["rx_error"] = int(parts[3])
        self._reply({"ok": True})


def main(argv: list[str]) -> None:
    cert = argv[argv.index("--cert") + 1]
    key = argv[argv.index("--key") + 1]
    specs = [a for a in argv if a.count(":") == 4]
    for spec in specs:
        ip, port, nports, fw, serial = spec.split(":")
        e = CxEmulator(model=f"{'6100' if int(nports) <= 24 else '6200F'} {nports}G", firmware=fw, ports=int(nports))
        e.state["system"]["serial_number"] = serial
        e.state["system"]["hostname"] = f"fs-sw-{nports}"
        EMUS[ip] = e
        srv = ThreadingHTTPServer((ip, int(port)), make_handler(ip))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"emulated CX {nports}-port fw {fw} at https://{ip}:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 18099), Control).serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
