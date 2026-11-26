"""Read-only printer collection engine (PRN-011–030).

``SnmpReader`` is the only path to a device and exposes GET and bounded GETNEXT walks. There is no SET.
The production reader uses pysnmp; ``EmulatedPrinterAgent`` serves an in-memory MIB so the full
pipeline (poll → samples → intervals → supplies → faults → health) can be exercised in isolated tests
and demos without hardware. Results label the source and never fabricate values for missing OIDs.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from . import mib

MAX_WALK_ROWS = 256          # bounded per table
MAX_DIAGNOSTIC_ROWS = 2000   # bounded GUI diagnostic walk


class SnmpReader(Protocol):
    source: str

    async def get(self, oid: str) -> Any | None: ...

    async def walk(self, base: str, max_rows: int = MAX_WALK_ROWS) -> list[tuple[str, Any]]: ...

    async def close(self) -> None: ...


class SnmpUnreachable(Exception):
    """No response at all: host down / filtered / wrong credentials (SNMPv3 auth failures look the same)."""


class SnmpTruncated(Exception):
    """A table walk stopped on a transport error after returning some rows. The rows are real readings and
    are kept, but the table is INCOMPLETE: absence of a row must not be read as cleared or removed."""

    def __init__(self, rows: list):
        super().__init__("table walk truncated")
        self.rows = rows


# ---- production reader ---------------------------------------------------------------------------
class PysnmpReader:
    """pysnmp-backed read-only reader. Credentials live only in this object for one poll."""

    def __init__(self, host: str, version: str, identity: str, auth_key: str = "", privacy_key: str = "",
                 timeout: float = 3.0, retries: int = 1, *, auth_protocol: str = "SHA256",
                 priv_protocol: str = "AES128"):
        from .snmp import permitted_target
        if not permitted_target(host):
            raise ValueError("Target outside authorized read-only management ranges")
        self.host, self.version, self.timeout, self.retries = host, version, timeout, retries
        self._identity, self._auth, self._priv = identity, auth_key, privacy_key
        self._protocols = {"auth_protocol": auth_protocol, "priv_protocol": priv_protocol}
        self.source = "SNMPV" + version.upper()
        self._engine = None
        self._transport = None
        self._auth_data = None

    async def _ensure(self):
        if self._engine is not None:
            return
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine, UdpTransportTarget
        from .snmp import build_auth_data
        self._auth_data = build_auth_data(self.version, self._identity, self._auth, self._priv, **self._protocols)
        self._engine = SnmpEngine()
        self._transport = await UdpTransportTarget.create((self.host, 161), timeout=self.timeout, retries=self.retries)

    @staticmethod
    def _py(value):
        name = value.__class__.__name__
        if "NoSuch" in name or "EndOfMib" in name:
            return None
        if name in ("Integer", "Integer32", "Counter32", "Counter64", "Gauge32", "Unsigned32", "TimeTicks"):
            return int(value)
        if name == "OctetString":
            raw = bytes(value)
            try:
                text = raw.decode("utf-8")
                return text if text.isprintable() else raw
            except UnicodeDecodeError:
                return raw
        if name == "ObjectIdentifier":
            return value.prettyPrint()
        return value.prettyPrint()

    async def get(self, oid: str):
        from pysnmp.hlapi.v3arch.asyncio import ContextData, ObjectIdentity, ObjectType, get_cmd
        await self._ensure()
        err, status, _, binds = await get_cmd(self._engine, self._auth_data, self._transport, ContextData(),
                                              ObjectType(ObjectIdentity(oid)), lookupMib=False)
        if err:
            if "timeout" in str(err).lower() or "No SNMP response" in str(err):
                raise SnmpUnreachable(str(type(err).__name__))
            return None
        if status or not binds:
            return None
        return self._py(binds[0][1])

    async def walk(self, base: str, max_rows: int = MAX_WALK_ROWS):
        from pysnmp.hlapi.v3arch.asyncio import ContextData, ObjectIdentity, ObjectType, next_cmd
        await self._ensure()
        rows: list[tuple[str, Any]] = []
        current = base
        while len(rows) < max_rows:
            err, status, _, binds = await next_cmd(self._engine, self._auth_data, self._transport, ContextData(),
                                                   ObjectType(ObjectIdentity(current)), lookupMib=False)
            if err:
                if not rows and ("timeout" in str(err).lower() or "No SNMP response" in str(err)):
                    raise SnmpUnreachable(type(err).__name__)
                if rows:
                    raise SnmpTruncated(rows)
                break
            if status or not binds:
                break
            name, value = binds[0]
            oid = name.prettyPrint()
            if not oid.startswith(base + ".") or oid == current:
                break
            v = self._py(value)
            if v is None:
                break
            rows.append((oid, v))
            current = oid
        return rows

    async def close(self):
        if self._engine is not None:
            self._engine.close_dispatcher()
            self._engine = None


# ---- emulator ------------------------------------------------------------------------------------
def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(p) for p in oid.strip(".").split("."))


class EmulatedPrinterAgent:
    """In-memory SNMP agent for isolated tests / demos. Source is labelled EMULATED, never SNMPV*."""

    source = "EMULATED"

    def __init__(self, values: dict[str, Any] | None = None, *, reachable: bool = True):
        self.values = dict(values or {})
        self.reachable = reachable
        # Test hooks: OIDs whose GET/walk time out, and table bases whose walk stops after N rows.
        self.timeout_oids: set[str] = set()
        self.truncate_tables: dict[str, int] = {}
        self.requests: list[tuple[str, str]] = []   # (op, oid) — proves only GET/GETNEXT are used

    async def get(self, oid: str):
        self.requests.append(("GET", oid))
        if not self.reachable or oid in self.timeout_oids:
            raise SnmpUnreachable("emulated timeout")
        return self.values.get(oid)

    async def walk(self, base: str, max_rows: int = MAX_WALK_ROWS):
        self.requests.append(("GETNEXT", base))
        if not self.reachable or base in self.timeout_oids:
            raise SnmpUnreachable("emulated timeout")
        prefix = _oid_key(base)
        rows = sorted(((k, v) for k, v in self.values.items()
                       if _oid_key(k)[:len(prefix)] == prefix and len(_oid_key(k)) > len(prefix)),
                      key=lambda kv: _oid_key(kv[0]))
        if base in self.truncate_tables and rows:
            raise SnmpTruncated(rows[:self.truncate_tables[base]])
        return rows[:max_rows]

    async def close(self):
        return None


def emulated_printer(*, manufacturer: str = "Kyocera", model: str = "ECOSYS MA4000cix", serial: str = "EMU0001",
                     impressions: int = 10000, bw: int | None = None, colour: int | None = None,
                     supplies: list[dict] | None = None, printer_status: int = 3, device_status: int = 2,
                     error_bytes: bytes = b"\x00\x00", alerts: list[dict] | None = None,
                     counter_unit: int = 7, hostname: str = "prn-emu-01") -> dict[str, Any]:
    """Build an emulated MIB. Unset OIDs stay absent (= unsupported), exactly like real devices."""
    enterprise = {v: k for k, v in mib.ENTERPRISES.items()}.get(manufacturer, 99999)
    v: dict[str, Any] = {
        mib.SYS_DESCR: f"{manufacturer} {model}", mib.SYS_OBJECT_ID: f"1.3.6.1.4.1.{enterprise}.1.1",
        mib.SYS_NAME: hostname, mib.SYS_UPTIME: 123456,
        mib.HR_DEVICE_DESCR + ".1": f"{manufacturer} {model}", mib.HR_DEVICE_STATUS + ".1": device_status,
        mib.HR_PRINTER_STATUS + ".1": printer_status, mib.HR_PRINTER_ERROR_STATE + ".1": error_bytes,
        mib.PRT_SERIAL + ".1": serial, mib.PRT_MARKER_LIFE + ".1.1": impressions,
        mib.PRT_MARKER_UNIT + ".1.1": counter_unit,
        mib.PRT_MARKER_COLORANTS + ".1.1": 4 if colour is not None or "c" in model.lower()[-4:] else 1,
        mib.IF_PHYS_ADDRESS + ".1": b"\x00\x17\xc8\x01\x02\x03",
    }
    if manufacturer == "Kyocera":
        v["1.3.6.1.4.1.1347.43.10.1.1.12.1.1"] = impressions
        if bw is not None:
            v["1.3.6.1.4.1.1347.42.3.1.2.1.1.1.1"] = bw
        if colour is not None:
            v["1.3.6.1.4.1.1347.42.3.1.2.1.1.1.3"] = colour
    for i, s in enumerate(supplies or [], start=1):
        v[mib.PRT_SUPPLY_TYPE + f".1.{i}"] = s.get("type", 3)
        v[mib.PRT_SUPPLY_CLASS + f".1.{i}"] = s.get("class", 3)
        v[mib.PRT_SUPPLY_DESCR + f".1.{i}"] = s.get("descr", "Toner")
        v[mib.PRT_SUPPLY_UNIT + f".1.{i}"] = s.get("unit", 19)
        v[mib.PRT_SUPPLY_MAX + f".1.{i}"] = s.get("max", 100)
        v[mib.PRT_SUPPLY_LEVEL + f".1.{i}"] = s.get("level", 80)
    for i, a in enumerate(alerts or [], start=1):
        v[mib.PRT_ALERT_CODE + f".1.{i}"] = a["code"]
        v[mib.PRT_ALERT_SEVERITY + f".1.{i}"] = a.get("severity", 4)
        v[mib.PRT_ALERT_DESCR + f".1.{i}"] = a.get("descr", "")
    return v


# ---- poll result -------------------------------------------------------------------------------
@dataclass
class Sample:
    metric_key: str
    oid: str | None
    instance: str = ""
    raw_value: str | None = None
    normalized_value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    value_state: str = "VALID"
    interpretation: str | None = None
    verification: str = "STANDARD"


@dataclass
class PollResult:
    observed_at: datetime
    source: str
    reachable: bool
    snmp_ok: bool
    error: str | None = None
    identity: dict = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    meters: dict[str, dict] = field(default_factory=dict)       # meter_kind -> {value, verification, oid}
    supplies: list[dict] = field(default_factory=list)
    operational_state: str = "UNKNOWN"
    device_status: str | None = None
    faults: list[dict] = field(default_factory=list)
    faults_observed: bool = False
    trays: list[dict] = field(default_factory=list)
    capabilities: dict[str, dict] = field(default_factory=dict)
    unsupported_oids: list[str] = field(default_factory=list)
    incomplete_metrics: list[str] = field(default_factory=list)
    profile_key: str | None = None
    profile_version: int | None = None


_SUPPLY = {"supply_level", "supply_max", "supply_type", "supply_class", "supply_descr", "supply_unit",
           "supply_colorant", "colorant_value"}
COMPANIONS = {"marker_life_count": {"marker_counter_unit"}, "supply_level": _SUPPLY, "supply_max": _SUPPLY,
              "alert_code": {"alert_severity", "alert_descr"}, "input_level": {"input_max", "input_name"},
              "printer_status": {"device_status", "error_state"}}

FAULT_ALIASES = {"MARKER_TONER_ALMOST_EMPTY": "LOW_TONER", "MARKER_TONER_EMPTY": "NO_TONER",
                 "MARKER_INK_ALMOST_EMPTY": "LOW_TONER", "MARKER_INK_EMPTY": "NO_TONER", "JAM": "JAMMED",
                 "COVER_OPEN": "DOOR_OPEN", "INTERLOCK_OPEN": "DOOR_OPEN", "INPUT_MEDIA_SUPPLY_EMPTY": "NO_PAPER",
                 "INPUT_MEDIA_SUPPLY_LOW": "LOW_PAPER", "OUTPUT_MEDIA_TRAY_FULL": "OUTPUT_FULL",
                 "OUTPUT_MEDIA_TRAY_ALMOST_FULL": "OUTPUT_NEAR_FULL", "INPUT_MEDIA_TRAY_MISSING": "INPUT_TRAY_MISSING",
                 "MARKER_TONER_CARTRIDGE_MISSING": "MARKER_SUPPLY_MISSING"}


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        try:
            t = bytes(v).decode("utf-8").strip("\x00 ").strip()
            return t if t.isprintable() else bytes(v).hex()
        except UnicodeDecodeError:
            return bytes(v).hex()
    return str(v).strip()


def _int(v: Any) -> int | None:
    if v is None or isinstance(v, (bytes, bytearray)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _suffix(oid: str, base: str) -> str:
    return oid[len(base) + 1:] if oid.startswith(base + ".") else oid


def select_profile(profiles: list[dict], sys_object_id: str | None, sys_descr: str | None) -> dict:
    """Pick the most specific enabled profile; fall back to generic (PRN-006/009)."""
    ent = mib.enterprise_number(sys_object_id)
    candidates = []
    for p in profiles:
        if not p.get("enabled", True):
            continue
        ok = True
        if p.get("match_enterprise") is not None:
            ok = ok and ent == p["match_enterprise"]
        if p.get("match_descr_regex"):
            try:
                ok = ok and bool(re.search(p["match_descr_regex"], sys_descr or "", re.I))
            except re.error:
                ok = False
        if ok:
            candidates.append(p)
    candidates.sort(key=lambda p: (p.get("priority", 100), p["key"]))
    return candidates[0] if candidates else next(p for p in profiles if p["key"] == "rfc3805-generic")


async def poll(reader: SnmpReader, profiles: list[dict], *, now: datetime | None = None,
               metric_filter: set[str] | None = None, metric_budget_seconds: float = 45) -> PollResult:
    """One read-only poll. ``profiles`` are dicts (see mib.BUILTIN_PROFILES) with ``metrics`` lists."""
    now = now or datetime.now(timezone.utc)
    res = PollResult(observed_at=now, source=reader.source, reachable=False, snmp_ok=False)
    try:
        descr = await reader.get(mib.SYS_DESCR)
        obj = await reader.get(mib.SYS_OBJECT_ID)
    except SnmpUnreachable as exc:
        res.error = f"no SNMP response ({exc})"[:255]
        return res
    except (OSError, asyncio.TimeoutError) as exc:
        res.error = f"transport error ({type(exc).__name__})"
        return res
    res.reachable = True
    if descr is None and obj is None:
        res.error = "SNMP responded without system MIB (authentication/view restriction or not a printer)"
        return res
    res.snmp_ok = True
    profile = select_profile(profiles, _text(obj), _text(descr))
    res.profile_key, res.profile_version = profile["key"], profile.get("version", 1)

    if metric_filter is not None:   # a due metric always brings the companions needed to interpret it
        metric_filter = set(metric_filter)
        for key in list(metric_filter):
            metric_filter |= COMPANIONS.get(key, set())
    values: dict[str, list[tuple[str, Any]]] = {}
    verification: dict[str, str] = {}
    deadline = asyncio.get_running_loop().time() + metric_budget_seconds
    completed = set()
    for d in profile["metrics"]:
        if metric_filter is not None and d["metric_key"] not in metric_filter and d["category"] != "identity":
            continue
        verification[d["metric_key"]] = d.get("verification", "STANDARD")
        try:
            async with asyncio.timeout_at(deadline):
                if d["access"] == "table":
                    rows = await reader.walk(d["oid"])
                else:
                    v = await reader.get(d["oid"])
                    rows = [(d["oid"], v)] if v is not None else []
        except TimeoutError:
            res.error = "poll time budget reached; saved available metrics"
            break
        except SnmpUnreachable:
            res.error = "device stopped responding during poll"
            res.samples.append(Sample(d["metric_key"], d["oid"], value_state="MISSING",
                                      interpretation="no response"))
            continue
        except SnmpTruncated as exc:
            # Keep rows that were read; leave the metric out of `completed` so downstream logic
            # (fault clearing, supply removal) treats the table as incomplete rather than authoritative.
            res.error = "table walk interrupted; partial rows kept"
            rows = exc.rows
            res.incomplete_metrics.append(d["metric_key"])
        else:
            completed.add(d["metric_key"])
        if not rows:
            res.unsupported_oids.append(d["oid"])
            res.samples.append(Sample(d["metric_key"], d["oid"], value_state="UNSUPPORTED",
                                      interpretation="noSuchObject/noSuchInstance or empty table",
                                      verification=d.get("verification", "STANDARD")))
            continue
        values[d["metric_key"]] = rows
        for oid, v in rows:
            inst = _suffix(oid, d["oid"]) if d["access"] == "table" else ""
            if d["value_type"] == "text":
                res.samples.append(Sample(d["metric_key"], oid, inst, raw_value=_text(v)[:255] if _text(v) else None,
                                          text_value=(_text(v) or "")[:255], verification=d.get("verification", "STANDARD")))
            else:
                iv = _int(v)
                state, val = mib.decode_special(iv) if d["value_type"] in ("gauge",) else (
                    ("VALID", iv) if iv is not None and iv >= 0 else ("INVALID", None))
                res.samples.append(Sample(d["metric_key"], oid, inst, raw_value=str(v)[:255],
                                          normalized_value=None if val is None else val * d.get("scale", 1.0),
                                          unit=d.get("unit"), value_state="VALID" if state == "VALID" else (
                                              "UNKNOWN_SPECIAL" if state in ("OTHER", "UNKNOWN", "SOME_REMAINING") else state),
                                          interpretation=None if state == "VALID" else f"Printer-MIB special value {state}",
                                          verification=d.get("verification", "STANDARD")))

    first = lambda key: _text(values[key][0][1]) if values.get(key) else None  # noqa: E731
    descr_t, obj_t = _text(descr), _text(obj)
    device_descr = first("device_descr")
    serial = first("serial_number")
    if serial and (len(serial) > 100 or serial.casefold() in {"unknown", "n/a", "none", ""}):
        serial = None
    model_info = mib.model_from_description(device_descr or descr_t)
    mac = values.get("mac_address")
    res.identity = {
        "manufacturer": mib.manufacturer_from_object_id(obj_t), "system_object_id": obj_t,
        "system_description": descr_t, "hostname": first("sys_name"), "serial_number": serial,
        "reported_model": device_descr or descr_t, "model": model_info["model"],
        "model_confidence": model_info["confidence"], "model_reason": model_info["reason"],
        "mac_address": _text(mac[0][1]) if mac else None, "firmware": None,
    }
    fw = re.search(r"(?:firmware|fw|version|ver\.?)\s*[:=]?\s*([A-Za-z0-9._-]{3,40})", descr_t or "", re.I)
    if fw:
        res.identity["firmware"] = fw.group(1)

    # ---- meters: prtMarkerLifeCount interpreted only with its unit
    units = {_suffix(o, mib.PRT_MARKER_UNIT): _int(v) for o, v in values.get("marker_counter_unit", [])}
    for oid, v in values.get("marker_life_count", []):
        inst = _suffix(oid, mib.PRT_MARKER_LIFE)
        unit = mib.MARKER_COUNTER_UNIT.get(units.get(inst) or 0)
        iv = _int(v)
        if unit in ("impressions", "sheets") and iv is not None and iv >= 0:
            kind = "impressions_total" if unit == "impressions" else "sheets_total"
            res.meters.setdefault(kind, {"value": iv, "verification": "STANDARD", "oid": oid})
    for key in ("impressions_total", "bw_impressions", "colour_impressions", "copy_impressions", "scan_count"):
        rows = values.get(key)
        if rows and _int(rows[0][1]) is not None and _int(rows[0][1]) >= 0:
            # A STANDARD Printer-MIB value takes precedence; a vendor value is kept under its own provenance.
            if key not in res.meters:
                res.meters[key] = {"value": _int(rows[0][1]), "verification": verification.get(key, "STANDARD"),
                                   "oid": rows[0][0]}

    # ---- supplies
    def table(key, base):
        return {_suffix(o, base): v for o, v in values.get(key, [])}
    levels, maxes = table("supply_level", mib.PRT_SUPPLY_LEVEL), table("supply_max", mib.PRT_SUPPLY_MAX)
    types, classes = table("supply_type", mib.PRT_SUPPLY_TYPE), table("supply_class", mib.PRT_SUPPLY_CLASS)
    descrs, sunits = table("supply_descr", mib.PRT_SUPPLY_DESCR), table("supply_unit", mib.PRT_SUPPLY_UNIT)
    colorant_idx = table("supply_colorant", mib.PRT_SUPPLY_COLORANT_INDEX)
    colorants = table("colorant_value", mib.PRT_COLORANT_VALUE)
    for idx in sorted(set(levels) | set(types) | set(descrs), key=lambda s: _oid_key(s) if s else ()):
        stype = mib.SUPPLY_TYPE.get(_int(types.get(idx)) or 2, "unknown")
        receptacle = mib.SUPPLY_CLASS.get(_int(classes.get(idx)) or 1) == "receptacle_filled"
        level_raw, max_raw = _int(levels.get(idx)), _int(maxes.get(idx))
        lstate, pct = mib.supply_percent(level_raw, max_raw)
        dtext = _text(descrs.get(idx))
        cidx = _int(colorant_idx.get(idx))
        cname = None
        if cidx:
            cname = next((_text(v) for k, v in colorants.items() if k.split(".")[-1] == str(cidx)), None)
        part = None
        pm = re.search(r"\b(TK-\d{3,5}[A-Z]{0,2}|CF\d{3}[A-Z]?|W\d{4}[A-Z]?|10[1-9]R\d{5}|006R\d{5}|T\d{3,4}[A-Z0-9]{0,3})\b",
                       dtext or "")
        if pm:
            part = pm.group(1)
        res.supplies.append({
            "supply_index": idx, "supply_type": stype, "receptacle": receptacle,
            "colour": mib.classify_colour(dtext, cname) if stype not in ("waste_toner", "waste_ink", "fuser",
                                                                         "maintenance_kit", "transfer_unit") else "n/a",
            "description": (dtext or "")[:255] or None, "part_number": part,
            "unit": mib.SUPPLY_UNIT.get(_int(sunits.get(idx)) or 2), "max_capacity": max_raw if max_raw is not None and max_raw >= 0 else None,
            "level": level_raw if level_raw is not None and level_raw >= 0 else None, "level_state": lstate, "percent_remaining": pct,
            "status": mib.supply_status(lstate if lstate in ("VALID", "SOME_REMAINING") else "UNKNOWN", pct,
                                        receptacle=receptacle),
        })

    # ---- status, faults, trays
    pst = values.get("printer_status")
    dst = values.get("device_status")
    res.operational_state = mib.HR_PRINTER_STATUS_MAP.get(_int(pst[0][1]) or 2, "unknown").upper() if pst else "UNKNOWN"
    res.device_status = mib.HR_DEVICE_STATUS_MAP.get(_int(dst[0][1]) or 1, "unknown").upper() if dst else None
    err = values.get("error_state")
    faults = mib.decode_error_state(err[0][1]) if err else []
    acodes = table("alert_code", mib.PRT_ALERT_CODE)
    asev = table("alert_severity", mib.PRT_ALERT_SEVERITY)
    adesc = table("alert_descr", mib.PRT_ALERT_DESCR)
    for idx, code in acodes.items():
        a = mib.decode_alert(_int(code), _int(asev.get(idx)), _text(adesc.get(idx)))
        if a["severity"] != "info":
            faults.append(a)
    seen, merged = set(), []
    for f in faults:   # de-duplicate the same condition reported by both the bitmask and the alert table
        key = (f["category"], FAULT_ALIASES.get(f["code"], f["code"]))
        if key not in seen:
            seen.add(key)
            merged.append(f)
    res.faults = merged
    fault_metrics = {d["metric_key"] for d in profile["metrics"] if d["metric_key"] in {"error_state", "alert_code"}}
    res.faults_observed = bool(fault_metrics) and fault_metrics <= completed
    # A missing scalar cannot establish that the error-state bitmask is clear.
    if "error_state" in fault_metrics and not err:
        res.faults_observed = False
    if res.device_status == "DOWN" or any(f["severity"] == "critical" for f in merged):
        res.operational_state = "ERROR" if res.operational_state in ("UNKNOWN", "OTHER", "IDLE") else res.operational_state
    ilev, imax, iname = (table("input_level", mib.PRT_INPUT_LEVEL), table("input_max", mib.PRT_INPUT_MAX),
                         table("input_name", mib.PRT_INPUT_NAME))
    for idx in sorted(ilev):
        state, lvl = mib.decode_special(_int(ilev[idx]))
        res.trays.append({"index": idx, "name": _text(iname.get(idx)), "level": lvl, "level_state": state,
                          "max": _int(imax.get(idx))})

    res.capabilities = capability_matrix(res, values, verification)
    return res


def capability_matrix(res: PollResult, values: dict, verification: dict[str, str]) -> dict[str, dict]:
    """Per-device capability state from THIS poll's evidence (SUPPORTED / UNSUPPORTED / UNVERIFIED /
    TEMPORARILY_UNAVAILABLE / INVALID). Vendor-only evidence stays UNVERIFIED."""
    caps: dict[str, dict] = {}

    def put(name, state, evidence):
        caps[name] = {"state": state, "evidence": evidence[:255]}

    def meter_cap(name, key):
        m = res.meters.get(key)
        if m is None:
            put(name, "UNSUPPORTED", f"{key} not reported by the device or profile")
        elif m["verification"] == "STANDARD":
            put(name, "SUPPORTED", f"{key} via standard MIB {m['oid']}")
        else:
            put(name, "UNVERIFIED", f"{key} via vendor OID {m['oid']} (mapping not yet hardware-verified)")

    meter_cap("impressions", "impressions_total")
    meter_cap("sheets", "sheets_total")
    meter_cap("mono_counter", "bw_impressions")
    meter_cap("colour_counter", "colour_impressions")
    meter_cap("copy_counter", "copy_impressions")
    meter_cap("scan_counter", "scan_count")
    tot, bw, col = (res.meters.get(k, {}).get("value") for k in ("impressions_total", "bw_impressions", "colour_impressions"))
    if tot is not None and bw is not None and col is not None and bw + col > tot * 1.02 + 10:
        put("mono_colour_split", "INVALID", f"mono {bw} + colour {col} exceeds total {tot}")
    elif bw is not None and col is not None:
        put("mono_colour_split", "UNVERIFIED" if "UNVERIFIED" in (caps["mono_counter"]["state"], caps["colour_counter"]["state"])
            else "SUPPORTED", "separate mono and colour counters reported")
    else:
        put("mono_colour_split", "UNSUPPORTED", "no separate mono/colour counters")
    colorants = values.get("marker_colorants")
    if colorants:
        n = _int(colorants[0][1])
        put("colour_printing", "SUPPORTED" if (n or 0) > 1 else "UNSUPPORTED", f"prtMarkerProcessColorants={n}")
    else:
        put("colour_printing", "UNVERIFIED", "prtMarkerProcessColorants not reported")
    paths = [_int(v) for _, v in values.get("media_path_type", [])]
    if paths:
        put("duplex", "SUPPORTED" if any(p in (3, 4, 5) for p in paths if p) else "UNSUPPORTED",
            f"prtMediaPathType values {sorted(set(p for p in paths if p))}")
    else:
        put("duplex", "UNVERIFIED", "prtMediaPathTable not reported")
    sup = res.supplies
    if sup:
        valid = [s for s in sup if s["level_state"] in ("VALID", "SOME_REMAINING")]
        put("supply_levels", "SUPPORTED" if valid else "TEMPORARILY_UNAVAILABLE" if any(
            s["level_state"] == "UNKNOWN" for s in sup) else "INVALID", f"{len(valid)}/{len(sup)} supply rows with usable level")
        put("supply_identifiers", "SUPPORTED" if any(s["part_number"] for s in sup) else "UNVERIFIED",
            "part numbers parsed from prtMarkerSuppliesDescription" if any(s["part_number"] for s in sup)
            else "descriptions do not include a recognised part number")
        for comp, types in (("drum", ("drum",)), ("fuser", ("fuser",)), ("waste_toner", ("waste_toner", "waste_ink")),
                            ("maintenance_kit", ("transfer_unit", "cleaner_unit", "developer"))):
            present = [s for s in sup if s["supply_type"] in types]
            put(comp, "SUPPORTED" if present else "UNSUPPORTED",
                f"{len(present)} {comp} row(s) in prtMarkerSuppliesTable")
    else:
        for comp in ("supply_levels", "supply_identifiers", "drum", "fuser", "waste_toner", "maintenance_kit"):
            put(comp, "UNSUPPORTED", "prtMarkerSuppliesTable not reported")
    put("trays", "SUPPORTED" if res.trays else "UNSUPPORTED", f"{len(res.trays)} input tray row(s)")
    put("faults", "SUPPORTED" if values.get("error_state") or values.get("alert_code") else "UNSUPPORTED",
        "hrPrinterDetectedErrorState/prtAlertTable")
    put("firmware", "SUPPORTED" if res.identity.get("firmware") else "UNVERIFIED",
        "parsed from sysDescr" if res.identity.get("firmware") else "firmware string not found in sysDescr")
    put("network", "SUPPORTED" if res.identity.get("mac_address") else "UNSUPPORTED", "ifPhysAddress")
    multifunction = bool(values.get("scan_count") or values.get("copy_impressions"))
    put("copy_scan", "UNVERIFIED" if multifunction else "UNSUPPORTED",
        "vendor copy/scan counters present" if multifunction else "no copy/scan counters reported")
    return caps


async def diagnostic_walk(reader: SnmpReader, root: str, max_rows: int = MAX_DIAGNOSTIC_ROWS) -> list[dict]:
    """Bounded read-only walk for the GUI (Raw SNMP Data tab). Values are rendered as text."""
    if root not in mib.DIAGNOSTIC_ROOTS.values():
        raise ValueError("Diagnostic walks are limited to approved read-only MIB roots")
    rows = await reader.walk(root, max_rows=max_rows)
    return [{"oid": o, "value": (_text(v) or "")[:255]} for o, v in rows]
