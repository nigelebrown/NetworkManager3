/** Printer detail: Overview, Usage, Consumables, Faults & Maintenance, Raw SNMP Data, Configuration. */
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { prn, waitJob, type Job, type PrinterChange, type PrinterConfiguration } from "../services/printerSrs";
import { PrinterDetails as ManualRecords } from "./Printers";
import { PrinterNav, UtilizationChart } from "./PrinterSuite";

const TABS = [["overview", "Overview"], ["usage", "Usage"], ["consumables", "Consumables"], ["faults", "Faults & maintenance"],
  ["raw", "Raw SNMP data"], ["config", "Configuration"]] as const;
const admin = () => getSession()?.role === "admin";
const capTone = (s: string) => s === "SUPPORTED" ? "good" : s === "UNVERIFIED" ? "fair" : s === "INVALID" ? "poor" : "none";
const tone = (s: string | null | undefined) => s === "HEALTHY" || s === "REACHABLE" || s === "AVAILABLE" || s === "VERIFIED" ? "good"
  : s === "DEGRADED" || s === "STALE" ? "fair" : s === "CRITICAL" || s === "UNREACHABLE" || s === "FAILED" || s === "CONFLICT" ? "poor" : "none";

function Box({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return <section className="inm-card"><div className="inm-cardhead"><h2>{title}</h2>{right}</div><div className="inm-cardbody">{children}</div></section>;
}
function Rows({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  return <div className="inm-tablewrap"><table className="inm-table"><thead><tr>{head.map(h => <th key={h}>{h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, k) => <td key={k}>{c}</td>)}</tr>)}</tbody></table>
    {!rows.length && <div className="inm-empty">{empty}</div>}</div>;
}

export default function PrinterDevice() {
  const { id = "" } = useParams<{ id: string }>();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "overview";
  const d = useLoad(() => prn.detail(id), [id], 20000, ["printers"]);
  const dev = d.data?.device;
  return <div className="space-y-4">
    <Link className="inm-link text-sm" to="/printers/inventory">← Printer inventory</Link>
    <PageHeader eyebrow="Sites & inventory · Printers" title={dev ? `${dev.hostname || dev.serial_number} — ${dev.manufacturer} ${dev.model}` : "Printer"}
      subtitle="Printer health, monitoring, and verified device configuration." />
    <PrinterNav />
    <ErrorNote error={d.error} />
    <div role="tablist" className="inm-actionbar">{TABS.map(([k, label]) => <button key={k} role="tab" aria-selected={tab === k}
      className={tab === k ? "inm-btn-primary" : "inm-btn-secondary"} onClick={() => setSp({ tab: k })}>{label}</button>)}</div>
    {d.data && tab === "overview" && <Overview d={d.data} reload={d.reload} />}
    {tab === "usage" && <Box title="Usage"><UtilizationChart printerId={id} /></Box>}
    {tab === "consumables" && <Consumables id={id} />}
    {tab === "faults" && <Faults id={id} />}
    {tab === "raw" && <Raw id={id} />}
    {d.data && tab === "config" && <Config id={id} d={d.data} reload={d.reload} />}
  </div>;
}

