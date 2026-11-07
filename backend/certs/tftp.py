"""A TFTP server that serves ONE in-memory file, to ONE client address, ONE time (RFC 1350 read requests only).

The AP's `copy tftp ...` is the only documented way to load a WebUI certificate and speaks TFTP on UDP port 69.
This server exists only for the seconds of that transfer: nothing is written to disk, any other file name or client
is refused, and it shuts down after the first completed transfer or the deadline.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

RRQ, DATA, ACK, ERROR = 1, 3, 4, 5
BLOCK = 512


class TftpOnce:
    def __init__(self, filename: str, data: bytes, allowed_ip: str, bind: str = "0.0.0.0", port: int = 69,
                 lifetime: float = 120.0):
        self.filename, self.data, self.allowed_ip, self.lifetime = filename, data, allowed_ip, lifetime
        self.completed = threading.Event()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind((bind, port))
        except OSError as exc:
            self._sock.close()
            raise OSError(f"cannot listen on UDP {bind}:{port} ({exc.strerror or type(exc).__name__})") from None
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "TftpOnce":
        self._thread.start()
        return self

    def wait(self, timeout: float) -> bool:
        return self.completed.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    # -- internals --
    @staticmethod
    def _error(sock: socket.socket, addr, code: int, msg: str) -> None:
        sock.sendto(struct.pack("!HH", ERROR, code) + msg.encode() + b"\0", addr)

    def _serve(self) -> None:
        end = time.monotonic() + self.lifetime
        self._sock.settimeout(1.0)
        while not self._stop.is_set() and time.monotonic() < end and not self.completed.is_set():
            try:
                pkt, addr = self._sock.recvfrom(1024)
            except (socket.timeout, OSError):
                continue
            if len(pkt) < 4 or struct.unpack("!H", pkt[:2])[0] != RRQ:
                continue
            parts = pkt[2:].split(b"\0")
            name = parts[0].decode("ascii", "replace")
            if addr[0] != self.allowed_ip:
                self._error(self._sock, addr, 2, "Access violation")
            elif name != self.filename:
                self._error(self._sock, addr, 1, "File not found")
            else:
                self._send(addr, end)
        self.stop()

    def _send(self, addr, end: float) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        try:
            block, off = 1, 0
            while True:
                chunk = self.data[off:off + BLOCK]
                pkt = struct.pack("!HH", DATA, block & 0xFFFF) + chunk
                for _ in range(5):
                    s.sendto(pkt, addr)
                    try:
                        ack, a = s.recvfrom(64)
                    except socket.timeout:
                        if time.monotonic() > end:
                            return
                        continue
                    if a[0] == addr[0] and len(ack) >= 4 and struct.unpack("!HH", ack[:4]) == (ACK, block & 0xFFFF):
                        break
                else:
                    return  # no ACK after retries: abandon this attempt, keep listening for a retry
                off += BLOCK
                block += 1
                if len(chunk) < BLOCK:
                    self.completed.set()
                    return
        except OSError:
            return
        finally:
            s.close()
