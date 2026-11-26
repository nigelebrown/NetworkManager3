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
import logging

import re

from . import Credentials, WriterError
from ...domain import change_control as CC

_log = logging.getLogger(__name__)

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
        elif m := re.fullmatch(r"a-channels ([0-9A-Za-z+,\-]+)", s):
            out["a-channels"] = m.group(1)
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
SSID_FLAGS = ("dot11k", "dot11v", "dot11r", "radius-accounting")
ACCOUNTING_INTERVAL = "radius-interim-accounting-interval"
GUEST_KEYS = ("captive-portal", "mac-authentication", "set-role-pre-auth", "set-role-mac-auth")
MULTI_AUTH_KEYS = ("mac-authentication", "l2-auth-failthrough", "set-role-mac-auth")
RC17_WLAN_OPS = ("ap.role.define", "ap.captive_portal.external", "ap.ssid.multi_auth")
RC18_RADIUS_OPS = ("ap.radius.server", "ap.ssid.auth_servers")
AUTH_SERVER_KEYS = (("ip", "ip"), ("port", "port"), ("acct_port", "acctport"), ("timeout", "timeout"),
                    ("retry_count", "retry-count"))


def auth_server_lines(op: dict) -> list[str]:
    out = [f"{cli} {op[f]}" for f, cli in AUTH_SERVER_KEYS if op.get(f) is not None]
    if op.get("_secret"):
        out.append(f"key {op['_secret']}")
    elif op.get("secret_ref"):
        out.append("key ********")                      # preview only; apply refuses a masked secret
    return out


def ssid_auth_lines(op: dict) -> list[str]:
    lines = [f"auth-server {s}" for s in op["servers"]]
    lines.append("server-load-balancing" if op["load_balancing"] else "no server-load-balancing")
    if op["accounting"] == "enabled":
        lines += ["radius-accounting", f"radius-interim-accounting-interval {op['interval_minutes']}"]
    elif op["accounting"] == "disabled":
        lines.append("no radius-accounting")
    return lines


def role_lines(op: dict) -> list[str]:
    out = []
    for r in op["rules"]:
        dest = "any any" if r["dest"] == "any" else f"{r['dest']['ip']} {r['dest']['mask']}"
        ports = "any any" if r["ports"] is None else f"{r['ports'][0]} {r['ports'][1]}"
        out.append(f"rule {dest} match {r['protocol']} {ports} {r['action']}")
    return out


def portal_lines(op: dict) -> list[str]:
    out = [f"server {op['server']}", f"port {op['port']}", f'url "{op["url"]}"']
    if op["https"]:
        out.append("https")
    if op.get("redirect_url"):
        out.append(f"redirect-url {op['redirect_url']}")
    return out


def multi_auth_settings(lines: list[str] | None) -> dict:
    if lines is None:
        raise WriterError("PRECONDITION", "The exact existing SSID profile was not found.")
    out = {}
    for key in MULTI_AUTH_KEYS:
        m = [ln for ln in lines if ln == key or ln == "no " + key or ln.startswith(key + " ")]
        if len(m) > 1:
            raise WriterError("PRECONDITION", "Ambiguous authentication settings; no write attempted.")
        out[key] = m[0] if m else None
    return out


def guest_auth_settings(lines):
    if lines is None or "type guest" not in lines:
        raise WriterError("PRECONDITION", "An existing guest SSID is required.")
    result = {}
    for key in GUEST_KEYS:
        matches = [line for line in lines if line == key or line.startswith(key + " ") or line == "no " + key]
        if len(matches) != 1:
            raise WriterError("PRECONDITION", "An explicit, unambiguous guest authentication baseline is required.")
        value = matches[0]
        valid = (re.fullmatch(r"captive-portal external profile [A-Za-z0-9][A-Za-z0-9_.-]{0,31}", value) if key == "captive-portal" else
                 value == "mac-authentication" if key == "mac-authentication" else
                 re.fullmatch(key + r" [A-Za-z0-9][A-Za-z0-9_.-]{0,31}", value))
        if not valid:
            raise WriterError("PRECONDITION", "Existing guest authentication setting cannot be safely restored.")
        result[key] = value
    return result


def ssid_settings(lines: list[str] | None) -> dict:
    if lines is None:
        raise WriterError("PRECONDITION", "The exact existing SSID profile was not found.")
    result = {}
    for key in SSID_FLAGS:
        matches = [line for line in lines if line == key or line == "no " + key
                   or line.startswith(key + " ") or line.startswith("no " + key + " ")]
        if len(matches) > 1 or (matches and matches[0] not in (key, "no " + key)):
            raise WriterError("PRECONDITION", "SSID setting format is ambiguous; no write attempted.")
        result[key] = matches[0] if matches else None
    intervals = [line for line in lines if line.startswith(ACCOUNTING_INTERVAL + " ")]
    if len(intervals) > 1 or (intervals and not re.fullmatch(
            r"radius-interim-accounting-interval (?:[0-9]|[1-5][0-9]|60)(?: (?:[0-9]|[1-5][0-9]))?", intervals[0])):
        raise WriterError("PRECONDITION", "Prior RADIUS accounting interval cannot be safely restored.")
    result[ACCOUNTING_INTERVAL] = intervals[0] if intervals else None
    return result


