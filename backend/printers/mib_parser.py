"""Safe SMIv1/SMIv2 MIB text parser for the printer MIB library.

The file is treated purely as text: nothing is compiled, imported or executed (unlike MIB compilers
that generate Python code). We extract the module name, IMPORTS, and every OBJECT IDENTIFIER /
MODULE-IDENTITY / OBJECT-IDENTITY / OBJECT-TYPE assignment with its parent, sub-identifier, syntax,
access, units and description, then resolve OIDs against well-known roots plus objects from other
modules already installed. Unknown parents are reported as unresolved imports, never guessed.
"""
from __future__ import annotations

import re

MAX_BYTES = 2 * 1024 * 1024
MAX_OBJECTS = 20000

WELL_KNOWN = {
    "ccitt": "0", "iso": "1", "joint-iso-ccitt": "2", "org": "1.3", "dod": "1.3.6", "internet": "1.3.6.1",
    "directory": "1.3.6.1.1", "mgmt": "1.3.6.1.2", "mib-2": "1.3.6.1.2.1", "transmission": "1.3.6.1.2.1.10",
    "experimental": "1.3.6.1.3", "private": "1.3.6.1.4", "enterprises": "1.3.6.1.4.1", "security": "1.3.6.1.5",
    "snmpV2": "1.3.6.1.6", "snmpDomains": "1.3.6.1.6.1", "snmpProxys": "1.3.6.1.6.2", "snmpModules": "1.3.6.1.6.3",
    "zeroDotZero": "0.0", "system": "1.3.6.1.2.1.1", "interfaces": "1.3.6.1.2.1.2", "host": "1.3.6.1.2.1.25",
    "hrDevice": "1.3.6.1.2.1.25.3", "printmib": "1.3.6.1.2.1.43", "ifMIB": "1.3.6.1.2.1.31",
}

_DEF = re.compile(
    r"(?m)^[ \t]*([a-zA-Z][\w-]*)[ \t]+"
    r"(OBJECT\s+IDENTIFIER|OBJECT-TYPE|MODULE-IDENTITY|OBJECT-IDENTITY)\b"
    r"(.*?)::=\s*\{([^}]*)\}", re.S)
_STR = re.compile(r'"[^"]*"', re.S)
_KEYS = r"(?:SYNTAX|UNITS|MAX-ACCESS|ACCESS|STATUS|DESCRIPTION|REFERENCE|INDEX|AUGMENTS|DEFVAL|$)"

METRIC_HINTS = [
    (re.compile(r"(colou?r|full-?colou?r).*(print|impress|page|count|total)", re.I), "colour_impressions"),
    (re.compile(r"(mono|black(white)?|bw|b-?w|monochrome).*(print|impress|page|count|total)", re.I), "bw_impressions"),
    (re.compile(r"copy.*(count|total|impress|page)", re.I), "copy_impressions"),
    (re.compile(r"scan.*(count|total|page)", re.I), "scan_count"),
    (re.compile(r"fax.*(count|total|impress|page)", re.I), "fax_impressions"),
    (re.compile(r"duplex.*(count|total|sheet|page)", re.I), "duplex_sheets"),
    (re.compile(r"(total|life|engine).*(impress|page|print)|(impress|page).*(total|life)count", re.I), "impressions_total"),
    (re.compile(r"sheet.*(count|total)", re.I), "sheets_total"),
    (re.compile(r"serial", re.I), "serial_number"),
    (re.compile(r"firmware|fwver|softwarever", re.I), "firmware"),
    (re.compile(r"(toner|ink|supply|consumable).*(level|remain|percent)", re.I), "supply_level_vendor"),
]


def _strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        res, i, in_str = [], 0, False
        while i < len(line):
            c = line[i]
            if c == '"':
                in_str = not in_str
            if not in_str and line.startswith("--", i):
                j = line.find("--", i + 2)
                if j == -1:
                    break
                i = j + 2
                continue
            res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def suggest_metric(name: str, syntax: str | None) -> str | None:
    s = (syntax or "").lower()
    numeric = any(k in s for k in ("counter", "integer", "gauge", "unsigned"))
    for pat, metric in METRIC_HINTS:
        if pat.search(name):
            if metric in ("serial_number", "firmware") and numeric:
                continue
            if metric not in ("serial_number", "firmware") and syntax and not numeric:
                continue
            return metric
    return None


