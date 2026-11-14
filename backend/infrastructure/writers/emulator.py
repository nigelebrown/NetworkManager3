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


IFACE_WRITABLE = {"admin", "vlan_mode", "vlan_tag", "vlan_trunks", "description", "other_config", "stp_config",
                  "rate_limits", "rate_limits_units", "qos_trust"}


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
        self.cli_commands = ["show port-access clients", "show power-over-ethernet",
                             "show qos trust", "show lacp interfaces"]
        if "6200" in model:
            self.cli_commands.append("show vsf detail")
        self.checkpoints: dict[str, dict] = {}
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
            return httpx.Response(200, text=f"EMULATED SHOW result: {cmd} — no device write")
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            if self.drop_after is not None and self.writes >= self.drop_after and not p.startswith("/fullconfigs"):
                raise httpx.ConnectError("emulated link loss")
            if self.fail_path and self.fail_path in p:
                return httpx.Response(500, text="emulated device error")
        elif self.fail_read and self.fail_read in p:
            return httpx.Response(500, text="emulated device read error")
        body = json.loads(req.content) if req.content else {}
        if p in ("/logs/audit", "/logs/event") and m == "GET":
            return httpx.Response(200, json=copy.deepcopy(self.audit_records[p.rsplit("/", 1)[-1]]))
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
        for coll, keyf in (("ntp_associations", "address"), ("syslog_remotes", "remote_host")):
            if p == f"/system/{coll}" and m == "GET":
                return httpx.Response(200, json=copy.deepcopy(self.state[coll]))
            if p == f"/system/{coll}" and m == "POST":
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
                self.state["vlans"][vid] = {"id": body["id"], "name": body.get("name")}
            return httpx.Response(201)
        if p.startswith("/system/vlans/") and m == "GET":
            v = self.state["vlans"].get(p.rsplit("/", 1)[-1])
            return httpx.Response(200, json=v) if v else httpx.Response(404)
        if p.startswith("/system/interfaces/"):
            rest = p[len("/system/interfaces/"):]
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
                return httpx.Response(200, json=it["poe"] if poe else {k: v for k, v in it.items() if k != "poe"})
            if m == "PATCH":
                self.writes += 1
                if not poe:
                    unknown = set(body) - IFACE_WRITABLE
                    if unknown:          # strict schema: an unknown attribute is a 400, as on AOS-CX
                        return httpx.Response(400, text=f"Unknown or read-only attribute(s): {sorted(unknown)}")
                    if body.get("qos_trust") not in (None, "none", "cos", "dscp"):
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
wlan ssid-profile Corp-WiFi
 essid Corp-WiFi
 opmode wpa2-psk-aes
 vlan 100
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
            if ln.startswith(("a-channel", "g-channel")):
                _, ch, pw_ = ln.split()
                r = next(x for x in self.radios if x["band"] == ("5" if ln[0] == "a" else "2.4"))
                r["channel"], r["tx_power_dbm"] = ch, int(pw_)
                continue
            if ln.startswith("no wlan "):
                self._drop_block(ln[3:])
                continue
            if ln == "arm" or ln.startswith(("wlan ssid-profile ", "wlan auth-server ")):
                ctx = ln
                self._ensure_block(ctx)
                continue
            if ln == "exit":
                ctx = None
                continue
            if ctx is None:
                continue
            if ctx.startswith("wlan auth-server") and ln.startswith("key "):
                ln = "key " + ("%064x" % abs(hash(ln[4:])))[:32]      # stored encrypted, as on Instant
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
