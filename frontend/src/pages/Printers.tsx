import { PageHeader } from "../components/viz";
import { request } from "../services/api";
import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, ErrorNote, Stat, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { printerApi, type DiscoveredPrinter, type PrinterAnalysis } from "../services/printers";

type PrinterProfile = { id: string; name: string; version: string };
const profilePath = "/printers/credential-profiles";
const fieldStyle = "mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm";
const btn = "rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50";
const sub = "rounded border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800";
const dollar = (v: number | null | undefined, currency = "JMD") =>
  v == null ? "Not calculated" : `${currency} ${v.toFixed(2)}`;

const meterOptions = [
  ["impressions_total", "Total impressions (printed sides)"],
  ["sheets_total", "Total physical sheets"],
  ["bw_impressions", "Black-and-white impressions"],
  ["colour_impressions", "Colour impressions"],
  ["copy_impressions", "Copy impressions"],
  ["scan_count", "Scans"],
  ["fax_impressions", "Fax impressions"],
];

const locationFields = [
  ["area", "Area"], ["division", "Division"], ["branch", "Branch"], ["station", "Station"],
  ["section_unit", "Section / Unit"], ["building", "Building"],
] as const;

export function Printers() {
  const inventory = useLoad(() => printerApi.list(), [], 30000, ["printers"]);
  const stats = useLoad(() => printerApi.dashboard(), [], 30000, ["printers"]);
  const [editing, setEditing] = useState(false);
  const [snmp, setSnmp] = useState(false);
  const [newPrinter, setNewPrinter] = useState<Record<string, string>>({
    manufacturer: "HP", model: "", serial_number: "", hostname: "", management_ip: "",
    area: "", division: "", branch: "", station: "", section_unit: "", building: "",
  });
  const [discovery, setDiscovery] = useState<{ scanned: number; printers: DiscoveredPrinter[] } | null>(null);
  const profiles = useLoad(() => request<PrinterProfile[]>(profilePath), [], 0);
  const [credentials, setCredentials] = useState({name: "", version: "3"});
  const [profileId, setProfileId] = useState("");
  const [target, setTarget] = useState("");
  const [profileResult, setProfileResult] = useState<Record<string, unknown> | null>(null);
  const [analysis, setAnalysis] = useState<PrinterAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const action = async (task: () => Promise<unknown>, done: string) => {
    setBusy(true); setError(null); setMessage(null);
    try { await task(); setMessage(done); inventory.reload(); stats.reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Operation failed"); }
    finally { setBusy(false); }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const b = Object.fromEntries(Object.entries(newPrinter).map(([k, v]) => [k, v.trim() || null]));
    action(() => printerApi.add(b), "Printer registered. Select it below to record meter and supply readings.");
  };

  return (
    <div className="space-y-5">
      <div>
        <PageHeader eyebrow="Sites & inventory" title="Printers" subtitle="Printer fleet inventory, observed consumables, page counters and read-only diagnostics. Predictions remain rule-based." />
        <p className="mt-1 text-sm text-slate-400">
          HP, Epson, Kyocera, Sharp and other printer models. Equipment stays read-only.
          Manually entered measurements are never presented as automatically collected values.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Printers registered" value={stats.data?.total ?? "—"} />
        <Stat label="Reported online" value={stats.data?.online ?? "—"} />
        <Stat label="Reported faults" value={stats.data?.faulted ?? "—"} tone="warn" />
        <Stat label="Low consumables" value={stats.data?.low_supplies.length ?? "—"} tone="warn" />
      </div>
      <div className="flex flex-wrap gap-2">
        <button className={editing || !snmp ? btn : sub} aria-expanded={editing} aria-controls="printer-registration"
          onClick={() => { setEditing(!editing); setSnmp(false); }}> {editing ? "Close registration" : "Add a printer"} </button>
        <button className={snmp ? btn : sub} aria-expanded={snmp} aria-controls="printer-discovery"
          onClick={() => { setSnmp(!snmp); setEditing(false); }}>Discover printers</button>
        <button className={sub} disabled={busy} onClick={() => action(async () => { setAnalysis(await printerApi.analyse()); }, "Rule-based analysis completed.")}>Analyse now</button>
      </div>
      <ErrorNote error={error ?? inventory.error ?? stats.error} />
      {message && <p role="status" className="rounded border border-emerald-800 p-3 text-sm text-emerald-300">{message}</p>}
      <div id="printer-registration" hidden={!editing}>{editing && <Card title="Register a physical printer">
        <p className="mb-3 text-xs text-slate-400">Step 1: identify equipment. Step 2: assign JCF location. Step 3: save. Step 4: open the printer to enter actual readings.</p>
        <form onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 md:grid-cols-3">
            <label className="text-sm">Manufacturer
              <select className={fieldStyle} value={newPrinter.manufacturer}
                onChange={(e) => setNewPrinter(v => ({ ...v, manufacturer: e.target.value }))}>
                {["HP", "Epson", "Kyocera", "Sharp", "Brother", "Canon", "Lexmark", "Xerox", "Other"].map(x =>
                  <option key={x}>{x}</option>)}
              </select>
            </label>
            {(["model", "serial_number", "hostname", "management_ip"] as const).map(k => (
              <label className="text-sm" key={k}>{({ model: "Model", serial_number: "Serial number",
                hostname: "Hostname", management_ip: "Management IPv4" } as Record<string, string>)[k]}
                <input className={fieldStyle} required={k === "model" || k === "serial_number"}
                  value={newPrinter[k] ?? ""} onChange={e => setNewPrinter(v => ({ ...v, [k]: e.target.value }))} />
              </label>
            ))}
          </div>
          <h3 className="text-sm font-semibold text-slate-300">Physical assignment</h3>
          <div className="grid gap-3 md:grid-cols-3">
            {locationFields.map(([k, label]) => <label className="text-sm" key={k}>{label}
              <input className={fieldStyle} value={newPrinter[k] ?? ""}
                onChange={e => setNewPrinter(v => ({ ...v, [k]: e.target.value }))} />
            </label>)}
          </div>
          <p className="text-xs text-amber-300">Location entries are provisional free text until the JCF location registry is connected. Device writes remain disabled.</p>
          <button className={btn} disabled={busy}>Register printer</button>
        </form>
      </Card>}</div>
      <div id="printer-discovery" hidden={!snmp}>{snmp && <Card title="Discover printers">
        <ErrorNote error={profiles.error} />
        <p>Choose a saved SNMP profile and enter an approved IPv4 address or CIDR from /24 to /32.</p>
        <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={e => {
          e.preventDefault();
          setProfileResult(null); setDiscovery(null);
          action(async () => {
            const scope = target.trim();
            const address = scope.split("/")[0];
            if (!/^(?:\d{1,3}\.){3}\d{1,3}$/.test(address) ||
                address.split(".").some(part => Number(part) > 255 || String(Number(part)) !== part) ||
                (scope.includes("/") && !/^\/(?:2[4-9]|3[0-2])$/.test(scope.slice(address.length)))) {
              throw new Error("Enter an IPv4 address or an approved CIDR from /24 to /32.");
            }
            const url = `${profilePath}/${encodeURIComponent(profileId)}`;
            if (scope.includes("/")) {
              setDiscovery(await request<{ scanned: number; printers: DiscoveredPrinter[] }>(url + "/discover", {
                method: "POST", body: JSON.stringify({management_range: scope}),
              }));
            } else {
              setProfileResult(await request<Record<string, unknown>>(url + "/test", {
                method: "POST", body: JSON.stringify({management_ip: scope}),
              }));
            }
          }, "Printer check completed.");
        }}>
          <label>Saved profile<select className={fieldStyle} required value={profileId} onChange={e => setProfileId(e.target.value)}>
            <option value="">Choose a profile</option>{profiles.data?.map(p => <option value={p.id} key={p.id}>{p.name} (v{p.version})</option>)}
          </select></label>
          <label>Management IP or CIDR<input className={fieldStyle} required value={target} onChange={e => setTarget(e.target.value)} /></label>
          <button className={sub} disabled={busy || !profileId}>{busy ? "Checking…" : "Discover"}</button>
        </form>
        {profileResult && <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(profileResult, null, 2)}</pre>}
        {discovery && <div className="mt-4 space-y-2 text-sm">
          <p>{discovery.printers.length} printers responded across {discovery.scanned} addresses.</p>
          {discovery.printers.map(printer => <div key={printer.management_ip} className="flex flex-wrap items-center gap-3 border-b py-2">
            <span className="font-mono">{printer.management_ip}</span>
            <span>{printer.hostname ?? "Hostname unavailable"}</span>
            <span>{printer.system_description}</span>
            <button className={sub} onClick={() => {
              setNewPrinter(previous => ({ ...previous, hostname: printer.hostname ?? "", management_ip: printer.management_ip }));
              setSnmp(false);
              setEditing(true);
            }}>Review and register</button>
          </div>)}
        </div>}
        <details className="mt-4"><summary className="cursor-pointer font-medium">Add a credential profile</summary>
        <form className="flex flex-wrap items-end gap-3" onSubmit={e => {
          e.preventDefault();
          const form = e.currentTarget;
          const body = {...Object.fromEntries(new FormData(form)), ...credentials};
          action(async () => {
            try {
              const saved = await request<PrinterProfile>(profilePath, {method: "POST", body: JSON.stringify(body)});
              setProfileId(saved.id);
              setCredentials(previous => ({...previous, name: ""}));
              profiles.reload();
            } finally {
              form.reset();
            }
          }, "Printer credential profile saved.");
        }}>
          <label>Profile name<input className={fieldStyle} required maxLength={100} value={credentials.name}
            onChange={e => setCredentials({...credentials, name: e.target.value})} /></label>
          <label>SNMP version<select className={fieldStyle} value={credentials.version}
            onChange={e => setCredentials({...credentials, version: e.target.value})}>
            <option value="1">SNMPv1</option><option value="2c">SNMPv2c</option><option value="3">SNMPv3 (SHA-256 / AES-128)</option>
          </select></label>
          {credentials.version === "3" ? <>
            <label>Username<input className={fieldStyle} name="username" required maxLength={100} /></label>
            <label>Authentication passphrase<input className={fieldStyle} name="auth_passphrase" type="password" required minLength={8} maxLength={200} /></label>
            <label>Privacy passphrase<input className={fieldStyle} name="privacy_passphrase" type="password" required minLength={8} maxLength={200} /></label>
          </> : <label>Read-only community<input className={fieldStyle} name="community" type="password" required maxLength={200} /></label>}
          <button className={btn} disabled={busy}>Save profile</button>
        </form>
        </details>
      </Card>}</div>
      {analysis && <Card title="Rule-based recommendations — not trained ML">
        <p className="mb-2 text-xs text-slate-400">{analysis.disclaimer}</p>
        {analysis.recommendations.length === 0
          ? <p className="text-sm text-slate-500">No low-supply recommendations from the currently recorded observations.</p>
          : analysis.recommendations.map((r, i) => <div key={i} className="border-b border-slate-800 p-2 text-sm">
              <Badge value={r.severity.toLowerCase()}/> {r.recommended_action} · <Link className="text-sky-400 underline" to={`/printers/${r.printer_id}`}>View printer</Link>
            </div>)}
        {analysis.model_comparison.map((r) => <p key={r.model} className="mt-2 text-sm">
          {r.model}: {r.mean_consumable_cost_per_1000_impressions == null ? "Insufficient evidence for cost comparison" :
            `Mean consumable cost / 1,000 impressions: ${r.mean_consumable_cost_per_1000_impressions}`} ({r.verified_cycles} recorded cycles)
        </p>)}
      </Card>}
      <Card title="Printer inventory">
        <div className="overflow-x-auto"><table className="w-full min-w-[780px] text-left text-sm">
          <thead className="text-xs uppercase text-slate-400"><tr>
            {["Manufacturer", "Model", "Serial", "Management IP", "Location", "Status", "Source", ""].map(h =>
              <th key={h} className="px-2 py-2">{h}</th>)}
          </tr></thead>
          <tbody className="divide-y divide-slate-800">
            {inventory.data?.map(d => <tr key={d.id}>
              <td className="px-2 py-2">{d.manufacturer}</td><td className="px-2 py-2">{d.model}</td>
              <td className="px-2 py-2 font-mono text-xs">{d.serial_number}</td>
              <td className="px-2 py-2 font-mono text-xs">{d.management_ip ?? "—"}</td>
              <td className="px-2 py-2">{[d.area, d.division, d.branch, d.station, d.section_unit, d.building].filter(Boolean).join(" / ") || "Not assigned"}</td>
              <td className="px-2 py-2"><Badge value={d.status} /></td>
              <td className="px-2 py-2 text-emerald-300">{d.provenance}</td>
              <td className="px-2 py-2"><Link to={`/printers/${d.id}`} className="text-sky-400 hover:underline">Open</Link></td>
            </tr>)}
          </tbody>
        </table></div>
        {!inventory.data?.length && <p className="p-3 text-sm text-slate-500">No printers registered. Select Add a printer to start.</p>}
      </Card>
      {!!stats.data?.low_supplies.length && <Card title="Consumables requiring attention">
        {stats.data.low_supplies.map((s, i) => <div key={i} className="flex flex-wrap gap-3 border-b border-slate-800 py-2 text-sm">
          <Badge value={s.severity.toLowerCase()} /> <Link className="text-sky-400" to={`/printers/${s.printer_id}`}>{s.hostname}</Link>
          {s.colour} {s.supply}: {s.percent_remaining}% · {s.source}
        </div>)}
      </Card>}
    </div>
  );
}

