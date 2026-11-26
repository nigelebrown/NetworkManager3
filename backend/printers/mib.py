"""Read-only printer MIB knowledge (RFC 1213 system, RFC 2790 Host Resources, RFC 3805 Printer-MIB).

Pure data and decoders: no network access. Vendor-private OIDs are included only where publicly
documented and are labelled ``VENDOR_UNVERIFIED`` until a hardware acceptance run verifies them for
that model. A vendor mapping is never assumed from an unrelated model, and no write OID is defined.
"""
from __future__ import annotations

import re
from typing import Any

# ---- identity / system --------------------------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"
HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3"
HR_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5"
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1"
HR_PRINTER_ERROR_STATE = "1.3.6.1.2.1.25.3.5.1.2"
PRT_SERIAL = "1.3.6.1.2.1.43.5.1.1.17"
PRT_PRINTER_NAME = "1.3.6.1.2.1.43.5.1.1.16"
PRT_MARKER_LIFE = "1.3.6.1.2.1.43.10.2.1.4"
PRT_MARKER_UNIT = "1.3.6.1.2.1.43.10.2.1.3"
PRT_MARKER_COLORANTS = "1.3.6.1.2.1.43.10.2.1.6"
PRT_SUPPLY_COLORANT_INDEX = "1.3.6.1.2.1.43.11.1.1.3"
PRT_SUPPLY_CLASS = "1.3.6.1.2.1.43.11.1.1.4"
PRT_SUPPLY_TYPE = "1.3.6.1.2.1.43.11.1.1.5"
PRT_SUPPLY_DESCR = "1.3.6.1.2.1.43.11.1.1.6"
PRT_SUPPLY_UNIT = "1.3.6.1.2.1.43.11.1.1.7"
PRT_SUPPLY_MAX = "1.3.6.1.2.1.43.11.1.1.8"
PRT_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"
PRT_COLORANT_VALUE = "1.3.6.1.2.1.43.12.1.1.4"
PRT_INPUT_MAX = "1.3.6.1.2.1.43.8.2.1.9"
PRT_INPUT_LEVEL = "1.3.6.1.2.1.43.8.2.1.10"
PRT_INPUT_NAME = "1.3.6.1.2.1.43.8.2.1.13"
PRT_DUPLEX_MEDIA_PATH = "1.3.6.1.2.1.43.13.4.1.9"     # prtMediaPathType (2 = longEdgeBindingDuplex ...)
PRT_ALERT_SEVERITY = "1.3.6.1.2.1.43.18.1.1.2"
PRT_ALERT_GROUP = "1.3.6.1.2.1.43.18.1.1.4"
PRT_ALERT_CODE = "1.3.6.1.2.1.43.18.1.1.7"
PRT_ALERT_DESCR = "1.3.6.1.2.1.43.18.1.1.8"
IF_PHYS_ADDRESS = "1.3.6.1.2.1.2.2.1.6"

# Bounded GUI diagnostic walk roots (read-only GETNEXT; never SET).
DIAGNOSTIC_ROOTS = {
    "system": "1.3.6.1.2.1.1",
    "host-resources-printer": "1.3.6.1.2.1.25.3",
    "printer-mib": "1.3.6.1.2.1.43",
}

# sysObjectID enterprise numbers → manufacturer (IANA private enterprise numbers).
ENTERPRISES = {
    11: "HP", 1347: "Kyocera", 253: "Xerox", 1248: "Epson", 1602: "Canon", 2435: "Brother",
    641: "Lexmark", 367: "Ricoh", 2385: "Sharp", 18334: "Konica Minolta", 1129: "Toshiba",
    236: "Samsung", 2001: "OKI",
}

# ---- enumerations --------------------------------------------------------------------------------
MARKER_COUNTER_UNIT = {3: "tenThousandthsOfInches", 4: "micrometers", 5: "characters", 6: "lines",
                       7: "impressions", 8: "sheets", 9: "dotRow", 11: "hours", 16: "feet", 17: "meters"}
