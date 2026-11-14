"""Aruba AOS-CX REST configuration writer (CX 6100 / CX 6200), API v10.09–v10.15 (negotiated).

Uses only documented REST resources (shown for v10.14):
  POST   /login (form body + CSRF), /logout                  session cookie auth
  GET    /fullconfigs/running-config                        full configuration (JSON) for the vault
  PUT    /fullconfigs/{cp}?from=/rest/v10.14/fullconfigs/running-config        create checkpoint
  PUT    /fullconfigs/running-config?from=/rest/v10.14/fullconfigs/{cp}        rollback to checkpoint
  PUT    /fullconfigs/startup-config?from=/rest/v10.14/fullconfigs/running-config   save
  PUT    /fullconfigs/running-config  (JSON body)           restore a vault artifact
  POST   /system/vlans                                      create VLAN
  GET    /system/vlans/{id}                                 readback VLAN
  PATCH  /system/interfaces/{port}                          access/trunk/admin/LLDP
  PATCH  /system/interfaces/{port}/poe_interface            PoE admin
  POST   /system/interfaces  (type lag)  / PATCH lag        LACP LAG and members
  GET    /system/interfaces/{port}?selector=configuration   readback

Every write is followed by a readback; any mismatch raises so the executor rolls back to the checkpoint.
HARDWARE CERTIFICATION is recorded per model/firmware by the certification workflow, not assumed here.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

import httpx

from . import Credentials, WriterError

# Default/preferred REST train. The session negotiates the real one per switch (E2E-09): it logs in on the
# preferred version and, if the switch does not serve it, reads GET /rest and picks the newest supported version.
API = "/rest/v10.14"
PREFERRED_API_VERSION = "v10.14"
# Versions whose request/response shapes this adapter implements (OpenAPI 10.09+ shapes; CSRF available 10.09+).
SUPPORTED_API_VERSIONS = ("v10.15", "v10.14", "v10.13", "v10.12", "v10.11", "v10.10", "v10.09")
PORT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{1,3}$|^lag\d{1,3}$")
_SECRET_RE = re.compile(
    r'(?i)((?:password|passwd|secret|token|x-csrf-token|sid|authorization|cookie)'
    r'\s*(?:=|:)\s*(?:["\']?))([^&\s,}\"\']+|\"[^\"]*\")'
)
_JSON_SECRET_RE = re.compile(
    r'(?i)(["\'](?:password|passwd|secret|token|x-csrf-token|sid|authorization|cookie)["\']\s*:\s*["\'])'
    r'([^"\']*)(["\'])'
)


def redact(text: str) -> str:
    """Remove anything credential-like from text that may reach logs, audit details or HTTP errors (E2E-04)."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}***", _JSON_SECRET_RE.sub(r"\1***\3", text or ""))


def firmware_train(firmware: str | None) -> str | None:
    m = re.search(r"(\d+)\.(\d+)", firmware or "")
    return f"{int(m.group(1))}.{int(m.group(2))}" if m else None


def _port(p: str) -> str:
    if not isinstance(p, str) or not PORT_RE.match(p):
        raise WriterError("INVALID", f"Invalid port name {p!r}")
    return quote(p, safe="")


