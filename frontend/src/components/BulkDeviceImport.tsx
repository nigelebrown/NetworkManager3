import { useState } from "react";
import { v1 } from "../services/inm";

type InputRow = {
  family: "access_point" | "switch";
  site_name: string;
  hostname: string;
  serial_number: string;
  model: string;
  management_ip: string | null;
  firmware: string | null;
  status: "up" | "down" | "unknown";
  metrics: Record<string, number>;
};

const required = ["family", "site_name", "hostname", "serial_number", "model"];
const headers = [...required, "management_ip", "firmware", "status"];

/** RFC 4180 quoting, embedded commas, escaped double quotes and CRLF. No packages. */
export function readCsv(text: string): string[][] {
  const content = text.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let fields: string[] = [], value = "", quoted = false, closed = false;
  for (let i = 0; i < content.length; i++) {
    const ch = content[i];
    if (quoted) {
      if (ch === '"' && content[i + 1] === '"') { value += '"'; i++; }
      else if (ch === '"') { quoted = false; closed = true; }
      else value += ch;
    } else if (ch === '"' && value === "" && !closed) {
      quoted = true;
    } else if (ch === ",") {
      fields.push(value.trim()); value = ""; closed = false;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && content[i + 1] === "\n") i++;
      fields.push(value.trim());
      if (fields.some(x => x !== "")) rows.push(fields);
      value = ""; fields = []; closed = false;
    } else {
      if (closed && ch !== " " && ch !== "\t")
        throw new Error("Invalid CSV: characters after closing quote.");
      value += ch;
    }
  }
  if (quoted) throw new Error("Invalid CSV: unclosed quotation mark.");
  if (value !== "" || fields.length) {
    fields.push(value.trim());
    if (fields.some(x => x !== "")) rows.push(fields);
  }
  return rows;
}

export function parseDeviceCsv(text: string): InputRow[] {
  const data = readCsv(text);
  if (!data.length) throw new Error("CSV file is empty.");
  const names = data[0].map(x => x.trim().toLowerCase());
  if (new Set(names).size !== names.length) throw new Error("CSV has duplicate column names.");
  for (const key of required) if (!names.includes(key))
    throw new Error("Missing required column: " + key);
  if (data.length < 2) throw new Error("CSV has no device rows.");
  if (data.length > 101) throw new Error("Import up to 100 devices per batch.");
  const seen = new Set<string>();
  return data.slice(1).map((cells, n) => {
    if (cells.length !== names.length) throw new Error("Row " + (n + 2) + ": incorrect number of columns.");
    const row: Record<string, string> = {};
    names.forEach((key, i) => { row[key] = cells[i] ?? ""; });
    const family = row.family.toLowerCase();
    const normalized = family === "ap" || family === "access point" ? "access_point"
      : family === "cx" ? "switch" : family;
    if (normalized !== "access_point" && normalized !== "switch")
      throw new Error("Row " + (n + 2) + ": family must be access_point or switch.");
    for (const key of required.slice(1))
      if (!row[key]?.trim()) throw new Error("Row " + (n + 2) + ": " + key + " is required.");
    const serial = row.serial_number.toLowerCase().trim();
    if (seen.has(serial)) throw new Error("Duplicate serial in CSV: " + row.serial_number);
    seen.add(serial);
    const status = (row.status || "unknown").toLowerCase();
    if (!["up", "down", "unknown"].includes(status))
      throw new Error("Row " + (n + 2) + ": status must be up, down or unknown.");
    return {
      family: normalized as InputRow["family"],
      site_name: row.site_name.trim(),
      hostname: row.hostname.trim(),
      serial_number: row.serial_number.trim(),
      model: row.model.trim(),
      management_ip: row.management_ip?.trim() || null,
      firmware: row.firmware?.trim() || null,
      status: status as InputRow["status"],
      metrics: {},
    };
  });
}

export default function BulkDeviceImport({ onImported }: { onImported: () => void }) {
  const [rows, setRows] = useState<InputRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sample = headers.join(",") + "\n" +
    "access_point,Head Office,HQ-AP-535-01,CN-AP-001,AP-535,10.101.1.15,8.10.0.21,unknown\n" +
    "switch,Head Office,HQ-CX6200-01,CN-CX-001,CX 6200F,10.103.1.20,10.14,unknown\n";
  const onFile = async (file: File | undefined) => {
    setRows([]); setSummary(null); setError(null);
    if (!file) return;
    if (file.size > 1024 * 1024) { setError("CSV file exceeds the 1 MB upload limit."); return; }
    try { setRows(parseDeviceCsv(await file.text())); }
    catch (err) { setError(err instanceof Error ? err.message : "Invalid CSV file."); }
  };
  const importRows = async () => {
    setBusy(true); setError(null); setSummary(null);
    try {
      const result = await v1.bulkQuickDevices(rows);
      setSummary(`${result.added} new device(s), ${result.updated} updated. REAL · MANUAL; configuration writes LOCKED.`);
      setRows([]); onImported();
    } catch (exc) { setError(exc instanceof Error ? exc.message : "Import failed."); }
    finally { setBusy(false); }
  };

  return <div className="space-y-3">
    <p className="text-sm text-slate-300">
      Register up to 100 existing Aruba APs/CX switches at a time without scanning the network.
      Review entries before saving. Existing devices are updated by serial number; no device settings are modified.
    </p>
    <p className="text-xs text-slate-400">
      Required CSV columns: <code>family,site_name,hostname,serial_number,model</code>.
      Optional: <code>management_ip,firmware,status</code>.
      Use family <code>access_point</code> or <code>switch</code>.
    </p>
    <button type="button" className="rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800"
      onClick={() => {
        const blob = new Blob([sample], { type: "text/csv;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob); link.download = "network-devices-template.csv";
        link.click(); URL.revokeObjectURL(link.href);
      }}>Download sample CSV</button>
    <label className="block text-sm">Select CSV file
      <input type="file" accept=".csv,text/csv" className="mt-1 block w-full text-sm" onChange={e => onFile(e.target.files?.[0])} />
    </label>
    {error && <p role="alert" className="rounded border border-red-800 p-2 text-sm text-red-300">{error}</p>}
    {summary && <p role="status" className="rounded border border-emerald-800 p-2 text-sm text-emerald-200">{summary}</p>}
    {!!rows.length && <>
      <p className="text-sm font-semibold">Review {rows.length} device(s) before import</p>
      <div className="max-h-64 overflow-auto rounded border border-slate-700">
        <table className="w-full min-w-[680px] text-left text-sm">
          <thead className="sticky top-0 bg-slate-900 text-slate-400"><tr>
            {["Type", "Location", "Name", "Serial", "Model", "Management IP"].map(h => <th key={h} className="px-2 py-2">{h}</th>)}
          </tr></thead>
          <tbody>{rows.map(r => <tr key={r.serial_number} className="border-t border-slate-800">
            <td className="px-2 py-2">{r.family === "switch" ? "Switch" : "AP"}</td>
            <td className="px-2 py-2">{r.site_name}</td>
            <td className="px-2 py-2">{r.hostname}</td>
            <td className="px-2 py-2">{r.serial_number}</td>
            <td className="px-2 py-2">{r.model}</td>
            <td className="px-2 py-2">{r.management_ip ?? "—"}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <button type="button" disabled={busy} className="rounded bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
        onClick={importRows}>{busy ? "Importing…" : `Confirm and import ${rows.length} device(s)`}</button>
    </>}
  </div>;
}