def regulatory_channels(text: str) -> tuple[str, set[str]]:
    """Read documented 5 GHz 20/40 MHz regulatory rows; use the indoor/outdoor intersection."""
    country = re.search(r"(?im)^Allowed Channels for AP Type\s+\S+\s+Country Code\s+([A-Z]{2})\s*$", text)
    if not country:
        raise WriterError("PRECONDITION", "Regulatory domain is unavailable.")
    rows = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(?:5\s*GHz|802\.11a)(?:\s+(40)\s*MHz)?\s*\((indoor|outdoor)\)\s+(.+?)\s*", line, re.I)
        if not match:
            continue
        width, placement, raw = match.groups()
        key = (int(width or 20), placement.lower())
        if key in rows:
            raise WriterError("PRECONDITION", "Duplicate regulatory channel row.")
        tokens = set()
        for value in raw.split():
            if width and re.fullmatch(r"[0-9]{1,3}-[0-9]{1,3}", value):
                lo, hi = map(int, value.split("-"))
                if hi == lo + 4:
                    tokens.add(str(lo) + "+")
            elif not width and value.isdecimal():
                tokens.add(str(int(value)))
        rows[key] = tokens
    if (20, "indoor") not in rows or (20, "outdoor") not in rows:
        raise WriterError("PRECONDITION", "Unrecognized 5 GHz regulatory channel output.")
    allowed = rows[(20, "indoor")] & rows[(20, "outdoor")]
    allowed |= rows.get((40, "indoor"), set()) & rows.get((40, "outdoor"), set())
    return country.group(1).upper(), allowed


def _ssid_header(op):
    if not NAME_RE.fullmatch(str(op.get("ssid", ""))):
        raise WriterError("INVALID", "Invalid SSID profile.")
    return "wlan ssid-profile " + op["ssid"]


def _validated(op):
    try:
        return CC.validate_operation(op, family="access_point", allow_dfs=op.get("allow_dfs") is True)
    except CC.PolicyViolation as exc:
        raise WriterError("INVALID", str(exc)) from None



