"""Aruba AOS-CX 10.14 REST configuration writer (CX 6100 / CX 6200).

Uses only the documented v10.14 REST resources:
  POST   /login, /logout                                    session cookie auth
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

API = "/rest/v10.14"
PORT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{1,3}$|^lag\d{1,3}$")


def _port(p: str) -> str:
    if not isinstance(p, str) or not PORT_RE.match(p):
        raise WriterError("INVALID", f"Invalid port name {p!r}")
    return quote(p, safe="")


def _vlan_url(v: int) -> str:
    return f"{API}/system/vlans/{int(v)}"


class ArubaCxWriter:
    family = "switch"
    key = "aruba_cx_10_14"

    def __init__(self, creds: Credentials, *, verify: bool | str = True, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self.creds = creds
        self._http = httpx.Client(base_url=f"https://{creds.host}", verify=verify, timeout=timeout,
                                  transport=transport, trust_env=False, follow_redirects=False)
        self._open = False

    # ---- session -------------------------------------------------------------------------
    def open(self) -> None:
        try:
            r = self._http.post(API + "/login", params={"username": self.creds.username,
                                                       "password": self.creds.password})
        except httpx.HTTPError as exc:
            raise WriterError("UNREACHABLE", f"Cannot reach switch: {type(exc).__name__}") from None
        if r.status_code in (401, 403):
            raise WriterError("AUTH_REJECTED", "Switch rejected the credentials.")
        if r.status_code >= 400:
            raise WriterError("REST_DISABLED", f"Login failed (HTTP {r.status_code}).")
        self._open = True

    def close(self) -> None:
        try:
            if self._open:
                self._http.post(API + "/logout")
        except httpx.HTTPError:
            pass
        finally:
            self._open = False
            self._http.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, API + path, **kw)
        except httpx.HTTPError as exc:
            raise WriterError("CONNECTION_LOST", f"{method} {path}: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise WriterError("DEVICE_REJECTED", f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r

    # ---- read ------------------------------------------------------------------------------
    def identity(self) -> dict:
        d = self._req("GET", "/system", params={"attributes": "platform_name,software_version,hostname,"
                                                              "subsystems"}).json()
        return {"model": d.get("platform_name"), "firmware": d.get("software_version"), "hostname": d.get("hostname")}

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
        self._req("PUT", f"/fullconfigs/{name}", params={"from": f"{API}/fullconfigs/running-config"})
        return name

    def rollback(self, checkpoint: str) -> None:
        if not re.match(r"^inm-cp-\d+$", checkpoint):
            raise WriterError("INVALID", "Unknown checkpoint.")
        self._req("PUT", "/fullconfigs/running-config", params={"from": f"{API}/fullconfigs/{checkpoint}"})

    def save(self) -> None:
        self._req("PUT", "/fullconfigs/startup-config", params={"from": f"{API}/fullconfigs/running-config"})

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
                     "json": {"vlan_mode": "access", "vlan_tag": _vlan_url(v)}}]
        if k == "switch.port.trunk_vlans":
            native = int(op.get("native", 1))
            return [{"method": "PATCH", "path": f"/system/interfaces/{_port(op['port'])}",
                     "json": {"vlan_mode": "native-untagged", "vlan_tag": _vlan_url(native),
                              "vlan_trunks": [_vlan_url(x) for x in v]}}]
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
        if k == "switch.lag.members":
            lag = op["lag"]
            if not re.match(r"^lag\d{1,3}$", lag):
                raise WriterError("INVALID", "LAG must be named lagN.")
            members = [f"{API}/system/interfaces/{_port(p)}" for p in v]
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
        raise WriterError("UNSUPPORTED", f"No readback for {k}")
