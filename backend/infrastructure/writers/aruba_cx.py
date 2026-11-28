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
import logging

import json
from pathlib import Path
import re
import time
from urllib.parse import quote

import httpx

from . import Credentials, WriterError

_log = logging.getLogger(__name__)

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


def _vlan_ids(references) -> list[int]:
    if not references:
        return []
    values = references.values() if isinstance(references, dict) else references
    if isinstance(values, str):
        values = [values]
    return sorted(int(str(value).rsplit("/", 1)[-1]) for value in values)


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
        # Only idempotent reads may be replayed after an auth/CSRF expiry.
        # Never retry a configuration POST/PATCH/PUT/DELETE: a firmware-specific
        # proxy or server could have processed the write before returning 401/403.
        # Fail closed and let the change executor reconcile through readback and
        # its durable recovery journal instead of potentially writing twice.
        replayable_read = method.upper() in ("GET", "HEAD", "OPTIONS")
        expired = r.status_code == 401 or (
            r.status_code == 403 and self.csrf is not None and "csrf" in r.text.lower()
        )
        if expired and not replayable_read:
            self._open, self.csrf = False, None
            raise WriterError(
                "AUTH_REJECTED",
                f"{method} {path}: credentials/session expired during a state-changing request; "
                "not replayed automatically. Reconcile device state before retrying.",
            )
        if r.status_code == 401 and retry_auth:
            self._open, self.csrf = False, None
            self.open()
            return self.request(method, path, retry_auth=False, headers=extra, **kw)
        if r.status_code == 401:
            raise WriterError("AUTH_REJECTED", f"{method} {path}: session rejected after re-login.")
        if r.status_code == 403 and self.csrf is not None and "csrf" in r.text.lower() and retry_auth:
            self._open, self.csrf = False, None
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
        except httpx.HTTPError as _exc:
            _log.debug("best-effort CX REST logout: %s", type(_exc).__name__)
        finally:
            self._open = False
            self._http.close()

    def __enter__(self) -> "CxSession":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


RC17_OPS = ("switch.acl.define", "switch.acl.apply", "switch.route.static", "switch.qos.dscp_policy", "switch.vsf.member")
RC18_OPS = ("switch.aaa.radius_server", "switch.aaa.server_group", "switch.aaa.accounting", "switch.port.auth_policy",
            "switch.route.ospf")


def read_cx_system(request, attributes: str) -> dict:
    """Read system identity on CX releases that store the serial under the chassis."""
    try:
        return request("GET", "/system", params={"attributes": attributes + ",serial_number"}).json()
    except WriterError as exc:
        if "HTTP 400" not in str(exc) or "serial_number" not in str(exc):
            raise
    system = request("GET", "/system", params={"attributes": attributes}).json()
    chassis = request("GET", "/system/subsystems/chassis,1", params={"attributes": "product_info"}).json()
    system["serial_number"] = (chassis.get("product_info") or {}).get("serial_number")
    return system