def parse(text: str) -> dict:
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValueError("MIB file is larger than 2 MB")
    clean = _strip_comments(text.replace("\r\n", "\n"))
    m = re.search(r"([A-Za-z][\w-]*)\s+DEFINITIONS\s*(?:IMPLICIT\s+TAGS\s*)?::=\s*BEGIN", clean)
    if not m:
        raise ValueError("Not an SMI MIB module (no '<NAME> DEFINITIONS ::= BEGIN')")
    module = m.group(1)
    strings: list[str] = []

    def stash(mm):
        strings.append(mm.group(0)[1:-1])
        return f"__S{len(strings) - 1}__"
    body = _STR.sub(stash, clean)
    imports: dict[str, list[str]] = {}
    im = re.search(r"\bIMPORTS\b(.*?);", body, re.S)
    if im:
        for names, mod in re.findall(r"(.*?)\bFROM\s+([A-Za-z][\w-]*)", im.group(1), re.S):
            imports[mod] = [n.strip() for n in names.replace("\n", " ").split(",") if n.strip()]
    objects = []
    for dm in _DEF.finditer(body):
        name, kind_kw, rest, value = dm.group(1), re.sub(r"\s+", " ", dm.group(2)), dm.group(3), dm.group(4)
        if name in ("SYNTAX", "INDEX", "IMPORTS", "EXPORTS", "FROM"):
            continue
        parts = value.split()
        if not parts:
            continue
        parent = parts[0]
        subids = []
        for p in parts[1:]:
            n = re.fullmatch(r"(?:[\w-]+\()?(\d+)\)?", p)
            if not n:
                subids = None
                break
            subids.append(n.group(1))
        pm = re.fullmatch(r"[\w-]+\((\d+)\)", parent)
        if pm:     # { iso(1) org(3) ... }
            parent, subids = None, [pm.group(1)] + (subids or [])
        if subids is None:
            continue

        def field(key):
            fm = re.search(rf"\b{key}\b\s+(.*?)\s*(?={_KEYS})", rest + " $", re.S)
            return re.sub(r"\s+", " ", fm.group(1)).strip() if fm else None
        syntax = field("SYNTAX") if kind_kw == "OBJECT-TYPE" else None
        access = field("MAX-ACCESS") or field("ACCESS") if kind_kw == "OBJECT-TYPE" else None
        units = field("UNITS")
        desc = field("DESCRIPTION")
        if desc:
            desc = re.sub(r"__S(\d+)__", lambda x: strings[int(x.group(1))], desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:4000]
        if units:
            units = re.sub(r"__S(\d+)__", lambda x: strings[int(x.group(1))], units)[:40]
        index = field("INDEX") or field("AUGMENTS")
        if kind_kw != "OBJECT-TYPE":
            kind = "node"
        elif syntax and syntax.upper().startswith("SEQUENCE OF"):
            kind = "table"
        elif index:
            kind = "entry"
        else:
            kind = "scalar"       # refined to "column" once the parent entry is known
        objects.append({"name": name, "parent": parent, "subids": subids, "kind": kind, "syntax": syntax and syntax[:200],
                        "access": access and access[:32], "units": units, "description": desc})
        if len(objects) > MAX_OBJECTS:
            raise ValueError("MIB defines too many objects")
    ent = re.search(r"::=\s*\{\s*enterprises\s+(\d+)\s*\}", body)
    return {"module": module, "imports": imports, "objects": objects,
            "enterprise": int(ent.group(1)) if ent else None}


def resolve(parsed: dict, known: dict[str, str]) -> dict:
    """Assign dotted OIDs. ``known`` maps names from other installed modules → OID."""
    table = dict(WELL_KNOWN) | dict(known)
    pending = list(parsed["objects"])
    resolved: dict[str, str] = {}
    progress = True
    while pending and progress:
        progress = False
        rest = []
        for o in pending:
            base = "" if o["parent"] is None else table.get(o["parent"]) or resolved.get(o["parent"])
            if base is None:
                rest.append(o)
                continue
            oid = ".".join(x for x in [base, *o["subids"]] if x)
            o["oid"] = oid
            resolved[o["name"]] = oid
            progress = True
        pending = rest
    for o in pending:
        o["oid"] = None
    kinds = {o["name"]: o["kind"] for o in parsed["objects"]}
    for o in parsed["objects"]:
        if o["kind"] == "scalar" and kinds.get(o["parent"]) == "entry":
            o["kind"] = "column"
        o["suggested_metric"] = suggest_metric(o["name"], o["syntax"]) if o["kind"] in ("scalar", "column") else None
    unresolved = sorted({o["parent"] for o in pending if o["parent"]} - set(resolved))
    return {"resolved": resolved, "unresolved": unresolved,
            "status": "RESOLVED" if not pending else "UNRESOLVED_IMPORTS"}


def metric_definition_for(obj: dict) -> dict:
    """Profile metric draft from a MIB object: columns are walked as tables; scalars use .0."""
    syntax = (obj.get("syntax") or "").lower()
    value_type = ("counter" if "counter" in syntax else "text" if any(k in syntax for k in ("string", "displaystring"))
                  else "enum" if "{" in syntax else "gauge")
    if obj["kind"] == "column":
        return {"oid": obj["oid"], "access": "table", "value_type": value_type}
    return {"oid": obj["oid"] + ".0", "access": "scalar", "value_type": value_type}