class CxSession:
    """The single AOS-CX REST session used by every CX code path (writer, telemetry, onboarding probe and
    baseline capture) — E2E-16.

    * Login is POST {prefix}/login with credentials in an application/x-www-form-urlencoded BODY, never in the
      URL (E2E-04). Errors and logs never contain the request URL or body.
    * Asks for a CSRF token (x-use-csrf-token: true) and sends X-CSRF-Token on every later request; firmware
      that predates CSRF simply returns none (E2E-05).
    * Session cookie expiry (HTTP 401 on a later call) triggers exactly one re-login and retry.
    * API version is negotiated (E2E-09): preferred version first; on 404 the switch's GET /rest catalogue
      decides; versions outside SUPPORTED_API_VERSIONS are refused with UNSUPPORTED_API.
    """

    def __init__(self, host: str, username: str, password: str, *, verify: bool | str = True,
                 timeout: float = 15.0, transport: httpx.BaseTransport | None = None,
                 api_version: str | None = None):
        self.host, self._user, self._pw = host, username, password
        self.api_version = api_version or PREFERRED_API_VERSION
        self.csrf: str | None = None
        self.logins = 0
        self._http = httpx.Client(base_url=f"https://{host}", verify=verify, timeout=timeout, transport=transport,
                                  trust_env=False, follow_redirects=False)
        self._open = False

    @property
    def prefix(self) -> str:
        return f"/rest/{self.api_version}"

    def _post_login(self) -> httpx.Response:
        try:
            return self._http.post(self.prefix + "/login", data={"username": self._user, "password": self._pw},
                                   headers={"x-use-csrf-token": "true", "accept": "application/json"})
        except httpx.HTTPError as exc:
            raise WriterError("UNREACHABLE", f"Cannot reach switch {self.host}: {type(exc).__name__}") from None

    def available_versions(self) -> list[str]:
        try:
            r = self._http.get("/rest", headers={"accept": "application/json"})
        except httpx.HTTPError as exc:
            raise WriterError("UNREACHABLE", f"Cannot reach switch {self.host}: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise WriterError("REST_DISABLED", f"REST API catalogue unavailable (HTTP {r.status_code}).")
        try:
            data = r.json()
        except ValueError:
            raise WriterError("PARSE_ERROR", "REST API catalogue is not JSON.") from None
        found = {k for k in (data or {}) if re.match(r"^v\d+\.\d+$", k)}
        latest = (data or {}).get("latest") or {}
        if isinstance(latest, dict) and re.match(r"^v\d+\.\d+$", str(latest.get("version") or "")):
            found.add(latest["version"])
        return sorted(found, key=lambda v: tuple(int(x) for x in v[1:].split(".")), reverse=True)

    def open(self) -> None:
        r = self._post_login()
        if r.status_code == 404:                      # this switch does not serve the preferred version
            usable = [v for v in self.available_versions() if v in SUPPORTED_API_VERSIONS]
            if not usable:
                raise WriterError("UNSUPPORTED_API", "Switch offers no REST API version this adapter supports "
                                                     f"(supported: {', '.join(SUPPORTED_API_VERSIONS)}).")
            self.api_version = usable[0]
            r = self._post_login()
        if r.status_code in (301, 302, 303, 307, 308):
            raise WriterError("AUTH_REJECTED", "Switch redirected the authentication request; no session established.")
        if r.status_code in (401, 403):
            raise WriterError("AUTH_REJECTED", "Switch rejected the credentials.")
        if r.status_code == 429:
            raise WriterError("RATE_LIMITED", "Switch refused the login: too many sessions or requests.")
        if r.status_code >= 400:
            raise WriterError("REST_DISABLED", f"Login failed (HTTP {r.status_code}).")
        if r.status_code != 200:
            raise WriterError("AUTH_REJECTED", f"Unexpected login response HTTP {r.status_code}.")
        if "text/html" in r.headers.get("content-type", "").lower():
            raise WriterError("AUTH_REJECTED", "Switch returned an HTML login page, not an authenticated session.")
        if not self._http.cookies:
            raise WriterError("AUTH_REJECTED", "Switch did not establish an authenticated session cookie.")
        self.csrf = r.headers.get("x-csrf-token")
        self.logins += 1
        self._open = True

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"accept": "application/json"}
        if self.csrf:
            h["x-csrf-token"] = self.csrf
        if extra:
            h.update(extra)
        return h

    def request(self, method: str, path: str, *, retry_auth: bool = True, **kw) -> httpx.Response:
        if not self._open:
            self.open()
        extra = kw.pop("headers", None)
        headers = self._headers(extra)
        try:
            r = self._http.request(method, self.prefix + path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise WriterError("CONNECTION_LOST", f"{method} {path}: {type(exc).__name__}") from None
        if r.status_code in (301, 302, 303, 307, 308):
            raise WriterError("DEVICE_REJECTED", f"{method} {path}: unexpected redirect HTTP {r.status_code}.")
        if r.status_code == 401 and retry_auth:   # session expired / idle timeout: one fresh login, then retry
            self._open, self.csrf = False, None
            self.open()
            return self.request(method, path, retry_auth=False, headers=extra, **kw)
        if r.status_code == 401:
            raise WriterError("AUTH_REJECTED", f"{method} {path}: session rejected after re-login.")
        if r.status_code == 403 and self.csrf is not None and "csrf" in r.text.lower() and retry_auth:
            self._open, self.csrf = False, None        # token expired/rotated: renegotiate once
            self.open()
            return self.request(method, path, retry_auth=False, headers=extra, **kw)
        if r.status_code == 429:
            raise WriterError("RATE_LIMITED", f"{method} {path}: HTTP 429")
        if r.status_code >= 400:
            raise WriterError("DEVICE_REJECTED", f"{method} {path}: HTTP {r.status_code} {redact(r.text[:200])}")
        return r

    def close(self) -> None:
        try:
            if self._open:
                self._http.post(self.prefix + "/logout", headers=self._headers())
        except httpx.HTTPError:
            pass
        finally:
            self._open = False
            self._http.close()

    def __enter__(self) -> "CxSession":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class ArubaCxWriter:
    family = "switch"
    key = "aruba_cx_10_14"
    # Every typed operation implemented below (asserted equal to domain.adapters.CX_TYPED_OPS by a contract test).
    OPERATIONS = ("switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans",
                  "switch.port.admin_state", "switch.port.poe", "switch.lldp", "switch.lag.members",
                  "switch.central.disable", "switch.port.description", "switch.port.stp_bpdu_guard",
                  "switch.port.storm_control", "switch.port.qos_trust", "switch.system.ntp", "switch.system.dns",
                  "switch.system.syslog", "config.restore")

    def __init__(self, creds: Credentials, *, verify: bool | str = True, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self.creds = creds
        self.session = CxSession(creds.host, creds.username, creds.password, verify=verify, timeout=timeout,
                                 transport=transport, api_version=(creds.extra or {}).get("api_version"))

    @property
    def prefix(self) -> str:
        return self.session.prefix

    def _vlan_url(self, v: int) -> str:
        return f"{self.prefix}/system/vlans/{int(v)}"

    # ---- session -------------------------------------------------------------------------
    def open(self) -> None:
        self.session.open()

    def close(self) -> None:
        self.session.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return self.session.request(method, path, **kw)

    def protocol(self) -> dict:
        """What was negotiated with this switch — recorded with every execution and capability report."""
        return {"api_version": self.session.api_version, "csrf": self.session.csrf is not None}

    def read_audit_logs(self, *, kind: str = "audit", limit: int = 50) -> list[dict]:
        """Read documented AOS-CX 10.09+ REST audit/event log resource.

        No change to device configuration. Unknown response attributes are
        dropped by a bounded redacting normalizer; no raw log JSON escapes.
        """
        from ..device_audit import normalize_cx
        if kind not in ("audit", "event") or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise WriterError("INVALID", "Unsupported switch log category or count.")
        data = self._req("GET", f"/logs/{kind}").json()
        try:
            return normalize_cx(data, kind=kind, limit=limit)
        except ValueError:
            raise WriterError("PARSE_ERROR", "AOS-CX returned an unexpected log format.") from None

    # ---- read ------------------------------------------------------------------------------
    def identity(self) -> dict:
        d = self._req("GET", "/system", params={"attributes": "platform_name,software_version,hostname,"
                                                              "serial_number"}).json()
        return {"model": d.get("platform_name"), "firmware": d.get("software_version"), "hostname": d.get("hostname"),
                "serial_number": d.get("serial_number"), **self.protocol()}

    def telemetry(self) -> dict:
        """READ-ONLY monitoring: identity, CPU/memory, hardware health, every port (link, speed, VLAN,
        counters), PoE per port and LLDP neighbours. Tolerant of attributes a firmware does not expose."""
        sysd = self._req("GET", "/system", params={"attributes": "hostname,platform_name,software_version,"
                                                                  "serial_number,boot_time,subsystems"}).json()
        hw = {"cpu_percent": None, "memory_percent": None, "temp_max_c": None, "fans_total": 0, "fans_failed": 0,
              "psu_total": 0, "psu_failed": 0, "sensors": []}
        try:
            subs = self._req("GET", "/system/subsystems", params={"depth": 2}).json() or {}
        except WriterError:
            subs = {}
        for name, sub in (subs.items() if isinstance(subs, dict) else []):
            if not isinstance(sub, dict):
                continue
            ru = sub.get("resource_utilization") or {}
            if isinstance(ru.get("cpu"), (int, float)):
                hw["cpu_percent"] = float(ru["cpu"])
            if isinstance(ru.get("memory"), (int, float)):
                hw["memory_percent"] = float(ru["memory"])
            for f in (sub.get("fans") or {}).values() if isinstance(sub.get("fans"), dict) else []:
                hw["fans_total"] += 1
                hw["fans_failed"] += int(str(f.get("status", "ok")).lower() not in ("ok", "normal"))
            for ps in (sub.get("power_supplies") or {}).values() if isinstance(sub.get("power_supplies"), dict) else []:
                hw["psu_total"] += 1
                hw["psu_failed"] += int(str(ps.get("status", "ok")).lower() not in ("ok", "normal"))
            for t in (sub.get("temp_sensors") or {}).values() if isinstance(sub.get("temp_sensors"), dict) else []:
                c = t.get("temperature")
                if isinstance(c, (int, float)):
                    c = c / 1000.0 if c > 200 else float(c)        # CX reports millidegrees
                    hw["sensors"].append({"name": t.get("name"), "c": c, "status": t.get("status")})
                    hw["temp_max_c"] = c if hw["temp_max_c"] is None else max(hw["temp_max_c"], c)
        ifs = self._req("GET", "/system/interfaces", params={
            "depth": 2, "attributes": "name,type,admin,admin_state,link_state,link_speed,duplex,vlan_mode,vlan_tag,"
                                      "vlan_trunks,statistics,description,link_resets"}).json() or {}
        ports = []
        for name, it in sorted(ifs.items() if isinstance(ifs, dict) else []):
            if not isinstance(it, dict) or not PORT_RE.match(str(it.get("name") or name)) or str(name).startswith("lag"):
                continue
            st = it.get("statistics") or {}
            tag = str(it.get("vlan_tag") or "")
            ports.append({"name": str(it.get("name") or name), "admin": it.get("admin") or it.get("admin_state"),
                          "link": it.get("link_state") or "unknown", "speed_bps": it.get("link_speed"),
                          "duplex": it.get("duplex"), "vlan_mode": it.get("vlan_mode"),
                          "vlan": int(tag.rsplit("/", 1)[-1]) if tag.rsplit("/", 1)[-1].isdigit() else None,
                          "description": (it.get("description") or "")[:64],
                          "rx_bytes": st.get("rx_bytes"), "tx_bytes": st.get("tx_bytes"),
                          "rx_errors": st.get("rx_error") if "rx_error" in st else st.get("rx_errors"),
                          "tx_errors": st.get("tx_error") if "tx_error" in st else st.get("tx_errors"),
                          "rx_dropped": st.get("rx_dropped"), "tx_dropped": st.get("tx_dropped"),
                          "link_resets": it.get("link_resets"), "poe_w": None, "poe_status": None, "lldp": None})
        for p in ports:
            if p["link"] != "up":
                continue
            q = _port(p["name"])
            try:
                poe = self._req("GET", f"/system/interfaces/{q}/poe_interface").json() or {}
                ps = poe.get("status") or {}
                mw = ps.get("measured_power") if "measured_power" in ps else ps.get("power_drawn")
                p["poe_w"] = round(mw / 1000.0, 1) if isinstance(mw, (int, float)) and mw > 100 else mw
                p["poe_status"] = ps.get("detection_status") or ps.get("status")
            except WriterError:
                pass
            try:
                nb = self._req("GET", f"/system/interfaces/{q}/lldp_neighbors", params={"depth": 2}).json() or {}
                for n in (nb.values() if isinstance(nb, dict) else []):
                    if isinstance(n, dict):
                        info = n.get("neighbor_info") or {}
                        p["lldp"] = {"system_name": info.get("chassis_name") or n.get("chassis_id"),
                                     "port": info.get("port_description") or n.get("port_id"),
                                     "capabilities": info.get("chassis_capability_enabled")}
                        break
            except WriterError:
                pass
        up = sum(1 for p in ports if p["link"] == "up")
        metrics = {"ports_up": float(up), "ports_total": float(len(ports)),
                   "port_errors_total": float(sum((p["rx_errors"] or 0) + (p["tx_errors"] or 0) for p in ports)),
                   "poe_power_w": round(sum(p["poe_w"] or 0 for p in ports), 1),
                   "poe_ports_powered": float(sum(1 for p in ports if (p["poe_w"] or 0) > 0)),
                   "lldp_neighbors": float(sum(1 for p in ports if p["lldp"]))}
        for k in ("cpu_percent", "memory_percent", "temp_max_c"):
            if hw[k] is not None:
                metrics[k] = hw[k]
        metrics["fans_failed"], metrics["psu_failed"] = float(hw["fans_failed"]), float(hw["psu_failed"])
        return {"identity": {"hostname": sysd.get("hostname"), "model": sysd.get("platform_name"),
                             "firmware": sysd.get("software_version"), "serial_number": sysd.get("serial_number")},
                "metrics": metrics, "ports": ports, "hardware": hw}

    def management_authority(self) -> dict:
        """Detect whether Aruba Central (or another NMS) is the configuration authority (C10)."""
        try:
            d = self._req("GET", "/system/aruba_central").json() or {}
        except WriterError as exc:
            if "HTTP 404" in str(exc):
                return {"authority": "LOCAL", "detail": "No Aruba Central configuration on the switch."}
            raise
        enabled = not d.get("disable", False)
        connected = str((d.get("status") or {}).get("connection_state") or d.get("connection_state") or "").lower()
        if enabled and connected in ("connected", "up", "established"):
            return {"authority": "ARUBA_CENTRAL", "detail": "Switch is connected to Aruba Central.", "raw": d}
        if enabled:
            return {"authority": "CENTRAL_CONFIGURED_NOT_CONNECTED",
                    "detail": "Central is enabled but not connected; it may reclaim the configuration.", "raw": d}
        return {"authority": "LOCAL", "detail": "Aruba Central is disabled on the switch.", "raw": d}

    def backup(self) -> bytes:
        cfg = self._req("GET", "/fullconfigs/running-config").json()
        return json.dumps(cfg, sort_keys=True, indent=1).encode()

    def checkpoint(self) -> str:
        name = f"inm-cp-{int(time.time())}"
        self._req("PUT", f"/fullconfigs/{name}", params={"from": f"{self.prefix}/fullconfigs/running-config"})
        return name

    def rollback(self, checkpoint: str) -> None:
        if not re.match(r"^inm-cp-\d+$", checkpoint):
            raise WriterError("INVALID", "Unknown checkpoint.")
        self._req("PUT", "/fullconfigs/running-config", params={"from": f"{self.prefix}/fullconfigs/{checkpoint}"})

    def save(self) -> None:
        self._req("PUT", "/fullconfigs/startup-config", params={"from": f"{self.prefix}/fullconfigs/running-config"})

    def restore(self, config: bytes) -> None:
        try:
            body = json.loads(config)
        except ValueError:
            raise WriterError("INVALID", "Vault artifact is not an AOS-CX JSON configuration.") from None
        self._req("PUT", "/fullconfigs/running-config", json=body)

    # ---- typed operations ------------------------------------------------------------------
    def render(self, op: dict) -> list[dict]:
        k, v = op.get("op"), op.get("value")
        if k == "switch.vlan.define":
            return [{"method": "POST", "path": "/system/vlans", "json": {"id": int(v), "name": op.get("name") or f"VLAN{int(v)}"}}]
        if k == "switch.port.access_vlan":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"vlan_mode": "access", "vlan_tag": self._vlan_url(v)}}]
        if k == "switch.port.trunk_vlans":
            native = int(op.get("native", 1))
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"vlan_mode": "native-untagged", "vlan_tag": self._vlan_url(native),
                              "vlan_trunks": [self._vlan_url(x) for x in v]}}]
        if k == "switch.port.admin_state":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}", "json": {"admin": v}}]
        if k == "switch.port.poe":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}/poe_interface",
                     "json": {"config": {"admin_disable": v == "disabled"}}}]
        if k == "switch.lldp":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"other_config": {"lldp_enable_dir": "rxtx" if v in ("enabled", True) else "off"}}}]
        if k == "switch.central.disable":
            return [{"method": "PATCH", "path": "/system/aruba_central", "json": {"disable": True}}]
        if k == "switch.port.description":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"description": str(v)[:64]}}]
        if k == "switch.port.stp_bpdu_guard":
            on = v == "enabled"
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"stp_config": {"bpdu_guard_enable": on, "admin_edge_port_enable": on}}}]
        if k == "switch.port.storm_control":
            lim = {t: int(op[f"{t.replace('-', '_')}_kbps"]) for t in ("broadcast", "multicast", "unknown-unicast")
                   if op.get(f"{t.replace('-', '_')}_kbps") is not None}
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"rate_limits": lim, "rate_limits_units": {t: "kbps" for t in lim}}}]
        if k == "switch.port.qos_trust":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}", "json": {"qos_trust": v}}]
        if k == "switch.system.ntp":
            vrf = op.get("vrf", "mgmt")
            return [{"method": "POST", "path": "/system/ntp_associations",
                     "json": {"address": v, "vrf": f"{self.prefix}/system/vrfs/{vrf}",
                              "association_attributes": {"iburst": True, "prefer": bool(op.get("prefer"))}}}]
        if k == "switch.system.dns":
            vrf = op.get("vrf", "mgmt")
            return [{"method": "PATCH", "path": f"/system/vrfs/{vrf}",
                     "json": {"dns_name_servers": {str(i): ip for i, ip in enumerate(v)},
                              **({"dns_domain_name": op["domain"]} if op.get("domain") else {})}}]
        if k == "switch.system.syslog":
            vrf = op.get("vrf", "mgmt")
            return [{"method": "POST", "path": "/system/syslog_remotes",
                     "json": {"remote_host": v, "vrf": f"{self.prefix}/system/vrfs/{vrf}",
                              "severity": op.get("severity", "info"), "transport": "udp", "port_number": 514}}]
        if k == "switch.lag.members":
            lag = op["lag"]
            if not re.match(r"^lag\d{1,3}$", lag):
                raise WriterError("INVALID", "LAG must be named lagN.")
            members = [f"{self.prefix}/system/interfaces/{_port(p)}" for p in v]
            return [{"method": "PUT", "path": f"/system/interfaces/{lag}",
                     "json": {"name": lag, "type": "lag", "admin": "up", "lacp": op.get("lacp", "active"),
                              "interfaces": members}}]
        raise WriterError("UNSUPPORTED", f"{k} is not supported by {self.key}")

    def apply(self, op: dict) -> list[dict]:
        sent = []
        for call in self.render(op):
            self._req(call["method"], call["path"], json=call["json"])
            sent.append(call)
        return sent

    def readback(self, op: dict) -> tuple[bool, dict]:
        k, v = op.get("op"), op.get("value")
        if k == "switch.system.ntp":
            d = self._req("GET", "/system/ntp_associations", params={"depth": 1}).json() or {}
            got = sorted(str(x.get("address")) for x in d.values() if isinstance(x, dict))
            return v in got, {"ntp_servers": got}
        if k == "switch.system.syslog":
            d = self._req("GET", "/system/syslog_remotes", params={"depth": 1}).json() or {}
            got = sorted(str(x.get("remote_host")) for x in d.values() if isinstance(x, dict))
            return v in got, {"syslog_servers": got}
        if k == "switch.system.dns":
            d = self._req("GET", f"/system/vrfs/{op.get('vrf', 'mgmt')}",
                          params={"attributes": "dns_name_servers,dns_domain_name"}).json() or {}
            got = [d.get("dns_name_servers", {}).get(str(i)) for i in range(len(d.get("dns_name_servers") or {}))]
            ok = got == list(v) and (not op.get("domain") or d.get("dns_domain_name") == op["domain"])
            return ok, {"dns_name_servers": got, "dns_domain_name": d.get("dns_domain_name")}
        if k == "switch.central.disable":
            auth = self.management_authority()
            return auth["authority"] == "LOCAL", {"authority": auth["authority"]}
        if k == "switch.vlan.define":
            try:
                d = self._req("GET", f"/system/vlans/{int(v)}").json()
            except WriterError as exc:
                if "HTTP 404" in str(exc):
                    return False, {"id": None, "present": False}
                raise
            return d.get("id") == int(v), {"id": d.get("id"), "name": d.get("name")}
        if k == "switch.lag.members":
            d = self._req("GET", f"/system/interfaces/{op['lag']}", params={"selector": "configuration"}).json()
            got = sorted(str(x).rsplit("/", 1)[-1].replace("%2F", "/") for x in (d.get("interfaces") or []))
            return got == sorted(v), {"members": got, "lacp": d.get("lacp")}
        path = f"/system/interfaces/{_port(op['port'])}"
        if k == "switch.port.poe":
            d = self._req("GET", path + "/poe_interface").json()
            got = (d.get("config") or {}).get("admin_disable")
            return got == (v == "disabled"), {"admin_disable": got}
        d = self._req("GET", path, params={"selector": "configuration"}).json()
        if k == "switch.port.access_vlan":
            return (d.get("vlan_mode") == "access" and str(d.get("vlan_tag", "")).endswith(f"/{int(v)}"),
                    {"vlan_mode": d.get("vlan_mode"), "vlan_tag": d.get("vlan_tag")})
        if k == "switch.port.trunk_vlans":
            got = sorted(int(str(x).rsplit("/", 1)[-1]) for x in (d.get("vlan_trunks") or []))
            return got == sorted(v), {"vlan_mode": d.get("vlan_mode"), "vlan_trunks": got}
        if k == "switch.port.admin_state":
            return d.get("admin") == v, {"admin": d.get("admin")}
        if k == "switch.lldp":
            got = (d.get("other_config") or {}).get("lldp_enable_dir")
            return got == ("rxtx" if v in ("enabled", True) else "off"), {"lldp_enable_dir": got}
        if k == "switch.port.description":
            return d.get("description") == str(v)[:64], {"description": d.get("description")}
        if k == "switch.port.stp_bpdu_guard":
            got = (d.get("stp_config") or {}).get("bpdu_guard_enable")
            return got is (v == "enabled"), {"bpdu_guard_enable": got}
        if k == "switch.port.storm_control":
            want = self.render(op)[0]["json"]["rate_limits"]
            got = {t: (d.get("rate_limits") or {}).get(t) for t in want}
            return got == want, {"rate_limits": d.get("rate_limits")}
        if k == "switch.port.qos_trust":
            return d.get("qos_trust") == v, {"qos_trust": d.get("qos_trust")}
        raise WriterError("UNSUPPORTED", f"No readback for {k}")