function Overview({ d, reload }: { d: any; reload: () => void }) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const m = d.monitoring, i = d.identity, dev = d.device;
  const caps = useLoad(() => prn.capabilities(dev.id), [dev.id], 60000, ["printers"]);
  const act = useAction();
  const [note, setNote] = useState("");
  return <div className="space-y-4">
    <section className="inm-status-overview">
      <div className="inm-kpi"><span className="inm-kpi-label">Health</span><span className="inm-kpi-value">{m?.health?.score ?? "—"}</span>
        <span className="inm-kpi-sub"><Pill value={m?.health?.status ?? "UNKNOWN"} tone={tone(m?.health?.status)} /></span></div>
      <div className="inm-kpi"><span className="inm-kpi-label">Reachability</span><span className="inm-kpi-value text-base"><Pill value={m?.reachability ?? "UNKNOWN"} tone={tone(m?.reachability)} /></span>
        <span className="inm-kpi-sub">SNMP <Pill value={m?.snmp_state ?? "UNKNOWN"} tone={tone(m?.snmp_state)} /></span></div>
      <div className="inm-kpi"><span className="inm-kpi-label">Device state</span><span className="inm-kpi-value text-base">{m?.operational_state ?? "UNKNOWN"}</span>
        <span className="inm-kpi-sub">last success {ago(m?.last_success_at)}</span></div>
      <div className="inm-kpi"><span className="inm-kpi-label">Identity</span><span className="inm-kpi-value text-base"><Pill value={i?.identity_state ?? "UNVERIFIED"} tone={tone(i?.identity_state)} /></span>
        <span className="inm-kpi-sub">confidence {m?.discovery_confidence ?? "UNKNOWN"}</span></div>
    </section>
    <div className="grid gap-4 lg:grid-cols-2">
      <Box title="Verified identity">
        <dl className="grid grid-cols-2 gap-1 text-sm">
          <dt>Registered</dt><dd>{dev.manufacturer} {dev.model} · {dev.serial_number}</dd>
          <dt>Reported model</dt><dd>{i?.reported_model ?? "—"}</dd><dt>Reported serial</dt><dd>{i?.reported_serial ?? "—"}</dd>
          <dt>Firmware</dt><dd>{dev.firmware ?? "—"}</dd><dt>sysObjectID</dt><dd><code>{i?.system_object_id ?? "—"}</code></dd>
          <dt>Management IP</dt><dd>{dev.management_ip ?? "—"}</dd><dt>Site</dt><dd>{dev.site ?? "Unassigned"}</dd>
          <dt>Segment</dt><dd>{[i?.technology, i?.is_colour == null ? "colour ?" : i.is_colour ? "colour" : "mono", i?.is_multifunction ? "MFP" : i?.is_multifunction === false ? "print-only" : "MFP ?"].filter(Boolean).join(" · ")}</dd>
          <dt>Original import</dt><dd className="text-xs">{i?.original_import_text ?? "—"}</dd>
        </dl>
        {(d.identity_changes ?? []).filter((c: any) => c.decision === "NEEDS_REVIEW").map((c: any) => <div key={c.id} className="mt-2 rounded border border-amber-300 p-2 text-sm">  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
          <b>{c.field}</b>: {c.old_value} → {c.new_value}<div className="text-xs">{c.reason}</div>
          {admin() && <div className="mt-1 flex flex-wrap gap-1"><input aria-label="Identity decision note" placeholder="note" value={note} onChange={e => setNote(e.target.value)} />
            {(c.field === "serial_number" ? ["SEPARATE_DEVICE", "KEEP_REGISTERED"] : ["APPLY", "KEEP_REGISTERED"]).map(x =>
              <button key={x} className={btn2} disabled={note.length < 3 || act.busy} onClick={() => act.run(async () => { await prn.decideIdentity(dev.id, c.id, x, note); reload(); return "Decision recorded."; })}>{x.replace("_", " ").toLowerCase()}</button>)}</div>}
        </div>)}
        <Notice error={act.error} message={act.message} />
      </Box>
      <Box title="Capability matrix">
        <Rows head={["Capability", "State", "Evidence"]} rows={(caps.data?.capabilities ?? []).map(c => [c.capability.replace(/_/g, " "),
          <Pill value={c.state} tone={capTone(c.state)} />, <span className="text-xs">{c.evidence}</span>])} empty="No capability evidence yet." />
      </Box>
    </div>
    {m?.health?.components && <Box title="Health evidence"><Rows head={["Component", "Weight", "Score", "Evidence"]}
      rows={m.health.components.map((c: any) => [c.component, c.weight, c.score ?? "unknown", c.evidence])} empty="" /></Box>}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
    <details className="inm-card"><summary className="cursor-pointer p-3 text-sm font-semibold">Manual readings, consumable cycles and maintenance entry</summary>
      <div className="p-3"><ManualRecords /></div></details>
  </div>;
}