class ArubaInstantWriter:
    family = "access_point"
    key = "aruba_instant_8_10"
    # Every typed operation implemented below (asserted equal to domain.adapters.INSTANT_TYPED_OPS).
    OPERATIONS = ("ap.ssid.vlan", "ap.ssid.create", "ap.ssid.enterprise", "ap.radio.channel", "ap.radio.width",
                  "ap.radio.tx_power", "ap.client_match", "ap.band_steering", "ap.airtime_fairness",
                  "ap.ssid.roaming", "ap.ssid.accounting", "ap.radio.allowed_channels", "ap.ssid.guest_auth",
                  "ap.role.define", "ap.captive_portal.external", "ap.ssid.multi_auth",
                  "ap.radius.server", "ap.ssid.auth_servers")

    def __init__(self, creds: Credentials, *, verify: bool | str = True, client=None, ssh_runner=None,
                 snapshot=None, diagnostic_target=None):
        from ...aruba.client import ArubaClient, ArubaTarget
        from ...aruba.ssh import run_config_lines
        self.creds = creds
        self.client = client or ArubaClient(ArubaTarget(creds.host, creds.port or 4343, creds.username, creds.password),
                                            verify=verify)
        self._ssh = ssh_runner or run_config_lines
        self._snapshot = snapshot
        self.ap_ip = creds.extra.get("ap_ip", creds.host)
        self.diagnostic_target = diagnostic_target

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
        except Exception as _exc:
            _log.debug("best-effort closing the Instant client: %s", type(_exc).__name__)

    def _read_configuration(self, *, full_fidelity: bool = False) -> str:
        """Retry only idempotent configuration reads after transient transport/503 errors."""
        import time
        from ...aruba import parsers
        from ...aruba.configuration import fetch_running_config
        from ...aruba.errors import ArubaConnectionError, ArubaError
        for attempt in range(3):
            try:
                if not full_fidelity:
                    return fetch_running_config(self.client)
                text = parsers.output_text(self.client.show("show running-config no-encrypt"))
                if not text or not re.search(r"(?m)^arm\s*$", text):
                    raise WriterError("BACKUP_UNVERIFIED", "Full Instant configuration was not returned.")
                return text
            except ArubaError as exc:
                transient = isinstance(exc, ArubaConnectionError) or "HTTP 503" in str(exc)
                if transient and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise WriterError("CONNECTION_LOST", str(exc)[:200]) from None
        raise WriterError("CONNECTION_LOST", "Configuration read retries exhausted.")

    def _running(self) -> str:
        return self._read_configuration()

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
        from ...aruba.ssh import SshShowClient
        ssh = isinstance(self.client, SshShowClient)
        target = self.diagnostic_target if kind in ("system", "rf") else None
        if ssh and target:
            if target != self.client.host:
                raise WriterError("UNSUPPORTED", "This SSH connection can read the controller only. Member RF and system diagnostics require targeted REST.")
            target = None
        try:
            data = self.client.show(cmd, iap_ip=target) if target else self.client.show(cmd)
        except ArubaError as exc:
            raise WriterError("DEVICE_REJECTED", f"Instant diagnostic failed: {type(exc).__name__}") from None
        if not isinstance(data, dict):
            raise WriterError("PARSE_ERROR", "Instant returned an unexpected diagnostic reply.")
        result = data.get("Command output") or data.get("command output") or data.get("output")
        if result is None:
            raise WriterError("PARSE_ERROR", "Instant did not return diagnostic output.")
        return {"kind": kind, "command": cmd, "output": safe_output(result),
                "source": "INSTANT_SSH_SHOW" if ssh else "INSTANT_REST_SHOW"}

    def identity(self) -> dict:
        """Read the exact target AP identity, never assume a controller's identity is its member's."""
        from ...aruba import parsers
        from ...aruba.errors import ArubaError
        try:
            aps = parsers.normalize_aps(parsers.extract_rows(self.client.show("show aps"), "show aps"))
            members = [ap for ap in aps if ap.get("ip") == self.ap_ip]
            if len(members) != 1:
                raise WriterError("IDENTITY_UNVERIFIED", "The target AP is not uniquely present in the controller inventory.")
            member = members[0]
            firmware = parsers.parse_version(self.client.show("show version", iap_ip=self.ap_ip))
            if not member.get("serial") or not member.get("model") or not firmware:
                raise WriterError("IDENTITY_UNVERIFIED", "Target AP serial, model or firmware could not be verified.")
            return {"serial_number": member["serial"], "model": member["model"],
                    "firmware": firmware, "management_ip": self.ap_ip}
        except ArubaError:
            raise WriterError("IDENTITY_UNVERIFIED", "Target AP identity commands failed.") from None

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
        """Full-fidelity vault input, never a redacted UI view or rotating ciphertext.

        Instant randomizes some encrypted fields on every show. Read the
        documented no-encrypt form so equality checks include the actual secrets;
        the executor seals these bytes before persistence.
        """
        return self._read_configuration(full_fidelity=True).encode()

    def checkpoint(self) -> str:
        """Instant has no device checkpoint: capture the running config + radio state as the restore point."""
        import json
        if not self.creds.ssh_fingerprint:
            raise WriterError("PRECONDITION", "Pin the device SSH host key before configuration tests.")
        self._cp = {"running": self._running(), "radios": self._radios(),
                    "radio_overrides": self._radio_overrides()}
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

    def _radio_overrides(self) -> dict | None:
        """Capture configured overrides, not ARM's current dynamically selected RF values."""
        from ...aruba.errors import ArubaError
        try:
            payload = self.client.show("show ap-env", iap_ip=self.ap_ip)
        except ArubaError:
            return None
        text = payload.get("Command output") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not re.search(r"(?im)^name\s*:", text):
            return None
        values = dict(re.findall(r"(?im)^([ag]_(?:chan|tx_power))\s*:\s*(-?\d+(?:\.\d+)?)\s*$", text))
        return {band: {"channel": values.get(f"{prefix}_chan", "0"),
                       "power": values.get(f"{prefix}_tx_power", "0")}
                for band, prefix in (("5", "a"), ("2.4", "g"))}

    def rollback(self, checkpoint: str) -> None:
        prior = getattr(self, "_cp", None)
        if not prior:
            raise WriterError("ROLLBACK_UNAVAILABLE", "No captured prior state.")
        before_text, cur_text = prior["running"], self._running()
        before_arm, cur_arm = arm_block(before_text), arm_block(cur_text)
        lines = []
        for key in ("client-match", "band-steering-mode", "air-time-fairness-mode",
                    "wide-bands", "min-tx-power", "max-tx-power", "80mhz-support"):
            if before_arm.get(key) == cur_arm.get(key):
                continue
            old = before_arm.get(key)
            if key in ("client-match", "80mhz-support"):
                # 80 MHz is enabled by default; its positive command removes a
                # temporary explicit disable when the original config omitted it.
                enabled = old if old is not None else key == "80mhz-support"
                lines.append(key if enabled else f"no {key}")
            else:
                lines.append(f"{key} {old}" if old is not None else f"no {key}")
        if before_arm.get("a-channels") != cur_arm.get("a-channels"):
            old_channels = before_arm.get("a-channels")
            if not old_channels:
                raise WriterError("ROLLBACK_FAILED", "No explicit previous channel list was captured.")
            try:
                CC.normalize_channel_list(old_channels.split(","), allow_dfs=True)
            except CC.PolicyViolation as exc:
                raise WriterError("ROLLBACK_FAILED", "Prior channel list is outside the supported 20/40 MHz grammar.") from None
            lines.append("a-channels " + old_channels)
        if lines:
            self._ssh_lines(["arm", *lines, "exit"])

        # rc18: restore modified/deleted RADIUS servers (attributes and, when rotated, the previous secret) and the
        # SSID's ordered auth-server list before the generic create/delete handling below.
        last = getattr(self, "_last_rc18", None) or {}
        for hdr in re.findall(r"^(wlan auth-server \S+)$", before_text, re.M):
            pb, cb = cli_block(before_text, hdr), cli_block(cur_text, hdr)
            name = hdr.split()[-1]
            if not NAME_RE.fullmatch(name):
                raise WriterError("ROLLBACK_FAILED", "Unsupported prior RADIUS server name.")
            pd, cd = block_dict(pb), block_dict(cb)
            fix = [f"{cli} {pd[cli]}" for _, cli in AUTH_SERVER_KEYS if pd.get(cli) is not None and pd.get(cli) != cd.get(cli)]
            rotated = last.get("name") == name and last.get("op") == "ap.radius.server" and bool(last.get("_previous_secret"))
            if cb is None or rotated or pd.get("key") != cd.get("key"):   # keys may be shown masked: trust the op
                secret = last.get("_previous_secret") if last.get("name") == name and last.get("_previous_secret") else \
                    last.get("_secret") if last.get("name") == name and last.get("action") == "delete" else None
                if secret is None:
                    raise WriterError("ROLLBACK_FAILED", f"The prior shared secret of {name} is not available.")
                fix.append(f"key {secret}")
            if cb is None:
                fix = [f"{cli} {pd[cli]}" for _, cli in AUTH_SERVER_KEYS if pd.get(cli) is not None] + [fix[-1]]
            if fix:
                self._ssh_lines([hdr, *fix, "exit"])
        for prof in re.findall(r"^wlan ssid-profile (\S+)$", before_text, re.M):
            hdr = "wlan ssid-profile " + prof
            pb, cb = cli_block(before_text, hdr), cli_block(self._running(), hdr)
            if pb is None or cb is None:
                continue
            ps = [ln for ln in pb if ln.startswith("auth-server ")]
            cs = [ln for ln in cb if ln.startswith("auth-server ")]
            plb, clb = "server-load-balancing" in pb, "server-load-balancing" in cb
            if ps != cs or plb != clb:
                if not NAME_RE.fullmatch(prof):
                    raise WriterError("ROLLBACK_FAILED", "Unsupported prior SSID profile name.")
                self._ssh_lines([hdr, *[f"no {ln}" for ln in cs], *ps,
                                 "server-load-balancing" if plb else "no server-load-balancing", "exit"])
        cur_text = self._running()

        # Delete dependent SSIDs before their newly created RADIUS servers.
        for kind in ("ssid-profile", "auth-server", "external-captive-portal", "access-rule"):
            headers = re.findall(rf"^(wlan {kind} \S+)$", cur_text, re.M)
            undo = [f"no {hdr}" for hdr in headers if cli_block(before_text, hdr) is None]
            if undo:
                self._ssh_lines(undo)

        from ...aruba.configuration import SsidChange, build_ssid_payload, parse_running_config
        from ...aruba.errors import ArubaError
        before = {x["profile"]: x for x in parse_running_config(before_text)["ssids"]}
        now = {x["profile"]: x for x in parse_running_config(self._running())["ssids"]}
        for prof, old in before.items():
            header = "wlan ssid-profile " + prof
            prior_block = cli_block(before_text, header)
            current_block = cli_block(self._running(), header)
            if prior_block is not None and current_block is not None:
                # Restore only settings managed by typed WLAN operations; never replay arbitrary CLI.
                changed = []
                for key in (*SSID_FLAGS, ACCOUNTING_INTERVAL):
                    a = [x for x in prior_block if x == key or x == "no " + key or x.startswith(key + " ")]
                    b = [x for x in current_block if x == key or x == "no " + key or x.startswith(key + " ")]
                    if a != b:
                        if not NAME_RE.fullmatch(prof):
                            raise WriterError("ROLLBACK_FAILED", "Unsupported prior SSID profile name.")
                        old_value = ssid_settings(prior_block)[key]
                        changed.append(old_value or "no " + key)
                if "type guest" not in prior_block:
                    pm, cm = multi_auth_settings(prior_block), multi_auth_settings(current_block)
                    for key in MULTI_AUTH_KEYS:
                        if pm[key] != cm[key]:
                            if pm[key] is not None and key == "set-role-mac-auth" and \
                                    not re.fullmatch(r"set-role-mac-auth [A-Za-z0-9][A-Za-z0-9_.-]{0,31}", pm[key]):
                                raise WriterError("ROLLBACK_FAILED", "Prior MAC-auth role cannot be safely restored.")
                            changed.append(pm[key] if pm[key] is not None else
                                           ("no " + key if key != "set-role-mac-auth" else "no set-role-mac-auth"))
                if "type guest" in prior_block:
                    guest_changed = []
                    for key in GUEST_KEYS:
                        a = [x for x in prior_block if x == key or x == "no " + key or x.startswith(key + " ")]
                        b = [x for x in current_block if x == key or x == "no " + key or x.startswith(key + " ")]
                        if a != b:
                            if not NAME_RE.fullmatch(prof):
                                raise WriterError("ROLLBACK_FAILED", "Unsupported prior guest SSID name.")
                            guest_changed.append(guest_auth_settings(prior_block)[key])
                    changed.extend(guest_changed)
                if changed:
                    self._ssh_lines([header, *changed, "exit"])
            if old.get("vlan") is None and now.get(prof, {}).get("vlan") is not None:
                self._ssh_lines([f"wlan ssid-profile {prof}", "no vlan", "exit"])
            elif isinstance(old.get("vlan"), str) and now.get(prof, {}).get("vlan") != old["vlan"]:
                if not NAME_RE.fullmatch(old["vlan"]):
                    raise WriterError("ROLLBACK_FAILED", "Unsupported prior VLAN name.")
                self._ssh_lines([f"wlan ssid-profile {prof}", f"vlan {old['vlan']}", "exit"])
            elif isinstance(old.get("vlan"), int) and now.get(prof, {}).get("vlan") != old["vlan"]:
                try:
                    self.client.config_post("ssid", build_ssid_payload(SsidChange(profile=prof, vlan=old["vlan"]), creating=False))
                except ArubaError as exc:
                    raise WriterError("ROLLBACK_FAILED", str(exc)[:200]) from None
        overrides = prior.get("radio_overrides")
        if overrides is not None:
            current = self._radio_overrides()
            if current is None:
                raise WriterError("ROLLBACK_FAILED", "Cannot verify configured radio overrides.")
            changed = False
            for band, old in overrides.items():
                if old != current.get(band):
                    changed = True
                    cmd = "a-channel" if band == "5" else "g-channel"
                    self._ssh_lines([f"{cmd} {old['channel']} {old['power']}"],
                                    exec_mode=True, host=self.ap_ip)
            if changed and self._radio_overrides() != overrides:
                raise WriterError("ROLLBACK_FAILED", "Radio override rollback readback does not match the baseline.")

    def save(self) -> None:
        """Instant `commit apply` already persists cluster configuration."""

    def restore(self, config: bytes) -> None:
        raise WriterError("UNSUPPORTED", "Full-config restore on Instant is a guided procedure (Replace device wizard).")

    # ---- typed operations ------------------------------------------------------------------
    def render(self, op: dict) -> list[dict]:
        k, v = op.get("op"), op.get("value")
        if k in RC18_RADIUS_OPS:
            secret, prev = op.get("_secret"), op.get("_previous_secret")
            op = _validated({x: y for x, y in op.items() if not x.startswith("_")}) | \
                ({"_secret": secret} if secret else {}) | ({"_previous_secret": prev} if prev else {})
            if k == "ap.radius.server":
                header = f"wlan auth-server {op['name']}"
                lines = [f"no {header}"] if op["action"] == "delete" else [header, *auth_server_lines(op), "exit"]
            else:
                current = [ln.split(" ", 1)[1] for ln in (cli_block(self._running(), _ssid_header(op)) or [])
                           if ln.startswith("auth-server ")]
                lines = [_ssid_header(op), *[f"no auth-server {x}" for x in current],   # re-add in priority order
                         *ssid_auth_lines(op), "exit"]
            return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                     "lines": ["configure terminal", *lines, "end", "commit apply"]}]
        if k in RC17_WLAN_OPS:
            op = _validated(op)
            if k == "ap.role.define":
                lines = [f"wlan access-rule {op['role']}", *role_lines(op), "exit"]
            elif k == "ap.captive_portal.external":
                lines = [f"wlan external-captive-portal {op['profile']}", *portal_lines(op), "exit"]
            elif op["value"] == "enabled":
                lines = [_ssid_header(op), "mac-authentication", "l2-auth-failthrough",
                         f"set-role-mac-auth {op['mac_role']}", "exit"]
            else:
                lines = [_ssid_header(op), "no mac-authentication", "no l2-auth-failthrough", "exit"]
            return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                     "lines": ["configure terminal", *lines, "end", "commit apply"]}]
        if k == "ap.ssid.guest_auth":
            op = _validated(op)
            return [{"transport": "SSH-CONFIG", "host": self.creds.host, "lines": ["configure terminal", _ssid_header(op),
                    "captive-portal external profile " + op["portal_profile"], "mac-authentication",
                    "set-role-pre-auth " + op["pre_auth_role"], "set-role-mac-auth " + op["mac_role"], "exit", "end", "commit apply"]}]
        if k in ("ap.ssid.roaming", "ap.ssid.accounting", "ap.radio.allowed_channels"):
            op = _validated(op)
            if k == "ap.radio.allowed_channels":
                lines = ["arm", "a-channels " + ",".join(op["value"]), "exit"]
            elif k == "ap.ssid.roaming":
                lines = [_ssid_header(op), *(key if op[key] == "enabled" else "no " + key
                                              for key in SSID_FLAGS[:3]), "exit"]
            else:
                lines = [_ssid_header(op), "radius-accounting" if v == "enabled" else "no radius-accounting"]
                if v == "enabled":
                    lines.append(f"{ACCOUNTING_INTERVAL} {op['interval_minutes']}")
                lines.append("exit")
            return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                     "lines": ["configure terminal", *lines, "end", "commit apply"]}]
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
                            # preview without a resolved secret is masked; apply refuses it (see apply precondition)
                            passphrase=(secret or "********") if op.get("security", "wpa2-psk-aes") != "opensystem" else None,
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
        if k == "ap.radio.width":
            lines.append("no 80mhz-support")
        lines.append("exit")
        return [{"transport": "SSH-CONFIG", "host": self.creds.host,
                 "lines": ["configure terminal", *lines, "end", "commit apply"]}]

    def _check_cluster_channels(self, tokens: list[str]) -> None:
        """Check each represented supported model against its reported country/channel table."""
        from ...aruba import parsers
        from ...aruba.errors import ArubaError
        try:
            members = parsers.normalize_aps(parsers.extract_rows(self.client.show("show aps"), "show aps"))
            representatives = {}
            for member in members:
                raw_model = str(member.get("model") or "").upper()
                match = re.search(r"\bAP-?(505|535)\b", raw_model)
                model = "AP-" + match.group(1) if match else None
                if model is None or not member.get("ip"):
                    raise WriterError("PRECONDITION", "Cluster membership or model is outside the verified channel scope.")
                representatives[model] = member["ip"]
            if not representatives or not any(m.get("ip") == self.ap_ip for m in members):
                raise WriterError("PRECONDITION", "The selected AP is not in the returned cluster membership.")
            countries = set()
            for target in representatives.values():
                raw = parsers.output_text(self.client.show("show ap allowed-channels", iap_ip=target)) or ""
                country, allowed = regulatory_channels(raw)
                countries.add(country)
                if not set(tokens) <= allowed:
                    raise WriterError("PRECONDITION", "Requested channels are not in the reported regulatory intersection.")
            if len(countries) != 1:
                raise WriterError("PRECONDITION", "Cluster members report inconsistent regulatory domains.")
        except ArubaError:
            raise WriterError("PRECONDITION", "Could not verify cluster regulatory channels.") from None

    def apply(self, op: dict) -> list[dict]:
        from ...aruba.errors import ArubaError
        prior = getattr(self, "_cp", None)
        if op.get("op") == "ap.radio.channel" and (not prior or prior.get("radio_overrides") is None):
            raise WriterError("PRECONDITION", "Capture configured radio overrides before changing a channel.")
        if op.get("op") == "ap.ssid.guest_auth":
            op = _validated(op)
            if not prior or not prior.get("running"):
                raise WriterError("PRECONDITION", "Capture a guest configuration checkpoint first.")
            text = self._running()
            header = _ssid_header(op)
            guest_auth_settings(cli_block(prior["running"], header))
            guest_auth_settings(cli_block(text, header))
            portal = cli_block(text, "wlan external-captive-portal " + op["portal_profile"])
            if not portal or any("fail-through" in line.lower() for line in portal):
                raise WriterError("PRECONDITION", "Select an existing external portal without fail-through.")
            for role in (op["pre_auth_role"], op["mac_role"]):
                if not cli_block(text, "wlan access-rule " + role):
                    raise WriterError("PRECONDITION", "Selected role must already exist with configured access rules.")
        if op.get("op") in ("ap.ssid.roaming", "ap.ssid.accounting", "ap.radio.allowed_channels"):
            op = _validated(op)
            if not prior or not prior.get("running"):
                raise WriterError("PRECONDITION", "Capture a configuration checkpoint before changing WLAN or channel settings.")
            text = self._running()
            if op["op"] == "ap.radio.allowed_channels":
                arm = arm_block(text)
                baseline = arm_block(prior["running"])
                if arm.get("80mhz-support") is not False or baseline.get("80mhz-support") is not False:
                    raise WriterError("PRECONDITION", "Explicitly disable 80 MHz before changing the allowed channel list.")
                if arm.get("wide-bands") not in ("none", "5ghz"):
                    raise WriterError("PRECONDITION", "Set an explicit supported 20/40 MHz width policy first.")
                for state in (baseline, arm):
                    try:
                        CC.normalize_channel_list((state.get("a-channels") or "").split(","), allow_dfs=True)
                    except CC.PolicyViolation:
                        raise WriterError("PRECONDITION", "An explicit, restorable 20/40 MHz channel baseline is required.") from None
                if arm["wide-bands"] == "none" and any(t.endswith("+") for t in op["value"]):
                    raise WriterError("PRECONDITION", "40-MHz allowed channels need a 40-MHz width policy.")
                self._check_cluster_channels(op["value"])
            else:
                header = _ssid_header(op)
                block = cli_block(text, header)
                ssid_settings(block)
                ssid_settings(cli_block(prior["running"], header))
                if op["op"] == "ap.ssid.accounting" and op["value"] == "enabled":
                    servers = [ln.split(" ", 1)[1] for ln in block if ln.startswith(("auth-server ", "accounting-server "))]
                    if not any(NAME_RE.fullmatch(name) and cli_block(text, "wlan auth-server " + name) is not None
                               for name in servers):
                        raise WriterError("PRECONDITION", "Configure and validate an external RADIUS server before enabling accounting.")
        if op.get("op") in RC18_RADIUS_OPS:
            if not prior or not prior.get("running"):
                raise WriterError("PRECONDITION", "Capture a configuration checkpoint before changing RADIUS settings.")
            v = _validated({x: y for x, y in op.items() if not x.startswith("_")})
            text = self._running()
            self._last_rc18 = dict(op)                     # rollback needs the resolved secrets of THIS change
            if v["op"] == "ap.radius.server":
                exists = cli_block(text, f"wlan auth-server {v['name']}") is not None
                if v["action"] == "create" and exists:
                    raise WriterError("PRECONDITION", "RADIUS server already exists; use modify.")
                if v["action"] != "create" and not exists:
                    raise WriterError("PRECONDITION", "RADIUS server does not exist.")
                if v["action"] == "delete" and re.search(rf"^ auth-server {re.escape(v['name'])}$", text, re.M):
                    raise WriterError("PRECONDITION", "RADIUS server is still assigned to an SSID; reassign it first.")
                if v.get("secret_ref") and v["action"] != "delete" and not op.get("_secret"):
                    raise WriterError("PRECONDITION", "The shared secret could not be resolved from secret_ref.")
                if v["action"] == "delete" and not op.get("_secret"):
                    raise WriterError("PRECONDITION", "The deleted server's secret must be resolvable for rollback.")
            else:
                block = cli_block(text, _ssid_header(v))
                d = block_dict(block)
                if block is None or d.get("opmode") not in ENTERPRISE_OPMODES.values():
                    raise WriterError("PRECONDITION", "Assign RADIUS servers only to an existing enterprise SSID.")
                for name in v["servers"]:
                    if cli_block(text, f"wlan auth-server {name}") is None:
                        raise WriterError("PRECONDITION", f"RADIUS server {name} does not exist.")
        if op.get("op") in RC17_WLAN_OPS:
            op = _validated(op)
            if not prior or not prior.get("running"):
                raise WriterError("PRECONDITION", "Capture a configuration checkpoint before changing WLAN security.")
            text = self._running()
            if op["op"] == "ap.role.define" and cli_block(text, f"wlan access-rule {op['role']}") is not None:
                raise WriterError("PRECONDITION", "Role already exists; refusing to overwrite it.")
            if op["op"] == "ap.captive_portal.external" and \
                    cli_block(text, f"wlan external-captive-portal {op['profile']}") is not None:
                raise WriterError("PRECONDITION", "Portal profile already exists; refusing to overwrite it.")
            if op["op"] == "ap.ssid.multi_auth":
                block = cli_block(text, _ssid_header(op))
                multi_auth_settings(block)
                d = block_dict(block)
                if d.get("opmode") not in ENTERPRISE_OPMODES.values() or not d.get("auth-server"):
                    raise WriterError("PRECONDITION", "MAC + 802.1X needs an existing enterprise SSID with a RADIUS server.")
                if op["value"] == "enabled" and not cli_block(text, "wlan access-rule " + op["mac_role"]):
                    raise WriterError("PRECONDITION", "The MAC-authentication role must already exist with rules.")
        if op.get("op") == "ap.ssid.vlan":
            previous = block_dict(cli_block(self._running(), f"wlan ssid-profile {op['ssid']}")).get("vlan")
            if previous and not (previous.isdigit() or NAME_RE.fullmatch(previous)):
                raise WriterError("PRECONDITION", "Prior SSID VLAN cannot be safely restored.")
        if op.get("op") in ("ap.ssid.create", "ap.ssid.enterprise"):
            needs_secret = op.get("op") == "ap.ssid.enterprise" or op.get("security", "wpa2-psk-aes") != "opensystem"
            if needs_secret and not op.get("_secret"):
                # rc18 sweep: never send a masked or placeholder key to a device
                raise WriterError("PRECONDITION", "The SSID/RADIUS secret could not be resolved from secret_ref; "
                                                  "nothing was sent.")
            text = self._running()
            headers = [f"wlan ssid-profile {op['ssid']}"]
            if op.get("op") == "ap.ssid.enterprise":
                headers.append(f"wlan auth-server {op['radius_name']}")
            if any(cli_block(text, header) is not None for header in headers):
                raise WriterError("PRECONDITION", "SSID or RADIUS profile already exists; refusing to overwrite it.")
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
        if k in RC18_RADIUS_OPS:
            op = _validated({x: y for x, y in op.items() if not x.startswith("_")})
            text = self._running()
            if k == "ap.radius.server":
                block = cli_block(text, f"wlan auth-server {op['name']}")
                if op["action"] == "delete":
                    return block is None, {"present": block is not None}
                d = block_dict(block)
                got = {f: d.get(cli) for f, cli in AUTH_SERVER_KEYS if op.get(f) is not None}
                want = {f: str(op[f]) for f, cli in AUTH_SERVER_KEYS if op.get(f) is not None}
                ok = block is not None and got == want and (not op.get("secret_ref") or "key" in d)
                return ok, {"present": block is not None, **got, "key_set": "key" in d}
            block = cli_block(text, _ssid_header(op)) or []
            servers = [ln.split(" ", 1)[1] for ln in block if ln.startswith("auth-server ")]
            lb = "server-load-balancing" in block
            acct = "radius-accounting" in block
            ok = servers == op["servers"] and lb == op["load_balancing"] and (
                op["accounting"] == "unchanged" or acct == (op["accounting"] == "enabled"))
            return ok, {"servers": servers, "load_balancing": lb, "accounting": acct}
        if k in RC17_WLAN_OPS:
            op = _validated(op)
            text = self._running()
            if k == "ap.role.define":
                got = cli_block(text, f"wlan access-rule {op['role']}")
                return got == role_lines(op), {"rules": got}
            if k == "ap.captive_portal.external":
                got = cli_block(text, f"wlan external-captive-portal {op['profile']}") or []
                want = portal_lines(op)
                return all(w in got for w in want) and not any("fail-through" in g for g in got), {"lines": got}
            st = multi_auth_settings(cli_block(text, _ssid_header(op)))
            if op["value"] == "enabled":
                ok = st == {"mac-authentication": "mac-authentication", "l2-auth-failthrough": "l2-auth-failthrough",
                            "set-role-mac-auth": f"set-role-mac-auth {op['mac_role']}"}
            else:
                ok = st["mac-authentication"] in (None, "no mac-authentication") and \
                    st["l2-auth-failthrough"] in (None, "no l2-auth-failthrough")
            return ok, st
        if k == "ap.ssid.guest_auth":
            op = _validated(op)
            state = guest_auth_settings(cli_block(self._running(), _ssid_header(op)))
            wanted = {"captive-portal": "captive-portal external profile " + op["portal_profile"],
                      "mac-authentication": "mac-authentication", "set-role-pre-auth": "set-role-pre-auth " + op["pre_auth_role"],
                      "set-role-mac-auth": "set-role-mac-auth " + op["mac_role"]}
            return state == wanted, state
        if k in ("ap.ssid.roaming", "ap.ssid.accounting", "ap.radio.allowed_channels"):
            op = _validated(op)
            if k == "ap.radio.allowed_channels":
                arm = arm_block(self._running())
                try:
                    got = CC.normalize_channel_list((arm.get("a-channels") or "").split(","), allow_dfs=True)
                except CC.PolicyViolation:
                    return False, {"channels": None, "reason": "Missing or unrecognized channel list."}
                return got == op["value"] and arm.get("80mhz-support") is False, {"channels": got, "80mhz_support": arm.get("80mhz-support")}
            state = ssid_settings(cli_block(self._running(), _ssid_header(op)))
            if k == "ap.ssid.roaming":
                got = {key: "enabled" if state[key] == key else "disabled" for key in SSID_FLAGS[:3]}
                return all(got[key] == op[key] for key in got), got
            enabled = state["radius-accounting"] == "radius-accounting"
            interval = state[ACCOUNTING_INTERVAL]
            expected_interval = f"{ACCOUNTING_INTERVAL} {op.get('interval_minutes')}"
            ok = enabled == (v == "enabled") and (not enabled or interval in (expected_interval, expected_interval + " 0"))
            return ok, {"enabled": enabled, "interval": interval}
        if k=="ap.radio.channel" and op.get("arm_managed") is True:
            got=(self._radio_overrides() or {}).get(op.get("band","5"))
            ok=got is not None and float(got.get("channel",0))==0 and float(got.get("power",0))==0
            return ok,{"overrides":got,"native_arm":True}
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
        if k == "ap.radio.width" and arm.get("80mhz-support"):
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
