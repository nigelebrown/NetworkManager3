"""Stateful protocol emulators for AOS-CX 10.14 REST and Aruba Instant 8.10.

Used for (1) automated contract tests of every write, failure and rollback path and (2) DEMO devices so
the full GUI workflow can be exercised. Results produced here are always labelled EMULATED and are never
accepted as hardware certification evidence.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import Credentials
from .aruba_cx import API, ArubaCxWriter
from .aruba_instant import ArubaInstantWriter


IFACE_WRITABLE = {"aclv4_in_cfg", "policy_in_cfg", "admin", "vlan_mode", "vlan_tag", "vlan_trunks", "description", "other_config", "stp_config",
                  "rate_limits", "qos_config"}


class CxEmulator:
    """In-memory AOS-CX: vlans, interfaces, poe, lags, fullconfigs and checkpoints. Failure injection:
    fail_path (substring -> HTTP 500 on writes), fail_read (same on reads), drop_after (connection drops after N writes), lie (readback ignores writes)."""

    def __init__(self, *, model="6200F 48G", firmware="ML.10.14.1020", ports=48):
        self.state = {
            "system": {"platform_name": model, "software_version": firmware, "hostname": "emulated-cx",
                       "serial_number": "EMU-CX-0001"},
            "subsystems": {"management_module,1/1": {"resource_utilization": {"cpu": 23, "memory": 41},
                           "fans": {"1": {"status": "ok"}, "2": {"status": "ok"}},
                           "power_supplies": {"1/1": {"status": "ok"}},
                           "temp_sensors": {"1/1-CPU": {"name": "CPU", "temperature": 41000, "status": "normal"}}}},
            "central": None,
            "vlans": {"1": {"id": 1, "name": "DEFAULT_VLAN_1"}},
            "ntp_associations": {},
            # rc17 objects: ACLs, QoS classes/policies, static routes (default VRF), VSF members (6200 only)
            "acls": {}, "classes": {}, "policies": {}, "static_routes": {}, "vsf_members": {},
            # rc18 objects: RADIUS servers per VRF, AAA server groups, port-access accounting, OSPF routers
            "radius_servers": {"default": {}, "mgmt": {}},
            "aaa_server_groups": {"radius": {"group_name": "radius", "group_type": "radius", "origin": "built-in"}},
            "aaa_accounting": {}, "ospf_routers": {},
            "syslog_remotes": {},
            "vrfs": {"mgmt": {"name": "mgmt", "dns_name_servers": {}, "dns_domain_name": None},
                     "default": {"name": "default", "dns_name_servers": {}, "dns_domain_name": None}},
            "interfaces": {f"1/1/{i}": {"name": f"1/1/{i}", "admin": "up", "vlan_mode": "access",
                                        "vlan_tag": f"{API}/system/vlans/1", "vlan_trunks": [],
                                        "other_config": {"lldp_enable_dir": "rxtx"},
                                        "link_state": "up" if i % 3 else "down", "link_speed": 1000000000,
                                        "duplex": "full", "description": "",
                                        "statistics": {"rx_bytes": 1000 * i, "tx_bytes": 2000 * i,
                                                       "rx_error": 0, "tx_error": 0},
                                        "lldp": ({"chassis_id": "ap", "neighbor_info": {"chassis_name": f"AP-{i}",
                                                  "port_description": "eth0"}} if i <= 4 else None),
                                        "poe": {"config": {"admin_disable": False},
                                                "status": {"measured_power": 12500 if i <= 4 else 0,
                                                           "detection_status": "delivering" if i <= 4 else "searching"}}}
                           for i in range(1, ports + 1)},
        }
        # Explicit synthetic AAA baseline on the designated certification port.
        lab_port = f"1/1/{min(ports, 24)}"
        if lab_port in self.state["interfaces"]:
            self.state["interfaces"][lab_port].update({
                "port_access_auth_mode": "client-mode",
                "auth_methods": {method: {"authentication_method": method,
                    "radius_server_group": "LAB-RADIUS", "auth_enable": False,
                    "cached_reauth_enable": False, "canned_eap_success_enable": False}
                    for method in ("dot1x", "mac-auth")}})
        self.cli_commands = ["show port-access clients", "show power-over-ethernet",
                             "show qos trust", "show lacp interfaces",
                             "show interface transceiver detail", "show interface dom"]
        if "6200" in model:
            self.cli_commands.append("show vsf detail")
        self.checkpoints: dict[str, dict] = {}
        self.prefix = "/rest/v10.14"
        if lab_port in self.state["interfaces"]:                     # one authenticated client for status monitoring
            self.state["interfaces"][lab_port]["port_access_clients"] = {"00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55", "status": "authenticated", "auth_attributes": {"dot1x": {"status": "success"}},
                "role": "EMULATED-EMPLOYEE", "vlan_id": 20}}
        self.fw = {"primary": firmware, "secondary": "", "booted": "primary"}
        self.fail_boot = False              # test hook: the switch comes back on the old image
        self.startup: dict | None = None
        self.audit_records = {
            "audit": {
                "a-001": {"timestamp": "2026-11-13T13:00:00Z", "user": "lab-admin",
                          "service": "https-server", "data": "GET /rest/v10.14/system",
                          "severity": "info"},
                "a-002": {"timestamp": "2026-11-13T13:00:03Z", "user": "lab-admin",
                          "service": "https-server", "data": "password=example-secret", "severity": "warning"},
            },
            "event": {"e-001": {"timestamp": "2026-11-13T13:01:00Z",
                               "message": "Interface 1/1/3 is down", "severity": "warning"}},
        }
        self.writes = 0
        self.fail_path: str | None = None
        self.fail_read: str | None = None        # substring -> HTTP 500 on GET (failed snapshot/telemetry read)
        self.drop_after: int | None = None
        self.lie = False
        self.password = "emulator-password"
        self.username = "admin"
        # strict protocol state (E2E-04/05/09)
        m = re.search(r"(\d+)\.(\d+)", firmware or "")
        self.train = (int(m.group(1)), int(m.group(2))) if m else (10, 14)
        self.sessions: dict[str, dict] = {}
        self.violations: list[str] = []        # every protocol rule a client broke
        self.logins = 0

    def api_versions(self) -> list[str]:
        """REST versions this firmware serves: v10.04, v10.08 and v10.09 … v10.<train> (as on AOS-CX)."""
        major, minor = self.train
        vs = ["v10.04", "v10.08"] + [f"v10.{i:02d}" for i in range(9, minor + 1)]
        return vs if major == 10 else ["v10.04"]

    def csrf_supported(self) -> bool:
        return self.train >= (10, 9)

    def expire_sessions(self) -> None:
        """Simulate idle-timeout / reboot: every existing session cookie becomes invalid."""
        self.sessions.clear()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _auth(self, req: httpx.Request, q: dict, p: str) -> httpx.Response | None:
        """Strict AOS-CX session rules. Returns an error response, or None if the request may proceed."""
        if p == "/login":
            if req.method != "POST":
                return httpx.Response(405)
            if "password" in q or "username" in q:
                self.violations.append("credentials-in-url")
                return httpx.Response(400, text="Credentials must be sent in the request body.")
            # AOS-CX login uses application/x-www-form-urlencoded form data in the BODY.
            # Reject URL-query credentials and unsupported bodies; verify exact parsed form values.
            from urllib.parse import parse_qs
            ctype = req.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ctype != "application/x-www-form-urlencoded":
                self.violations.append("login-invalid-form-body")
                return httpx.Response(415, text="Expected form-encoded request body")
            try:
                fields = parse_qs(req.content.decode("utf-8"), strict_parsing=True)
            except (ValueError, UnicodeDecodeError):
                return httpx.Response(400, text="Malformed login request")
            form = {k: v[0] for k, v in fields.items() if len(v) == 1}
            if form.get("username") != self.username or form.get("password") != self.password:
                return httpx.Response(401, text="Login failed")
            if len(self.sessions) >= 48:
                return httpx.Response(429, text="Session limit reached")
            sid = secrets.token_hex(12)
            token = secrets.token_hex(16) if (self.csrf_supported() and
                                              req.headers.get("x-use-csrf-token", "").lower() == "true") else None
            self.sessions[sid] = {"csrf": token}
            self.logins += 1
            headers = {"set-cookie": f"id={sid}; Path=/; Secure; HttpOnly"}
            if token:
                headers["x-csrf-token"] = token
            return httpx.Response(200, headers=headers)
        cookie = req.headers.get("cookie", "")
        m = re.search(r"(?:^|;\s*)id=([0-9a-f]+)", cookie)
        sess = self.sessions.get(m.group(1)) if m else None
        if sess is None:
            # A stale cookie after expiry is normal (the switch answers 401 and the client logs in again);
            # sending no cookie at all is a client protocol violation.
            if m is None:
                self.violations.append(f"no-session:{p}")
            else:
                self.expired_hits = getattr(self, "expired_hits", 0) + 1
            return httpx.Response(401, text="Unauthorized")
        if sess["csrf"] and req.headers.get("x-csrf-token") != sess["csrf"]:
            self.violations.append(f"csrf-missing:{p}")
            return httpx.Response(403, text="CSRF token missing or invalid")
        if p == "/logout":
            self.sessions.pop(m.group(1), None)
            return httpx.Response(200)
        return None

    def _running(self):
        return {k: v for k, v in self.state.items()}

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        path = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if path in ("/rest", "/rest/"):
            vs = self.api_versions()
            return httpx.Response(200, json={**{v: {"version": v, "prefix": f"/rest/{v}"} for v in vs},
                                             "latest": {"version": vs[-1], "prefix": f"/rest/{vs[-1]}"}})
        mv = re.match(r"^/rest/(v\d+\.\d+)(/.*)$", path)
        if not mv or mv.group(1) not in self.api_versions():
            return httpx.Response(404, text="Unknown REST version")
        p = mv.group(2)
        self.prefix = f"/rest/{mv.group(1)}"
        m = req.method
        denied = self._auth(req, q, p)
        if denied is not None:
            return denied
        if p in ("/login", "/logout"):
            return httpx.Response(200)
        if p == "/cli/commands" and m == "GET":
            return httpx.Response(200, json={"commands": list(self.cli_commands)})
        if p == "/cli" and m == "POST":
            try:
                cmd = json.loads(req.content).get("cmd")
            except (ValueError, AttributeError):
                return httpx.Response(400, text="Invalid command body")
            if cmd not in self.cli_commands:
                return httpx.Response(400, text="Invalid show command")
            if cmd == "show interface transceiver detail":
                return httpx.Response(200, text="! EMULATED SHOW result: show interface transceiver detail — no device write\n"
                                      + self.transceiver_text())
            return httpx.Response(200, text=f"EMULATED SHOW result: {cmd} — no device write")
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            if self.drop_after is not None and self.writes >= self.drop_after and not p.startswith("/fullconfigs"):
                raise httpx.ConnectError("emulated link loss")
            if self.fail_path and self.fail_path in p:
                return httpx.Response(500, text="emulated device error")
        elif self.fail_read and self.fail_read in p:
            return httpx.Response(500, text="emulated device read error")
        multipart = req.headers.get("content-type", "").startswith("multipart/")
        body = json.loads(req.content) if req.content and not multipart else {}
        if p in ("/logs/audit", "/logs/event") and m == "GET":
            return httpx.Response(200, json=copy.deepcopy(self.audit_records[p.rsplit("/", 1)[-1]]))
        fw = self.fw
        if p == "/firmware" and m == "GET":
            return httpx.Response(200, json={"current_version": self.state["system"]["software_version"],
                "primary_version": fw["primary"], "secondary_version": fw["secondary"],
                "default_image": fw["booted"], "booted_image": fw["booted"]})
        if p == "/firmware" and m == "PUT":
            slot = q.get("image")
            if slot not in ("primary", "secondary") or slot == fw["booted"]:
                return httpx.Response(400, text="Invalid or booted image slot")
            mm = re.search(rb'filename="([A-Z]{2})_(\d+)_(\d+)_(\d+)\.swi"', req.content or b"")
            if not mm:
                return httpx.Response(400, text="Image rejected: not a valid signed AOS-CX image")
            if mm.group(1).decode() != self.state["system"]["software_version"].split(".")[0] and \
                    "." in self.state["system"]["software_version"]:
                return httpx.Response(400, text="Image is for a different platform")
            self.writes += 1
            if not self.lie:
                fw[slot] = ".".join(x.decode() for x in mm.groups())
            return httpx.Response(200)
        if p == "/boot" and m == "POST":
            slot = q.get("image")
            if slot not in ("primary", "secondary") or not fw[slot]:
                return httpx.Response(400, text="No image in that slot")
            self.writes += 1
            if not self.fail_boot:
                fw["booted"] = slot
                self.state["system"]["software_version"] = fw[slot]
            raise httpx.ConnectError("emulated reboot")
        if p == "/system":
            return httpx.Response(200, json=self.state["system"])
        if p == "/system/subsystems":
            return httpx.Response(200, json=self.state["subsystems"])
        if p == "/system/aruba_central":
            if m == "PATCH" and self.state.get("central"):
                self.writes += 1
                self.state["central"].update(body)
                return httpx.Response(200)
            return httpx.Response(200, json=self.state["central"]) if self.state.get("central") else httpx.Response(404)
        if p == "/system/interfaces" and m == "GET":
            return httpx.Response(200, json={k: {x: y for x, y in v.items() if x not in ("poe", "lldp")}
                                              for k, v in self.state["interfaces"].items()})
        if p.startswith("/fullconfigs/"):
            name = p.split("/")[2]
            if m == "GET":
                src = self._running() if name == "running-config" else self.checkpoints.get(name)
                return httpx.Response(200, json=src) if src is not None else httpx.Response(404)
            if m == "PUT":
                frm = q.get("from")
                if frm:
                    src_name = frm.rsplit("/", 1)[-1]
                    src = self._running() if src_name == "running-config" else self.checkpoints.get(src_name)
                    if src is None:
                        return httpx.Response(404)
                    snap = copy.deepcopy(src)
                    if name == "running-config":
                        self.state = snap
                    elif name == "startup-config":
                        self.startup = snap
                    else:
                        self.checkpoints[name] = snap
                    return httpx.Response(200)
                if name == "running-config":
                    self.state = copy.deepcopy(body)
                    return httpx.Response(200)
        rc18 = self._rc18(p, m, body)
        if rc18 is not None:
            return rc18
        rc17 = self._rc17(p, m, body)
        if rc17 is not None:
            return rc17
        for coll, keyf in (("ntp_associations", "address"), ("syslog_remotes", "remote_host")):
            if p == ("/system/vrfs/mgmt/ntp_associations" if coll == "ntp_associations" else f"/system/{coll}") and m == "GET":
                return httpx.Response(200, json=copy.deepcopy(self.state[coll]))
            if p == ("/system/vrfs/mgmt/ntp_associations" if coll == "ntp_associations" else f"/system/{coll}") and m == "POST":
                self.writes += 1
                if not isinstance(body.get(keyf), str) or "vrf" not in body:
                    return httpx.Response(400, text=f"{keyf} and vrf are required")
                k = f"{body[keyf]},{body['vrf'].rsplit('/', 1)[-1]}"
                if k in self.state[coll]:
                    return httpx.Response(400, text="Entry exists")
                if not self.lie:
                    self.state[coll][k] = dict(body)
                return httpx.Response(201)
        if p.startswith("/system/vrfs/"):
            vrf = self.state["vrfs"].get(p.rsplit("/", 1)[-1])
            if vrf is None:
                return httpx.Response(404)
            if m == "GET":
                return httpx.Response(200, json=copy.deepcopy(vrf))
            if m == "PATCH":
                self.writes += 1
                bad = set(body) - {"dns_name_servers", "dns_domain_name"}
                if bad:
                    return httpx.Response(400, text=f"Unknown VRF attribute(s): {sorted(bad)}")
                if not self.lie:
                    vrf.update(copy.deepcopy(body))
                return httpx.Response(200)
        if p == "/system/vlans" and m == "GET":
            # depth>=1 returns objects, depth 0 returns URIs (as AOS-CX does)
            if q.get("depth", "0") != "0":
                return httpx.Response(200, json={k: copy.deepcopy(v) for k, v in self.state["vlans"].items()})
            return httpx.Response(200, json={k: f"{path}/{k}" for k in self.state["vlans"]})
        if p == "/system/vlans" and m == "POST":
            self.writes += 1
            vid = str(body["id"])
            if vid in self.state["vlans"]:
                return httpx.Response(400, text="VLAN exists")
            if not self.lie:
                self.state["vlans"][vid] = {"id": body["id"], "name": body.get("name"), "type": "static", "voice": False}
            return httpx.Response(201)
        if p.startswith("/system/vlans/") and m in ("GET", "PUT"):
            v = self.state["vlans"].get(p.rsplit("/", 1)[-1])
            if v is None:
                return httpx.Response(404)
            if m == "GET":
                return httpx.Response(200, json=v)
            if set(body) - {"id", "name", "type", "voice"} or body.get("id") != v["id"] or type(body.get("voice")) is not bool:
                return httpx.Response(400, text="Invalid writable VLAN configuration")
            self.writes += 1
            if not self.lie:
                v.update(copy.deepcopy(body))
            return httpx.Response(204)
        if p.startswith("/system/interfaces/"):
            rest = p[len("/system/interfaces/"):]
            if "/port_access_auth_configurations/" in rest:
                name, method = rest.split("/port_access_auth_configurations/", 1)
                item = self.state["interfaces"].get(name, {}).get("auth_methods", {}).get(method)
                if item is None:
                    return httpx.Response(404)
                if m == "GET":
                    return httpx.Response(200, json=item)
                if m == "PATCH":
                    if set(body) != {"auth_enable", "cached_reauth_enable", "canned_eap_success_enable"} or any(type(v) is not bool for v in body.values()):
                        return httpx.Response(400, text="Invalid authentication attributes")
                    self.writes += 1
                    if not self.lie:
                        item.update(body)
                    return httpx.Response(204)
                return httpx.Response(405)
            if rest.endswith("/lldp_neighbors"):
                it = self.state["interfaces"].get(rest[: -len("/lldp_neighbors")])
                if it is None:
                    return httpx.Response(404)
                return httpx.Response(200, json={"n1": it["lldp"]} if it.get("lldp") else {})
            poe = rest.endswith("/poe_interface")
            name = rest[: -len("/poe_interface")] if poe else rest
            if m == "PUT" and name.startswith("lag"):
                self.writes += 1
                if not self.lie:
                    self.state["interfaces"][name] = dict(body)
                return httpx.Response(200)
            it = self.state["interfaces"].get(name)
            if it is None:
                return httpx.Response(404)
            if m == "GET":
                return httpx.Response(200, json=it["poe"] if poe else {k: v for k, v in it.items()
                    if k != "poe" and (req.url.params.get("selector") != "writable" or k in IFACE_WRITABLE)})
            if m in ("PATCH", "PUT"):
                self.writes += 1
                if not poe:
                    unknown = set(body) - IFACE_WRITABLE
                    if unknown:          # strict schema: an unknown attribute is a 400, as on AOS-CX
                        return httpx.Response(400, text=f"Unknown or read-only attribute(s): {sorted(unknown)}")
                    if (body.get("qos_config") or {}).get("qos_trust") not in (None, "none", "cos", "dscp"):
                        return httpx.Response(400, text="qos_trust must be none, cos or dscp")
                if "vlan_tag" in body and body["vlan_tag"].rsplit("/", 1)[-1] not in self.state["vlans"]:
                    return httpx.Response(400, text="VLAN does not exist")
                if not self.lie:
                    if poe:
                        it["poe"]["config"].update(body.get("config", {}))
                        if it["poe"]["config"].get("admin_disable"):
                            it["poe"]["status"] = {"measured_power": 0, "detection_status": "disabled"}
                    else:
                        for k, v in body.items():
                            if isinstance(v, dict) and isinstance(it.get(k), dict):
                                it[k].update(v)
                            else:
                                it[k] = v
                return httpx.Response(200)
        return httpx.Response(404)

    _SRV_KEYS = {"address", "port", "port_type", "vrf", "passkey", "timeout", "retries", "accounting_udp_port",
                 "auth_type", "tracking_enable", "server_group"}
    _AUTH_KEYS = {"authentication_method", "auth_enable", "reauth_enable", "cached_reauth_enable", "reauth_period",
                  "quiet_period", "max_retries", "max_requests", "eapol_timeout", "discovery_period",
                  "radius_server_group", "canned_eap_success_enable"}

    def _ok(self, code=204, **kw):
        self.writes += 1
        return httpx.Response(code, **kw)

    def _rc18(self, p: str, m: str, body: dict):
        """Strict store for RADIUS servers, AAA server groups, port-access accounting, port authentication
        configurations, port-access clients and OSPF (EMULATED; OSPF only on a 6200 platform)."""
        from urllib.parse import unquote
        st = self.state
        b = body if isinstance(body, dict) else {}
        for vrf in ("default", "mgmt"):
            base = f"/system/vrfs/{vrf}/radius_servers"
            if p == base:
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(st["radius_servers"][vrf]))
                if m == "POST":
                    if set(b) - self._SRV_KEYS or not b.get("address") or b.get("port_type") not in ("udp", "tcp") \
                            or not isinstance(b.get("port"), int) or not isinstance(b.get("passkey"), str) or "*" in b["passkey"]:
                        return httpx.Response(400, text="Invalid RADIUS server")
                    key = f"{b['address']},{b['port']},{b['port_type']}"
                    if key in st["radius_servers"][vrf]:
                        return httpx.Response(400, text="RADIUS server exists")
                    if not self.lie:
                        st["radius_servers"][vrf][key] = {**copy.deepcopy(b), "server_group":
                                                          {f"{self.prefix}/system/aaa_server_groups/radius": len(st["radius_servers"][vrf]) + 1}}
                    return self._ok(201)
                return httpx.Response(405)
            if p.startswith(base + "/"):
                key = unquote(p[len(base) + 1:])
                srv = st["radius_servers"][vrf].get(key)
                if srv is None:
                    return httpx.Response(404)
                if m == "GET":
                    out = {k: v for k, v in srv.items() if k != "passkey"}      # secrets are never read back
                    return httpx.Response(200, json=copy.deepcopy(out))
                if m == "PATCH":
                    if set(b) - (self._SRV_KEYS - {"address", "port", "port_type", "vrf"}) or \
                            ("passkey" in b and (not isinstance(b["passkey"], str) or "*" in b["passkey"])):
                        return httpx.Response(400, text="Invalid RADIUS server attributes")
                    for g in (b.get("server_group") or {}):
                        if unquote(g.rsplit("/", 1)[-1]) not in st["aaa_server_groups"]:
                            return httpx.Response(400, text="Unknown server group")
                    if not self.lie:
                        srv.update(copy.deepcopy(b))
                    return self._ok()
                if m == "DELETE":
                    if not self.lie:
                        del st["radius_servers"][vrf][key]
                    return self._ok()
                return httpx.Response(405)
        gbase = "/system/aaa_server_groups"
        if p == gbase:
            if m == "GET":
                return httpx.Response(200, json=copy.deepcopy(st["aaa_server_groups"]))
            if m == "POST":
                if set(b) - {"group_name", "group_type", "load_balancing_mode", "origin"} or b.get("group_type") != "radius" \
                        or b.get("load_balancing_mode") not in (None, "priority-based", "round-robin"):
                    return httpx.Response(400, text="Invalid server group")
                if b["group_name"] in st["aaa_server_groups"]:
                    return httpx.Response(400, text="Group exists")
                if not self.lie:
                    st["aaa_server_groups"][b["group_name"]] = {**copy.deepcopy(b), "origin": "configuration"}
                return self._ok(201)
            return httpx.Response(405)
        if p.startswith(gbase + "/"):
            name = unquote(p[len(gbase) + 1:])
            g = st["aaa_server_groups"].get(name)
            if g is None:
                return httpx.Response(404)
            if m == "GET":
                return httpx.Response(200, json=copy.deepcopy(g))
            if g.get("origin") == "built-in" and m in ("PATCH", "DELETE"):
                return httpx.Response(400, text="Built-in group")
            if m == "PATCH":
                if set(b) - {"load_balancing_mode"}:
                    return httpx.Response(400, text="Invalid group attributes")
                if not self.lie:
                    g.update(b)
                return self._ok()
            if m == "DELETE":
                ref = f"{self.prefix}{gbase}/{name}"
                if any(ref in (sv.get("server_group") or {}) for v in st["radius_servers"].values() for sv in v.values()):
                    return httpx.Response(400, text="Group still has servers")
                if not self.lie:
                    del st["aaa_server_groups"][name]
                return self._ok()
            return httpx.Response(405)
        abase = "/system/aaa_accounting_attributes"
        if p == abase and m == "POST":
            if b.get("session_type") != "port-access" or b.get("accounting_mode") not in ("start-stop", "stop-only"):
                return httpx.Response(400, text="Invalid accounting attributes")
            if not self.lie:
                st["aaa_accounting"]["port-access"] = copy.deepcopy(b)
            return self._ok(201)
        if p == abase + "/port-access":
            cur = st["aaa_accounting"].get("port-access")
            if m == "GET":
                return httpx.Response(200, json=copy.deepcopy(cur)) if cur else httpx.Response(404)
            if cur is None:
                return httpx.Response(404)
            if m == "PATCH":
                if set(b) - {"accounting_mode", "interim_update_enable", "interim_update_interval", "interim_update_onreauth_enable"}:
                    return httpx.Response(400, text="Invalid accounting attributes")
                if not self.lie:
                    cur.update(b)
                    if cur.get("accounting_mode") != "start-stop":
                        cur.pop("interim_update_interval", None); cur["interim_update_enable"] = False
                return self._ok()
            if m == "DELETE":
                if not self.lie:
                    del st["aaa_accounting"]["port-access"]
                return self._ok()
            return httpx.Response(405)
        if p.startswith("/system/interfaces/") and "/port_access_" in p:
            rest = unquote(p[len("/system/interfaces/"):])
            if rest.endswith("/port_access_clients"):
                it = st["interfaces"].get(rest[:-len("/port_access_clients")])
                if it is None:
                    return httpx.Response(404)
                return httpx.Response(200, json=copy.deepcopy(it.get("port_access_clients", {})))
            name, _, method = rest.partition("/port_access_auth_configurations")
            it = st["interfaces"].get(name)
            if it is None:
                return httpx.Response(404)
            methods = it.setdefault("auth_methods", {})
            method = method.lstrip("/")
            if not method:
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(methods))
                if m == "POST":
                    if set(b) - self._AUTH_KEYS or b.get("authentication_method") not in ("dot1x", "mac-auth") \
                            or b["authentication_method"] in methods or any(
                                k.endswith("_enable") and type(v) is not bool for k, v in b.items()):
                        return httpx.Response(400, text="Invalid port authentication configuration")
                    if not self.lie:
                        methods[b["authentication_method"]] = copy.deepcopy(b)
                    return self._ok(201)
                return httpx.Response(405)
            item = methods.get(method)
            if item is None:
                return httpx.Response(404)
            if m == "GET":
                return httpx.Response(200, json=copy.deepcopy(item))
            if m == "PATCH":
                if set(b) - (self._AUTH_KEYS - {"authentication_method"}) or any(
                        k.endswith("_enable") and type(v) is not bool for k, v in b.items()):
                    return httpx.Response(400, text="Invalid authentication attributes")
                if method == "mac-auth" and set(b) & {"max_requests", "eapol_timeout", "discovery_period"}:
                    return httpx.Response(400, text="Attribute applies to 802.1X only")
                if not self.lie:
                    item.update(copy.deepcopy(b))
                return self._ok()
            if m == "DELETE":
                if not self.lie:
                    del methods[method]
                return self._ok()
            return httpx.Response(405)
        obase = "/system/vrfs/default/ospf_routers"
        if p.startswith(obase):
            if "6200" not in st["system"]["platform_name"]:
                return httpx.Response(404, text="OSPF not supported on this platform")
            parts = [unquote(x) for x in p[len(obase):].strip("/").split("/") if x]
            if not parts:
                if m == "GET":
                    return httpx.Response(200, json={k: f"{self.prefix}{obase}/{k}" for k in st["ospf_routers"]})
                if m == "POST":
                    tag = str(b.get("instance_tag"))
                    if tag in st["ospf_routers"] or set(b) - {"instance_tag", "admin_router_id", "maximum_paths",
                                                             "passive_interface_default", "redistribute", "active_interfaces"}:
                        return httpx.Response(400, text="Invalid OSPF router")
                    if not self.lie:
                        st["ospf_routers"][tag] = {**copy.deepcopy(b), "areas": {}}
                    return self._ok(201)
                return httpx.Response(405)
            r = st["ospf_routers"].get(parts[0])
            if r is None:
                return httpx.Response(404)
            if len(parts) == 1:
                if m == "GET":
                    return httpx.Response(200, json={k: v for k, v in copy.deepcopy(r).items() if k != "areas"})
                if m == "PATCH":
                    if set(b) - {"admin_router_id", "maximum_paths", "passive_interface_default", "redistribute", "active_interfaces"}:
                        return httpx.Response(400, text="Invalid OSPF attributes")
                    if not self.lie:
                        r.update(copy.deepcopy(b))
                    return self._ok()
                if m == "DELETE":
                    if not self.lie:
                        del st["ospf_routers"][parts[0]]
                    return self._ok()
            if len(parts) >= 2 and parts[1] == "areas":
                if len(parts) == 2 and m == "POST":
                    if r["areas"] and b.get("area_id") not in r["areas"]:
                        return httpx.Response(400, text="Access OSPF supports a single area")
                    if not self.lie:
                        r["areas"][b["area_id"]] = copy.deepcopy(b)
                    return self._ok(201)
                if len(parts) == 3:
                    a = r["areas"].get(parts[2])
                    if a is None:
                        return httpx.Response(404)
                    if m == "GET":
                        return httpx.Response(200, json=copy.deepcopy(a))
                    if m == "PATCH":
                        if not self.lie:
                            a.update(copy.deepcopy(b))
                        return self._ok()
            return httpx.Response(405)
        return None

    def _rc17(self, p: str, m: str, body: dict):
        """Strict store for ACLs, classes, policies, static routes and VSF members (EMULATED)."""
        from urllib.parse import unquote
        st = self.state
        for coll in ("acls", "classes", "policies"):
            base = f"/system/{coll}/"
            if p.startswith(base) and "/" not in p[len(base):]:
                key = unquote(p[len(base):])
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(st[coll][key])) if key in st[coll] else httpx.Response(404)
                if m == "PUT":
                    if coll == "acls" and (body.get("list_type") != "ipv4" or not isinstance(body.get("cfg_aces"), dict)
                                           or any(a.get("action") not in ("permit", "deny") for a in body["cfg_aces"].values())):
                        return httpx.Response(400, text="Invalid ACL")
                    if coll == "policies":
                        for e in (body.get("cfg_entries") or {}).values():
                            if unquote(str(e.get("class", "")).rsplit("/", 1)[-1]) not in st["classes"]:
                                return httpx.Response(400, text="Policy references an unknown class")
                    self.writes += 1
                    if not self.lie:
                        st[coll][key] = copy.deepcopy(body)
                    return httpx.Response(200)
                return httpx.Response(405)
        if p == "/system/vrfs/default/static_routes" and m == "POST":
            key = body.get("prefix")
            if key in st["static_routes"]:
                return httpx.Response(400, text="Route exists")
            self.writes += 1
            if not self.lie:
                st["static_routes"][key] = {**copy.deepcopy(body), "static_nexthops": {}}
            return httpx.Response(201)
        if p.startswith("/system/vrfs/default/static_routes/"):
            rest = unquote(p[len("/system/vrfs/default/static_routes/"):])
            key, sub = (rest[:-len("/static_nexthops")], "static_nexthops") if rest.endswith("/static_nexthops") else (rest, "")
            route = st["static_routes"].get(unquote(key))
            if route is None:
                return httpx.Response(404)
            if not sub:
                return httpx.Response(200, json=copy.deepcopy(route)) if m == "GET" else httpx.Response(405)
            if sub == "static_nexthops":
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(route["static_nexthops"]))
                if m == "POST":
                    try:
                        import ipaddress
                        ipaddress.IPv4Address(body.get("ip_address"))
                    except ValueError:
                        return httpx.Response(400, text="Invalid next hop")
                    self.writes += 1
                    if not self.lie:
                        route["static_nexthops"][str(len(route["static_nexthops"]))] = copy.deepcopy(body)
                    return httpx.Response(201)
            return httpx.Response(405)
        if p.startswith("/system/vsf_members"):
            if "6200" not in st["system"]["platform_name"]:
                return httpx.Response(404, text="VSF not supported on this platform")
            parts = p.split("/")[3:]
            if len(parts) == 1:
                n = parts[0]
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(st["vsf_members"][n])) if n in st["vsf_members"] else httpx.Response(404)
                if m == "PUT":
                    if not str(body.get("type", "")).strip():
                        return httpx.Response(400, text="Member type required")
                    self.writes += 1
                    if not self.lie:
                        st["vsf_members"][n] = {**st["vsf_members"].get(n, {"links": {}}), "id": body.get("id"), "type": body["type"]}
                    return httpx.Response(200)
            if len(parts) == 3 and parts[1] == "links":
                mem = st["vsf_members"].get(parts[0])
                if mem is None:
                    return httpx.Response(404)
                if m == "GET":
                    return httpx.Response(200, json=copy.deepcopy(mem["links"][parts[2]])) if parts[2] in mem["links"] else httpx.Response(404)
                if m == "PUT":
                    self.writes += 1
                    if not self.lie:
                        mem["links"][parts[2]] = copy.deepcopy(body)
                    return httpx.Response(200)
            return httpx.Response(405)
        if p.startswith("/system/interfaces/") and m in ("PATCH", "PUT"):
            for attr, coll in (("aclv4_in_cfg", "acls"), ("policy_in_cfg", "policies")):
                ref = body.get(attr)
                if ref and unquote(str(ref).rsplit("/", 1)[-1]) not in st[coll]:
                    return httpx.Response(400, text=f"{attr} references an unknown object")
        return None

    # EMULATED optics: 1/1/49 healthy SFP+, 1/1/50 SFP+ whose Rx power is set by optics_rx_dbm (default warning),
    # 1/1/51 module without DOM, 1/1/52 4-lane QSFP+ (lane 3 at 0 mW = loss of signal).
    optics_rx_dbm = -15.20

    def transceiver_text(self) -> str:
        thr = ("               High Alarm  High Warn  Low Warn  Low Alarm\n"
               "   Temperature C     75.00      70.00     0.00     -5.00\n"
               "   Voltage V          3.63       3.47     3.13      2.97\n"
               "   Bias Current mA   12.00      10.00     2.00      1.00\n"
               "   Rx Power dBm       0.00      -1.00   -14.00    -18.00\n"
               "   Tx Power dBm      -1.00      -2.00    -7.00     -9.00\n")

        def sfp(port, rx, serial):
            return (f"Transceiver in {port}\n   Interface Name    : {port}\n   Type              : SFP+SR\n"
                    "   Connector Type    : LC\n   Wavelength        : 850nm\n   Diagnostic Support: DOM\n"
                    "   Vendor Name       : HPE (EMULATED)\n   Vendor Part Number: J9150D\n"
                    f"   Vendor Serial Num : {serial}\n\n   Temperature  : 34.50C\n   Voltage      : 3.30V\n"
                    f"   Tx Bias      : 6.40mA\n   Rx Power     : {10 ** (rx / 10):.4f}mW, {rx:.2f}dBm\n"
                    f"   Tx Power     : 0.6026mW, -2.20dBm\n\n{thr}\n")
        lanes = "".join(f"   Lane {n}\n   Tx Bias      : 7.0{n}mA\n   Rx Power     : "
                        + ("0.0000mW\n" if n == 3 else f"0.5{n}00mW\n") + "   Tx Power     : 0.7000mW, -1.55dBm\n"
                        for n in range(1, 5))
        return (sfp("1/1/49", -3.10, "EMU-OPT-49") + sfp("1/1/50", self.optics_rx_dbm, "EMU-OPT-50")
                + "Transceiver in 1/1/51\n   Interface Name    : 1/1/51\n   Type              : SFP-1G-T\n"
                  "   Diagnostic Support: None\n   Vendor Name       : HPE (EMULATED)\n\n"
                + "Transceiver in 1/1/52\n   Interface Name    : 1/1/52\n   Type              : QSFP+SR4\n"
                  "   Diagnostic Support: DOM\n   Vendor Name       : HPE (EMULATED)\n   Vendor Part Number: JL309A\n"
                  "   Temperature  : 41.00C\n   Voltage      : 3.29V\n" + lanes + "\n")

    def writer(self, password: str | None = None) -> ArubaCxWriter:
        return ArubaCxWriter(Credentials("emulated-cx", "admin", password or self.password), transport=self.transport())


INSTANT_BASE = """name emulated-iap
virtual-controller-country US
ntp-server 10.10.0.10
syslog-server 10.10.0.20
arm
 wide-bands 5ghz
 min-tx-power 9
 max-tx-power 18
 band-steering-mode prefer-5ghz
 air-time-fairness-mode default-access
 client-match
 no 80mhz-support
 a-channels 36,40,44,48,149,153,157,161,36+,44+,149+,157+