class ArubaCxWriter:
    family = "switch"
    key = "aruba_cx_10_14"
    # Every typed operation implemented below (asserted equal to domain.adapters.CX_TYPED_OPS by a contract test).
    OPERATIONS = ("switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans",
                  "switch.port.admin_state", "switch.port.poe", "switch.lldp", "switch.lag.members",
                  "switch.central.disable", "switch.port.description", "switch.port.stp_bpdu_guard",
                  "switch.port.storm_control", "switch.port.qos_trust", "switch.system.ntp", "switch.system.dns",
                  "switch.system.syslog", "config.restore", "switch.vlan.voice", "switch.port.auth_dot1x", "switch.port.auth_mab",
                  "switch.acl.define", "switch.acl.apply", "switch.route.static", "switch.qos.dscp_policy", "switch.vsf.member",
                  "switch.aaa.radius_server", "switch.aaa.server_group", "switch.aaa.accounting", "switch.port.auth_policy",
                  "switch.route.ospf")

    def __init__(self, creds: Credentials, *, verify: bool | str = True, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self.creds = creds
        self._restore_checkpoints: dict[str, bytes] = {}
        self._session_args = {"verify": verify, "timeout": timeout, "transport": transport}
        self.session = CxSession(creds.host, creds.username, creds.password, verify=verify, timeout=timeout,
                                 transport=transport, api_version=(creds.extra or {}).get("api_version"))

    def reconnect(self) -> None:
        """Fresh session after a reboot (the old HTTP client cannot be reused). Read-only until a caller writes."""
        try:
            self.session.close()
        except Exception as _exc:  # noqa: BLE001 — the old connection died with the reboot
            _log.debug("best-effort closing a session that died with the reboot: %s", type(_exc).__name__)
        self.session = CxSession(self.creds.host, self.creds.username, self.creds.password, **self._session_args,
                                 api_version=(self.creds.extra or {}).get("api_version"))
        self.session.open()

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

    def read_diagnostics(self, *, kind: str) -> dict:
        """Read-only AnyCLI show command, selected by fixed key not client text.

        The device's own supported-command catalogue is checked first. POST to
        /cli is used solely for vendor-documented show commands, never config.
        """
        from ..device_diagnostics import CX_SHOW, safe_output
        if kind not in CX_SHOW:
            raise WriterError("UNSUPPORTED", "This CX diagnostic is not on the read-only allowlist.")
        command = CX_SHOW[kind]
        commands = self._req("GET", "/cli/commands").json()
        allowed = commands.get("commands") if isinstance(commands, dict) else None
        if not isinstance(allowed, list) or command not in allowed:
            raise WriterError("UNSUPPORTED", "The switch does not advertise this read-only show command.")
        response = self._req("POST", "/cli", json={"cmd": command})
        return {"kind": kind, "command": command, "output": safe_output(response.text),
                "source": "CX_REST_ANYCLI"}

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
        d = read_cx_system(self._req, "platform_name,software_version,hostname")
        return {"model": d.get("platform_name"), "firmware": d.get("software_version"), "hostname": d.get("hostname"),
                "serial_number": d.get("serial_number"), **self.protocol()}

    def firmware_upload(self, path, *, image: str = "secondary", filename: str | None = None) -> dict:
        """Upload an image to the NON-booted slot (documented AOS-CX REST: PUT /firmware?image=<slot> with a
        multipart 'fileupload'). Never reboots. The switch itself validates the image signature on upload."""
        if image not in ("primary", "secondary"):
            raise WriterError("INVALID", "Image slot must be primary or secondary.")
        status = self.firmware_status()
        if status.get("booted_image") == image:
            raise WriterError("PRECONDITION", f"Refusing to overwrite the booted {image} image.")
        with open(path, "rb") as fh:
            self._req("PUT", "/firmware", params={"image": image}, files={"fileupload": (filename or Path(path).name, fh)},
                      timeout=600.0)
        return self.firmware_status()

    def firmware_boot(self, image: str) -> None:
        """Reboot into the given slot (documented: POST /boot?image=<slot>). A dropped connection afterwards is
        expected; callers verify by reading /firmware again, never by re-sending the boot request."""
        if image not in ("primary", "secondary"):
            raise WriterError("INVALID", "Image slot must be primary or secondary.")
        try:
            self._req("POST", "/boot", params={"image": image})
        except WriterError as exc:
            if exc.category != "CONNECTION_LOST":
                raise

    def firmware_status(self) -> dict:
        """Documented AOS-CX REST singleton, read-only; never stage or reboot."""
        data = self._req("GET", "/firmware").json()
        keys = ("current_version", "primary_version", "secondary_version", "default_image", "booted_image")
        if not isinstance(data, dict) or not isinstance(data.get("current_version"), str) or not data["current_version"].strip():
            raise WriterError("PARSE_ERROR", "Firmware status response is incomplete.")
        result = {key: data[key][:128] if isinstance(data.get(key), str) and data[key].strip() else None for key in keys}
        for slot in ("default_image", "booted_image"):
            if result[slot] not in ("primary", "secondary"):
                result[slot] = None
        result["state"] = "COLLECTED" if all(result.get(k) for k in ("current_version", "default_image", "booted_image")) else "PARTIAL"
        result["source"] = "CX_REST_FIRMWARE"
        return result

    def telemetry(self) -> dict:
        """READ-ONLY monitoring: identity, CPU/memory, hardware health, every port (link, speed, VLAN,
        counters), PoE per port and LLDP neighbours. Tolerant of attributes a firmware does not expose."""
        sysd = read_cx_system(self._req, "hostname,platform_name,software_version,boot_time,subsystems")
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
                          "rx_packets": st.get("rx_packets"), "tx_packets": st.get("tx_packets"),
                          "rx_crc_errors": st.get("rx_crc_errors"),
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
            except WriterError as _exc:
                _log.debug("PoE status unavailable on %s (shown as not measured): %s", p["name"], type(_exc).__name__)
            try:
                nb = self._req("GET", f"/system/interfaces/{q}/lldp_neighbors", params={"depth": 2}).json() or {}
                for n in (nb.values() if isinstance(nb, dict) else []):
                    if isinstance(n, dict):
                        info = n.get("neighbor_info") or {}
                        p["lldp"] = {"system_name": info.get("chassis_name") or n.get("chassis_id"),
                                     "port": info.get("port_description") or n.get("port_id"),
                                     "capabilities": info.get("chassis_capability_enabled")}
                        break
            except WriterError as _exc:
                _log.debug("LLDP neighbour unavailable on %s (shown as not measured): %s", p["name"], type(_exc).__name__)
        import os
        if os.environ.get("INM_CX_OPTICAL_DOM", "false").lower() == "true":
            from ..device_diagnostics import parse_optical_dom
            try:
                optical = parse_optical_dom(self.read_diagnostics(kind="optical_dom")["output"])
            except WriterError as exc:
                optical = {"state": exc.category, "ports": {}}
            hw["optical_dom"] = {k: v for k, v in optical.items() if k != "ports"}
            for p in ports:
                p["optical_dom"] = optical["ports"].get(p["name"])
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
        name = f"inm-cp-{time.time_ns()}"
        try:
            self._req("PUT", f"/fullconfigs/{name}", params={"from": f"{self.prefix}/fullconfigs/running-config"})
            return name
        except WriterError as exc:
            if exc.category != "DEVICE_REJECTED" or "duplicate checkpoint" not in str(exc).lower():
                raise

        # AOS-CX can reject a new name when an existing checkpoint has identical
        # contents. Reuse only our own checkpoint after comparing the full JSON.
        baseline = json.loads(self.backup())
        checkpoints = self._req("GET", "/fullconfigs").json()
        if isinstance(checkpoints, list):
            for path in reversed(checkpoints):
                if not isinstance(path, str):
                    continue
                candidate = path.rsplit("/", 1)[-1]
                if not re.fullmatch(r"inm-cp-\d+", candidate):
                    continue
                saved = self._req("GET", f"/fullconfigs/{candidate}").json()
                if saved == baseline:
                    return candidate
        # Callers persist the full baseline before changing hardware. Keep this
        # session's exact configuration for restore when firmware rejects native
        # checkpoints with duplicate contents.
        self._restore_checkpoints[name] = json.dumps(baseline, sort_keys=True).encode()
        return name

    def rollback(self, checkpoint: str) -> None:
        if not re.match(r"^inm-cp-\d+$", checkpoint):
            raise WriterError("INVALID", "Unknown checkpoint.")
        if checkpoint in self._restore_checkpoints:
            baseline = self._restore_checkpoints[checkpoint]
            self.restore(baseline)
            if json.loads(self.backup()) != json.loads(baseline):
                raise WriterError("ROLLBACK_FAILED", "Full configuration restore did not match its checkpoint baseline.")
            return
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
        if k in ("switch.vlan.voice", "switch.port.auth_dot1x", "switch.port.auth_mab"):
            from ...domain.change_control import validate_operation, PolicyViolation
            try:
                validate_operation(op, family="switch")
            except PolicyViolation as exc:
                raise WriterError("INVALID", str(exc)) from None
            if k == "switch.vlan.voice":
                return [{"method": "PUT", "path": f"/system/vlans/{op['vlan']}", "json": {"voice": v == "enabled"}}]
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}/port_access_auth_configurations/{op['method']}",
                     "json": {"auth_enable": True, "cached_reauth_enable": False, "canned_eap_success_enable": False}}]
        if k == "switch.vlan.define":
            return [{"method": "POST", "path": "/system/vlans", "json": {"id": int(v), "name": op.get("name") or f"VLAN{int(v)}"}}]
        if k == "switch.port.access_vlan":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"vlan_mode": "access", "vlan_tag": self._vlan_url(v)}}]
        if k == "switch.port.trunk_vlans":
            native = int(op.get("native", 1))
            return [{"method": "PUT", "path": f"/system/interfaces/{_port(op['port'])}",
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
                     "json": {"rate_limits": {**lim, **{t + "_units": "kbps" for t in lim}}}}]
        if k == "switch.port.qos_trust":
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}", "json": {"qos_config": {"qos_trust": v}}}]
        if k == "switch.system.ntp":
            vrf = op.get("vrf", "mgmt")
            return [{"method": "POST", "path": f"/system/vrfs/{vrf}/ntp_associations",
                     "json": {"address": v, "vrf": f"{self.prefix}/system/vrfs/{vrf}",
                              "association_attributes": {"burst_mode": "iburst", "prefer": bool(op.get("prefer"))}}}]
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
        if k in RC17_OPS:
            return self._render_rc17(op)
        if k in RC18_OPS:
            return self._render_rc18(op)
        raise WriterError("UNSUPPORTED", f"{k} is not supported by {self.key}")

    # ---- rc17: ACLs, static routes, DSCP QoS policy, VSF member provisioning --------------------------------
    # Request shapes follow the AOS-CX 10.x REST object model (acls "{name},{list_type}" with cfg_aces, vrfs/*/
    # static_routes + static_nexthops, classes/policies with cfg_entries, vsf_members/*/links). Exact attribute
    # names must be confirmed against the device's own Swagger (/api/) before certification — see
    # docs/PROTOCOL_ASSUMPTIONS (A-CX-09..13). A device that rejects a body returns DEVICE_REJECTED; nothing is guessed.
    def _ref(self, path: str) -> str:
        return f"{self.prefix}{path}"

    @staticmethod
    def _cx_ip(v: str) -> str:
        import ipaddress
        if v == "any":
            return "any"
        n = ipaddress.IPv4Network(v)
        return f"{n.network_address}/{n.netmask}"

    def _render_rc17(self, op: dict) -> list[dict]:
        from urllib.parse import quote
        k = op["op"]
        if k == "switch.acl.define":
            aces = {}
            for e in op["entries"]:
                ace = {"action": e["action"], "src_ip": self._cx_ip(e.get("src", "any")),
                       "dst_ip": self._cx_ip(e.get("dst", "any"))}
                if e.get("protocol", "any") != "any":
                    ace["protocol"] = {"tcp": 6, "udp": 17, "icmp": 1}[e["protocol"]]
                if e.get("dst_port") is not None:
                    ace["dst_l4_port_min"] = ace["dst_l4_port_max"] = e["dst_port"]
                if e.get("comment"):
                    ace["comment"] = str(e["comment"])[:64]
                aces[str(e["seq"])] = ace
            return [{"method": "PUT", "path": f"/system/acls/{op['name']},ipv4",
                     "json": {"name": op["name"], "list_type": "ipv4", "cfg_aces": aces}}]
        if k == "switch.acl.apply":
            ref = self._ref(f"/system/acls/{op['value']},ipv4") if op.get("value") else None
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}", "json": {"aclv4_in_cfg": ref}}]
        if k == "switch.route.static":
            base = "/system/vrfs/default/static_routes"
            route = f"{base}/{quote(op['prefix'], safe='')}"
            return [{"method": "POST", "path": base, "create_if_missing": route,
                     "json": {"prefix": op["prefix"], "address_family": "ipv4", "type": "forward",
                              "vrf": self._ref("/system/vrfs/default")}},
                    {"method": "POST", "path": f"{route}/static_nexthops",
                     "json": {"id": 0, "ip_address": op["next_hop"], "distance": op.get("distance", 1), "type": "nexthop"}}]
        if k == "switch.qos.dscp_policy":
            name = op["name"]
            calls = [{"method": "PUT", "path": f"/system/classes/{name},ipv4",
                      "json": {"name": name, "type": "ipv4", "cfg_entries": {"10": {
                          "type": "match", "protocol": {"tcp": 6, "udp": 17}[op["protocol"]],
                          "dst_l4_port_min": op["dst_port"], "dst_l4_port_max": op["dst_port"]}}}},
                     {"method": "PUT", "path": f"/system/policies/{name}",
                      "json": {"name": name, "cfg_entries": {"10": {"class": self._ref(f"/system/classes/{name},ipv4"),
                                                                   "action_set": {"dscp": op["dscp"]}}}}}]
            calls += [{"method": "PATCH", "path": f"/system/interfaces/{_port(p)}",
                       "json": {"policy_in_cfg": self._ref(f"/system/policies/{name}")}} for p in op.get("ports", [])]
            return calls
        if k == "switch.vsf.member":
            n = int(op["member"])
            calls = [{"method": "PUT", "path": f"/system/vsf_members/{n}", "json": {"id": n, "type": op["member_type"]}}]
            for link, ports in sorted(op["links"].items()):
                calls.append({"method": "PUT", "path": f"/system/vsf_members/{n}/links/{link}",
                              "json": {"id": int(link), "interfaces": [self._ref(f"/system/interfaces/{_port(p)}") for p in ports]}})
            return calls
        raise WriterError("UNSUPPORTED", f"{k} is not supported by {self.key}")

    def _readback_rc17(self, op: dict) -> tuple[bool, dict]:
        from urllib.parse import quote
        k = op["op"]

        def get(path, **params):
            try:
                return self._req("GET", path, params=params or None).json()
            except WriterError as exc:
                if "HTTP 404" in str(exc):
                    return None
                raise
        if k == "switch.acl.define":
            d = get(f"/system/acls/{op['name']},ipv4", depth=2) or {}
            got = d.get("cfg_aces") or {}
            want = self._render_rc17(op)[0]["json"]["cfg_aces"]
            ok = {s: {x: a.get(x) for x in want[s]} for s, a in got.items() if s in want} == want and set(got) == set(want)
            return ok, {"entries": len(got), "sequences": sorted(got, key=int)}
        if k == "switch.acl.apply":
            d = get(f"/system/interfaces/{_port(op['port'])}") or {}
            ref = d.get("aclv4_in_cfg")
            ok = (ref is None) if not op.get("value") else str(ref or "").endswith(f"/system/acls/{op['value']},ipv4")
            return ok, {"aclv4_in_cfg": ref}
        if k == "switch.route.static":
            hops = get(f"/system/vrfs/default/static_routes/{quote(op['prefix'], safe='')}/static_nexthops", depth=2) or {}
            got = sorted(str(h.get("ip_address")) for h in hops.values() if isinstance(h, dict))
            return op["next_hop"] in got, {"prefix": op["prefix"], "next_hops": got}
        if k == "switch.qos.dscp_policy":
            pol = get(f"/system/policies/{op['name']}", depth=2) or {}
            dscp = ((pol.get("cfg_entries") or {}).get("10") or {}).get("action_set", {}).get("dscp")
            applied = [p for p in op.get("ports", []) if str((get(f"/system/interfaces/{_port(p)}") or {})
                                                            .get("policy_in_cfg") or "").endswith(f"/system/policies/{op['name']}")]
            return dscp == op["dscp"] and len(applied) == len(op.get("ports", [])), {"dscp": dscp, "applied_ports": applied}
        if k == "switch.vsf.member":
            n = int(op["member"])
            m = get(f"/system/vsf_members/{n}", depth=1) or {}
            links = {l: sorted(str(x).rsplit("/", 1)[-1].replace("%2F", "/") for x in (get(f"/system/vsf_members/{n}/links/{l}") or {}).get("interfaces") or [])
                     for l in op["links"]}
            ok = m.get("type") == op["member_type"] and all(links[l] == sorted(op["links"][l]) for l in op["links"])
            return ok, {"member": n, "type": m.get("type"), "links": links}
        raise WriterError("UNSUPPORTED", k)

    # ---- rc18: RADIUS / AAA lifecycle, port authentication policy, single-area OSPF (6200) ------------------------
    # Documented AOS-CX 10.x resources (developer.arubanetworks.com/aoscx): radius_servers keyed
    # "{address},{port},{port_type}" under a VRF; aaa_server_groups keyed by group_name with membership and priority
    # held on each server's `server_group` map; aaa_accounting_attributes keyed by session_type "port-access";
    # port_access_auth_configurations keyed by authentication_method; ospf_routers keyed by instance_tag.
    @staticmethod
    def _srv_path(r: dict) -> str:
        return f"/system/vrfs/{r['vrf']}/radius_servers/{r['address']},{r['port']},{r['port_type']}"

    _SRV_FIELDS = ("timeout", "retries", "accounting_udp_port", "auth_type", "tracking_enable")
    _AUTH_FIELDS = ("auth_enable", "reauth_enable", "cached_reauth_enable", "reauth_period", "quiet_period",
                    "max_retries", "max_requests", "eapol_timeout", "discovery_period", "radius_server_group")

    @staticmethod
    def _norm18(op: dict) -> dict:
        """Validate + fill documented defaults (vrf, port, port_type...) for any caller, keeping the resolved secret."""
        from ...domain import change_control as CC
        secret = op.get("_secret")
        clean = {k: v for k, v in op.items() if k != "_secret"}
        if clean.get("op") == "switch.aaa.radius_server" and not clean.get("secret_ref") and \
                clean.get("action", "create") == "create":
            clean["secret_ref"] = "resolved" if secret else "unresolved"   # apply still refuses without _secret
        try:
            out = CC.validate_operation(clean, family="switch")
        except CC.PolicyViolation as exc:
            raise WriterError("INVALID", str(exc)) from None
        return out | ({"_secret": secret} if secret else {})

    def _render_rc18(self, op: dict) -> list[dict]:
        op = self._norm18(op)
        k, action = op["op"], op.get("action", "create")
        if k == "switch.aaa.radius_server":
            body = {f: op[f] for f in self._SRV_FIELDS if f in op}
            if op.get("_secret"):
                body["passkey"] = op["_secret"]
            elif action == "create" or op.get("secret_ref"):
                body["passkey"] = "********"          # render preview only; apply refuses a masked secret
            if action == "create":
                return [{"method": "POST", "path": f"/system/vrfs/{op['vrf']}/radius_servers",
                         "json": {"address": op["address"], "port": op["port"], "port_type": op["port_type"],
                                  "vrf": self._ref(f"/system/vrfs/{op['vrf']}"), **body}}]
            if action == "modify":
                return [{"method": "PATCH", "path": self._srv_path(op), "json": body}]
            return [{"method": "DELETE", "path": self._srv_path(op), "json": None}]
        if k == "switch.aaa.server_group":
            gpath = f"/system/aaa_server_groups/{op['group_name']}"
            if action == "delete":
                return [{"method": "GROUP_MEMBERS", "path": gpath, "json": {"servers": []}},
                        {"method": "DELETE", "path": gpath, "json": None}]
            first = ({"method": "POST", "path": "/system/aaa_server_groups",
                      "json": {"group_name": op["group_name"], "group_type": "radius",
                               "load_balancing_mode": op["load_balancing_mode"]}} if action == "create" else
                     {"method": "PATCH", "path": gpath, "json": {"load_balancing_mode": op["load_balancing_mode"]}})
            return [first, {"method": "GROUP_MEMBERS", "path": gpath, "json": {"servers": op["servers"]}}]
        if k == "switch.aaa.accounting":
            path = "/system/aaa_accounting_attributes/port-access"
            if op["accounting_mode"] == "disabled":
                return [{"method": "DELETE", "path": path, "json": None, "missing_ok": True}]
            body = {"session_type": "port-access", "accounting_mode": op["accounting_mode"]}
            if op["accounting_mode"] == "start-stop":
                body["interim_update_enable"] = "interim_update_interval" in op
                if "interim_update_interval" in op:
                    body["interim_update_interval"] = op["interim_update_interval"]
            return [{"method": "UPSERT", "path": path, "collection": "/system/aaa_accounting_attributes", "json": body}]
        if k == "switch.port.auth_policy":
            base = f"/system/interfaces/{_port(op['port'])}/port_access_auth_configurations"
            body = {f: op[f] for f in self._AUTH_FIELDS if f in op}
            if "radius_server_group" in body:
                body["radius_server_group"] = self._ref(f"/system/aaa_server_groups/{body['radius_server_group']}")
            if action == "create":
                return [{"method": "POST", "path": base, "json": {"authentication_method": op["method"], **body}}]
            if action == "modify":
                return [{"method": "PATCH", "path": f"{base}/{op['method']}", "json": body}]
            return [{"method": "DELETE", "path": f"{base}/{op['method']}", "json": None}]
        if k == "switch.route.ospf":
            base = f"/system/vrfs/{op['vrf']}/ospf_routers"
            rpath = f"{base}/{op['instance_tag']}"
            if op.get("action") == "delete":
                return [{"method": "DELETE", "path": rpath, "json": None}]
            router = {"admin_router_id": op["router_id"], "maximum_paths": op["maximum_paths"],
                      "passive_interface_default": op["passive_interface_default"], "redistribute": op["redistribute"],
                      "active_interfaces": [self._ref(f"/system/interfaces/{i if re.fullmatch(r'vlan[0-9]{1,4}', i) else _port(i)}")
                                            for i in op["interfaces"]]}
            calls = [{"method": "POST", "path": base, "json": {"instance_tag": op["instance_tag"], **router}}
                     if op.get("action") == "create" else {"method": "PATCH", "path": rpath, "json": router}]
            calls.append({"method": "UPSERT", "path": f"{rpath}/areas/{op['area']}", "collection": f"{rpath}/areas",
                          "json": {"area_id": op["area"], "area_type": op["area_type"]}})
            return calls
        raise WriterError("UNSUPPORTED", f"{k} is not supported by {self.key}")

    def _get_or_none(self, path: str, **params):
        try:
            return self._req("GET", path, params=params or None).json()
        except WriterError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def _set_group_members(self, gpath: str, servers: list[dict]) -> None:
        """Membership + priority live on each RADIUS server's `server_group` map. Read-modify-write so the server's
        other memberships (e.g. the built-in 'radius' family group) are preserved; priority 1 = primary."""
        gref = self._ref(gpath)
        wanted = {self._srv_path(r): i + 1 for i, r in enumerate(servers)}
        for vrf in ("default", "mgmt"):
            current = self._get_or_none(f"/system/vrfs/{vrf}/radius_servers", depth=1) or {}
            for key, srv in current.items():
                path = f"/system/vrfs/{vrf}/radius_servers/{key}"
                if gref in (srv.get("server_group") or {}) and path not in wanted:
                    groups = {g: v for g, v in srv["server_group"].items() if g != gref}
                    self._req("PATCH", path, json={"server_group": groups})
        for path, prio in wanted.items():
            srv = self._get_or_none(path, depth=1)
            if srv is None:
                raise WriterError("PRECONDITION", f"RADIUS server {path.rsplit('/', 1)[-1]} does not exist; create it first.")
            groups = dict(srv.get("server_group") or {})
            groups[gref] = prio
            self._req("PATCH", path, json={"server_group": groups})

    def _apply_rc18(self, op: dict) -> list[dict]:
        op = self._norm18(op)
        k = op["op"]
        if k == "switch.route.ospf":
            platform = str(read_cx_system(self._req, "platform_name").get("platform_name") or "")
            if "6200" not in platform:
                raise WriterError("UNSUPPORTED", f"OSPF is not available on {platform or 'this platform'} "
                                                 "(CX 6100: static routing only).")
        if k == "switch.aaa.radius_server" and op.get("action", "create") != "delete":
            if (op.get("action") == "create" or op.get("secret_ref")) and not op.get("_secret"):
                raise WriterError("PRECONDITION", "The RADIUS shared secret could not be resolved from secret_ref.")
        sent = []
        for call in self._render_rc18(op):
            m = call["method"]
            if m == "GROUP_MEMBERS":
                self._set_group_members(call["path"], call["json"]["servers"])
            elif m == "UPSERT":
                exists = self._get_or_none(call["path"]) is not None
                if exists:
                    self._req("PATCH", call["path"], json={x: y for x, y in call["json"].items()
                                                           if x not in ("session_type", "area_id")})
                else:
                    self._req("POST", call["collection"], json=call["json"])
            elif m == "DELETE":
                try:
                    self._req("DELETE", call["path"])
                except WriterError as exc:
                    if not (call.get("missing_ok") and "HTTP 404" in str(exc)):
                        raise
            else:
                self._req(m, call["path"], json=call["json"])
            sent.append({**call, "json": ({**call["json"], "passkey": "********"} if isinstance(call["json"], dict)
                                          and "passkey" in call["json"] else call["json"])})
        return sent

    def _readback_rc18(self, op: dict) -> tuple[bool, dict]:
        op = self._norm18(op)
        k, action = op["op"], op.get("action", "create")
        if k == "switch.aaa.radius_server":
            d = self._get_or_none(self._srv_path(op), depth=1)
            if action == "delete":
                return d is None, {"present": d is not None}
            if d is None:
                return False, {"present": False}
            got = {f: d.get(f) for f in self._SRV_FIELDS if f in op}
            return got == {f: op[f] for f in self._SRV_FIELDS if f in op}, {"present": True, **got}
        if k == "switch.aaa.server_group":
            gpath = f"/system/aaa_server_groups/{op['group_name']}"
            g = self._get_or_none(gpath)
            if action == "delete":
                return g is None, {"present": g is not None}
            gref = self._ref(gpath)
            prios = {}
            for r in op["servers"]:
                srv = self._get_or_none(self._srv_path(r), depth=1) or {}
                prios[r["address"]] = (srv.get("server_group") or {}).get(gref)
            ok = bool(g) and g.get("load_balancing_mode") == op["load_balancing_mode"] and \
                [prios[r["address"]] for r in op["servers"]] == list(range(1, len(op["servers"]) + 1))
            return ok, {"load_balancing_mode": (g or {}).get("load_balancing_mode"), "priorities": prios}
        if k == "switch.aaa.accounting":
            d = self._get_or_none("/system/aaa_accounting_attributes/port-access")
            if op["accounting_mode"] == "disabled":
                return d is None, {"present": d is not None}
            want = self._render_rc18(op)[0]["json"]
            got = {x: (d or {}).get(x) for x in want}
            return got == want, got
        if k == "switch.port.auth_policy":
            d = self._get_or_none(f"/system/interfaces/{_port(op['port'])}/port_access_auth_configurations/{op['method']}")
            if action == "delete":
                return d is None, {"present": d is not None}
            want = {f: op[f] for f in self._AUTH_FIELDS if f in op}
            got = {f: (d or {}).get(f) for f in want}
            if "radius_server_group" in want:
                got["radius_server_group"] = str(got["radius_server_group"] or "").rsplit("/", 1)[-1] or None
            return d is not None and got == want, got
        if k == "switch.route.ospf":
            rpath = f"/system/vrfs/{op['vrf']}/ospf_routers/{op['instance_tag']}"
            r = self._get_or_none(rpath, depth=1)
            if action == "delete":
                return r is None, {"present": r is not None}
            area = self._get_or_none(f"{rpath}/areas/{op['area']}")
            ifs = sorted(str(x).rsplit("/", 1)[-1].replace("%2F", "/") for x in (r or {}).get("active_interfaces") or [])
            ok = bool(r) and r.get("admin_router_id") == op["router_id"] and bool(area) and \
                area.get("area_type") == op["area_type"] and ifs == sorted(op["interfaces"]) and \
                sorted(r.get("redistribute") or []) == op["redistribute"]
            return ok, {"router_id": (r or {}).get("admin_router_id"), "area": (area or {}).get("area_id"),
                        "interfaces": ifs}
        raise WriterError("UNSUPPORTED", k)

    def optics(self) -> dict:
        """Transceiver identification + DOM per port/lane from the advertised read-only show command (A-CX-15)."""
        from ..optics import parse_transceiver_detail
        try:
            out = self.read_diagnostics(kind="transceivers")["output"]
        except WriterError as exc:
            if exc.category == "UNSUPPORTED":
                return {"state": "UNSUPPORTED", "ports": {}, "reason": str(exc)}
            raise
        return parse_transceiver_detail(out)

    def port_access_clients(self, port: str) -> list[dict]:
        """Authentication status monitoring (read-only): clients on a port with method and state."""
        d = self._get_or_none(f"/system/interfaces/{_port(port)}/port_access_clients", depth=2) or {}
        out = []
        for mac, c in d.items():
            c = c if isinstance(c, dict) else {}
            out.append({"mac": c.get("mac", mac), "status": c.get("status"), "methods": sorted((c.get("auth_attributes") or {}).keys()),
                        "role": c.get("role") or c.get("applied_role"), "vlan": c.get("vlan_id") or c.get("vlan")})
        return out

    def apply(self, op: dict) -> list[dict]:
        sent = []
        if op.get("op") in RC18_OPS:
            return self._apply_rc18(op)
        if op.get("op") == "switch.vsf.member":
            platform = str(read_cx_system(self._req, "platform_name").get("platform_name") or "")
            if "6200" not in platform:
                raise WriterError("UNSUPPORTED", f"VSF provisioning is not supported on {platform or 'this platform'}.")
        for call in self.render(op):
            body = call["json"]
            if call.get("create_if_missing"):
                try:
                    self._req("GET", call["create_if_missing"])
                    continue                     # route already exists; only its next hop is added
                except WriterError as exc:
                    if "HTTP 404" not in str(exc):
                        raise
            if op["op"] == "switch.vlan.voice":
                baseline = self._req("GET", call["path"], params={"selector": "writable"}).json()
                if not isinstance(baseline, dict) or baseline.get("id") != op["vlan"] or baseline.get("type") != "static" or type(baseline.get("voice")) is not bool:
                    raise WriterError("PRECONDITION", "Existing static VLAN and explicit voice baseline required.")
                body = {**baseline, **body}
            if op["op"] in ("switch.port.auth_dot1x", "switch.port.auth_mab"):
                port_path = f"/system/interfaces/{_port(op['port'])}"
                port = self._req("GET", port_path, params={"selector": "configuration"}).json()
                if not isinstance(port, dict) or port.get("admin") != "down" or port.get("vlan_mode") != "access":
                    raise WriterError("PRECONDITION", "Port must be administratively down in access mode.")
                if port.get("port_access_auth_mode") != "client-mode" or any(
                    value for key, value in port.items() if key.startswith("port_access_") and
                    ("role" in key or "override" in key)) or port.get("aaa_port_access_radius_override_enable"):
                    raise WriterError("PRECONDITION", "Require explicit client-mode and no configured fallback/override role.")
                auth = self._req("GET", call["path"], params={"selector": "configuration"}).json()
                if not isinstance(auth, dict) or auth.get("authentication_method") != op["method"] or not auth.get("radius_server_group"):
                    raise WriterError("PRECONDITION", "Provision the selected method and its RADIUS group first.")
                if type(auth.get("auth_enable")) is not bool:
                    raise WriterError("PRECONDITION", "No explicit authentication baseline.")
            if op["op"] == "switch.port.trunk_vlans":
                # PATCH merges reference maps. PUT the writable configuration
                # to replace the allowed VLAN set while preserving other settings.
                body = self._req("GET", call["path"], params={"selector": "writable"}).json()
                body.update(call["json"])
            self._req(call["method"], call["path"], json=body)
            sent.append(call)
        return sent

    def readback(self, op: dict) -> tuple[bool, dict]:
        k, v = op.get("op"), op.get("value")
        if k in RC17_OPS:
            return self._readback_rc17(op)
        if k in RC18_OPS:
            return self._readback_rc18(op)
        if k == "switch.vlan.voice":
            d = self._req("GET", f"/system/vlans/{op['vlan']}", params={"selector": "configuration"}).json()
            return (d.get("id") == op["vlan"] and d.get("voice") is (v == "enabled")), {"id": d.get("id"), "voice": d.get("voice")}
        if k in ("switch.port.auth_dot1x", "switch.port.auth_mab"):
            d = self._req("GET", f"/system/interfaces/{_port(op['port'])}/port_access_auth_configurations/{op['method']}", params={"selector": "configuration"}).json()
            return (d.get("authentication_method") == op["method"] and d.get("auth_enable") is True and d.get("cached_reauth_enable") is False and d.get("canned_eap_success_enable") is False), {key: d.get(key) for key in ("authentication_method", "auth_enable", "cached_reauth_enable", "canned_eap_success_enable")}
        if k == "switch.system.ntp":
            d = self._req("GET", f"/system/vrfs/{op.get('vrf', 'mgmt')}/ntp_associations",
                          params={"depth": 2, "selector": "configuration"}).json() or {}
            got = sorted(str(x.get("address")) for x in d.values() if isinstance(x, dict))
            return v in got, {"ntp_servers": got}
        if k == "switch.system.syslog":
            d = self._req("GET", "/system/syslog_remotes", params={"depth": 2, "selector": "configuration"}).json() or {}
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
            return (d.get("vlan_mode") == "access" and _vlan_ids(d.get("vlan_tag")) == [int(v)],
                    {"vlan_mode": d.get("vlan_mode"), "vlan_tag": d.get("vlan_tag")})
        if k == "switch.port.trunk_vlans":
            got = _vlan_ids(d.get("vlan_trunks"))
            return (got == sorted(v) and d.get("vlan_mode") == "native-untagged"
                    and _vlan_ids(d.get("vlan_tag")) == [int(op.get("native", 1))],
                    {"vlan_mode": d.get("vlan_mode"), "vlan_trunks": got})
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
            got = (d.get("qos_config") or {}).get("qos_trust")
            return got == v, {"qos_trust": got}
        raise WriterError("UNSUPPORTED", f"No readback for {k}")