function Consumables({ id }: { id: string }) {
  const c = useLoad(() => prn.consumables(id), [id], 30000, ["printers"]);
  const act = useAction();
  const [cost, setCost] = useState<Record<number, string>>({});
  return <div className="space-y-4"><ErrorNote error={c.error} /><Notice error={act.error} message={act.message} />
    <Box title="Current supplies & components"><Rows head={["Index", "Type", "Colour", "Description", "Part", "Level", "State", "Status", "Installed", "Observed"]}
      rows={(c.data?.current ?? []).map(s => [s.supply_index, s.supply_type, s.colour, s.description ?? "—", s.part_number ?? "—",
        s.percent_remaining == null ? (s.level == null ? "—" : `${s.level} ${s.unit ?? ""}`) : `${s.percent_remaining}%`, s.level_state,
        <Pill value={s.status} tone={s.status === "OK" ? "good" : s.status === "UNKNOWN" ? "none" : "poor"} />,
        s.installed_at ? `${new Date(s.installed_at).toLocaleDateString()} @ ${s.installed_meter ?? "?"}` : "—", ago(s.last_observed_at)])}
      empty="No supply table reported (or not yet polled)." /></Box>
    <Box title="Forecast"><Rows head={["Supply", "Status", "Days to empty", "Range", "Expected", "Confidence"]}
      rows={(c.data?.forecasts ?? []).map(f => [f.supply, f.status, f.days_to_empty ?? "—",
        f.days_to_empty_low != null ? `${f.days_to_empty_low}–${f.days_to_empty_high ?? "∞"}` : "—", f.expected_empty_date ?? "—", f.confidence ?? f.reason ?? "—"])} empty="" /></Box>
    <Box title="Supply events & replacements"><Rows head={["When", "Supply", "Event", "Before → after", "Meter", "Cost", "Status", ""]}
      rows={(c.data?.events ?? []).map(e => [new Date(e.observed_at).toLocaleString(), `${e.supply_type} ${e.colour}`, e.event_type,
        `${e.level_before ?? "?"} → ${e.level_after ?? "?"}`, e.meter_value ?? "—", e.cost_amount == null ? "—" : `${e.currency} ${e.cost_amount}`,
        <Pill value={e.status} tone={e.status === "CONFIRMED" ? "good" : e.status === "OPEN" ? "fair" : "none"} />,
        admin() && e.event_type.startsWith("REPLACEMENT") && e.status !== "REJECTED" && <span className="flex gap-1">
          <input aria-label="Replacement cost" className="w-20" placeholder="cost" value={cost[e.id] ?? ""} onChange={x => setCost({ ...cost, [e.id]: x.target.value })} />
          <button className={btn2} onClick={() => act.run(async () => { await prn.decideSupply(id, e.id, { decision: "CONFIRM", ...(cost[e.id] ? { cost_amount: cost[e.id] } : {}) }); c.reload(); return "Replacement confirmed."; })}>Confirm</button>
          {e.status === "OPEN" && <button className={btn2} onClick={() => act.run(async () => { await prn.decideSupply(id, e.id, { decision: "REJECT" }); c.reload(); return "Rejected."; })}>Reject</button>}</span>])}
      empty="No supply events." /></Box>
    <Box title="Measured yields (between confirmed replacements)"><Rows head={["Supply", "Installed", "Removed", "Yield", "Cost", "Cost / 1,000"]}
      rows={(c.data?.measured_yields ?? []).map(y => [`${y.supply_type} ${y.colour}`, y.installed_at.slice(0, 10), y.removed_at.slice(0, 10), y.measured_yield,
        y.cost_amount == null ? "—" : `${y.currency} ${y.cost_amount}`, y.cost_per_1000 ?? "—"])} empty="Needs two confirmed replacements of the same supply." /></Box>
  </div>;
}

