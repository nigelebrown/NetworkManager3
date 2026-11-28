import { useMemo, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../services/api";
import { v1 } from "../services/inm";
import { rem, type Group, type GroupAssignResult } from "../services/remediation";

type Family = "access_point" | "switch";
type InputRow = {
  row: number;                       // 1-based line number in the uploaded file (header = 1)
  family: Family | null;
  site_name: string;
  hostname: string;
  serial_number: string;
  model: string;
  management_ip: string | null;
  firmware: string | null;
  status: "up" | "down" | "unknown";
  exported_group?: string;
  exported_status?: string;
  issue?: string;                    // structural problem found while mapping (row cannot be imported as-is)
};

export const FIELDS = ["family", "site_name", "hostname", "serial_number", "model", "management_ip", "firmware",
  "status", "exported_group"] as const;
export type Field = typeof FIELDS[number];
export type Mapping = Record<Field, number>;   // column index, -1 = not mapped
const LABELS: Record<Field, string> = {
  family: "Device type", site_name: "Site", hostname: "Device name", serial_number: "Serial number", model: "Model",
  management_ip: "Management IP", firmware: "Firmware", status: "Status (up/down/unknown)", exported_group: "Group",
};
const REQUIRED: Field[] = ["site_name", "hostname", "serial_number", "model"];
const ALIASES: Record<Field, string[]> = {
  family: ["family", "type", "device type", "device_type"],
  site_name: ["site_name", "site", "location", "site name"],
  hostname: ["hostname", "device name", "name", "device_name"],
  serial_number: ["serial_number", "serial", "serial number", "sn"],
  model: ["model", "device model"],
  management_ip: ["management_ip", "ip address", "ip", "management ip", "mgmt ip"],
  firmware: ["firmware", "firmware version", "version", "software version"],
  status: ["status", "state"],
  exported_group: ["group", "exported_group", "group name"],
};
const normalizeSite = (value: string) => value.replace(/[\r\n\t]+/g, " ").replace(/ {2,}/g, " ").trim();
const headers = ["family", "site_name", "hostname", "serial_number", "model", "management_ip", "firmware", "status"];

/** RFC 4180 quoting, embedded commas, escaped double quotes and CRLF. No packages. */
export function readCsv(text: string): string[][] {
  const content = text.replace(/^﻿/, "");
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

export function guessMapping(names: string[]): Mapping {
  const lower = names.map(n => n.trim().toLowerCase());
  const mapping = {} as Mapping;
  for (const field of FIELDS) mapping[field] = lower.findIndex(n => ALIASES[field].includes(n));
  return mapping;
}

/** Aruba Central exports have no family column; infer it from export-specific columns when possible. */
export function guessFamily(names: string[]): Family | null {
  const lower = names.map(n => n.trim().toLowerCase());
  return lower.includes("virtual controller") ? "access_point"
    : lower.includes("uplink ports") || lower.includes("nae aggregate status") ? "switch" : null;
}

const clean = (value?: string) => value && !["-", "--", "n/a"].includes(value.toLowerCase()) ? value.trim() : "";

/** Map every data row. Problems are recorded per row (never abort the whole file) so valid rows can still be imported. */
export function mapRows(data: string[][], mapping: Mapping, fixedFamily: Family | null): InputRow[] {
  const width = data[0]?.length ?? 0;
  const seen = new Set<string>();
  return data.slice(1).map((cells, n): InputRow => {
    const get = (f: Field) => mapping[f] >= 0 ? clean(cells[mapping[f]]) : "";
    const familyRaw = get("family").toLowerCase();
    const family: Family | null = familyRaw ? (["ap", "access point", "access_point", "iap"].includes(familyRaw) ? "access_point"
      : ["switch", "cx", "aruba cx"].includes(familyRaw) ? "switch" : null) : fixedFamily;
    const statusRaw = get("status").toLowerCase();
    const explicit = ["up", "down", "unknown"].includes(statusRaw);
    const row: InputRow = {
      row: n + 2, family, site_name: normalizeSite(get("site_name")), hostname: get("hostname"),
      serial_number: get("serial_number"), model: get("model"), management_ip: get("management_ip") || null,
      firmware: get("firmware") || null, status: explicit && mapping.family >= 0 ? statusRaw as InputRow["status"] : "unknown",
      exported_group: get("exported_group"), exported_status: explicit && mapping.family >= 0 ? "" : get("status"),
    };
    if (cells.length !== width) row.issue = "Wrong number of columns";
    else if (!family) row.issue = familyRaw ? `Unknown device type "${familyRaw}"` : "Device type not set";
    else {
      const missing = REQUIRED.filter(f => f !== "site_name" && !row[f as "hostname"]);
      if (missing.length) row.issue = "Missing " + missing.map(f => LABELS[f].toLowerCase()).join(", ");
    }
    const key = row.serial_number.toLowerCase();
    if (!row.issue && key) {
      if (seen.has(key)) row.issue = "Duplicate serial in this file";
      seen.add(key);
    }
    return row;
  });
}

export default function BulkDeviceImport({ onImported }: { onImported?: () => void }) {
  const [data, setData] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [fixedFamily, setFixedFamily] = useState<Family | null>(null);
  const [edits, setEdits] = useState<Record<number, Partial<InputRow>>>({});
  const [done, setDone] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState<Record<string, string[]>>({});
  const [summary, setSummary] = useState<string | null>(null);
  const [rejections, setRejections] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  // Imported devices that carry a group value: assigned to device groups after import (rc17).
  const [grouped, setGrouped] = useState<{ id: string; family: Family; group: string }[]>([]);
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});   // "<family>|<name>" -> group id | "create" | "skip"
  const [groupMsg, setGroupMsg] = useState<string | null>(null);
  const [reassign, setReassign] = useState(false);
  const [groupResults, setGroupResults] = useState<GroupAssignResult[] | null>(null);
  const sample = headers.join(",") + "\n" +
    "access_point,Head Office,HQ-AP-535-01,CN-AP-001,AP-535,10.101.1.15,8.10.0.21,unknown\n" +
    "switch,Head Office,HQ-CX6200-01,CN-CX-001,CX 6200F,10.103.1.20,10.14,unknown\n";
  const onFile = async (file: File | undefined) => {
    setData([]); setMapping(null); setEdits({}); setDone(new Set()); setImported({}); setSummary(null); setError(null); setRejections({});
    if (!file) return;
    if (file.size > 1024 * 1024) { setError("CSV file exceeds the 1 MB upload limit."); return; }
    try {
      const parsed = readCsv(await file.text());
      if (!parsed.length) throw new Error("CSV file is empty.");
      if (parsed.length < 2) throw new Error("CSV has no device rows.");
      if (parsed.length > 1001) throw new Error("Review up to 1,000 devices per CSV.");
      const names = parsed[0].map(x => x.trim().toLowerCase());
      if (new Set(names).size !== names.length) throw new Error("CSV has duplicate column names.");
      setData(parsed); setMapping(guessMapping(parsed[0])); setFixedFamily(guessFamily(parsed[0]));
    } catch (err) { setError(err instanceof Error ? err.message : "Invalid CSV file."); }
  };
  const rows = useMemo(() => mapping ? mapRows(data, mapping, fixedFamily)
    .map(r => ({...r, ...edits[r.row]})).filter(r => !done.has(r.row)) : [], [data, mapping, fixedFamily, edits, done]);
  const ipCounts = new Map<string, number>();
  for (const row of rows) if (row.management_ip) ipCounts.set(row.management_ip, (ipCounts.get(row.management_ip) ?? 0) + 1);
  const reason = (row: InputRow) => {
    if (row.issue) return row.issue;
    const site = normalizeSite(row.site_name);
    if (!site) return "Missing site";
    if (Array.from(site).length > 100 || /[\x00-\x1f]/.test(site)) return "Invalid site name";
    if (row.management_ip) {
      const parts = row.management_ip.split(".");
      if (parts.length !== 4 || parts.some(p => !/^(0|[1-9]\d{0,2})$/.test(p) || Number(p) > 255))
        return "Invalid IPv4 address";
      if ((ipCounts.get(row.management_ip) ?? 0) > 1) return "Repeated management IP";
    }
    for (const [field, value, max] of [
      ["Device name", row.hostname, 100], ["Serial number", row.serial_number, 64],
      ["Model", row.model, 64], ["Firmware", row.firmware ?? "", 64],
    ] as const) {
      if (Array.from(value).length > max || /[\x00-\x1f]/.test(value)) return `${field} is invalid`;
    }
    return rejections[row.row] ?? "";
  };
  const ready = rows.filter(row => !reason(row));
  const skipped = rows.filter(row => reason(row));
  const missingRequired = mapping ? REQUIRED.filter(f => mapping[f] < 0) : [];
  const exportSkipped = () => {
    const csv = [
      ["source_row", ...headers, "exported_group", "exported_status", "reason"],
      ...skipped.map(r => [String(r.row), r.family ?? "", r.site_name, r.hostname, r.serial_number, r.model,
        r.management_ip, r.firmware, r.status, r.exported_group, r.exported_status, reason(r)]),
    ].map(row => row.map(value => {
      const cell = value ?? "";
      const safe = /^[\s]*[=+\-@]/.test(cell) ? "'" + cell : cell;
      return '"' + safe.replace(/"/g, '""') + '"';
    }).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url; link.download = "inventory-rows-to-fix.csv"; link.click();
    URL.revokeObjectURL(url);
  };
  const importRows = async () => {
    setBusy(true); setError(null); setSummary(null);
    let added = 0, updated = 0;
    const completed = new Set<number>();
    const rejected: Record<number, string> = {};
    try {
      for (let offset = 0; offset < ready.length; offset += 100) {
        let batch = ready.slice(offset, offset + 100);
        while (batch.length) {
          try {
            const result = await v1.bulkQuickDevices(batch.map(r => ({
              family: r.family as Family, site_name: normalizeSite(r.site_name), hostname: r.hostname,
              serial_number: r.serial_number, model: r.model, management_ip: r.management_ip, firmware: r.firmware,
              status: r.status, metrics: {},
            })), "partial");
            for (const rej of result.rejected ?? []) rejected[batch[rej.index].row] = rej.reason;
            const ids = new Map(result.results.map(row => [row.serial_number.toLowerCase(), row.id]));
            const withGroup = batch.filter(r => r.exported_group && r.family && ids.has(r.serial_number.toLowerCase()))
              .map(r => ({ id: ids.get(r.serial_number.toLowerCase())!, family: r.family as Family, group: r.exported_group! }));
            if (withGroup.length) setGrouped(previous => [...previous, ...withGroup]);
            setImported(previous => {
              const next = {...previous};
              for (const row of batch) {
                const id = ids.get(row.serial_number.toLowerCase());
                if (id && row.family) next[row.family] = Array.from(new Set([...(next[row.family] ?? []), id]));
              }
              return next;
            });
            added += result.added; updated += result.updated;
            batch.filter(r => !(r.row in rejected)).forEach(row => completed.add(row.row));
            break;
          } catch (exc) {
            if (!(exc instanceof ApiError) || exc.status !== 422) throw exc;
            let details: {loc: unknown[]; msg: string}[];
            try { details = JSON.parse(exc.message); } catch { throw exc; }
            if (!Array.isArray(details) || !details.length || details.some(d =>
              !Array.isArray(d?.loc) || d.loc[0] !== "body" || d.loc[1] !== "devices" ||
              typeof d.loc[2] !== "number" || !batch[d.loc[2]] || typeof d.msg !== "string")) throw exc;
            for (const detail of details) {
              const row = batch[detail.loc[2] as number];
              rejected[row.row] = `${String(detail.loc[3] ?? "Row")}: ${detail.msg}`;
            }
            batch = batch.filter(row => !Object.prototype.hasOwnProperty.call(rejected, row.row));
          }
        }
      }
    } catch (exc) { setError(exc instanceof Error ? exc.message : "Import failed."); }
    finally {
      setRejections(previous => ({...previous, ...rejected}));
      setDone(previous => new Set([...previous, ...completed]));
      setSummary(`${added} added, ${updated} updated. ${rows.length - completed.size} rows remain in review.`);
      if (completed.size) onImported?.();
      setBusy(false);
    }
  };
  const groupKeys = Array.from(new Set(grouped.map(g => `${g.family}|${g.group}`))).sort();
  const loadGroups = async () => {
    const list = await rem.groups();
    setGroups(list);
    setChoice(Object.fromEntries(groupKeys.map(key => {
      const [family, name] = key.split("|");
      const same = list.find(g => g.name.toLowerCase() === name.toLowerCase() && g.family === family);
      const clash = list.some(g => g.name.toLowerCase() === name.toLowerCase() && g.family !== family);
      return [key, same ? same.id : clash ? "skip" : "create"];
    })));
  };
  const assignGroups = async () => {
    setBusy(true); setGroupMsg(null); setError(null); setGroupResults(null);
    try {
      const assignments = groupKeys.map(key => {
        const [family, name] = key.split("|");
        const target = choice[key] ?? "skip";
        return { group: name, family, device_ids: grouped.filter(g => `${g.family}|${g.group}` === key).map(g => g.id),
          target: (target === "create" || target === "skip" ? target : "existing") as "create" | "existing" | "skip",
          group_id: target === "create" || target === "skip" ? undefined : target };
      });
      const r = await rem.importAssign({ assignments, reassign });
      const n = (k: string) => r.counts[k] ?? 0;
      setGroupMsg(`${n("ASSIGNED")} assigned, ${n("ALREADY_MEMBER")} already members, ${n("SKIPPED")} skipped, ${n("REJECTED")} rejected` +
        (r.created_groups.length ? `; created ${r.created_groups.map(g => `“${g.name}”`).join(", ")}` : "") + ".");
      setGroupResults(r.results.filter(x => x.status === "REJECTED" || (x.moved_from ?? []).length || (x.other_groups ?? []).length));
      setGrouped([]); setGroups(null);
    } catch (exc) { setError(exc instanceof Error ? exc.message : "Group assignment failed."); }
    finally { setBusy(false); }
  };
  const edit = (row: number, patch: Partial<InputRow>) => setEdits(e => ({...e, [row]: {...e[row], ...patch}}));

  return <div className="space-y-3">
    <p className="text-sm text-slate-600">
      Upload an Aruba AP or switch export, or any CSV with one device per row. Map its columns, review every row and import
      the valid ones in batches of 100; rows that need fixing stay here and can be exported. Existing devices are matched by serial number.
    </p>
    <button type="button" className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
      onClick={() => {
        const blob = new Blob([sample], { type: "text/csv;charset=utf-8" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob); link.download = "network-devices-template.csv";
        link.click(); URL.revokeObjectURL(link.href);
      }}>Download sample CSV</button>
    <label className="block text-sm">Select CSV file
      <input type="file" disabled={busy} accept=".csv,text/csv" className="mt-1 block w-full text-sm" onChange={e => onFile(e.target.files?.[0])} />
    </label>
    {error && <p role="alert" className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">{error}</p>}
    {summary && <p role="status" className="rounded border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-900">{summary}</p>}
    {Object.entries(imported).flatMap(([family, ids]) => Array.from({length: Math.ceil(ids.length / 500)}, (_, index) => {
      const selected = ids.slice(index * 500, (index + 1) * 500);
      return <Link key={family + index} className="block text-blue-700 underline" to="/inventory"
        state={{ credentialDeviceIds: selected, family }}>
        Assign credentials to {selected.length} imported {family === "switch" ? "switches" : "access points"}
      </Link>;
    }))}
    {groupMsg && <p role="status" className="rounded border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-900">{groupMsg}</p>}
    {!!groupResults?.length && <table aria-label="Group assignment exceptions" className="w-full text-left text-sm"><thead><tr>
      <th scope="col" className="p-1">Device</th><th scope="col" className="p-1">Group</th><th scope="col" className="p-1">Result</th><th scope="col" className="p-1">Detail</th></tr></thead>
      <tbody>{groupResults.map((x, i) => <tr key={i} className="border-t border-slate-200"><td className="p-1 font-mono text-xs">{x.device_id.slice(0, 8)}</td>
        <td className="p-1">{x.group}</td><td className="p-1">{x.status}</td>
        <td className="p-1 text-xs">{x.reason ?? (x.moved_from?.length ? `moved from ${x.moved_from.join(", ")}` : x.other_groups?.length ? `also in ${x.other_groups.join(", ")}` : "")}</td></tr>)}</tbody></table>}
    {grouped.length > 0 && <fieldset className="rounded border border-slate-200 p-3">
      <legend className="px-1 text-sm font-semibold">Assign imported devices to groups</legend>
      <p className="text-sm text-slate-600">{grouped.length} imported device(s) have a group value. Groups are used for templates and
        wave deployments; membership never changes device configuration by itself.</p>
      {!groups ? <button type="button" className="mt-2 rounded border border-slate-300 px-3 py-2 text-sm" disabled={busy}
        onClick={() => loadGroups().catch(e => setError(e instanceof Error ? e.message : "Could not load groups."))}>Review group assignment</button> : <>
        <table className="mt-2 w-full text-left text-sm"><thead><tr><th scope="col" className="p-1">Group value</th><th scope="col" className="p-1">Type</th>
          <th scope="col" className="p-1">Devices</th><th scope="col" className="p-1">Assign to</th></tr></thead>
          <tbody>{groupKeys.map(key => {
            const [family, name] = key.split("|");
            const clash = groups.some(g => g.name.toLowerCase() === name.toLowerCase() && g.family !== family);
            return <tr key={key} className="border-t border-slate-200"><td className="p-1">{name}</td>
              <td className="p-1">{family === "switch" ? "Switches" : "Access points"}</td>
              <td className="p-1">{grouped.filter(g => `${g.family}|${g.group}` === key).length}</td>
              <td className="p-1"><select aria-label={`Group for ${name} (${family})`} className="rounded border border-slate-300 px-1"
                value={choice[key] ?? "skip"} onChange={e => setChoice(c => ({...c, [key]: e.target.value}))}>
                <option value="skip">Do not assign</option>
                {!clash && !groups.some(g => g.name.toLowerCase() === name.toLowerCase()) && <option value="create">Create group “{name}”</option>}
                {groups.filter(g => g.family === family).map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>{clash && <p className="text-xs text-amber-800">A group with this name exists for another device type.</p>}</td></tr>;
          })}</tbody></table>
        <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={reassign} disabled={busy}
          onChange={e => setReassign(e.target.checked)} /> Move devices out of their other groups of the same type (reassign)</label>
        <button type="button" className="mt-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50" disabled={busy}
          onClick={assignGroups}>Assign to groups</button></>}
    </fieldset>}
    {mapping && <fieldset className="rounded border border-slate-200 p-3">
      <legend className="px-1 text-sm font-semibold">Column mapping</legend>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {FIELDS.map(field => <div key={field} className="text-sm">
          <label htmlFor={`map-${field}`}>{LABELS[field]}{REQUIRED.includes(field) ? " *" : ""}</label>
          <select id={`map-${field}`} className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" disabled={busy}
            value={mapping[field]} onChange={e => setMapping({...mapping, [field]: Number(e.target.value)})}>
            <option value={-1}>— not in file —</option>
            {data[0].map((name, i) => <option key={i} value={i}>{name || `Column ${i + 1}`}</option>)}
          </select></div>)}
        {mapping.family < 0 && <div className="text-sm">
          <label htmlFor="map-fixed-family">Device type for every row *</label>
          <select id="map-fixed-family" className="mt-1 block w-full rounded border border-slate-300 px-2 py-1" disabled={busy}
            value={fixedFamily ?? ""} onChange={e => setFixedFamily((e.target.value || null) as Family | null)}>
            <option value="">Choose…</option><option value="access_point">Access points</option><option value="switch">Switches</option>
          </select></div>}
      </div>
      {missingRequired.length > 0 && <p className="mt-2 text-sm text-amber-800">
        Map {missingRequired.map(f => LABELS[f]).join(", ")}{missingRequired.includes("site_name") ? " (or enter sites row by row below)" : ""}.</p>}
    </fieldset>}
    {!!rows.length && <>
      <p className="text-sm font-semibold">Review {rows.length} devices · {ready.length} ready · {skipped.length} need review.</p>
      <div className="max-h-80 overflow-auto rounded border border-slate-200">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="sticky top-0 bg-slate-50 text-slate-600"><tr>
            {["Row", "Type", "Location", "Name", "Serial", "Model", "Management IP", "Exported group", "Exported status", "Import status"]
              .map(h => <th key={h} scope="col" className="px-2 py-2">{h}</th>)}
          </tr></thead>
          <tbody>{rows.map(r => <tr key={r.row} className="border-t border-slate-200">
            <td className="px-2 py-2">{r.row}</td>
            <td className="px-2 py-2">{r.family === "switch" ? "Switch" : r.family === "access_point" ? "AP" : "—"}</td>
            <td className="px-2 py-2"><input aria-label={`Site for row ${r.row}`} disabled={busy} maxLength={100}
              className="rounded border border-slate-300 px-1" value={r.site_name} placeholder="Site required"
              onChange={e => edit(r.row, {site_name: e.target.value})} /></td>
            <td className="px-2 py-2">{r.hostname}</td>
            <td className="px-2 py-2">{r.serial_number}</td>
            <td className="px-2 py-2">{r.model}</td>
            <td className="px-2 py-2"><input aria-label={`Management IP for row ${r.row}`} disabled={busy}
              className="rounded border border-slate-300 px-1" value={r.management_ip ?? ""}
              onChange={e => edit(r.row, {management_ip: e.target.value.trim() || null})} /></td>
            <td className="px-2 py-2">{r.exported_group || "—"}</td>
            <td className="px-2 py-2">{r.exported_status || "—"}</td>
            <td className={`px-2 py-2 ${reason(r) ? "text-amber-800" : "text-emerald-800"}`}>{reason(r) || "Ready"}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {skipped.length > 0 && <button type="button" disabled={busy} onClick={exportSkipped}
        className="mr-2 rounded border border-slate-300 px-3 py-2 text-sm">Export {skipped.length} rows to fix</button>}
      <button type="button" disabled={busy || !ready.length} className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        onClick={importRows}>{busy ? "Importing…" : `Confirm and import ${ready.length} device(s)`}</button>
    </>}
  </div>;
}