SUPPLY_UNIT = {1: "other", 2: "unknown", 3: "tenThousandthsOfInches", 4: "micrometers", 7: "impressions",
               8: "sheets", 11: "hours", 12: "thousandthsOfOunces", 13: "tenthsOfGrams",
               14: "hundrethsOfFluidOunces", 15: "tenthsOfMilliliters", 16: "feet", 17: "meters",
               18: "items", 19: "percent"}
SUPPLY_TYPE = {1: "other", 2: "unknown", 3: "toner", 4: "waste_toner", 5: "ink", 6: "ink_cartridge",
               7: "ink_ribbon", 8: "waste_ink", 9: "drum", 10: "developer", 11: "fuser_oil", 12: "solid_wax",
               13: "ribbon_wax", 14: "waste_wax", 15: "fuser", 16: "corona_wire", 17: "fuser_oil_wick",
               18: "cleaner_unit", 19: "fuser_cleaning_pad", 20: "transfer_unit", 21: "toner_cartridge",
               22: "fuser_oiler", 23: "water", 24: "waste_water", 25: "glue_water_additive",
               26: "waste_paper", 27: "binding_supply", 28: "banding_supply", 29: "stitching_wire",
               30: "shrink_wrap", 31: "paper_wrap", 32: "staples", 33: "inserts", 34: "covers"}
SUPPLY_CLASS = {1: "other", 3: "consumed", 4: "receptacle_filled"}
HR_DEVICE_STATUS_MAP = {1: "unknown", 2: "running", 3: "warning", 4: "testing", 5: "down"}
HR_PRINTER_STATUS_MAP = {1: "other", 2: "unknown", 3: "idle", 4: "printing", 5: "warmup"}
ALERT_SEVERITY = {1: "other", 3: "critical", 4: "warning", 5: "warning"}
ALERT_CODE = {  # prtAlertCode (subset relevant for operations)
    3: ("COVER_OPEN", "DOOR", "warning"), 4: ("COVER_CLOSED", "DOOR", "info"),
    5: ("INTERLOCK_OPEN", "DOOR", "warning"), 8: ("CONFIGURATION_CHANGE", "OTHER", "info"),
    9: ("JAM", "PAPER", "critical"), 501: ("DOOR_OPEN", "DOOR", "warning"),
    801: ("INPUT_MEDIA_TRAY_MISSING", "PAPER", "warning"), 805: ("INPUT_MEDIA_SUPPLY_LOW", "PAPER", "warning"),
    806: ("INPUT_MEDIA_SUPPLY_EMPTY", "PAPER", "critical"), 807: ("OUTPUT_MEDIA_TRAY_MISSING", "PAPER", "warning"),
    808: ("OUTPUT_MEDIA_TRAY_ALMOST_FULL", "PAPER", "warning"), 809: ("OUTPUT_MEDIA_TRAY_FULL", "PAPER", "critical"),
    1001: ("MARKER_FUSER_UNDER_TEMPERATURE", "HARDWARE", "warning"),
    1002: ("MARKER_FUSER_OVER_TEMPERATURE", "HARDWARE", "critical"),
    1101: ("MARKER_TONER_EMPTY", "SUPPLY", "critical"), 1104: ("MARKER_TONER_ALMOST_EMPTY", "SUPPLY", "warning"),
    1102: ("MARKER_INK_EMPTY", "SUPPLY", "critical"), 1105: ("MARKER_INK_ALMOST_EMPTY", "SUPPLY", "warning"),
    1107: ("MARKER_WASTE_TONER_RECEPTACLE_ALMOST_FULL", "SUPPLY", "warning"),
    1108: ("MARKER_WASTE_INK_RECEPTACLE_ALMOST_FULL", "SUPPLY", "warning"),
    1109: ("MARKER_WASTE_TONER_RECEPTACLE_FULL", "SUPPLY", "critical"),
    1110: ("MARKER_WASTE_INK_RECEPTACLE_FULL", "SUPPLY", "critical"),
    1111: ("MARKER_OPC_LIFE_ALMOST_OVER", "SUPPLY", "warning"), 1112: ("MARKER_OPC_LIFE_OVER", "SUPPLY", "critical"),
    1113: ("MARKER_DEVELOPER_ALMOST_EMPTY", "SUPPLY", "warning"), 1114: ("MARKER_DEVELOPER_EMPTY", "SUPPLY", "critical"),
    1115: ("MARKER_TONER_CARTRIDGE_MISSING", "SUPPLY", "critical"),
    1301: ("MEDIA_PATH_MEDIA_TRAY_MISSING", "PAPER", "warning"),
    1302: ("MEDIA_PATH_MEDIA_TRAY_ALMOST_FULL", "PAPER", "warning"),
    1303: ("MEDIA_PATH_MEDIA_TRAY_FULL", "PAPER", "critical"),
    1501: ("INTERPRETER_MEMORY_INCREASE", "OTHER", "info"),
    1502: ("INTERPRETER_MEMORY_DECREASE", "OTHER", "info"),
}