function Faults({ id }: { id: string }) {
  const [days, setDays] = useState(90);
  const f = useLoad(() => prn.faults(id, days), [id, days], 30000, ["printers"]);
  const act = useAction();
  const [m, setM] = useState({ component: "", event_type: "fault", description: "", cause_category: "MECHANICAL", reported_at: "", restored_at: "",
    technician: "", vendor: "", cost_amount: "", service_ticket_ref: "", evidence: "" });
  const r = f.data?.reliability;
  return <div className="space-y-4"><ErrorNote error={f.error} /><Notice error={act.error} message={act.message} />
    <div className="inm-actionbar"><select aria-label="Period" value={days} onChange={e => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map(x => <option key={x} value={x}>{x} days</option>)}</select>
      <span className="text-sm">Availability (7 d): <b>{f.data?.availability_7d == null ? "—" : `${Math.round(f.data.availability_7d * 1000) / 10}%`}</b></span>
      <span className="text-sm">MTBF <b>{r?.mtbf_hours ?? "—"}</b> h · MTTR <b>{r?.mttr_hours ?? "—"}</b> h · qualifying failures <b>{r?.qualifying_failures ?? 0}</b></span></div>
    <Box title="Faults"><Rows head={["Started", "Code", "Category", "Severity", "Duration (min)", "Active", "Recurrences", "Service ticket"]}
      rows={(f.data?.faults ?? []).map(x => [new Date(x.started_at).toLocaleString(), x.code, x.category, <Pill value={x.severity} tone={x.severity === "critical" ? "poor" : "fair"} />,
        x.duration_minutes, x.active ? "yes" : "no", x.recurrences_in_period, x.service_ticket_ref ?? "—"])} empty="No faults in the period." /></Box>
    <Box title="State history"><Rows head={["Dimension", "State", "From", "To", "Minutes"]} rows={(f.data?.state_history ?? []).slice(0, 200).map(s => [s.dimension, s.state,
      new Date(s.started_at).toLocaleString(), s.ended_at ? new Date(s.ended_at).toLocaleString() : "now", s.duration_minutes])} empty="No state history." /></Box>
    <Box title="Maintenance records"><Rows head={["When", "Component", "Cause", "Qualifying", "Restored", "Technician", "Cost", "Ticket"]}
      rows={(f.data?.maintenance ?? []).map(x => [new Date(x.observed_at).toLocaleString(), x.component, x.cause_category, x.qualifying_failure ? "yes" : "no",
        x.restored_at ? new Date(x.restored_at).toLocaleString() : "—", x.technician ?? "—", x.cost_amount == null ? "—" : `${x.currency} ${x.cost_amount}`, x.service_ticket_ref ?? "—"])} empty="No records." />
      {admin() && <div className="mt-3 grid gap-2 md:grid-cols-4 text-sm">
        {(["component", "description", "technician", "vendor", "cost_amount", "service_ticket_ref", "evidence"] as const).map(k =>
          <label key={k} className="capitalize">{k.replace(/_/g, " ")}<input className={inputCls} value={m[k]} onChange={e => setM({ ...m, [k]: e.target.value })} /></label>)}
        <label>Cause<select className={inputCls} value={m.cause_category} onChange={e => setM({ ...m, cause_category: e.target.value })}>
          {["MECHANICAL", "CONSUMABLE", "WAN", "POWER", "USER", "UNKNOWN"].map(c => <option key={c}>{c}</option>)}</select></label>
        <label>Reported<input type="datetime-local" className={inputCls} value={m.reported_at} onChange={e => setM({ ...m, reported_at: e.target.value })} /></label>
        <label>Restored<input type="datetime-local" className={inputCls} value={m.restored_at} onChange={e => setM({ ...m, restored_at: e.target.value })} /></label>
        <button className={btn} disabled={act.busy || !m.component || !m.description} onClick={() => act.run(async () => {
          const body: Record<string, unknown> = { component: m.component, event_type: m.event_type, description: m.description, cause_category: m.cause_category };
          for (const k of ["technician", "vendor", "service_ticket_ref", "evidence"] as const) if (m[k]) body[k] = m[k];
          if (m.cost_amount) body.cost_amount = m.cost_amount;
          if (m.reported_at) body.reported_at = new Date(m.reported_at).toISOString();
          if (m.restored_at) body.restored_at = new Date(m.restored_at).toISOString();
          const r2 = await prn.addMaintenance(id, body); f.reload(); return `Recorded; ${r2.linked_faults} device fault period(s) linked.`; })}>Add record</button></div>}
    </Box>
  </div>;
}

