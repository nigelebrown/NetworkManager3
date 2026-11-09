"""Log redaction shared by the API and the worker (E2E-04): no credential, session id or CSRF token may reach a
log line, even through third-party loggers (httpx logs request URLs at INFO)."""
from __future__ import annotations

import logging

TOKENS = ("passwd=", "password=", "sid=", "authorization:", "x-csrf-token", "csrf_token", "token=")


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage().lower()
        except Exception:
            return True
        if any(t in msg for t in TOKENS):
            record.msg, record.args = "[redacted log line containing a secret-like token]", ()
        return True


def install() -> None:
    f = RedactFilter()
    root = logging.getLogger()
    for h in root.handlers:
        if not any(isinstance(x, RedactFilter) for x in h.filters):
            h.addFilter(f)
    for name in ("httpx", "httpcore", "urllib3", "paramiko"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactFilter) for x in lg.filters):
            lg.addFilter(f)