wlan ssid-profile Corp-WiFi
 essid Corp-WiFi
 opmode wpa2-psk-aes
 vlan 100
wlan auth-server INM-LAB-RADIUS
 ip 192.0.2.80
 port 1812
 acctport 1813
 key emulated-only
wlan ssid-profile INM-LAB-1X
 essid INM-LAB-1X
 opmode wpa2-aes
 auth-server INM-LAB-RADIUS
 vlan 100
wlan external-captive-portal LAB-PORTAL-A
 server portal-a.example.test
wlan external-captive-portal LAB-PORTAL-B
 server portal-b.example.test
wlan access-rule LAB-PRE
 rule any any match any any any deny
wlan access-rule LAB-MAC
 rule any any match any any any deny
wlan ssid-profile INM-LAB-GUEST
 essid INM-LAB-GUEST
 type guest
 vlan 100
 captive-portal external profile LAB-PORTAL-A
 mac-authentication
 set-role-pre-auth LAB-PRE
 set-role-mac-auth LAB-MAC
"""


class InstantEmulator:
    """In-memory Instant VC: running-config text, ARM block edits via SSH lines, SSID via REST, per-AP radios."""

    def __init__(self, *, model: str = "AP-505", firmware: str = "8.10.0.0"):
        # Identity only: radio chain counts and per-model RF limits are NOT modelled (AP-505 vs AP-535 differ
        # on real hardware; those differences can only be certified on hardware).
        self.model, self.firmware = model, firmware
        self.text = INSTANT_BASE
        self.log_records = {
            "system": "Nov 13 13:00:00 cli: System started\nNov 13 13:00:02 auth: password=example-secret",
            "security": "Nov 13 13:01:00 auth: Administrator logged in",
            "user": "Nov 13 13:02:00 station: Wireless user associated",
            "wireless": "Nov 13 13:03:00 radio: Emulated RF diagnostic event",
        }
        self.diagnostic_text = {
            "show ap arm rf-summary": "EMULATED RF: channel 36; width 20 MHz",
            "show clients": "EMULATED CLIENTS: 2 connected",
            "show ap debug system-status": "EMULATED SYSTEM: CPU 10%",
        }
        self.radios = [{"band": "5", "channel": "36", "tx_power_dbm": 15}, {"band": "2.4", "channel": "1", "tx_power_dbm": 12}]
        self.radio_overrides = {"5": {"channel": "36", "power": "15"}, "2.4": {"channel": "1", "power": "12"}}
        self.fail_on: str | None = None
        self.lie = False
        self.fingerprint = "SHA256:emulated"
        emu = self

        class _Client:
            def login(self):
                return None

            def close(self):
                return None

            def show(self, cmd, iap_ip=None):
                if cmd == "show aps":
                    return {"show aps": [{"Name": "emulated", "IP Address": "emulated-vc*", "Type": emu.model, "Serial #": "EMULATED-AP"}]}
                if cmd == "show ap allowed-channels":
                    return {"Command output": "Allowed Channels for AP Type 505 Country Code US\n5GHz (indoor) 36 40 44 48 149 153 157 161\n5GHz (outdoor) 36 40 44 48 149 153 157 161\n5GHz 40MHz (indoor) 36-40 44-48 149-153 157-161\n5GHz 40MHz (outdoor) 36-40 44-48 149-153 157-161"}
                if cmd == "show ap-env":
                    values = ["name:emulated"]
                    for band, prefix in (("5", "a"), ("2.4", "g")):
                        setting = emu.radio_overrides[band]
                        values += [f"{prefix}_chan:{setting['channel']}", f"{prefix}_tx_power:{setting['power']}"]
                    return {"Command output": "\n".join(values)}
                if cmd in emu.diagnostic_text:
                    from ...aruba.client import validate_command
                    validate_command(cmd)
                    return {"Command output": emu.diagnostic_text[cmd]}
                if cmd == "show log wireless":
                    from ...aruba.client import validate_command
                    validate_command(cmd)
                    return {"Command output": emu.log_records["wireless"]}
                if cmd.startswith("show log "):
                    from ...aruba.client import validate_command
                    import re as _re
                    validate_command(cmd)
                    match = _re.fullmatch(r"show log (system|security|user) ([1-9][0-9]{0,2})", cmd)
                    if not match:
                        raise ValueError("Unsupported emulated log query")
                    return {"Command output": emu.log_records[match.group(1)]}
                return {"Command output": emu.text}

            def config_post(self, endpoint, body):
                if emu.fail_on and emu.fail_on in endpoint:
                    from ...aruba.errors import ArubaCommandError
                    raise ArubaCommandError("emulated rejection")
                p = body["ssid-profile"]
                if emu.lie:
                    return "Success"
                prof = p["ssid-profile"]
                sets = []
                if "essid" in p:
                    sets.append(f"essid {p['essid']['value']}")
                if "vlan" in p:
                    sets.append(f"vlan {p['vlan']['value']}")
                if "opmode" in p:
                    sets.append(f"opmode {p['opmode']}")
                if "wpa-passphrase" in p:
                    if p["wpa-passphrase"] == "********":
                        raise ValueError("masked secret reached the device")
                    sets.append("wpa-passphrase " + ("%064x" % abs(hash(p["wpa-passphrase"])))[:32])
                if p.get("type"):
                    sets.append(f"type {p['type']}")
                if p.get("enable") == "yes":
                    sets.append("enable")
                emu._cli([f"wlan ssid-profile {prof}", *sets, "exit"])
                return "Success"

        self.client = _Client()

    def ssh(self, host, user, pw, fp, lines):
        from ...aruba.errors import ArubaSshError
        if fp != self.fingerprint:
            raise ArubaSshError("host key mismatch")
        for ln in lines:
            if self.fail_on and self.fail_on in ln:
                raise ArubaSshError(f"The AP rejected '{ln}': % invalid input")
            if " key ********" in f" {ln}" or ln.strip() == "key ********":
                raise ArubaSshError("masked secret reached the device")
            if ln.strip().startswith("opmode") and "80" in ln:
                raise ArubaSshError("% invalid opmode")
        if self.lie:
            return [{"command": ln, "ok": True} for ln in lines]
        self._cli(lines)
        return [{"command": ln, "ok": True} for ln in lines]

    def _cli(self, lines: list[str]) -> None:
        """Minimal Instant CLI: context blocks (arm, wlan ssid-profile X, wlan auth-server X), 'no <block>',
        'no <key>' / '<key> <value>' inside a block, 'exit'; top-level a-channel/g-channel per-AP commands."""
        ctx = None
        for raw in lines:
            ln = raw.strip()
            if ln in ("configure terminal", "end", "commit apply", ""):
                continue
            if ln.startswith(("a-channel ", "g-channel ")):
                _, ch, pw_ = ln.split()
                r = next(x for x in self.radios if x["band"] == ("5" if ln[0] == "a" else "2.4"))
                r["channel"], r["tx_power_dbm"] = ch, float(pw_)
                self.radio_overrides[r["band"]] = {"channel": ch, "power": pw_}
                continue
            if ln.startswith("no wlan "):
                self._drop_block(ln[3:])
                continue
            if ln == "arm" or ln.startswith(("wlan ssid-profile ", "wlan auth-server ", "wlan access-rule ",
                                             "wlan external-captive-portal ")):
                ctx = ln
                self._ensure_block(ctx)
                continue
            if ln == "exit":
                ctx = None
                continue
            if ctx is None:
                continue
            if ctx.startswith("wlan ssid-profile") and (ln.startswith("auth-server ") or ln.startswith("no auth-server ")):
                lines_, start_, end_ = self._block_range(ctx)
                block_ = lines_[start_ + 1:end_]
                target = " " + (ln[3:] if ln.startswith("no ") else ln)
                block_ = [x for x in block_ if x != target]
                if not ln.startswith("no "):
                    block_.append(target)                        # ordered: first = primary, second = failover
                self.text = "\n".join(lines_[:start_ + 1] + block_ + lines_[end_:])
                continue
            if ctx.startswith("wlan access-rule") and ln.startswith("rule "):
                self._append_in_block(ctx, ln)                  # rules are ordered and accumulate
                continue
            if ctx.startswith("wlan auth-server") and ln.startswith("key "):
                ln = "key " + __import__("hashlib").sha256(ln[4:].encode()).hexdigest()[:32]   # stored encrypted, as on Instant
            self._set_in_block(ctx, ln)

    def _block_range(self, header: str):
        lines = self.text.split("\n")
        if header not in lines:
            return lines, None, None
        start = lines.index(header)
        end = start + 1
        while end < len(lines) and lines[end].startswith(" "):
            end += 1
        return lines, start, end

    def _ensure_block(self, header: str) -> None:
        lines, start, _ = self._block_range(header)
        if start is None:
            self.text = "\n".join([x for x in lines if x != ""] + [header]) + "\n"

    def _drop_block(self, header: str) -> None:
        lines, start, end = self._block_range(header)
        if start is not None:
            self.text = "\n".join(lines[:start] + lines[end:])

    def _append_in_block(self, header: str, ln: str) -> None:
        lines, start, end = self._block_range(header)
        self.text = "\n".join(lines[:end] + [" " + ln] + lines[end:])

    def _set_in_block(self, header: str, ln: str) -> None:
        lines, start, end = self._block_range(header)
        key = ln[3:].split()[0] if ln.startswith("no ") else ln.split()[0]
        block = [x for x in lines[start + 1:end] if x.strip().replace("no ", "", 1).split()[0] != key]
        if not (ln.startswith("no ") and header != "arm"):
            block.append(" " + ln)
        self.text = "\n".join(lines[:start + 1] + block + lines[end:])

    def writer(self) -> ArubaInstantWriter:
        return ArubaInstantWriter(Credentials("emulated-vc", "admin", "x", ssh_fingerprint=self.fingerprint),
                                  client=self.client, ssh_runner=self.ssh, snapshot=lambda: copy.deepcopy(self.radios))