function Raw({ id }: { id: string }) {
  const [metric, setMetric] = useState("");
  const [state, setState] = useState("");
  const s = useLoad(() => prn.metrics(id, { metric: metric || undefined, state: state || undefined }), [id, metric, state], 30000, ["printers"]);
  const act = useAction();
  const [walk, setWalk] = useState<Job | null>(null);
  const [root, setRoot] = useState("printer-mib");
  return <div className="space-y-4"><Notice error={act.error || s.error} message={act.message} />
    <Box title="Samples with provenance" right={<div className="inm-actionbar">
      <input aria-label="Metric filter" placeholder="metric key" value={metric} onChange={e => setMetric(e.target.value)} />
      <select aria-label="Value state" value={state} onChange={e => setState(e.target.value)}><option value="">Any state</option>
        {["VALID", "UNSUPPORTED", "MISSING", "UNKNOWN_SPECIAL", "INVALID", "INCONSISTENT"].map(x => <option key={x}>{x}</option>)}</select></div>}>
      <Rows head={["Observed", "Metric", "Instance", "OID", "Raw", "Normalised", "State", "Interpretation", "Source / profile"]}
        rows={(s.data?.samples ?? []).map(x => [new Date(x.observed_at).toLocaleString(), x.metric_key, x.instance || "—", <code className="text-[10px]">{x.oid}</code>,
          x.raw_value ?? "—", x.normalized_value ?? x.text_value ?? "—", <Pill value={x.value_state} tone={x.value_state === "VALID" ? "good" : "none"} />,
          x.interpretation ?? "—", `${x.source} · ${x.profile ?? "?"} v${x.profile_version ?? "?"}`])} empty="No samples in the last 7 days." />
    </Box>
    {admin() && <Box title="Bounded diagnostic walk (read-only GETNEXT)">
      <div className="inm-actionbar"><select aria-label="MIB root" value={root} onChange={e => setRoot(e.target.value)}>
        <option value="system">system (1.3.6.1.2.1.1)</option><option value="host-resources-printer">Host Resources printer (1.3.6.1.2.1.25.3)</option>
        <option value="printer-mib">Printer-MIB (1.3.6.1.2.1.43)</option></select>
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { const j = await waitJob((await prn.walk(id, root, 1000)).id); setWalk(j);
          return `${j.status}: ${j.result?.count ?? 0} rows.`; })}>Run walk</button></div>
      {walk?.result?.rows && <Rows head={["OID", "Value"]} rows={walk.result.rows.map((r: { oid: string; value: string }) => [<code className="text-[10px]">{r.oid}</code>, r.value])} empty="" />}
    </Box>}
  </div>;
}

