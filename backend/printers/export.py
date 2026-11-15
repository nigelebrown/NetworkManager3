"""Dependency-free report writers: CSV, Excel (.xlsx, Office Open XML) and PDF (tabular text).

Implemented with the standard library so the release image does not gain new pinned dependencies.
Cells are written as inline strings / numbers; formula-like text is neutralised (CSV injection).
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_FORMULA_PREFIX) and not _is_number(s) else s


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def to_csv(columns: list[str], rows: list[dict], meta: dict | None = None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for k, v in (meta or {}).items():
        w.writerow([f"# {k}", _safe_text(v)])
    w.writerow(columns)
    for r in rows:
        w.writerow([v if isinstance(v, (int, float)) else _safe_text(v) for v in (r.get(c) for c in columns)])
    return buf.getvalue().encode("utf-8-sig")


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def to_xlsx(columns: list[str], rows: list[dict], *, sheet: str = "Report", meta: dict | None = None) -> bytes:
    def cell(ref, v, bold=False):
        style = ' s="1"' if bold else ""
        if isinstance(v, bool) or v is None or not isinstance(v, (int, float)):
            return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(_safe_text(v))}</t></is></c>'
        return f'<c r="{ref}"{style}><v>{v}</v></c>'

    lines, r = [], 1
    for k, v in (meta or {}).items():
        lines.append(f'<row r="{r}">{cell(f"A{r}", k, True)}{cell(f"B{r}", v)}</row>')
        r += 1
    if meta:
        r += 1
    lines.append(f'<row r="{r}">' + "".join(cell(f"{_col(i)}{r}", c, True) for i, c in enumerate(columns)) + "</row>")
    for row in rows:
        r += 1
        lines.append(f'<row r="{r}">' + "".join(cell(f"{_col(i)}{r}", row.get(c)) for i, c in enumerate(columns)) + "</row>")
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'<sheetData>{"".join(lines)}</sheetData></worksheet>')
    name = escape(sheet[:31])
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, body in files.items():
            z.writestr(path, body)
    return out.getvalue()


def _pdf_text(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def to_pdf(title: str, columns: list[str], rows: list[dict], *, meta: dict | None = None) -> bytes:
    """Landscape A4 text table with automatic pagination. Courier keeps columns aligned."""
    width_chars = 150
    colw = max(8, min(28, (width_chars - len(columns)) // max(1, len(columns))))

    def fmt(vals):
        return " ".join(("" if v is None else str(v))[:colw].ljust(colw) for v in vals)

    lines = [title, f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    lines += [f"{k}: {v}" for k, v in (meta or {}).items()] + ["", fmt(columns), "-" * min(width_chars, (colw + 1) * len(columns))]
    lines += [fmt([r.get(c) for c in columns]) for r in rows]
    if not rows:
        lines.append("No rows for the selected filters and period.")
    per_page = 48
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    objs: list[bytes] = []
    kids = []
    font_id = 3
    obj_id = 4
    page_objs = []
    for page in pages:
        stream = "BT /F1 8 Tf 30 560 Td 10 TL " + " ".join(f"({_pdf_text(l)}) '" for l in page) + " ET"
        content_id, page_id = obj_id, obj_id + 1
        obj_id += 2
        page_objs.append((content_id, stream, page_id))
        kids.append(f"{page_id} 0 R")
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    for content_id, stream, page_id in page_objs:
        data = stream.encode("latin-1")
        objs.append(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {content_id} 0 R >>".encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


FORMATS = {"csv": ("text/csv", ".csv"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
           "pdf": ("application/pdf", ".pdf")}


def render(fmt: str, title: str, columns: list[str], rows: list[dict], meta: dict | None = None) -> tuple[bytes, str, str]:
    if fmt not in FORMATS:
        raise ValueError("format must be csv, xlsx or pdf")
    data = (to_csv(columns, rows, meta) if fmt == "csv" else to_xlsx(columns, rows, meta=meta)
            if fmt == "xlsx" else to_pdf(title, columns, rows, meta=meta))
    mime, ext = FORMATS[fmt]
    return data, mime, ext
