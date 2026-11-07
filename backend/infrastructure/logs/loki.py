"""LogStorePort on Grafana Loki (role 05). Push with a small, fixed label set; query read-only.

Labels are low-cardinality (app, service, source_type, event_type). Correlation and event IDs live in the JSON
line and are found with a line filter, so they never explode the label index.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx

_SECRETISH = re.compile(r"(?i)(password|passwd|secret|token|sid|authorization|passphrase)\s*[=:]\s*\S+")
_ALLOWED_LABEL = re.compile(r"^[a-z_]{1,32}$")


def redact_line(line: str) -> str:
    return _SECRETISH.sub(lambda m: f"{m.group(1)}=<redacted>", line)


class LokiLogStore:
    def __init__(self, base_url: str, timeout: float = 5.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def ready(self) -> tuple[bool, str]:
        r = self._http.get("/ready")
        return r.status_code == 200, r.text.strip()[:80]

    def build_info(self) -> str | None:
        try:
            return self._http.get("/loki/api/v1/status/buildinfo").json().get("version")
        except Exception:
            return None

    def push(self, labels: dict[str, str], line: str, ts: datetime) -> None:
        for k in labels:
            if not _ALLOWED_LABEL.match(k):
                raise ValueError(f"label {k!r} not allowed")
        ns = str(int((ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).timestamp() * 1_000_000_000))
        body = {"streams": [{"stream": {"app": "inm", **labels}, "values": [[ns, redact_line(line)]]}]}
        r = self._http.post("/loki/api/v1/push", json=body)
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Loki push failed: HTTP {r.status_code} {r.text[:200]}")

    def query(self, logql: str, since: datetime, limit: int = 200, until: datetime | None = None) -> list[dict]:
        until = until or datetime.now(timezone.utc)
        params = {"query": logql, "start": str(int(since.timestamp() * 1e9)), "end": str(int(until.timestamp() * 1e9)),
                  "limit": str(min(max(limit, 1), 1000)), "direction": "backward"}
        r = self._http.get("/loki/api/v1/query_range", params=params)
        if r.status_code != 200:
            raise RuntimeError(f"Loki query failed: HTTP {r.status_code} {r.text[:200]}")
        out = []
        for stream in r.json().get("data", {}).get("result", []):
            for ns, line in stream.get("values", []):
                entry = {"ts": datetime.fromtimestamp(int(ns) / 1e9, timezone.utc).isoformat(),
                         "labels": stream.get("stream", {}), "line": line}
                try:
                    entry["json"] = json.loads(line)
                except ValueError:
                    entry["json"] = None
                out.append(entry)
        out.sort(key=lambda e: e["ts"], reverse=True)
        return out[:limit]