function Config({ id, d, reload }: { id: string; d: any; reload: () => void }) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const creds = useLoad(prn.credentialProfiles, [], 0);
  const profiles = useLoad(prn.profiles, [], 0);
  const models = useLoad(prn.models, [], 0);
  const m = d.monitoring, i = d.identity ?? {};
  const [v, setV] = useState({ enabled: !!m?.enabled, credential_profile_id: m?.credential_profile_id ?? "", interval_seconds: m?.interval_seconds ?? 900,
    mib_profile_id: m?.profile_selection === "MANUAL" ? m?.mib_profile?.id ?? "" : "" });
  const [o, setO] = useState({ site_id: i.site_id ?? "", location_ref: i.location_ref ?? "", division_ref: i.division_ref ?? "", section_ref: i.section_ref ?? "",
    unit_ref: i.unit_ref ?? "", model_id: i.model_id ?? "" });
  const act = useAction();
  const ro = !admin();
  return <div className="space-y-4"><Notice error={act.error} message={act.message} />
    {admin() && <DeviceConfiguration id={id} />}
    <Box title="Monitoring (read-only SNMP)">
      <div className="grid gap-2 md:grid-cols-4 text-sm">
        <label><input type="checkbox" disabled={ro} checked={v.enabled} onChange={e => setV({ ...v, enabled: e.target.checked })} /> Monitoring enabled</label>
        <label>Credential profile<select disabled={ro} className={inputCls} value={v.credential_profile_id} onChange={e => setV({ ...v, credential_profile_id: e.target.value })}>
          <option value="">—</option>{(creds.data ?? []).map(c => <option key={c.id} value={c.id}>{c.name} (v{c.version})</option>)}</select></label>
        <label>Interval (s)<input disabled={ro} type="number" min={300} className={inputCls} value={v.interval_seconds} onChange={e => setV({ ...v, interval_seconds: Number(e.target.value) })} /></label>
        <label>Polling profile<select disabled={ro} className={inputCls} value={v.mib_profile_id} onChange={e => setV({ ...v, mib_profile_id: e.target.value })}>
          <option value="">Automatic ({m?.mib_profile?.key ?? "not yet selected"})</option>{(profiles.data ?? []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      </div>
      {!ro && <div className="mt-2 flex gap-2">
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { const r = await prn.setMonitoring(id, { ...v, credential_profile_id: v.credential_profile_id || null,
          mib_profile_id: v.mib_profile_id || null }); reload(); return ["Saved.", ...r.warnings].join(" "); })}>Save monitoring</button>
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const j = await waitJob((await prn.poll(id)).id); reload();
          return j.status === "SUCCEEDED" ? "Read-only poll completed." : `Poll ${j.status.toLowerCase()}: ${JSON.stringify(j.errors)}`; })}>Poll now</button></div>}
      <dl className="mt-3 grid grid-cols-2 gap-1 text-xs md:grid-cols-4">
        <dt>Next poll</dt><dd>{m?.next_poll_at ? new Date(m.next_poll_at).toLocaleString() : "—"}</dd><dt>Back-off until</dt><dd>{m?.backoff_until ?? "—"}</dd>
        <dt>Last error</dt><dd>{m?.last_error ?? "—"}</dd><dt>Polls</dt><dd>{m?.polls?.successful ?? 0}/{m?.polls?.total ?? 0}</dd>
        <dt>Approved by</dt><dd>{m?.approved_by ?? "—"}</dd><dt>Unsupported OIDs</dt><dd>{(m?.unsupported_oids ?? []).length}</dd>
      </dl>
      <details className="mt-2 text-xs"><summary>Per-metric freshness</summary><ul>{Object.entries(m?.metric_freshness ?? {}).map(([k, t]) => <li key={k}>{k}: {ago(String(t))}</li>)}</ul></details>
    </Box>
    <Box title="Organisation & model">
      <div className="grid gap-2 md:grid-cols-3 text-sm">
        <label>Site id<input disabled={ro} className={inputCls} value={o.site_id} onChange={e => setO({ ...o, site_id: e.target.value })} /></label>
        {(["location_ref", "division_ref", "section_ref", "unit_ref"] as const).map(k => <label key={k} className="capitalize">{k.replace("_ref", "")}
          <input disabled={ro} className={inputCls} value={o[k]} onChange={e => setO({ ...o, [k]: e.target.value })} /></label>)}
        <label>Catalogue model<select disabled={ro} className={inputCls} value={o.model_id} onChange={e => setO({ ...o, model_id: e.target.value })}>
          <option value="">—</option>{(models.data ?? []).map(x => <option key={x.id} value={x.id}>{x.manufacturer} {x.model} ({x.verification})</option>)}</select></label>
      </div>
      {!ro && <button className={`${btn} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
        await prn.organization(id, { site_id: o.site_id === "" ? null : Number(o.site_id), location_ref: o.location_ref || null, division_ref: o.division_ref || null,
          section_ref: o.section_ref || null, unit_ref: o.unit_ref || null, model_id: o.model_id || null }); reload(); return "Saved."; })}>Save organisation</button>}
      <p className="mt-2 text-xs text-slate-500">Organisation fields update your inventory records. Device settings are managed in the separate configuration form above.</p>
    </Box>
  </div>;
}


function DeviceConfiguration({ id }: { id: string }) {
  const cfg = useLoad(() => prn.configuration(id), [id], 0);
  const [location, setLocation] = useState("");
  const [preview, setPreview] = useState<PrinterChange | null>(null);
  const [result, setResult] = useState<PrinterChange | null>(null);
  const act = useAction();
  const ready = !!cfg.data?.hardware_verified && !!cfg.data?.writes_enabled && cfg.data?.current_location != null;
  const finish = (r: PrinterChange) => {
    setResult(r); setPreview(null); cfg.reload();
    if (r.state === "APPLIED_VERIFIED") return "Printer location saved and verified.";
    if (r.state === "ROLLED_BACK") return "Original printer location restored and verified.";
    throw new Error(r.state === "FAILED_RESTORED" ? "The change failed. The original location is restored."
      : "The result could not be verified. Recovery is required; inspect the saved change below.");
  };
  return <Box title="Device configuration">
    <Notice error={act.error} message={act.message} />
    <ErrorNote error={cfg.error} />
    {cfg.data && <>
      {!cfg.data.hardware_verified && <p className="text-sm">Configuration is not yet verified for this printer. Monitoring remains available.</p>}
      {cfg.data.hardware_verified && <div className="space-y-3">
        <p className="text-sm"><Pill value="Hardware write test passed" tone="good" /> Printer location is available for this device.</p>
        {!cfg.data.writes_enabled && <p role="alert">Device writes are locked or emergency stop is active.</p>}
        {cfg.data.read_error && <p role="alert">{cfg.data.read_error}</p>}
        <dl className="text-sm"><dt>Current printer location</dt><dd>{cfg.data.current_location === "" ? "(empty)" : cfg.data.current_location ?? "Unavailable"}</dd></dl>
        <form className="space-y-2" onSubmit={e => { e.preventDefault(); act.run(async () => {
          const r = await prn.previewLocation(id, location); setPreview(r); setResult(null);
        }); }}>
          <label className="block text-sm">New printer location
            <input aria-label="New printer location" className={inputCls} value={location} maxLength={255} disabled={!ready || act.busy}
              onChange={e => { setLocation(e.target.value); setPreview(null); }} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button className={btn2} disabled={!ready || act.busy} type="submit">Preview change</button>
            <button className={btn2} disabled={act.busy} type="button" onClick={() => { setPreview(null); cfg.reload(); }}>Read current settings</button>
          </div>
        </form>
        {preview && <div className="rounded border p-3 text-sm" role="region" aria-label="Printer change preview">
          <p><b>Location:</b> {preview.before || "(empty)"} → {preview.after || "(empty)"}</p>
          <button className={btn + " mt-2"} disabled={act.busy || !ready} onClick={() => act.run(async () =>
            finish(await prn.applyConfiguration(id, preview.id)))}>Apply location change</button>
        </div>}
      </div>}
      {result && <div className="mt-3 text-sm" role="status">Result: <b>{result.state.replace(/_/g, " ")}</b></div>}
      {!!cfg.data.history.length && <div className="mt-4">
        <h3 className="mb-2 font-semibold">Configuration history</h3>
        <Rows head={["When", "Location change", "Result", "Action"]} empty="No device changes yet." rows={cfg.data.history.map(r => [
          new Date(r.created_at).toLocaleString(), <span>{r.before || "(empty)"} → {r.after || "(empty)"}</span>, r.state.replace(/_/g, " "),
          ["APPLIED_VERIFIED", "RECOVERY_REQUIRED"].includes(r.state) && <button className={btn2} disabled={act.busy || !cfg.data?.writes_enabled}
            onClick={() => act.run(async () => finish(await prn.rollbackConfiguration(id, r.id)))}>Restore original location</button>,
        ])} />
      </div>}
      {cfg.data.settings && <OtherSettings id={id} cfg={cfg.data} reload={cfg.reload} />}
      <p className="mt-3 text-xs text-slate-500">Network, security and firmware controls are not offered on printers. Each setting needs its own hardware write-and-restore test on this printer and firmware.</p>
    </>}
  </Box>;
}


const SETTING_LABEL: Record<string, string> = { "system.location": "Location", "system.contact": "Administrative contact",
  "system.name": "Device name", "entity.asset_id": "Asset ID", "printer.name": "Printer name",
  "printer.default_input": "Default input tray (paper)", "printer.default_media_path": "Default media path (duplex)",
  "printer.energy_saving": "Energy saving" };

function OtherSettings({ id, cfg, reload }: { id: string; cfg: PrinterConfiguration; reload: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<PrinterChange | null>(null);
  const act = useAction();
  const admin = getSession()?.role === "admin";
  const entries = Object.entries(cfg.settings ?? {}).filter(([k]) => k !== "system.location");
  return <div className="mt-4 border-t pt-3">
    <h3 className="mb-2 font-semibold">Other device settings</h3>
    <Notice error={act.error} message={act.message} />
    <table className="inm-table"><thead><tr><th>Setting</th><th>Standard object</th><th>Status</th><th>Change</th></tr></thead>
      <tbody>{entries.map(([op, st]) => <tr key={op}><td>{SETTING_LABEL[op] ?? op}</td><td className="text-xs">{st.mib ?? "—"}</td>
        <td><Pill value={st.status.replace(/_/g, " ")} tone={st.status === "VERIFIED" ? "good" : st.status === "UNSUPPORTED" ? "none" : "fair"} />
          {st.reason && <div className="text-xs text-slate-500">{st.reason}</div>}</td>
        <td>{st.status === "VERIFIED" ? <span className="flex gap-1">
            <input aria-label={`New ${SETTING_LABEL[op] ?? op}`} className={inputCls} value={vals[op] ?? ""} onChange={e => setVals({ ...vals, [op]: e.target.value })} />
            <button className={btn2} disabled={act.busy || !cfg.writes_enabled || !(vals[op] ?? "").length} onClick={() => act.run(async () => {
              setPlan(await prn.previewSetting(id, op, st.type === "index" ? Number(vals[op]) : vals[op])); })}>Preview</button></span>
          : st.status === "NOT_VERIFIED" && admin ? <button className={btn2} disabled={act.busy || !cfg.writes_enabled} onClick={() => act.run(async () => {
              const r = await prn.hardwareTest(id, op); reload(); return `Hardware test ${r.status}.`; })}>Run hardware test (lab printer)</button> : null}</td></tr>)}</tbody></table>
    {plan && <div className="mt-2 rounded border p-3 text-sm" role="region" aria-label="Printer setting preview">
      <p><b>{SETTING_LABEL[plan.operation] ?? plan.operation}:</b> {plan.before || "(empty)"} → {plan.after || "(empty)"}</p>
      <button className={btn + " mt-2"} disabled={act.busy} onClick={() => act.run(async () => {
        const r = await prn.applyConfiguration(id, plan.id); setPlan(null); reload(); return `Result: ${r.state.replace(/_/g, " ")}`; })}>Apply change</button>
    </div>}
  </div>;
}