# hrPrinterDetectedErrorState bit map (RFC 2790 / RFC 3805 extension). Byte, mask → fault.
ERROR_STATE_BITS = [
    (0, 0x80, "LOW_PAPER", "PAPER", "warning"), (0, 0x40, "NO_PAPER", "PAPER", "critical"),
    (0, 0x20, "LOW_TONER", "SUPPLY", "warning"), (0, 0x10, "NO_TONER", "SUPPLY", "critical"),
    (0, 0x08, "DOOR_OPEN", "DOOR", "warning"), (0, 0x04, "JAMMED", "PAPER", "critical"),
    (0, 0x02, "OFFLINE", "OTHER", "warning"), (0, 0x01, "SERVICE_REQUESTED", "SERVICE", "critical"),
    (1, 0x80, "INPUT_TRAY_MISSING", "PAPER", "warning"), (1, 0x40, "OUTPUT_TRAY_MISSING", "PAPER", "warning"),
    (1, 0x20, "MARKER_SUPPLY_MISSING", "SUPPLY", "critical"), (1, 0x10, "OUTPUT_NEAR_FULL", "PAPER", "warning"),
    (1, 0x08, "OUTPUT_FULL", "PAPER", "critical"), (1, 0x04, "INPUT_TRAY_EMPTY", "PAPER", "warning"),
    (1, 0x02, "OVERDUE_PREVENTIVE_MAINTENANCE", "SERVICE", "warning"),
]

COLOUR_WORDS = {"black": ("black", "bk", "noir", "schwarz", " k ", "-k", "mono"),
                "cyan": ("cyan",), "magenta": ("magenta",), "yellow": ("yellow", "jaune", "gelb")}


# ---- decoders -------------------------------------------------------------------------------------
def decode_special(value: int | None) -> tuple[str, int | None]:
    """Printer-MIB special values. -1 other, -2 unknown, -3 'some remaining'. Never mapped to zero."""
    if value is None:
        return "MISSING", None
    if value == -1:
        return "OTHER", None
    if value == -2:
        return "UNKNOWN", None
    if value == -3:
        return "SOME_REMAINING", None
    if value < 0:
        return "INVALID", None
    return "VALID", value


def supply_percent(level: int | None, max_capacity: int | None) -> tuple[str, float | None]:
    """Percent remaining only when both level and capacity are concrete and consistent."""
    lstate, lv = decode_special(level)
    if lstate != "VALID":
        return lstate, None
    cstate, cap = decode_special(max_capacity)
    if cstate != "VALID" or not cap:
        return "NO_CAPACITY", None
    if lv > cap:
        return "INCONSISTENT", None
    return "VALID", round(lv * 100.0 / cap, 1)


def supply_status(level_state: str, percent: float | None, *, low_threshold: float = 15.0,
                  receptacle: bool = False) -> str:
    if level_state == "SOME_REMAINING":
        return "OK"
    if percent is None:
        return "UNKNOWN"
    if receptacle:   # waste receptacles report remaining *space*; low space means nearly full
        return "FULL" if percent <= 0 else "NEARLY_FULL" if percent <= low_threshold else "OK"
    if percent <= 0:
        return "EMPTY"
    if percent <= low_threshold:
        return "LOW"
    return "OK"


def classify_colour(description: str | None, colorant: str | None = None) -> str:
    for text in (colorant, description):
        t = f" {(text or '').lower()} "
        for colour, words in COLOUR_WORDS.items():
            if any(w in t for w in words):
                return colour
    return "other"


