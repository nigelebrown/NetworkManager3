"""Aruba Instant AOS 8.10 configuration writer (AP-505 / AP-535 clusters).

Reuses the proven SIMS paths:
  * REST  POST /rest/ssid (SSID ↔ VLAN), readback via `show running-config` parse (backend.aruba.configuration)
  * REST  GET  /rest/show-cmd (running-config, ARM RF summary) for backup and readback
  * SSH   allowlisted CLI with pinned host key (backend.aruba.ssh.run_config_lines) for ARM profile
          settings (ClientMatch, band steering, airtime fairness, TX power bounds, channel width policy)
          and per-AP static channel/EIRP (`a-channel` / `g-channel`).

Instant has no device-side configuration checkpoint, so rollback is an INVERSE OPERATION built from the
values read before the change, followed by a readback. 80 MHz is never configured: every width operation
also emits `no 80mhz-support`.
"""
from __future__ import annotations

import re

from . import Credentials, WriterError

ARM_KEYS = {
    "ap.client_match": "client-match", "ap.band_steering": "band-steering-mode",
    "ap.airtime_fairness": "air-time-fairness-mode", "ap.radio.tx_power": "tx-power", "ap.radio.width": "wide-bands",
}


def arm_block(text: str) -> dict:
    """Parse the `arm` section of an Instant running-config."""
    out: dict = {}
    in_arm = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if re.match(r"^arm\s*$", line):
            in_arm = True
            continue
        if in_arm and line and not line.startswith(" "):
            break
        if not in_arm or not line.strip():
            continue
        s = line.strip()
        if s == "client-match":
            out["client-match"] = True
        elif s == "no client-match":
            out["client-match"] = False
        elif m := re.match(r"^(band-steering-mode|air-time-fairness-mode|wide-bands) (\S+)$", s):
            out[m.group(1)] = m.group(2)
        elif m := re.match(r"^(min|max)-tx-power (\d+)$", s):
            out[f"{m.group(1)}-tx-power"] = int(m.group(2))
        elif s == "80mhz-support":
            out["80mhz-support"] = True
        elif s == "no 80mhz-support":
            out["80mhz-support"] = False
    out.setdefault("client-match", False)
    return out


def cli_block(text: str, header: str) -> list[str] | None:
    """Indented lines of one top-level CLI block (e.g. 'wlan ssid-profile Corp'), or None if absent."""
    lines = text.replace("\r", "").split("\n")
    try:
        i = [ln.rstrip() for ln in lines].index(header)
    except ValueError:
        return None
    out = []
    for ln in lines[i + 1:]:
        if not ln.startswith(" "):
            break
        out.append(ln.strip())
    return out


def block_dict(lines: list[str] | None) -> dict:
    out: dict = {}
    for ln in lines or []:
        k, _, v = ln.partition(" ")
        out[k] = v
    return out