export function PrinterDetails() {
  const { id = "" } = useParams<{ id: string }>();
  const detail = useLoad(() => printerApi.detail(id), [id], 15000, ["printers"]);
  const [tab, setTab] = useState<"meter" | "supply" | "cycle" | "maintenance">("meter");
  const [reading, setReading] = useState({ meter_kind: "impressions_total", counter_value: "" });
  const [supply, setSupply] = useState({ supply_name: "Black toner", colour: "black", percent_remaining: "" });
  const [cycle, setCycle] = useState({ consumable_kind: "toner", colour: "black", start_meter: "",
    cost_amount: "", currency: "JMD", part_number: "", declared_yield: "", quantity_added_ml: "" });
  const [finish, setFinish] = useState<Record<string, string>>({});
  const [service, setService] = useState({ component: "fuser", event_type: "inspection",
    severity: "info", description: "", cost_amount: "", currency: "JMD" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const execute = async (f: () => Promise<unknown>, msg: string) => {
    setBusy(true); setError(null); setSuccess(null);
    try { await f(); detail.reload(); setSuccess(msg); }
    catch (e) { setError(e instanceof Error ? e.message : "Save failed"); }
    finally { setBusy(false); }
  };
  const d = detail.data?.device;
  return <div className="space-y-4">
    <Link className="text-sm text-sky-400" to="/printers">← Printer inventory</Link>
    <PageHeader eyebrow="Sites & inventory · Printers" title={d ? `${d.manufacturer} ${d.model}` : "Printer details"} subtitle="Observed printer counters, supply levels, faults and maintenance history." />
    <ErrorNote error={error ?? detail.error} />
    {success && <p role="status" className="rounded border border-emerald-800 p-3 text-sm text-emerald-300">{success}</p>}
    {d && <Card title="Printer identity and observation status">
      <div className="grid gap-2 text-sm md:grid-cols-3">
        <div>Serial: <b>{d.serial_number}</b></div>
        <div>Management IP: <b>{d.management_ip ?? "Not recorded"}</b></div>
        <div>Status: <Badge value={d.status}/></div>
        <div>Source: <b>{d.provenance}</b></div>
        <div>Firmware: {d.firmware ?? "Not reported"}</div>
        <div>Location: {[d.area, d.division, d.station, d.section_unit, d.building].filter(Boolean).join(" / ") || "Not assigned"}</div>
      </div>
      <p className="mt-3 text-xs text-amber-300">This module never configures printers. Manual readings are operator-reported. SNMPv3 discovery is a single read-only snapshot, not continuous polling.</p>
    </Card>}
    <div className="flex flex-wrap gap-2">
      {([["meter", "Record meter"], ["supply", "Record toner / ink"], ["cycle", "Cartridge / refill cycle"],
         ["maintenance", "Service / fault"]] as const).map(([k, name]) =>
        <button key={k} className={tab === k ? btn : sub} onClick={() => setTab(k)}>{name}</button>)}
    </div>
    <Card title="Record real device evidence">
      {tab === "meter" && <form className="grid gap-3 md:grid-cols-3" onSubmit={(e: FormEvent) => {
        e.preventDefault(); execute(() => printerApi.meter(id, { ...reading, counter_value: Number(reading.counter_value) }), "Meter reading saved.");
      }}>
        <label className="text-sm">Counter type
          <select className={fieldStyle} value={reading.meter_kind} onChange={e => setReading(v => ({ ...v, meter_kind: e.target.value }))}>
            {meterOptions.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label className="text-sm">Cumulative reading
          <input className={fieldStyle} required type="number" min="0" step="1" value={reading.counter_value}
            onChange={e => setReading(v => ({ ...v, counter_value: e.target.value }))}/>
        </label>
        <button className={btn} disabled={busy}>Save meter reading</button>
        <p className="col-span-full text-xs text-slate-400">Impressions, logical pages and physical sheets are not equivalent. A falling counter is flagged as a reset or rollover, not negative usage.</p>
      </form>}
      {tab === "supply" && <form className="grid gap-3 md:grid-cols-4" onSubmit={(e: FormEvent) => {
        e.preventDefault(); execute(() => printerApi.supply(id, { ...supply, percent_remaining: supply.percent_remaining === "" ? null : Number(supply.percent_remaining) }), "Consumable reading saved.");
      }}>
        <label className="text-sm">Supply name
          <input className={fieldStyle} value={supply.supply_name} required onChange={e => setSupply(v => ({ ...v, supply_name: e.target.value }))}/>
        </label>
        <label className="text-sm">Colour
          <select className={fieldStyle} value={supply.colour} onChange={e => setSupply(v => ({ ...v, colour: e.target.value }))}>
            {["black", "cyan", "magenta", "yellow", "other"].map(x => <option key={x}>{x}</option>)}
          </select>
        </label>
        <label className="text-sm">Remaining (%) — if available
          <input className={fieldStyle} type="number" min="0" max="100" step="0.1" value={supply.percent_remaining}
            onChange={e => setSupply(v => ({ ...v, percent_remaining: e.target.value }))}/>
        </label>
        <button className={btn} disabled={busy}>Save supply reading</button>
      </form>}
      {tab === "cycle" && <form className="grid gap-3 md:grid-cols-4" onSubmit={(e: FormEvent) => {
        e.preventDefault(); execute(() => printerApi.openCycle(id, {
          ...cycle, start_meter: Number(cycle.start_meter),
          cost_amount: cycle.cost_amount ? Number(cycle.cost_amount) : null,
          declared_yield: cycle.declared_yield ? Number(cycle.declared_yield) : null,
          quantity_added_ml: cycle.quantity_added_ml ? Number(cycle.quantity_added_ml) : null,
        }), "Consumable installation/refill recorded. Close the cycle at replacement to calculate its observed print volume.");
      }}>
        {(["consumable_kind", "colour", "currency"] as const).map(k => <label key={k} className="text-sm">{k.replace("_", " ")}
          <select className={fieldStyle} value={cycle[k]} onChange={e => setCycle(v => ({ ...v, [k]: e.target.value }))}>
            {(k === "consumable_kind" ? ["toner", "cartridge", "ink_tank", "ink_bottle", "drum", "other"]
             : k === "colour" ? ["black", "cyan", "magenta", "yellow", "other"] : ["JMD", "USD"]).map(x => <option key={x}>{x}</option>)}
          </select>
        </label>)}
        {(["part_number", "start_meter", "cost_amount", "declared_yield", "quantity_added_ml"] as const).map(k =>
          <label className="text-sm" key={k}>{({ part_number: "Part number", start_meter: "Start meter (impressions)",
            cost_amount: "Consumable purchase cost", declared_yield: "Manufacturer yield (optional)",
            quantity_added_ml: "Refill quantity added (ml, optional)" })[k]}
            <input className={fieldStyle} required={k === "start_meter"} type={k === "part_number" ? "text" : "number"}
              min={k === "part_number" ? undefined : "0"} step={k === "quantity_added_ml" || k === "cost_amount" ? "0.01" : "1"}
              value={cycle[k]} onChange={e => setCycle(v => ({ ...v, [k]: e.target.value }))}/>
          </label>)}
        <button disabled={busy} className={btn}>Record new cartridge or refill</button>
        <p className="col-span-full text-xs text-slate-400">The recorded meter is total device impressions. A colour cartridge's actual colour-specific yield cannot be inferred solely from that total. Added refill volume is not automatically considered consumed.</p>
      </form>}
      {tab === "maintenance" && <form className="grid gap-3 md:grid-cols-3" onSubmit={(e: FormEvent) => {
        e.preventDefault(); execute(() => printerApi.maintenance(id, { ...service,
          cost_amount: service.cost_amount ? Number(service.cost_amount) : null }), "Maintenance record saved.");
      }}>
        <label className="text-sm">Component
          <input required className={fieldStyle} value={service.component} onChange={e => setService(v => ({ ...v, component: e.target.value }))}/>
        </label>
        <label className="text-sm">Event type
          <select className={fieldStyle} value={service.event_type} onChange={e => setService(v => ({ ...v, event_type: e.target.value }))}>
            {["fault", "inspection", "service", "replacement"].map(x => <option key={x}>{x}</option>)}
          </select>
        </label>
        <label className="text-sm">Severity
          <select className={fieldStyle} value={service.severity} onChange={e => setService(v => ({ ...v, severity: e.target.value }))}>
            {["info", "warning", "critical"].map(x => <option key={x}>{x}</option>)}
          </select>
        </label>
        <label className="text-sm md:col-span-2">Description / work performed
          <input required className={fieldStyle} value={service.description} onChange={e => setService(v => ({ ...v, description: e.target.value }))}/>
        </label>
        <label className="text-sm">Cost (JMD)
          <input className={fieldStyle} type="number" min="0" step="0.01" value={service.cost_amount}
            onChange={e => setService(v => ({ ...v, cost_amount: e.target.value }))}/>
        </label>
        <button className={btn} disabled={busy}>Save service record</button>
      </form>}
    </Card>
    {detail.data && <>
      <Card title="Meter readings and observed changes">
        {Object.entries(detail.data.meter_deltas).map(([k, v]) => <p key={k} className="text-sm">
          {k}: <b>{v.delta == null ? "No reliable delta" : `+${v.delta}`}</b> · {v.quality}
        </p>)}
        <div className="mt-2 max-h-52 overflow-auto text-sm">{detail.data.meters.map(m =>
          <p key={m.id} className="border-b border-slate-800 py-2">{m.meter_kind}: {m.counter_value.toLocaleString()} · {m.source} · {fmtTime(m.observed_at)}</p>)}
        {!detail.data.meters.length && <p className="text-slate-500">No meter readings yet.</p>}
        </div>
      </Card>
      <Card title="Consumables and stock-level observations">
        {detail.data.supplies.slice(0, 20).map(r =>
          <p key={r.id} className="border-b border-slate-800 py-2 text-sm">
            {r.supply_name} ({r.colour}): {r.percent_remaining == null ? "Not measurable" : `${r.percent_remaining}%`}
            {" "} {r.alert && <Badge value={r.alert.toLowerCase()} />} · {r.source} · {fmtTime(r.observed_at)}
          </p>)}
        {!detail.data.supplies.length && <p className="text-sm text-slate-500">No consumable levels have been entered.</p>}
      </Card>
      <Card title="Cartridge / ink-refill lifecycles and actual efficiency">
        {detail.data.cycles.map(c => <div key={c.id} className="mb-3 rounded border border-slate-800 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <b>{c.consumable_kind} · {c.colour}</b> <Badge value={c.status}/> {c.part_number ?? "No part number"}
          </div>
          <p className="mt-1">Start meter: {c.start_meter}, end meter: {c.end_meter ?? "Pending"}
            {" "} · Cost: {dollar(c.cost_amount, c.currency)}</p>
          <p className="mt-1">Recorded device impressions during cycle: <b>{c.analysis.impressions_during_cycle ?? "Not calculated"}</b></p>
          <p className="mt-1">Consumable cost / 1,000 impressions: <b>{dollar(c.analysis.cost_per_1000_impressions, c.currency)}</b></p>
          <p className="text-xs text-amber-300">{c.analysis.quality}</p>
          {c.analysis.warnings.map((w, i) => <p key={i} className="mt-1 text-xs text-slate-400">• {w}</p>)}
          {c.status === "open" && <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={e => {
            e.preventDefault();
            execute(() => printerApi.closeCycle(id, c.id, { end_meter: Number(finish[c.id]) }),
              "Lifecycle closed. Review the calculated yield and measurement-quality warnings.");
          }}>
            <label className="text-xs">Meter at replacement / next refill
              <input className={fieldStyle} type="number" min="0" step="1" required
                value={finish[c.id] ?? ""} onChange={e => setFinish(v => ({ ...v, [c.id]: e.target.value }))}/>
            </label>
            <button className={sub} disabled={busy}>Close cycle and calculate</button>
          </form>}
        </div>)}
        {!detail.data.cycles.length && <p className="text-sm text-slate-500">No cartridge/refill cycles recorded.</p>}
      </Card>
      <Card title="Faults, maintenance and service history">
        {detail.data.maintenance.map(m => <p key={m.id} className="border-b border-slate-800 py-2 text-sm">
          <Badge value={m.severity}/> {m.event_type} · {m.component}: {m.description} · {fmtTime(m.observed_at)}
        </p>)}
        {!detail.data.maintenance.length && <p className="text-sm text-slate-500">No service records yet.</p>}
      </Card>
    </>}
  </div>;
}