def decode_error_state(raw: Any) -> list[dict]:
    """Decode the hrPrinterDetectedErrorState octet string (bytes, hex string or int list)."""
    data: bytes
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    elif isinstance(raw, (list, tuple)):
        data = bytes(int(x) & 0xFF for x in raw)
    else:
        text = str(raw).strip().lower().removeprefix("0x").replace(" ", "").replace(":", "")
        if not text or not re.fullmatch(r"[0-9a-f]+", text) or len(text) % 2:
            return []
        data = bytes.fromhex(text)
    out = []
    for byte, mask, code, category, severity in ERROR_STATE_BITS:
        if byte < len(data) and data[byte] & mask:
            out.append({"code": code, "category": category, "severity": severity,
                        "description": code.replace("_", " ").lower(), "source": "hrPrinterDetectedErrorState"})
    return out


def decode_alert(code: int | None, severity: int | None, description: str | None) -> dict:
    known = ALERT_CODE.get(code or 0)
    sev = ALERT_SEVERITY.get(severity or 0, "warning")
    if known:
        name, category, default_sev = known
        sev = "critical" if sev == "critical" or default_sev == "critical" else default_sev if sev == "other" else sev
    else:
        name, category = f"ALERT_{code}", "OTHER"
    return {"code": name, "category": category, "severity": sev, "description": (description or name)[:255],
            "source": "prtAlertTable"}


def manufacturer_from_object_id(oid: str | None) -> str | None:
    m = re.match(r"^\.?1\.3\.6\.1\.4\.1\.(\d+)", oid or "")
    return ENTERPRISES.get(int(m.group(1))) if m else None


def enterprise_number(oid: str | None) -> int | None:
    m = re.match(r"^\.?1\.3\.6\.1\.4\.1\.(\d+)", oid or "")
    return int(m.group(1)) if m else None


_MODEL_PATTERNS = [
    re.compile(r"(?P<m>ECOSYS\s+[A-Z]{1,3}\d{3,5}[a-z]*)", re.I),
    re.compile(r"(?P<m>TASKalfa\s+\d{3,5}[a-z]*)", re.I),
    re.compile(r"(?P<m>VersaLink\s+[A-Z]\d{2,4}[a-z]*)", re.I),
    re.compile(r"(?P<m>AltaLink\s+[A-Z]\d{3,4}[a-z]*)", re.I),
    re.compile(r"(?P<m>(?:HP\s+)?(?:Color\s+)?LaserJet(?:\s+Pro|\s+Enterprise|\s+Managed)?(?:\s+MFP)?\s+[A-Z]?\d{3,4}[a-z]*)", re.I),
    re.compile(r"(?P<m>WF-[A-Z]?\d{4}[a-z]*)", re.I),
]


def model_from_description(descr: str | None) -> dict:
    """Extract an exact model only when a complete model token is present. Never expands abbreviations."""
    if not descr:
        return {"model": None, "confidence": "UNKNOWN", "reason": "no sysDescr / hrDeviceDescr"}
    for pat in _MODEL_PATTERNS:
        m = pat.search(descr)
        if m:
            return {"model": re.sub(r"\s+", " ", m.group("m")).strip(), "confidence": "HIGH",
                    "reason": "complete model token in device description"}
    return {"model": None, "confidence": "LOW",
            "reason": "description does not contain a recognised complete model token; not guessed"}


def model_family(model: str | None) -> dict:
    """Coarse family facts derivable from the exact model string (colour / MFP / technology).
    Unknown stays None: only explicit, well-known naming conventions are interpreted."""
    m = (model or "").lower()
    out: dict[str, Any] = {"technology": None, "colour": None, "multifunction": None}
    if not m:
        return out
    if m.startswith("wf-") or "workforce" in m:
        out["technology"] = "inkjet"
    elif any(k in m for k in ("ecosys", "taskalfa", "laserjet", "versalink", "altalink")):
        out["technology"] = "laser"
    if "ecosys" in m:
        token = m.split("ecosys", 1)[1].strip()
        out["multifunction"] = token.startswith("ma") or token.startswith("m")
        out["colour"] = token.endswith("cix") or token.endswith("cidn") or "c" in token[-4:-1]
    if "versalink" in m or "altalink" in m:
        letter = m.split("link", 1)[1].strip()[:1]
        out["colour"] = letter == "c"
    if "color laserjet" in m:
        out["colour"] = True
    if "mfp" in m or "taskalfa" in m:
        out["multifunction"] = True
    return out