ENTERPRISE_OPMODES = {"wpa2-enterprise": "wpa2-aes", "wpa3-enterprise": "wpa3-aes-ccm-128"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


class ArubaInstantWriter:
    family = "access_point"
    key = "aruba_instant_8_10"
    # Every typed operation implemented below (asserted equal to domain.adapters.INSTANT_TYPED_OPS).
    OPERATIONS = ("ap.ssid.vlan", "ap.ssid.create", "ap.ssid.enterprise", "ap.radio.channel", "ap.radio.width",
                  "ap.radio.tx_power", "ap.client_match", "ap.band_steering", "ap.airtime_fairness")

    def __init__(self, creds: Credentials, *, verify: bool | str = True, client=None, ssh_runner=None,
                 snapshot=None):
        from ...aruba.client import ArubaClient, ArubaTarget
        from ...aruba.ssh import run_config_lines
        self.creds = creds
        self.client = client or ArubaClient(ArubaTarget(creds.host, creds.port or 4343, creds.username, creds.password),
                                            verify=verify)
        self._ssh = ssh_runner or run_config_lines
        self._snapshot = snapshot
        self.ap_ip = creds.extra.get("ap_ip", creds.host)

    # ---- session -------------------------------------------------------------------------
    def open(self) -> None:
        from ...aruba.errors import ArubaAuthError, ArubaError
        try:
            self.client.login()
        except ArubaAuthError:
            raise WriterError("AUTH_REJECTED", "Instant VC rejected the credentials.") from None
        except ArubaError as exc:
            raise WriterError("UNREACHABLE", str(exc)[:200]) from None

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    def _running(self) -> str:
        from ...aruba.configuration import fetch_running_config
        from ...aruba.errors import ArubaError
        try:
            return fetch_running_config(self.client)
        except ArubaError as exc:
            raise WriterError("CONNECTION_LOST", str(exc)[:200]) from None

    def _ssh_lines(self, lines: list[str], *, exec_mode: bool = False, host: str | None = None) -> None:
        from ...aruba.errors import ArubaError
        if not self.creds.ssh_fingerprint:
            raise WriterError("PRECONDITION", "Pin the device SSH host key before ARM/radio changes.")
        seq = lines if exec_mode else ["configure terminal", *lines, "end", "commit apply"]
        try:
            self._ssh(host or self.creds.host, self.creds.username, self.creds.password, self.creds.ssh_fingerprint, seq)
        except ArubaError as exc:
            raise WriterError("DEVICE_REJECTED", str(exc)[:200]) from None

    def read_diagnostics(self, *, kind: str) -> dict:
        """Select, never interpolate, a documented read-only Instant show command."""
        from ..device_diagnostics import INSTANT_SHOW, safe_output
        from ...aruba.errors import ArubaError
        if kind not in INSTANT_SHOW:
            raise WriterError("UNSUPPORTED", "This Instant diagnostic is not on the read-only allowlist.")
        cmd = INSTANT_SHOW[kind]
        try:
            data = self.client.show(cmd)
        except ArubaError as exc:
            raise WriterError("DEVICE_REJECTED", f"Instant diagnostic failed: {type(exc).__name__}") from None
        if not isinstance(data, dict):
            raise WriterError("PARSE_ERROR", "Instant returned an unexpected diagnostic reply.")
        result = data.get("Command output") or data.get("command output") or data.get("output")
        if result is None:
            raise WriterError("PARSE_ERROR", "Instant did not return diagnostic output.")
        return {"kind": kind, "command": cmd, "output": safe_output(result),
                "source": "INSTANT_REST_SHOW"}

    def read_audit_logs(self, *, kind: str = "system", limit: int = 50) -> list[dict]:
        """Read a bounded Instant VC log view via the documented GET show-cmd API.

        No SSH or configuration request; the command and count are fixed/typed.
        The response is redacted before leaving the adapter.
        """
        from ..device_audit import normalize_instant
        from ...aruba.errors import ArubaError
        if kind not in ("system", "security", "user") or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise WriterError("INVALID", "Unsupported log category or count.")
        try:
            payload = self.client.show(f"show log {kind} {limit}")
            return normalize_instant(payload, kind=kind, limit=limit)
        except ArubaError as exc:
            raise WriterError("DEVICE_REJECTED", f"Instant log retrieval failed: {type(exc).__name__}") from None
        except ValueError:
            raise WriterError("PARSE_ERROR", "Instant returned an unexpected log format.") from None

    def management_authority(self) -> dict:
        """Instant clusters managed by Aruba Central / AirWave carry that in the running configuration."""
        text = self._running()
        if re.search(r"^\s*(aruba-central|cloud-server|activate-server)\b", text, re.M):
            return {"authority": "ARUBA_CENTRAL", "detail": "Cluster is configured for Aruba Central management."}
        if re.search(r"^\s*(ams-ip|ams-key|organization)\b", text, re.M):
            return {"authority": "AIRWAVE", "detail": "Cluster is configured for AirWave management."}
        return {"authority": "LOCAL", "detail": "No cloud/AMP management configuration found."}

    # ---- vault / checkpoint --------------------------------------------------------------
    def backup(self) -> bytes:
        return self._running().encode()

    def checkpoint(self) -> str:
        """Instant has no device checkpoint: capture the running config + radio state as the restore point."""
        import json
        self._cp = {"running": self._running(), "radios": self._radios()}
        return "instant-prior-state:" + str(abs(hash(json.dumps(self._cp["radios"], sort_keys=True, default=str))))

    def export_checkpoint(self) -> dict:
        """Prior state for the encrypted recovery journal (survives process restarts — E2E-14)."""
        return dict(getattr(self, "_cp", None) or {})

    def import_checkpoint(self, prior: dict) -> None:
        self._cp = dict(prior)

    def _radios(self) -> list[dict]:
        if self._snapshot is not None:
            return self._snapshot()
        from ...aruba.monitoring import collect_snapshot
        snap = collect_snapshot(self.client)
        ap = next((a for a in snap.aps if a.get("ip") == self.ap_ip), None)
        return list((ap or {}).get("radios") or [])

    def rollback(self, checkpoint: str) -> None:
        prior = getattr(self, "_cp", None)
        if not prior:
            raise WriterError("ROLLBACK_UNAVAILABLE", "No captured prior state.")
        arm = arm_block(prior["running"])
        lines = ["arm", "client-match" if arm.get("client-match") else "no client-match"]
        for k in ("band-steering-mode", "air-time-fairness-mode", "wide-bands"):
            if k in arm:
                lines.append(f"{k} {arm[k]}")
        for k in ("min-tx-power", "max-tx-power"):
            if k in arm:
                lines.append(f"{k} {arm[k]}")
        lines += ["no 80mhz-support", "exit"]
        self._ssh_lines(lines)
        # SSID / RADIUS blocks created or changed by the change: delete new ones, re-apply prior ones
        cur_text = self._running()
        undo: list[str] = []
        for hdr in sorted(set(re.findall(r"^(wlan (?:ssid-profile|auth-server) \S+)$", cur_text, re.M))):
            before, after = cli_block(prior["running"], hdr), cli_block(cur_text, hdr)
            if before is None:
                undo.append(f"no {hdr}")
            elif before != after:
                undo += [hdr, *[ln for ln in before if not ln.startswith("key ")], "exit"]
        if undo:
            self._ssh_lines(undo)
        from ...aruba.configuration import SsidChange, build_ssid_payload, parse_running_config
        from ...aruba.errors import ArubaError
        before = {x["profile"]: x for x in parse_running_config(prior["running"])["ssids"]}
        now = {x["profile"]: x for x in parse_running_config(self._running())["ssids"]}
        for prof, old in before.items():
            if isinstance(old.get("vlan"), int) and now.get(prof, {}).get("vlan") != old["vlan"]:
                try:
                    self.client.config_post("ssid", build_ssid_payload(SsidChange(profile=prof, vlan=old["vlan"]), creating=False))
                except ArubaError as exc:
                    raise WriterError("ROLLBACK_FAILED", str(exc)[:200]) from None
        for r in prior["radios"]:
            ch, pw = r.get("channel"), r.get("tx_power_dbm")
            if ch and str(ch).rstrip("+-E").isdigit() and pw:
                cmd = "a-channel" if str(r.get("band", "")).startswith("5") else "g-channel"
                self._ssh_lines([f"{cmd} {int(str(ch).rstrip('+-E'))} {int(pw)}"], exec_mode=True, host=self.ap_ip)

    def save(self) -> None:
        """Instant `commit apply` already persists cluster configuration."""

    def restore(self, config: bytes) -> None:
        raise WriterError("UNSUPPORTED", "Full-config restore on Instant is a guided procedure (Replace device wizard).")

    # ---- typed operations ------------------------------------------------------------------
    def render(self, op: dict) -> list[dict]:
        k, v = op.get("op"), op.get("value")
        if k == "ap.ssid.vlan":
            from ...aruba.configuration import SsidChange, build_ssid_payload
            payload = build_ssid_payload(SsidChange(profile=op["ssid"], vlan=int(v)), creating=False)
            return [{"transport": "REST", "endpoint": "ssid", "json": payload}]
        if k == "ap.radio.channel":
            band = op.get("band", "5")
            cmd = "a-channel" if band == "5" else "g-channel"
            return [{"transport": "SSH-EXEC", "host": self.ap_ip,
                     "lines": [f"{cmd} {int(v)} {int(op.get('power', 14))}"]}]
        if k == "ap.ssid.create":
            from ...aruba.configuration import SsidChange, build_ssid_payload, mask_payload
            secret = op.get("_secret")
            ch = SsidChange(profile=op["ssid"], essid=op.get("essid") or op["ssid"], vlan=int(op["vlan"]),
                            opmode=op.get("security", "wpa2-psk-aes"),
                            passphrase=(secret or "placeholder-secret") if op.get("security", "wpa2-psk-aes") != "opensystem" else None,
                            enabled=True)
            payload = build_ssid_payload(ch, creating=True)
            return [{"transport": "REST", "endpoint": "ssid", "json": payload if secret else mask_payload(payload)}]
        if k == "ap.ssid.enterprise":
            srv, prof = op["radius_name"], op["ssid"]
            key = op.get("_secret") or "********"
            lines = [f"wlan auth-server {srv}", f" ip {op['radius_ip']}", f" port {int(op.get('radius_port', 1812))}",
                     f" acctport {int(op.get('radius_acct_port', 1813))}", f" key {key}", "exit",
                     f"wlan ssid-profile {prof}", f" essid {op.get('essid') or prof}", " type employee",
                     f" vlan {int(op['vlan'])}", f" opmode {ENTERPRISE_OPMODES[op.get('security', 'wpa2-enterprise')]}",
                     f" auth-server {srv}", " enable", "exit"]
            return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                     "lines": ["configure terminal", *lines, "end", "commit apply"]}]
        lines = ["arm"]
        if k == "ap.client_match":
            lines.append("client-match" if v == "enabled" else "no client-match")
        elif k == "ap.band_steering":
            lines.append(f"band-steering-mode {'disable' if v == 'disabled' else v}")
        elif k == "ap.airtime_fairness":
            lines.append(f"air-time-fairness-mode {v}")
        elif k == "ap.radio.tx_power":
            lines += [f"min-tx-power {int(op['min_dbm'])}", f"max-tx-power {int(op['max_dbm'])}"]
        elif k == "ap.radio.width":
            if int(v) not in (20, 40):
                raise WriterError("INVALID", "Only 20 or 40 MHz.")
            lines.append("wide-bands none" if int(v) == 20 else "wide-bands 5ghz")
        else:
            raise WriterError("UNSUPPORTED", f"{k} is not supported by {self.key}")
        lines += ["no 80mhz-support", "exit"]
        return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                 "lines": ["configure terminal", *lines, "end", "commit apply"]}]

    def apply(self, op: dict) -> list[dict]:
        from ...aruba.errors import ArubaError
        calls = self.render(op)
        for c in calls:
            if c["transport"] == "REST":
                try:
                    self.client.config_post(c["endpoint"], c["json"])
                except ArubaError as exc:
                    raise WriterError("DEVICE_REJECTED", str(exc)[:200]) from None
            elif c["transport"] == "SSH-EXEC":
                self._ssh_lines(c["lines"], exec_mode=True, host=c["host"])
            else:
                self._ssh_lines(c["lines"][1:-2])   # wrapper adds configure/end/commit
        return calls

    def readback(self, op: dict) -> tuple[bool, dict]:
        k, v = op.get("op"), op.get("value")
        if k == "ap.ssid.create":
            b = block_dict(cli_block(self._running(), f"wlan ssid-profile {op['ssid']}"))
            want = {"essid": op.get("essid") or op["ssid"], "vlan": str(int(op["vlan"])),
                    "opmode": op.get("security", "wpa2-psk-aes")}
            got = {x: b.get(x) for x in want}
            return got == want, got
        if k == "ap.ssid.enterprise":
            text = self._running()
            srv = block_dict(cli_block(text, f"wlan auth-server {op['radius_name']}"))
            b = block_dict(cli_block(text, f"wlan ssid-profile {op['ssid']}"))
            got = {"radius_ip": srv.get("ip"), "radius_key_set": "key" in srv, "opmode": b.get("opmode"),
                   "auth_server": b.get("auth-server"), "vlan": b.get("vlan")}
            want = {"radius_ip": op["radius_ip"], "radius_key_set": True,
                    "opmode": ENTERPRISE_OPMODES[op.get("security", "wpa2-enterprise")],
                    "auth_server": op["radius_name"], "vlan": str(int(op["vlan"]))}
            return got == want, got
        if k == "ap.ssid.vlan":
            from ...aruba.configuration import SsidChange, parse_running_config, verify_ssid
            problems = verify_ssid(parse_running_config(self._running()), SsidChange(profile=op["ssid"], vlan=int(v)))
            return not problems, {"problems": problems}
        if k == "ap.radio.channel":
            band = op.get("band", "5")
            radios = self._radios()
            r = next((x for x in radios if str(x.get("band", "")).startswith(band[0])), None)
            got = str((r or {}).get("channel") or "").rstrip("+-E")
            return got == str(int(v)), {"channel": got or None}
        arm = arm_block(self._running())
        if arm.get("80mhz-support"):
            return False, {"error": "80 MHz support is enabled on the device", **arm}
        want = {"ap.client_match": ("client-match", v == "enabled"),
                "ap.band_steering": ("band-steering-mode", "disable" if v == "disabled" else v),
                "ap.airtime_fairness": ("air-time-fairness-mode", v),
                "ap.radio.width": ("wide-bands", "none" if v == 20 else "5ghz")}
        if k == "ap.radio.tx_power":
            ok = arm.get("min-tx-power") == int(op["min_dbm"]) and arm.get("max-tx-power") == int(op["max_dbm"])
            return ok, {"min-tx-power": arm.get("min-tx-power"), "max-tx-power": arm.get("max-tx-power")}
        key, val = want[k]
        return arm.get(key) == val, {key: arm.get(key)}
