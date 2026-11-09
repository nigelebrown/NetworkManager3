"""Stateful protocol emulators for AOS-CX 10.14 REST and Aruba Instant 8.10.

Used for (1) automated contract tests of every write, failure and rollback path and (2) DEMO devices so
the full GUI workflow can be exercised. Results produced here are always labelled EMULATED and are never
accepted as hardware certification evidence.
"""
from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import Credentials
from .aruba_cx import API, ArubaCxWriter
from .aruba_instant import ArubaInstantWriter


class CxEmulator:
    """In-memory AOS-CX: vlans, interfaces, poe, lags, fullconfigs and checkpoints. Failure injection:
    fail_path (substring -> HTTP 500), drop_after (connection drops after N writes), lie (readback ignores writes)."""

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
        self.checkpoints: dict[str, dict] = {}
        self.startup: dict | None = None
        self.writes = 0
        self.fail_path: str | None = None
        self.drop_after: int | None = None
        self.lie = False
        self.password = "emulator-password"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _running(self):
        return {k: v for k, v in self.state.items()}

    def handle(self, req: httpx.Request) -> httpx.Response:
        u = urlparse(str(req.url))
        path = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not path.startswith(API):
            return httpx.Response(404)
        p = path[len(API):]
        m = req.method
        if p == "/login":
            return httpx.Response(200 if q.get("password") == self.password else 401)
        if p == "/logout":
            return httpx.Response(200)
        if m in ("POST", "PUT", "PATCH", "DELETE"):
            if self.drop_after is not None and self.writes >= self.drop_after and not p.startswith("/fullconfigs"):
                raise httpx.ConnectError("emulated link loss")
            if self.fail_path and self.fail_path in p:
                return httpx.Response(500, text="emulated device error")
        body = json.loads(req.content) if req.content else {}
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

    def __init__(self):
        self.text = INSTANT_BASE
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
                return {"Command output": emu.text}

            def config_post(self, endpoint, body):
                if emu.fail_on and emu.fail_on in endpoint:
                    from ...aruba.errors import ArubaCommandError
                    raise ArubaCommandError("emulated rejection")
                p = body["ssid-profile"]
                if emu.lie:
                    return "Success"
                prof = p["ssid-profile"]
                lines = emu.text.split("\n")
                out, i, found = [], 0, False
                while i < len(lines):
                    ln = lines[i]
                    out.append(ln)
                    if ln.strip() == f"wlan ssid-profile {prof}":
                        found = True
                        i += 1
                        while i < len(lines) and lines[i].startswith(" "):
                            if not (lines[i].strip().startswith("vlan ") and "vlan" in p):
                                out.append(lines[i])
                            i += 1
                        if "vlan" in p:
                            out.append(f" vlan {p['vlan']['value']}")
                        continue
                    i += 1
                if not found:
                    out += [f"wlan ssid-profile {prof}", f" essid {prof}"] + ([f" vlan {p['vlan']['value']}"] if "vlan" in p else [])
                emu.text = "\n".join(out)
                return "Success"

        self.client = _Client()

    def ssh(self, host, user, pw, fp, lines):
        from ...aruba.errors import ArubaSshError
        if fp != self.fingerprint:
            raise ArubaSshError("host key mismatch")
        for ln in lines:
            if self.fail_on and self.fail_on in ln:
                raise ArubaSshError(f"The AP rejected '{ln}': % invalid input")
        if self.lie:
            return [{"command": ln, "ok": True} for ln in lines]
        for ln in lines:
            if ln.startswith(("a-channel", "g-channel")):
                _, ch, pw_ = ln.split()
                r = next(x for x in self.radios if x["band"] == ("5" if ln[0] == "a" else "2.4"))
                r["channel"], r["tx_power_dbm"] = ch, int(pw_)
            elif ln in ("configure terminal", "end", "commit apply", "arm", "exit"):
                continue
            else:
                self._arm_set(ln)
        return [{"command": ln, "ok": True} for ln in lines]

    def _arm_set(self, ln: str):
        key = ln.replace("no ", "") if ln.startswith("no ") else ln.split()[0]
        lines = self.text.split("\n")
        start = lines.index("arm")
        end = start + 1
        while end < len(lines) and lines[end].startswith(" "):
            end += 1
        block = [x for x in lines[start + 1:end] if x.strip().replace("no ", "").split()[0] != key]
        block.append(" " + ln)
        self.text = "\n".join(lines[:start + 1] + block + lines[end:])

    def writer(self) -> ArubaInstantWriter:
        return ArubaInstantWriter(Credentials("emulated-vc", "admin", "x", ssh_fingerprint=self.fingerprint),
                                  client=self.client, ssh_runner=self.ssh, snapshot=lambda: copy.deepcopy(self.radios))