# ---- built-in polling profiles -------------------------------------------------------------------
def _d(key, oid, access="scalar", value_type="counter", unit=None, category="usage", verification="STANDARD",
       interval=None, description=None, scale=1.0):
    return {"metric_key": key, "oid": oid, "access": access, "value_type": value_type, "unit": unit,
            "category": category, "verification": verification, "interval_seconds": interval,
            "description": description, "scale": scale}


GENERIC_METRICS = [
    _d("sys_descr", SYS_DESCR, value_type="text", category="identity", interval=86400),
    _d("sys_object_id", SYS_OBJECT_ID, value_type="text", category="identity", interval=86400),
    _d("sys_name", SYS_NAME, value_type="text", category="identity", interval=86400),
    _d("sys_uptime", SYS_UPTIME, value_type="gauge", unit="centiseconds", category="status"),
    _d("serial_number", PRT_SERIAL, access="table", value_type="text", category="identity", interval=86400),
    _d("device_descr", HR_DEVICE_DESCR, access="table", value_type="text", category="identity", interval=86400),
    _d("device_status", HR_DEVICE_STATUS, access="table", value_type="enum", category="status"),
    _d("printer_status", HR_PRINTER_STATUS, access="table", value_type="enum", category="status"),
    _d("error_state", HR_PRINTER_ERROR_STATE, access="table", value_type="text", category="status"),
    _d("marker_life_count", PRT_MARKER_LIFE, access="table", value_type="counter", category="usage",
       description="prtMarkerLifeCount; unit from prtMarkerCounterUnit"),
    _d("marker_counter_unit", PRT_MARKER_UNIT, access="table", value_type="enum", category="usage", interval=86400),
    _d("marker_colorants", PRT_MARKER_COLORANTS, access="table", value_type="gauge", category="identity", interval=86400),
    _d("supply_level", PRT_SUPPLY_LEVEL, access="table", value_type="gauge", category="supply"),
    _d("supply_max", PRT_SUPPLY_MAX, access="table", value_type="gauge", category="supply"),
    _d("supply_type", PRT_SUPPLY_TYPE, access="table", value_type="enum", category="supply", interval=86400),
    _d("supply_class", PRT_SUPPLY_CLASS, access="table", value_type="enum", category="supply", interval=86400),
    _d("supply_descr", PRT_SUPPLY_DESCR, access="table", value_type="text", category="supply", interval=86400),
    _d("supply_unit", PRT_SUPPLY_UNIT, access="table", value_type="enum", category="supply", interval=86400),
    _d("supply_colorant", PRT_SUPPLY_COLORANT_INDEX, access="table", value_type="gauge", category="supply", interval=86400),
    _d("colorant_value", PRT_COLORANT_VALUE, access="table", value_type="text", category="supply", interval=86400),
    _d("input_level", PRT_INPUT_LEVEL, access="table", value_type="gauge", category="status"),
    _d("input_max", PRT_INPUT_MAX, access="table", value_type="gauge", category="status", interval=86400),
    _d("input_name", PRT_INPUT_NAME, access="table", value_type="text", category="status", interval=86400),
    _d("media_path_type", PRT_DUPLEX_MEDIA_PATH, access="table", value_type="enum", category="identity", interval=86400),
    _d("alert_code", PRT_ALERT_CODE, access="table", value_type="enum", category="status"),
    _d("alert_severity", PRT_ALERT_SEVERITY, access="table", value_type="enum", category="status"),
    _d("alert_descr", PRT_ALERT_DESCR, access="table", value_type="text", category="status"),
    _d("mac_address", IF_PHYS_ADDRESS, access="table", value_type="text", category="network", interval=86400),
]

# Vendor additions. VENDOR_UNVERIFIED: values are collected and shown, but the capability stays UNVERIFIED
# until the hardware acceptance run (HW-PR rows: Kyocera fleet first, then any Xerox/HP/Epson models) confirms the
# mapping per model.
VENDOR_METRICS = {
    "hp": [
        _d("impressions_total", "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.5.0", verification="VENDOR_UNVERIFIED",
           description="HP total engine page count (publicly documented; validate per model)"),
        _d("colour_impressions", "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.7.0", verification="VENDOR_UNVERIFIED",
           description="HP colour page count (validate per model)"),
        _d("bw_impressions", "1.3.6.1.4.1.11.2.3.9.4.2.1.4.1.2.6.0", verification="VENDOR_UNVERIFIED",
           description="HP mono page count (validate per model)"),
    ],
    "kyocera": [
        _d("impressions_total", "1.3.6.1.4.1.1347.43.10.1.1.12.1.1", verification="VENDOR_UNVERIFIED",
           description="Kyocera total printed count (validate per model)"),
        _d("bw_impressions", "1.3.6.1.4.1.1347.42.3.1.2.1.1.1.1", verification="VENDOR_UNVERIFIED",
           description="Kyocera printer black & white count (validate per model)"),
        _d("colour_impressions", "1.3.6.1.4.1.1347.42.3.1.2.1.1.1.3", verification="VENDOR_UNVERIFIED",
           description="Kyocera printer full-colour count (validate per model)"),
        _d("copy_impressions", "1.3.6.1.4.1.1347.42.3.1.2.1.1.2.1", verification="VENDOR_UNVERIFIED",
           description="Kyocera copy black & white count (validate per model)"),
        _d("scan_count", "1.3.6.1.4.1.1347.46.10.1.1.5.3", verification="VENDOR_UNVERIFIED",
           description="Kyocera total scanned count (validate per model)"),
    ],
    "xerox": [
        _d("impressions_total", "1.3.6.1.4.1.253.8.53.13.2.1.6.1.20.1", verification="VENDOR_UNVERIFIED",
           description="Xerox total impressions (validate per model)"),
        _d("bw_impressions", "1.3.6.1.4.1.253.8.53.13.2.1.6.1.20.34", verification="VENDOR_UNVERIFIED",
           description="Xerox black impressions (validate per model)"),
        _d("colour_impressions", "1.3.6.1.4.1.253.8.53.13.2.1.6.1.20.33", verification="VENDOR_UNVERIFIED",
           description="Xerox colour impressions (validate per model)"),
    ],
    "epson": [],
}

BUILTIN_PROFILES = [
    {"key": "rfc3805-generic", "name": "Generic Printer-MIB (RFC 3805)", "vendor": None,
     "match_enterprise": None, "match_descr_regex": None, "priority": 1000, "metrics": GENERIC_METRICS},
    {"key": "hp-printer", "name": "HP LaserJet / PageWide", "vendor": "HP", "match_enterprise": 11,
     "match_descr_regex": None, "priority": 100, "metrics": GENERIC_METRICS + VENDOR_METRICS["hp"]},
    {"key": "kyocera-printer", "name": "Kyocera ECOSYS / TASKalfa", "vendor": "Kyocera", "match_enterprise": 1347,
     "match_descr_regex": None, "priority": 100, "metrics": GENERIC_METRICS + VENDOR_METRICS["kyocera"]},
    {"key": "xerox-printer", "name": "Xerox VersaLink / AltaLink", "vendor": "Xerox", "match_enterprise": 253,
     "match_descr_regex": None, "priority": 100, "metrics": GENERIC_METRICS + VENDOR_METRICS["xerox"]},
    {"key": "epson-printer", "name": "Epson WorkForce (standard MIB only)", "vendor": "Epson",
     "match_enterprise": 1248, "match_descr_regex": None, "priority": 100, "metrics": GENERIC_METRICS},
]

# Default recommended monthly volumes / max duty cycles are NOT invented here: administrators enter
# verified manufacturer figures per model (printer_models). Without them, duty-cycle analysis reports
# INSUFFICIENT_EVIDENCE.
