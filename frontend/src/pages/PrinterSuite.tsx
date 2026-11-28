import type React from "react";
/**
 * Printer Intelligence workspaces (SRS navigation). Every figure comes from the API with its source,
 * coverage and confidence. Missing data is shown as missing — never as zero. Printers stay read-only.
 */
import { ReactNode, useEffect, useMemo, useState } from "react";
import { Link, NavLink } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, TT, ago, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { getSession, request } from "../services/api";
import { download, prn, waitJob, type Cohort, type Job, type MetricDef, type MibObject, type PrinterRow, type Rec } from "../services/printerSrs";
import { Printers as LegacyInventory } from "./Printers";

const NAV = [
  ["/printers", "Overview"], ["/printers/inventory", "Inventory"], ["/printers/live", "Live monitoring"],
  ["/printers/utilization", "Utilization"], ["/printers/consumables", "Consumables"], ["/printers/maintenance", "Maintenance"],
  ["/printers/models", "Model comparison"], ["/printers/recommendations", "Recommendations"], ["/printers/reports", "Reports"],
  ["/printers/purchasing", "Supply purchasing"], ["/printers/print-jobs", "Print jobs"], ["/printers/profiles", "SNMP & MIB profiles"],
];
const role = () => getSession()?.role ?? "viewer";
const isAdmin = () => role() === "admin";
const canOperateNow = () => role() === "admin" || role() === "operator";
const num = (v: number | null | undefined, d = 0) => v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: d });
const pct = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v * 100)}%`;
const capTone = (s: string) => s === "SUPPORTED" ? "good" : s === "UNVERIFIED" ? "fair" : s === "INVALID" ? "poor" : "none";
const healthTone = (s: string | null | undefined) => s === "HEALTHY" ? "good" : s === "DEGRADED" ? "fair" : s === "CRITICAL" ? "poor" : "none";

const GATED: Record<string, string> = { "/printers/purchasing": "printer_purchasing", "/printers/print-jobs": "print_jobs" };
let gatedCache: Record<string, boolean> | null = null;
export function PrinterNav() {
  const [enabled, setEnabled] = useState<Record<string, boolean> | null>(gatedCache);
  useEffect(() => {
    if (gatedCache) return;
    request<{ modules: { key: string; status: string }[] }>("/v1/system/features").then(r => {
      gatedCache = Object.fromEntries(r.modules.map(m => [m.key, m.status !== "DISABLED_PENDING_AUTHORIZATION"]));
      setEnabled(gatedCache);
    }).catch(() => setEnabled({}));
  }, []);
  return <nav className="inm-actionbar flex-wrap" aria-label="Printer workspaces">
    {NAV.filter(([to]) => !GATED[to] || enabled?.[GATED[to]]).map(([to, label]) => <NavLink key={to} to={to} end={to === "/printers"}
      className={({ isActive }) => isActive ? "inm-btn-primary" : "inm-btn-secondary"}>{label}</NavLink>)}
  </nav>;
}

function Shell({ title, subtitle, right, children }: { title: string; subtitle: string; right?: ReactNode; children: ReactNode }) {
  return <div className="space-y-4" data-printer-workspace={title}>
    <PageHeader eyebrow="Sites & inventory · Printers" title={title} subtitle={subtitle} right={right} />
    <PrinterNav />
    {children}
  </div>;
}

function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="inm-kpi"><span className="inm-kpi-label">{label}</span><span className="inm-kpi-value">{value}</span>
    {hint && <span className="inm-kpi-sub">{hint}</span>}</div>;
}

function Section({ title, right, children, footer }: { title: string; right?: ReactNode; children: ReactNode; footer?: string }) {
  return <section className="inm-card"><div className="inm-cardhead"><h2>{title}</h2>{right}</div>
    <div className="inm-cardbody">{children}</div>{footer && <div className="inm-cardfooter">{footer}</div>}</section>;
}

function Table({ head, rows, empty }: { head: string[]; rows: ReactNode[][]; empty: string }) {
  return <div className="inm-tablewrap"><table className="inm-table">
    <thead><tr>{head.map(h => <th key={h}>{h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, k) => <td key={k}>{c}</td>)}</tr>)}</tbody>
  </table>{!rows.length && <div className="inm-empty">{empty}</div>}</div>;
}

const PrinterLink = ({ p }: { p: { id: string; hostname?: string | null; serial_number?: string } }) =>
  <Link className="inm-link" to={`/printers/${p.id}`}>{p.hostname || p.serial_number || p.id.slice(0, 8)}</Link>;

// ---- Overview --------------------------------------------------------------------------------------
export function PrinterOverview() {
  const ov = useLoad(prn.overview, [], 30000, ["printers"]);
  const o = ov.data;
  const faults = o?.open_faults ?? [];
  return <Shell title="Printer fleet overview" subtitle="Read-only SNMP monitoring, measured utilization, supplies and faults across the fleet.">
    <ErrorNote error={ov.error} />
    <section className="inm-status-overview" aria-label="Fleet summary">
      <Kpi label="Printers" value={o?.total ?? "—"} hint={o ? `${o.monitored} monitored read-only` : "Awaiting fleet data"} />
      <Kpi label="Reachable" value={o?.reachable ?? "—"} hint={o ? `${o.unreachable} unreachable` : "Awaiting fleet data"} />
      <Kpi label="Median health" value={o?.health.median ?? "—"} hint={o ? `${o.health.with_evidence} with evidence` : "Awaiting fleet data"} />
      <Kpi label="Recommendations" value={o?.recommendations_pending ?? "—"} hint="awaiting review" />
    </section>
    <div className="grid gap-4 lg:grid-cols-3">
      <Section title="Health status">
        <ul className="space-y-1 text-sm">{Object.entries(o?.health.by_status ?? {}).map(([k, v]) =>
          <li key={k} className="flex justify-between"><Pill value={k} tone={healthTone(k)} /><b>{v}</b></li>)}</ul>
      </Section>
      <Section title="Open faults">
        <ul className="space-y-1 text-sm">{faults.map(f => <li key={f.category + f.severity} className="flex justify-between">
          <span>{f.category.toLowerCase()} <Pill value={f.severity} tone={f.severity === "critical" ? "poor" : "fair"} /></span><b>{f.count}</b></li>)}
          {!faults.length && <li className="text-slate-500">{o ? "No open device-reported faults." : "Fault status is awaiting fleet data."}</li>}</ul>
      </Section>
      <Section title="Collection" footer="Freshness = last successful poll within three polling intervals.">
        <ul className="space-y-1 text-sm">
          <li className="flex justify-between">Fresh<b>{o?.collection.fresh ?? "—"}</b></li>
          <li className="flex justify-between">Stale<b>{o?.collection.stale ?? "—"}</b></li>
          <li className="flex justify-between">Never polled<b>{o?.collection.never_polled ?? "—"}</b></li>
          <li className="flex justify-between">In back-off<b>{o?.collection.in_backoff ?? "—"}</b></li>
          <li className="flex justify-between">Due now<b>{o?.collection.queue_depth ?? "—"}</b></li>
          <li className="flex justify-between">Poll success<b>{pct(o?.collection.poll_success_rate)}</b></li>
        </ul>
      </Section>
    </div>
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Supplies (current)">
        <ul className="space-y-1 text-sm">{Object.entries(o?.supplies ?? {}).map(([k, v]) =>
          <li key={k} className="flex justify-between"><Pill value={k} tone={k === "OK" ? "good" : k === "UNKNOWN" ? "none" : "poor"} /><b>{v}</b></li>)}</ul>
      </Section>
      <Section title="Fleet by manufacturer">
        <ul className="space-y-1 text-sm">{Object.entries(o?.by_manufacturer ?? {}).map(([k, v]) =>
          <li key={k} className="flex justify-between">{k}<b>{v}</b></li>)}</ul>
      </Section>
    </div>
  </Shell>;
}

// ---- Inventory (registration, discovery jobs, CSV import) -------------------------------------------
export function PrinterInventory() {
  const [f, setF] = useState({ q: "", manufacturer: "", monitored: "", status: "" });
  const list = useLoad(() => prn.list({ ...f, monitored: f.monitored || undefined }), [f.q, f.manufacturer, f.monitored, f.status], 30000, ["printers"]);
  return <Shell title="Printer inventory" subtitle="Register, discover (durable jobs) and import printers. Discovery and import never enable monitoring without approval.">
    <ErrorNote error={list.error} />
    <Section title="Fleet inventory" right={<div className="inm-actionbar">
      <input aria-label="Search printers" placeholder="Search name, serial, model, IP" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} />
      <select aria-label="Monitoring" value={f.monitored} onChange={e => setF({ ...f, monitored: e.target.value })}>
        <option value="">All</option><option value="true">Monitored</option><option value="false">Not monitored</option></select>
      <select aria-label="Status" value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
        <option value="">Any status</option>{["up", "down", "fault", "unknown"].map(s => <option key={s}>{s}</option>)}</select></div>}>
      <Table head={["Printer", "Model", "Serial", "IP", "Site / location", "Monitoring", "Health", "Identity", "Last success"]}
        rows={(list.data ?? []).map(p => [<PrinterLink p={p} />, `${p.manufacturer} ${p.model}`, p.serial_number, p.management_ip ?? "—",
          p.site ?? ([p.station, p.building].filter(Boolean).join(" / ") || "Unassigned"),
          <Pill value={p.monitoring.enabled ? (p.monitoring.freshness ?? "ON") : "OFF"} tone={p.monitoring.freshness === "FRESH" ? "good" : p.monitoring.enabled ? "fair" : "none"} />,
          p.monitoring.health_score == null ? <Pill value={p.monitoring.health_status ?? "UNKNOWN"} tone="none" /> : <span>{p.monitoring.health_score} <Pill value={p.monitoring.health_status} tone={healthTone(p.monitoring.health_status)} /></span>,
          <Pill value={p.identity_state} tone={p.identity_state === "VERIFIED" ? "good" : p.identity_state === "CONFLICT" ? "poor" : "none"} />,
          ago(p.monitoring.last_success_at)])}
        empty="No printers match. Register, discover or import printers below." />
    </Section>
    {isAdmin() && <DiscoveryJobs onDone={list.reload} />}
    {isAdmin() && <CsvImport onDone={list.reload} />}
    <details className="inm-card"><summary className="cursor-pointer p-3 text-sm font-semibold">Manual registration, credential profiles and one-off SNMP checks</summary>
      <div className="p-3"><LegacyInventory /></div></details>
  </Shell>;
}

function DiscoveryJobs({ onDone }: { onDone: () => void }) {
  const recent = useLoad(prn.discoveryJobs, [], 0);
  const settings = useLoad(prn.settings, [], 0);
  const creds = useLoad(prn.credentialProfiles, [], 0);
  const [target, setTarget] = useState("");
  const [selectedCreds, setSelectedCreds] = useState<string[]>([]);
  const [plan, setPlan] = useState<{ addresses: number; batches: number; concurrency: number } | null>(null);
  const [jobId, setJobId] = useState(() => sessionStorage.getItem("printer-discovery-job") ?? "");
  const [queued, setQueued] = useState<Job | null>(null);
  const current = useLoad(() => jobId ? prn.job(jobId) : Promise.resolve(null), [jobId], 2000);
  const job = current.data?.id === jobId ? current.data : queued?.id === jobId ? queued : null;
  const setJob = (j: Job) => { setQueued(j); setJobId(j.id); sessionStorage.setItem("printer-discovery-job", j.id); };
  const [pick, setPick] = useState<Record<string, boolean>>({});
  // Approval selections belong to a single discovery job; never carry them across saved scans.
  useEffect(() => { setPick({}); }, [jobId]);
  const act = useAction();
  const body = () => {
    const t = target.trim();
    return { credential_profile_ids: selectedCreds, ...(t.includes("/") ? { management_range: t } : { management_ips: t.split(/[\s,;]+/).filter(Boolean) }) };
  };
  const start = () => act.run(async () => {
    const j = await prn.discover(body()); setJob(j); recent.reload();
    return `Discovery queued. Progress refreshes automatically and resumes when you reopen this page.${j.warnings?.length ? " " + j.warnings.join(" ") : ""}`;
  });
  useEffect(() => {
    if (!jobId && recent.data?.length) {
      setJobId(recent.data[0].id); sessionStorage.setItem("printer-discovery-job", recent.data[0].id);
    }
  }, [jobId, recent.data]);
  const blocked = (version: string) => version === "1" ? settings.data?.snmp_policy?.allow_v1 === false
    : version === "2c" ? settings.data?.snmp_policy?.allow_v2c === false : false;
  const found: any[] = job?.result?.printers ?? [];  // eslint-disable-line @typescript-eslint/no-explicit-any
  return <Section title="Discovery job" footer="Read-only GET/GETNEXT. SNMPv3 authPriv preferred; v2c only where policy allows; v1 off by default.">
    <div className="flex flex-wrap items-end gap-2">
      <fieldset className="w-full"><legend className="text-sm font-semibold">Credential profiles to try</legend>
        <div className="flex flex-wrap gap-3">{(creds.data ?? []).map(c => <label key={c.id} className="text-sm">
          <input type="checkbox" checked={selectedCreds.includes(c.id)} disabled={blocked(c.version)}
            onChange={e => { setSelectedCreds(e.target.checked ? [...selectedCreds, c.id] : selectedCreds.filter(id => id !== c.id)); setPlan(null); }} />
          {" "}{c.name} (SNMPv{c.version}){blocked(c.version) ? " — disabled by printer policy" : ""}
        </label>)}</div>
        {!creds.data?.length && <p className="text-sm">Create a saved SNMP credential profile before starting discovery (at the bottom of this page: “Manual registration, credential profiles and one-off SNMP checks” → Discover printers → Add a credential profile).</p>}
      </fieldset>
      <label className="grow text-sm">CIDR (/9–/32) or IP list<input className={inputCls} value={target} onChange={e => { setTarget(e.target.value); setPlan(null); }} placeholder="10.103.44.0/24 or 10.103.44.21, 10.103.44.22" /></label>
      <button className={btn2} disabled={act.busy || !selectedCreds.length || !target.trim()} onClick={() => act.run(async () => {
        const p = await prn.discoveryPlan(body()); setPlan(p); return "Plan validated. No scan has started.";
      })}>Preview scan plan</button>
      <button className={btn} disabled={act.busy || ["QUEUED","RUNNING"].includes(job?.status ?? "") || !selectedCreds.length || !target.trim()} onClick={start}>{act.busy || ["QUEUED","RUNNING"].includes(job?.status ?? "") ? `Scanning… ${job?.progress ?? 0}/${job?.total ?? "?"}` : "Start discovery"}</button>
      {job && ["QUEUED", "RUNNING"].includes(job.status) && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { setJob(await prn.controlDiscovery(job.id, "pause")); current.reload(); return "Pause requested; the current batch saves its progress."; })}>Pause scan</button>}
      {job && ["PAUSED", "CANCELLED", "FAILED"].includes(job.status) && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { setJob(await prn.controlDiscovery(job.id, "resume")); current.reload(); return "Discovery queued from its saved position."; })}>Resume scan</button>}
    </div>
    {plan && <p className="mt-3 text-sm">{plan.addresses.toLocaleString()} addresses · {plan.batches.toLocaleString()} batches · at most {plan.concurrency} simultaneous probes. Large scans can take days; pause and resume without keeping this page open.</p>}
    <ErrorNote error={creds.error || current.error || recent.error || settings.error} />
    <p className="mt-2 text-xs">Version access is controlled under <Link className="inm-link" to="/printers/profiles">SNMP &amp; MIB profiles</Link>.</p>
    {!!recent.data?.length && <label className="mt-3 block text-sm">Previous discoveries
      <select className={inputCls} value={jobId} onChange={e => { setJobId(e.target.value); sessionStorage.setItem("printer-discovery-job", e.target.value); }}>
        <option value="">Choose a saved scan…</option>
        {recent.data.map(j => <option key={j.id} value={j.id}>{j.target} · {j.status} · {j.found} found · {new Date(j.created_at).toLocaleString()}</option>)}
      </select>
    </label>}
    <Notice error={act.error} message={act.message} />
    {job && <p role="status" className="mt-2 text-sm">{job.status}: {job.progress}/{job.total} checked · {found.length} printers found</p>}
    {job?.status === "SUCCEEDED" && !found.length && <p className="mt-2 text-sm">No printers identified. Expand the per-address results below to see why.</p>}
    {job && found.length > 0 && <>
      <Table head={["", "IP", "Manufacturer", "Model (reported)", "Serial", "Confidence", "Profile", "Existing"]}
        rows={found.map(p => [<input type="checkbox" aria-label={`Select ${p.management_ip}`} checked={!!pick[p.management_ip]} disabled={!p.serial_number}
          onChange={e => setPick({ ...pick, [p.management_ip]: e.target.checked })} />, p.management_ip, p.manufacturer ?? "—",
          p.model ?? <span title={p.reported_model}>not recognised</span>, p.serial_number ?? "missing", <Pill value={p.confidence} tone={p.confidence === "HIGH" ? "good" : "fair"} />,
          (creds.data ?? []).find(c => c.id === p.credential_profile_id)?.name || p.profile, p.existing_printer_id ? "registered" : "new"])} empty="" />
      <button className={`${btn} mt-2`} disabled={act.busy || !found.some(p => p.serial_number && pick[p.management_ip])} onClick={() => act.run(async () => {
        const items = found.filter(p => p.serial_number && pick[p.management_ip]).map(p => ({ management_ip: p.management_ip, manufacturer: p.manufacturer || "Unknown",
          model: p.model || p.reported_model || "Unknown", serial_number: p.serial_number, hostname: p.hostname }));
        const r = await prn.approve(job.id, { items }); onDone(); setPick({});
        return `${r.created.length} registered, ${r.updated.length} updated; monitoring enabled (read-only).`;
      })}>Approve selected for monitoring</button></>}
    {job && (job.errors ?? []).length > 0 && <details className="mt-2 text-xs"><summary>Per-address results ({(job.errors ?? []).length})</summary>
      <ul>{(job.errors as { address?: string; error: string }[]).slice(0, 500).map((e, i) => <li key={i}>{e.address}: {e.error}</li>)}</ul></details>}
  </Section>;
}

function CsvImport({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<any | null>(null);  // eslint-disable-line @typescript-eslint/no-explicit-any
  const act = useAction();
  return <Section title="CSV import" footer="Columns: manufacturer, model, serial_number (required); management_ip, hostname, area, division, branch, station, section_unit, building, site. Original text is preserved; abbreviated models are not expanded.">
    <textarea aria-label="CSV text" className={`${inputCls} h-28 font-mono text-xs`} value={text} onChange={e => { setText(e.target.value); setPreview(null); }}
      placeholder={"manufacturer,model,serial_number,management_ip,station\nKyocera,ECOSYS MA5500ifx,R4S0001,10.103.44.21,HQ"} />
    <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={async e => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setPreview(null); } }} />
    <div className="mt-2 flex gap-2">
      <button className={btn2} disabled={!text.trim() || act.busy} onClick={() => act.run(async () => { const p = await prn.importPreview(text); setPreview(p); return `${p.valid} valid, ${p.errors} with errors of ${p.total}.`; })}>Preview & validate</button>
      {preview && <button className={btn} disabled={!preview.valid || act.busy} onClick={() => act.run(async () => {
        const r = await prn.importCommit(text, "all", false, ""); onDone(); setPreview(null); return `${r.imported.length} imported, ${r.skipped.length} skipped.`; })}>Import valid rows</button>}
    </div>
    <Notice error={act.error} message={act.message} />
    {preview && <Table head={["Row", "Action", "Manufacturer", "Model", "Serial", "IP", "Errors / warnings"]}
      rows={preview.rows.map((r: any) => [r.row, <Pill value={r.action} tone={r.action === "ERROR" ? "poor" : r.action === "CREATE" ? "good" : "info"} />,  // eslint-disable-line @typescript-eslint/no-explicit-any
        r.data.manufacturer, r.data.model, r.data.serial_number, r.data.management_ip || "—", [...r.errors, ...r.warnings].join("; ")])} empty="" />}
  </Section>;
}

// ---- Live monitoring --------------------------------------------------------------------------------
export function PrinterLive() {
  const live = useLoad(prn.live, [], 15000, ["printers"]);
  const ops = live.data?.operations;
  return <Shell title="Live monitoring" subtitle="Reachability, SNMP availability and device state are tracked separately. Missed polls never create values.">
    <ErrorNote error={live.error} />
    <section className="inm-status-overview">
      <Kpi label="Monitored" value={ops?.monitored ?? "—"} /><Kpi label="Due / queue" value={ops?.queue_depth ?? "—"} hint={`${ops?.leased ?? 0} leased by workers`} />
      <Kpi label="Fresh / stale" value={`${ops?.fresh ?? "—"} / ${ops?.stale ?? "—"}`} hint={`${ops?.never_polled ?? 0} never polled`} />
      <Kpi label="Poll success" value={pct(ops?.poll_success_rate)} hint={`${ops?.in_backoff ?? 0} in back-off`} />
    </section>
    <Section title="Monitored printers" footer={`Observed ${ago(live.data?.observed_at)}. Health is evidence-based; INSUFFICIENT_DATA means unknown, not healthy.`}>
      <Table head={["Printer", "Reachability", "SNMP", "State", "Active faults", "Supplies", "Health", "Last success", "Next poll"]}
        rows={(live.data?.printers ?? []).map(p => [<PrinterLink p={p} />,
          <Pill value={p.monitoring.reachability} tone={p.monitoring.reachability === "REACHABLE" ? "good" : p.monitoring.reachability === "UNREACHABLE" ? "poor" : "none"} />,
          <Pill value={p.monitoring.snmp_state} tone={p.monitoring.snmp_state === "AVAILABLE" ? "good" : "poor"} />, p.monitoring.operational_state,
          p.active_faults.length ? p.active_faults.map(f => <Pill key={f.code} value={f.code} tone={f.severity === "critical" ? "poor" : "fair"} />) : "none",
          <span className="flex flex-wrap gap-1">{p.supplies.map((s, i) => <Pill key={i} value={`${s.colour === "n/a" ? s.supply : s.colour} ${s.percent == null ? s.level_state.toLowerCase() : s.percent + "%"}`}
            tone={s.status === "OK" ? "good" : s.status === "UNKNOWN" ? "none" : "poor"} />)}</span>,
          p.monitoring.health_score ?? <Pill value={p.monitoring.health_status ?? "UNKNOWN"} tone="none" />,
          <span title={p.last_error ?? ""}>{ago(p.monitoring.last_success_at)}{p.consecutive_failures ? ` · ${p.consecutive_failures} failed` : ""}</span>,
          p.next_poll_at ? new Date(p.next_poll_at).toLocaleTimeString() : "—"])}
        empty="No printers are monitored yet. Approve discovered printers or enable monitoring on a printer's Configuration tab." />
    </Section>
    {!!ops?.worker_errors.length && <Section title="Collection errors">
      <Table head={["Printer", "Error", "Consecutive failures"]} rows={ops.worker_errors.map(e => [<PrinterLink p={{ id: e.printer_id }} />, e.error, e.consecutive_failures])} empty="" />
    </Section>}
  </Shell>;
}

// ---- Utilization -------------------------------------------------------------------------------------
export function UtilizationChart({ printerId }: { printerId: string }) {
  const [g, setG] = useState("day");
  const [meter, setMeter] = useState("");
  const u = useLoad(() => prn.utilization(printerId, { granularity: g, meter: meter || undefined }), [printerId, g, meter], 60000, ["printers"]);
  const d = u.data;
  const lg = d?.granularity ?? g;   // label from the response, not the pending selection
  const chart = (d?.buckets ?? []).map(b => ({ name: b.bucket_start.slice(0, lg === "hour" ? 16 : lg === "month" ? 7 : lg === "year" ? 4 : 10).replace("T", " "),
    volume: b.volume, coverage: Math.round(b.coverage * 100) }));
  return <div className="space-y-3">
    <div className="inm-actionbar">
      <select aria-label="Granularity" value={g} onChange={e => setG(e.target.value)}>{["hour", "day", "week", "month", "year"].map(x => <option key={x}>{x}</option>)}</select>
      <select aria-label="Meter" value={meter} onChange={e => setMeter(e.target.value)}><option value="">Preferred meter</option>
        {(d?.available_meters ?? []).map(m => <option key={m}>{m}</option>)}</select>
    </div>
    <ErrorNote error={u.error} />
    {d && !d.meter_kind && <div className="inm-empty">{d.reason}</div>}
    {d?.meter_kind && <>
      <div className="grid gap-3 md:grid-cols-4">
        <Kpi label={`Total (${d.meter_kind})`} value={num(d.summary.total)} hint={`${d.summary.covered_buckets}/${d.summary.buckets} buckets measured`} />
        <Kpi label="Coverage" value={pct(d.summary.mean_coverage)} hint={`confidence ${d.summary.confidence}`} />
        <Kpi label="Average / day" value={num(d.averages?.per_day, 1)} hint="covered time only" />
        <Kpi label="Average / month" value={num(d.averages?.per_month)} hint="covered time only" />
      </div>
      <div style={{ height: 240 }}><ResponsiveContainer><BarChart data={chart}>
        <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" fontSize={10} /><YAxis fontSize={10} />
        <Tooltip {...TT} formatter={(v, n) => [v == null ? "missing (no coverage)" : String(v), String(n)]} />
        <Bar dataKey="volume" fill="#357fdb" /></BarChart></ResponsiveContainer></div>
      <p className="text-xs text-slate-500">{d.attribution} Interval quality: {Object.entries(d.interval_quality).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}.
        Gaps are shown as missing, measured zero as 0.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Section title="Duty cycle"><div className="text-sm"><Pill value={d.insights.duty_cycle.status} tone={d.insights.duty_cycle.status === "WITHIN_RANGE" ? "good" : d.insights.duty_cycle.status.startsWith("SUSTAINED") ? "fair" : "none"} />
          {d.insights.duty_cycle.reason && <p className="mt-1 text-xs text-slate-500">{d.insights.duty_cycle.reason}</p>}
          {(d.insights.duty_cycle.monthly ?? []).map(m => <div key={m.month} className="text-xs">{m.month}: {num(m.volume)} ({m.recommended_percent}% of recommended)</div>)}</div></Section>
        <Section title="Peaks & anomalies (robust z ≥ 3.5)"><ul className="text-xs">{d.insights.anomalies.map(a => <li key={a.day}>{a.day.slice(0, 10)} {a.kind} {num(a.volume)} vs median {num(a.median)} (z {a.robust_z})</li>)}
          {!d.insights.anomalies.length && <li className="text-slate-500">None detected (needs ≥14 covered days).</li>}</ul></Section>
      </div>
    </>}
  </div>;
}

export function PrinterUtilization() {
  const list = useLoad(() => prn.list({ monitored: true }), [], 60000, ["printers"]);
  const [pid, setPid] = useState("");
  const chosen = pid || list.data?.[0]?.id || "";
  return <Shell title="Utilization" subtitle="Validated counter deltas by hour, day, week, month and year, with coverage and confidence.">
    <ErrorNote error={list.error} />
    <Section title="Printer" right={<select aria-label="Printer" value={chosen} onChange={e => setPid(e.target.value)}>
      {(list.data ?? []).map(p => <option key={p.id} value={p.id}>{p.hostname || p.serial_number} — {p.model}</option>)}</select>}>
      {chosen ? <UtilizationChart printerId={chosen} /> : <div className="inm-empty">No monitored printers.</div>}
    </Section>
  </Shell>;
}

// ---- Consumables -------------------------------------------------------------------------------------
export function PrinterConsumables() {
  const c = useLoad(() => fetchFleet("consumables"), [], 60000, ["printers"]);
  return <Shell title="Consumables" subtitle="Indexed supply levels (special values decoded, unknown is not empty), replacement detection and statistical forecasts.">
    <ErrorNote error={c.error} />
    <Section title="Supplies by printer" footer="Forecasts are least-squares trends of device-reported levels with a ~95% range. Replacements with an ambiguous rise need operator confirmation on the printer's Consumables tab.">
      <Table head={["Printer", "Model", "Supplies", "Nearest empty", "To confirm"]}
        rows={(c.data?.printers ?? []).map((p: any) => [<PrinterLink p={{ id: p.printer_id, hostname: p.printer }} />, p.model,  // eslint-disable-line @typescript-eslint/no-explicit-any
          <span className="flex flex-wrap gap-1">{p.supplies.map((s: any, i: number) => <Pill key={i} value={`${s.colour === "n/a" ? s.supply_type : s.colour} ${s.percent == null ? s.level_state.toLowerCase() : s.percent + "%"}`}  // eslint-disable-line @typescript-eslint/no-explicit-any
            tone={s.status === "OK" ? "good" : s.status === "UNKNOWN" ? "none" : "poor"} />)}</span>,
          p.nearest_empty ? `${p.nearest_empty.supply}: ~${p.nearest_empty.days_to_empty} d (${p.nearest_empty.days_to_empty_low ?? "?"}–${p.nearest_empty.days_to_empty_high ?? "?"})` : "insufficient evidence",
          p.replacements_to_confirm ? <Link className="inm-link" to={`/printers/${p.printer_id}?tab=consumables`}>{p.replacements_to_confirm} open</Link> : "—"])}
        empty="No supply data yet. Supplies are collected from prtMarkerSuppliesTable on monitored printers." />
    </Section>
  </Shell>;
}

const fetchFleet = (kind: string) => request<any>(`/printers/fleet/${kind}`);  // eslint-disable-line @typescript-eslint/no-explicit-any

// ---- Maintenance ---------------------------------------------------------------------------------------
export function PrinterMaintenance() {
  const m = useLoad(() => fetchFleet("maintenance"), [], 60000, ["printers"]);
  const [csv, setCsv] = useState("");
  const act = useAction();
  return <Shell title="Maintenance & reliability" subtitle="Service records, Service Desk import, fault correlation, MTBF and MTTR by model.">
    <ErrorNote error={m.error} />
    <Section title="Reliability by model" footer={m.data?.method}>
      <Table head={["Model", "Devices", "Operating h", "Qualifying failures", "Excluded (WAN/power/user)", "MTBF h", "MTTR h", "Per 100k impressions", "Status"]}
        rows={(m.data?.by_model ?? []).map((r: any) => [`${r.manufacturer} ${r.model}`, r.devices, num(r.operating_hours), r.qualifying_failures,  // eslint-disable-line @typescript-eslint/no-explicit-any
          r.excluded_non_mechanical, r.mtbf_hours ?? <span title={r.mtbf_note ?? ""}>—</span>, r.mttr_hours ?? "—", r.incidents_per_100k_impressions ?? "—",
          <Pill value={r.status} tone={r.status === "CALCULATED" ? "good" : "none"} />])} empty="No printers." />
    </Section>
    <Section title="Service records">
      <Table head={["When", "Printer", "Component", "Type", "Cause", "Qualifying", "Restored", "Technician / vendor", "Cost", "Ticket"]}
        rows={(m.data?.records ?? []).map((r: any) => [new Date(r.observed_at).toLocaleDateString(), <PrinterLink p={{ id: r.printer_id, hostname: r.printer }} />,  // eslint-disable-line @typescript-eslint/no-explicit-any
          <span>{r.component}<div className="text-[10px] text-slate-500">{r.description}</div></span>, r.incident_type ?? r.event_type, r.cause_category, r.qualifying_failure ? "yes" : "no", r.restored_at ? new Date(r.restored_at).toLocaleString() : "—",
          [r.technician, r.vendor].filter(Boolean).join(" / ") || "—", r.cost_amount == null ? "—" : `${r.currency} ${num(r.cost_amount, 2)}`, r.service_ticket_ref ?? "—"])}
        empty="No maintenance records in this period." />
    </Section>
    {isAdmin() && <Section title="Import Service Desk records" footer="Columns: ticket_ref, serial_number, reported_at, description (+ restored_at, incident_type, cause_category, technician, vendor, cost_amount, currency, parts, warranty, component). Tickets already imported are skipped.">
      <textarea aria-label="Service desk CSV" className={`${inputCls} h-24 font-mono text-xs`} value={csv} onChange={e => setCsv(e.target.value)} />
      <button className={`${btn} mt-2`} disabled={!csv.trim() || act.busy} onClick={() => act.run(async () => { const r = await prn.serviceDesk(csv); m.reload(); return `${r.created} imported, ${r.skipped.length} skipped.`; })}>Import</button>
      <Notice error={act.error} message={act.message} />
    </Section>}
  </Shell>;
}

// ---- Model comparison & catalogue ------------------------------------------------------------------------
export function PrinterModels() {
  const cmp = useLoad(() => prn.compare({}), [], 120000, ["printers"]);
  const models = useLoad(prn.models, [], 120000, ["printers"]);
  const act = useAction();
  const [edit, setEdit] = useState<Record<string, string>>({});
  return <Shell title="Model comparison" subtitle="Verified cohorts by workload band and segment, with sample size, completeness and confidence.">
    <ErrorNote error={cmp.error || models.error} />
    <Section title="Cohort benchmarking" footer={`${cmp.data?.method ?? ""} ${cmp.data?.disclaimer ?? ""}`}>
      <Table head={["Model", "Segment", "In service", "Median monthly / device", "% of recommended", "Median yield", "Cost / 1,000", "Incidents / 100k", "MTBF h", "MTTR h", "TCO / 1,000", "Evidence"]}
        rows={(cmp.data?.cohorts ?? []).map((c: Cohort) => [`${c.manufacturer} ${c.model}`,
          [c.segment.colour == null ? "?" : c.segment.colour ? "colour" : "mono", c.segment.multifunction == null ? "?" : c.segment.multifunction ? "MFP" : "print", c.segment.technology ?? "?"].join(" · "),
          c.in_service, num(c.median_monthly_volume_per_device), c.median_recommended_utilization_percent ?? "—", num(c.median_measured_yield),
          c.median_cost_per_1000 ?? "—", c.incidents_per_100k_impressions ?? "—", c.median_mtbf_hours ?? "—", c.median_mttr_hours ?? "—",
          c.tco_per_1000 ?? <span title={c.tco_status}>incomplete</span>,
          <span><Pill value={c.evidence} tone={c.evidence === "SUFFICIENT" ? "good" : "none"} /><div className="text-[10px] text-slate-500">{c.sample.devices} devices · {c.sample.device_months} device-months · {c.workload_band}</div></span>])}
        empty="No cohorts yet." />
    </Section>
    <Section title="Model catalogue" footer="Duty-cycle analysis uses only VERIFIED manufacturer figures.">
      <Table head={["Model", "Technology", "Colour", "MFP", "Recommended / month", "Max duty / month", "Verification", "In service", ""]}
        rows={(models.data ?? []).map(m => [`${m.manufacturer} ${m.model}`, m.technology, m.colour_capable == null ? "?" : m.colour_capable ? "yes" : "no",
          m.multifunction == null ? "?" : m.multifunction ? "yes" : "no",
          isAdmin() ? <input aria-label={`Recommended volume ${m.model}`} className="w-24" defaultValue={m.recommended_monthly_volume ?? ""} onChange={e => setEdit({ ...edit, [`${m.id}:rec`]: e.target.value })} /> : num(m.recommended_monthly_volume),
          isAdmin() ? <input aria-label={`Max duty ${m.model}`} className="w-24" defaultValue={m.max_monthly_duty_cycle ?? ""} onChange={e => setEdit({ ...edit, [`${m.id}:max`]: e.target.value })} /> : num(m.max_monthly_duty_cycle),
          <Pill value={m.verification} tone={m.verification === "VERIFIED" ? "good" : "none"} />, m.in_service,
          isAdmin() && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
            const rec = edit[`${m.id}:rec`], mx = edit[`${m.id}:max`];
            await prn.updateModel(m.id, { manufacturer: m.manufacturer, model: m.model, verification: "VERIFIED",
              ...(rec ? { recommended_monthly_volume: Number(rec) } : {}), ...(mx ? { max_monthly_duty_cycle: Number(mx) } : {}) });
            models.reload(); return `${m.model} figures saved as VERIFIED.`; })}>Save as verified</button>])}
        empty="Models are added automatically from registrations." />
      <Notice error={act.error} message={act.message} />
    </Section>
  </Shell>;
}

// ---- Recommendations ---------------------------------------------------------------------------------------
export function PrinterRecommendations() {
  const [status, setStatus] = useState("PROPOSED");
  const recs = useLoad(() => prn.recs(status || undefined), [status], 60000, ["printers"]);
  const act = useAction();
  const [note, setNote] = useState<Record<string, string>>({});
  const canReview = ["admin", "approver"].includes(role());
  return <Shell title="Recommendations" subtitle={recs.data?.policy ?? "Advisory recommendations for human review."}
    right={["admin", "operator"].includes(role()) && <button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
      const r = await prn.runRecs(); recs.reload(); return `${r.created} new, ${r.updated} refreshed, ${r.superseded} superseded.`; })}>Run analysis</button>}>
    <Notice error={act.error || recs.error} message={act.message} />
    <div className="inm-actionbar"><select aria-label="Status" value={status} onChange={e => setStatus(e.target.value)}>
      <option value="">All</option>{["PROPOSED", "UNDER_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED"].map(s => <option key={s}>{s}</option>)}</select>
      <span className="text-xs text-slate-500">Rules {recs.data?.rule_version}</span></div>
    <div className="space-y-3">{(recs.data?.items ?? []).map((r: Rec) => <section key={r.id} className="inm-card"><div className="inm-cardbody space-y-1 text-sm">
      <div className="flex flex-wrap items-center gap-2"><Pill value={r.code} tone="info" /><b>{r.title}</b><Pill value={r.confidence} tone={r.confidence === "HIGH" ? "good" : r.confidence === "INSUFFICIENT" ? "none" : "fair"} />
        <Pill value={r.status} tone={statusTone(r.status === "APPROVED" ? "PASS" : r.status === "REJECTED" ? "FAIL" : "WARN")} />
        {r.printer_id && <Link className="inm-link" to={`/printers/${r.printer_id}`}>printer</Link>}</div>
      <div className="text-xs text-slate-500">Reasons: {r.reason_codes.join(", ")} · period {r.period_start?.slice(0, 10)} → {r.period_end?.slice(0, 10)}</div>
      <div className="text-xs">Assumptions: {r.assumptions.join("; ")}</div>
      <details className="text-xs"><summary>Supporting data</summary><pre className="whitespace-pre-wrap">{JSON.stringify(r.evidence, null, 1)}</pre></details>
      {r.reviewed_by && <div className="text-xs">Decision by {r.reviewed_by}: {r.decision_note}</div>}
      {canReview && ["PROPOSED", "UNDER_REVIEW"].includes(r.status) && <div className="flex flex-wrap gap-2">
        <input aria-label="Decision note" className="grow" placeholder="Decision note (required)" value={note[r.id] ?? ""} onChange={e => setNote({ ...note, [r.id]: e.target.value })} />
        {["UNDER_REVIEW", "APPROVED", "REJECTED"].map(d => <button key={d} className={btn2} disabled={act.busy || (note[r.id] ?? "").length < 3}
          onClick={() => act.run(async () => { await prn.review(r.id, d, note[r.id]); recs.reload(); return `Recorded ${d.toLowerCase()}.`; })}>{d.replace("_", " ").toLowerCase()}</button>)}</div>}
    </div></section>)}
      {!recs.data?.items.length && <div className="inm-empty">No recommendations in this state.</div>}</div>
  </Shell>;
}

// ---- Reports ---------------------------------------------------------------------------------------------
export function PrinterReports() {
  const cat = useLoad(prn.reports, [], 30000, ["printers"]);
  const sch = useLoad(() => isAdmin() ? prn.schedules() : Promise.resolve([]), [], 60000, ["printers"]);
  const [r, setR] = useState({ report: "fleet_inventory", format: "csv", manufacturer: "", model: "", location: "", division: "",
    category: "", date_from: "", date_to: "", cadence: "monthly" });
  const act = useAction();
  const filters = useMemo(() => Object.fromEntries(Object.entries({ manufacturer: r.manufacturer, model: r.model, location: r.location,
    division: r.division, category: r.category, date_from: r.date_from, date_to: r.date_to }).filter(([, v]) => v)), [r]);
  return <Shell title="Reports" subtitle="Ten fleet reports with filters; CSV, Excel and PDF. Totals reconcile with validated intervals.">
    <Notice error={act.error || cat.error} message={act.message} />
    <Section title="Generate">
      <div className="grid gap-2 md:grid-cols-4">
        <label className="text-sm">Report<select className={inputCls} value={r.report} onChange={e => setR({ ...r, report: e.target.value })}>
          {(cat.data?.reports ?? []).map(x => <option key={x.key} value={x.key}>{x.title}</option>)}</select></label>
        <label className="text-sm">Format<select className={inputCls} value={r.format} onChange={e => setR({ ...r, format: e.target.value })}>
          <option value="csv">CSV</option><option value="xlsx">Excel</option><option value="pdf">PDF</option></select></label>
        {(["manufacturer", "model", "location", "division"] as const).map(k => <label key={k} className="text-sm capitalize">{k}
          <input className={inputCls} value={r[k]} onChange={e => setR({ ...r, [k]: e.target.value })} /></label>)}
        <label className="text-sm">Category<select className={inputCls} value={r.category} onChange={e => setR({ ...r, category: e.target.value })}>
          <option value="">Any</option><option value="colour">Colour</option><option value="mono">Mono</option><option value="multifunction">Multifunction</option><option value="print_only">Print-only</option></select></label>
        <label className="text-sm">From<input type="date" className={inputCls} value={r.date_from} onChange={e => setR({ ...r, date_from: e.target.value })} /></label>
        <label className="text-sm">To<input type="date" className={inputCls} value={r.date_to} onChange={e => setR({ ...r, date_to: e.target.value })} /></label>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
          const j = await waitJob((await prn.report({ report: r.report, format: r.format, filters })).id); cat.reload();
          if (j.status !== "SUCCEEDED" || !j.download) throw new Error(`Report ${j.status.toLowerCase()}: ${JSON.stringify(j.errors)}`);
          await download(j.download, j.artifact_name ?? "report"); return `${j.result?.rows ?? 0} rows · ${j.result?.meta?.period ?? ""}`; })}>Generate & download</button>
        {isAdmin() && <><select aria-label="Cadence" value={r.cadence} onChange={e => setR({ ...r, cadence: e.target.value })}>
          <option>daily</option><option>weekly</option><option>monthly</option></select>
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await prn.schedule({ report: r.report, format: r.format, filters, cadence: r.cadence, recipients: [] });
            sch.reload(); return "Schedule saved. Add notification channels for delivery in Alert notifications."; })}>Schedule</button></>}
      </div>
    </Section>
    <Section title="Recent report jobs">
      <Table head={["Requested", "Report", "Format", "Status", "Rows", "By", ""]} rows={(cat.data?.jobs ?? []).map(j => [ago(j.created_at), String(j.params.report),
        String(j.params.format), <Pill value={j.status} tone={statusTone(j.status === "SUCCEEDED" ? "PASS" : j.status === "FAILED" ? "FAIL" : "WARN")} />,
        j.result?.rows ?? "—", j.requested_by, j.download && <button className={btn2} onClick={() => download(j.download!, j.artifact_name ?? "report")}>Download</button>])} empty="No reports yet." />
    </Section>
    {isAdmin() && <Section title="Scheduled reports">
      <Table head={["Report", "Format", "Cadence", "Next run", "Last job", ""]} rows={(sch.data ?? []).map((s: any) => [s.report, s.format, s.cadence,  // eslint-disable-line @typescript-eslint/no-explicit-any
        new Date(s.next_run_at).toLocaleString(), s.last_job_id ? s.last_job_id.slice(0, 8) : "—",
        <button className={btn2} onClick={() => act.run(async () => { await prn.unschedule(s.id); sch.reload(); return "Schedule removed."; })}>Remove</button>])} empty="No schedules." />
    </Section>}
  </Shell>;
}

// ---- SNMP & MIB profiles ------------------------------------------------------------------------------------
export function PrinterProfiles() {
  const mibs = useLoad(prn.mibs, [], 60000, ["printers"]);
  const profiles = useLoad(prn.profiles, [], 60000, ["printers"]);
  const printers = useLoad(() => prn.list({}), [], 60000, ["printers"]);
  const settings = useLoad(() => isAdmin() ? prn.settings() : Promise.resolve(null), [], 0);
  const act = useAction();
  const [mod, setMod] = useState("");
  const [search, setSearch] = useState("");
  const [suggested, setSuggested] = useState(true);
  const objects = useLoad(() => mod ? prn.mibObjects(mod, { q: search || undefined, suggested_only: suggested || undefined }) : Promise.resolve([] as MibObject[]), [mod, search, suggested], 0);
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const [draft, setDraft] = useState<(MetricDef & { object: string; error?: string })[]>([]);
  const [meta, setMeta] = useState({ key: "", name: "", vendor: "", match_enterprise: "", priority: "50" });
  const [testResult, setTestResult] = useState<any | null>(null);  // eslint-disable-line @typescript-eslint/no-explicit-any
  const [testPrinter, setTestPrinter] = useState("");
  const chosenMod = mibs.data?.find(m => m.id === mod);
  return <Shell title="SNMP & MIB profiles" subtitle="Upload vendor MIB files, map their objects to standard metrics, test read-only against a printer, then activate. No code changes and no print drivers.">
    <Notice error={act.error || mibs.error || profiles.error} message={act.message} />
    <Section title="MIB library" right={isAdmin() && <label className={btn2}>Upload MIB<input hidden type="file" accept=".mib,.my,.txt,.smi" onChange={e => {
      const f = e.target.files?.[0]; if (f) act.run(async () => { const m = await prn.uploadMib(f); mibs.reload(); setMod(m.id);
        return `${m.module_name} v${m.version}: ${m.object_count} objects, ${m.status}${m.unresolved?.length ? ` — upload the MIB that defines ${m.unresolved.join(", ")}` : ""}.`; });
      e.target.value = ""; }} /></label>} footer="Files are parsed as text only (never compiled or executed). Imports resolve against standard roots and other uploaded modules.">
      <Table head={["Module", "Version", "Vendor", "Objects", "Status", "Uploaded", ""]} rows={(mibs.data ?? []).filter(m => m.active).map(m => [m.module_name, m.version,
        m.vendor ?? (m.enterprise ? `enterprise ${m.enterprise}` : "—"), m.object_count,
        <span><Pill value={m.status} tone={m.status === "RESOLVED" ? "good" : "fair"} />{m.unresolved?.length ? <div className="text-[10px]">needs: {m.unresolved.join(", ")}</div> : null}</span>,
        `${ago(m.uploaded_at)} by ${m.uploaded_by}`, <button className={btn2} onClick={() => { setMod(m.id); setSel({}); setDraft([]); }}>Browse</button>])}
        empty="No MIB files uploaded. Standard Printer-MIB, Host Resources and system MIBs are built in." />
    </Section>
    {mod && <Section title={`Objects in ${chosenMod?.module_name ?? ""}`} right={<div className="inm-actionbar">
      <input aria-label="Search objects" placeholder="name, OID or description" value={search} onChange={e => setSearch(e.target.value)} />
      <label className="text-xs"><input type="checkbox" checked={suggested} onChange={e => setSuggested(e.target.checked)} /> suggested metrics only</label></div>}>
      <Table head={["", "Object", "OID", "Kind", "Syntax", "Access", "Suggested metric", "Description"]} rows={(objects.data ?? []).map(o => [
        <input type="checkbox" aria-label={`Select ${o.name}`} disabled={!o.oid || !["scalar", "column"].includes(o.kind)} checked={!!sel[o.id]} onChange={e => setSel({ ...sel, [o.id]: e.target.checked })} />,
        o.name, <code>{o.oid ?? "unresolved"}</code>, o.kind, o.syntax ?? "—", o.access ?? "—", o.suggested_metric ?? "—", <span className="text-xs">{o.description.slice(0, 140)}</span>])}
        empty="No objects match." />
      {isAdmin() && <button className={`${btn} mt-2`} disabled={!Object.values(sel).some(Boolean) || act.busy} onClick={() => act.run(async () => {
        const d = await prn.draft(Object.entries(sel).filter(([, v]) => v).map(([k]) => Number(k))); setDraft(d.metrics);
        setMeta(m => ({ ...m, vendor: chosenMod?.vendor ?? m.vendor, match_enterprise: chosenMod?.enterprise ? String(chosenMod.enterprise) : m.match_enterprise }));
        return d.note; })}>Build profile from selection</button>}
    </Section>}
    {isAdmin() && draft.length > 0 && <Section title="New polling profile" footer="Vendor mappings stay VENDOR_UNVERIFIED (shown as UNVERIFIED capabilities) until confirmed on hardware.">
      <div className="grid gap-2 md:grid-cols-5">
        {(["key", "name", "vendor", "match_enterprise", "priority"] as const).map(k => <label key={k} className="text-sm">{k.replace("_", " ")}
          <input className={inputCls} value={meta[k]} onChange={e => setMeta({ ...meta, [k]: e.target.value })} /></label>)}</div>
      <Table head={["Object", "Metric key", "OID", "Access", "Type", "Verification"]} rows={draft.map((m, i) => m.error ? [m.object, <span className="text-red-600">{m.error}</span>, "", "", "", ""] : [m.object,
        <input aria-label={`Metric for ${m.object}`} value={m.metric_key} onChange={e => setDraft(draft.map((x, k) => k === i ? { ...x, metric_key: e.target.value } : x))} />,
        <code>{m.oid}</code>, m.access, m.value_type, m.verification])} empty="" />
      <button className={`${btn} mt-2`} disabled={act.busy || !meta.key || !meta.name} onClick={() => act.run(async () => {
        await prn.createProfile({ key: meta.key, name: meta.name, vendor: meta.vendor || null, priority: Number(meta.priority) || 50,
          match_enterprise: meta.match_enterprise ? Number(meta.match_enterprise) : null,
          metrics: draft.filter(m => !m.error).map(({ metric_key, oid, access, value_type, category, verification, description }) =>
            ({ metric_key, oid, access, value_type, category, verification, description: description?.slice(0, 250) })) });
        setDraft([]); setSel({}); profiles.reload(); return "Profile created. Printers whose sysObjectID matches will use it automatically on the next poll."; })}>Create profile</button>
    </Section>}
    <Section title="Polling profiles" footer="Automatic selection: matching enterprise / description, lowest priority number wins; generic Printer-MIB is the fallback.">
      <Table head={["Profile", "Vendor match", "Priority", "Metrics", "Version", "Printers", "State", ""]} rows={(profiles.data ?? []).map(p => [
        <span>{p.name}<div className="text-[10px] text-slate-500">{p.key}{p.builtin ? " · built-in" : ""}</div></span>,
        p.match_enterprise ? `enterprise ${p.match_enterprise}` : p.match_descr_regex ?? "any", p.priority,
        <details><summary>{p.metrics.length}</summary><ul className="text-[10px]">{p.metrics.map(m => <li key={m.id}>{m.metric_key} <code>{m.oid}</code> {m.verification !== "STANDARD" && <Pill value={m.verification} tone="fair" />}</li>)}</ul></details>,
        p.version, p.printers, <Pill value={p.enabled ? "ENABLED" : "DISABLED"} tone={p.enabled ? "good" : "none"} />,
        isAdmin() && <span className="flex gap-1">
          <button className={btn2} disabled={p.key === "rfc3805-generic"} onClick={() => act.run(async () => { await prn.toggleProfile(p.id, !p.enabled); profiles.reload(); })}>{p.enabled ? "Disable" : "Enable"}</button>
          <button className={btn2} disabled={!testPrinter} onClick={() => act.run(async () => { setTestResult(await prn.testProfile(p.id, testPrinter)); return `Tested ${p.key} (read-only).`; })}>Test</button></span>])}
        empty="" />
      {isAdmin() && <label className="mt-2 block text-sm">Test against printer<select className={inputCls} value={testPrinter} onChange={e => setTestPrinter(e.target.value)}>
        <option value="">Choose a printer…</option>{(printers.data ?? []).filter((p: PrinterRow) => p.management_ip).map(p => <option key={p.id} value={p.id}>{p.hostname || p.serial_number} ({p.management_ip})</option>)}</select></label>}
      {testResult && <div className="mt-2 text-xs"><b>{testResult.snmp_ok ? "SNMP OK" : `Failed: ${testResult.error}`}</b> · meters {JSON.stringify(testResult.meters)}
        <Table head={["Metric", "OID", "State", "Value", "Verification"]} rows={testResult.metrics.slice(0, 80).map((m: any) => [m.metric_key, <code>{m.oid}</code>, m.value_state, m.raw_value ?? "—", m.verification])} empty="" /></div>}  {/* eslint-disable-line @typescript-eslint/no-explicit-any */}
    </Section>
    {isAdmin() && settings.data && <SettingsCard s={settings.data} reload={settings.reload} />}
  </Shell>;
}

function SettingsCard({ s, reload }: { s: any; reload: () => void }) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const [v, setV] = useState({ raw_days: s.retention.raw_days, aggregate_years: s.retention.aggregate_years, allow_v2c: s.snmp_policy.allow_v2c,
    allow_v1: s.snmp_policy.allow_v1, window: s.operating_window ? `${s.operating_window.days.join(",")} ${s.operating_window.start}-${s.operating_window.end}` : "" });
  const act = useAction();
  return <Section title="Policy & retention" footer="Raw samples and intervals roll up into validated daily aggregates before deletion.">
    <div className="grid gap-2 md:grid-cols-5 text-sm">
      <label>Raw retention (days)<input type="number" className={inputCls} value={v.raw_days} onChange={e => setV({ ...v, raw_days: Number(e.target.value) })} /></label>
      <label>Aggregates (years)<input type="number" className={inputCls} value={v.aggregate_years} onChange={e => setV({ ...v, aggregate_years: Number(e.target.value) })} /></label>
      <label>Operating window<input className={inputCls} placeholder="0,1,2,3,4 08:00-17:00" value={v.window} onChange={e => setV({ ...v, window: e.target.value })} /></label>
      <label><input type="checkbox" checked={v.allow_v2c} onChange={e => setV({ ...v, allow_v2c: e.target.checked })} /> Allow SNMPv2c</label>
      <label><input type="checkbox" checked={v.allow_v1} onChange={e => setV({ ...v, allow_v1: e.target.checked })} /> Allow legacy SNMPv1</label>
    </div>
    <button className={`${btn} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
      let operating_window = null;
      const m = v.window.trim().match(/^([0-6](?:,[0-6])*)\s+(\d\d:\d\d)-(\d\d:\d\d)$/);
      if (v.window.trim() && !m) throw new Error("Operating window format: 0,1,2,3,4 08:00-17:00 (0 = Monday)");
      if (m) operating_window = { days: m[1].split(",").map(Number), start: m[2], end: m[3] };
      await prn.saveSettings({ raw_days: v.raw_days, aggregate_years: v.aggregate_years, allow_v2c: v.allow_v2c, allow_v1: v.allow_v1, operating_window });
      reload(); return "Saved."; })}>Save policy</button>
    <Notice error={act.error} message={act.message} />
  </Section>;
}

/* ---------------- rc17: internal supply purchase requests ---------------- */
type Purchase = { id: string; printer_id: string; printer: string | null; serial_number: string | null; supply_description: string;
  part_number: string | null; quantity: number; estimated_unit_cost: number | null; currency: string; reason: string; origin: string;
  status: string; requested_by: string; decided_by: string | null; decision_note: string | null; order_reference: string | null;
  created_at: string; evidence: Record<string, unknown> };
type PurchasePolicy = { automatic: boolean; lead_time_days: number; default_quantity: number };
const PP = "/printer-purchasing";
const jpost = (b: unknown) => ({ method: "POST", body: JSON.stringify(b) });

function PrinterPurchasingModule() {
  const [status, setStatus] = useState("");
  const rows = useLoad(() => request<Purchase[]>(`${PP}/requests${status ? `?status=${status}` : ""}`), [status], 30000, ["printers"]);
  const pol = useLoad(() => request<PurchasePolicy>(`${PP}/policy`), [], 0, ["printers"]);
  const printers = useLoad(() => canOperateNow() ? prn.list() : Promise.resolve([] as PrinterRow[]), [], 0, ["printers"]);
  const [f, setF] = useState({ printer_id: "", supply_description: "", part_number: "", quantity: 1, reason: "" });
  const [ref, setRef] = useState<Record<string, string>>({});
  const act = useAction();
  const r = role();
  const canDecide = r === "admin" || r === "approver";
  const canOperate = r === "admin" || r === "operator";
  const step = (x: Purchase, action: string, body: Record<string, unknown> = {}) => act.run(async () => {
    const path = ["approve", "reject"].includes(action) ? `${PP}/requests/${x.id}/${action}` : `${PP}/requests/${x.id}/progress/${action}`;
    await request(path, jpost(body)); rows.reload(); return `Request ${action}d.`.replace("eed", "ed"); });
  return <Shell title="Supply purchasing" subtitle="Internal purchase requests for printer supplies. Automatic requests come only from measured LOW/EMPTY levels or a forecast within the lead time. An independent approver decides; ordering records your external PO reference."
    right={<button className={btn2} onClick={() => act.run(async () => { await download(`${PP}/requests.csv${status ? `?status=${status}` : ""}`, "printer-purchase-requests.csv"); })}>Export CSV</button>}>
    <Notice error={act.error || rows.error || pol.error} message={act.message} />
    {isAdmin() && pol.data && <PurchasePolicyCard p={pol.data} reload={pol.reload} />}
    {canOperate && <Section title="New request">
      <div className="grid gap-2 md:grid-cols-5">
        <label className="text-sm">Printer<select className={inputCls} value={f.printer_id} onChange={e => setF({ ...f, printer_id: e.target.value })}>
          <option value="">Select…</option>{(printers.data ?? []).map(p => <option key={p.id} value={p.id}>{p.hostname ?? p.serial_number} · {p.model}</option>)}</select></label>
        <label className="text-sm">Supply<input className={inputCls} value={f.supply_description} onChange={e => setF({ ...f, supply_description: e.target.value })} /></label>
        <label className="text-sm">Part number<input className={inputCls} value={f.part_number} onChange={e => setF({ ...f, part_number: e.target.value })} /></label>
        <label className="text-sm">Quantity<input type="number" min={1} max={50} className={inputCls} value={f.quantity} onChange={e => setF({ ...f, quantity: Number(e.target.value) })} /></label>
        <label className="text-sm">Reason<input className={inputCls} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></label>
      </div>
      <button className={`${btn} mt-2`} disabled={act.busy || !f.printer_id || f.supply_description.length < 2 || f.reason.length < 3} onClick={() => act.run(async () => {
        await request(`${PP}/requests`, jpost({ ...f, part_number: f.part_number || null })); rows.reload();
        setF({ printer_id: "", supply_description: "", part_number: "", quantity: 1, reason: "" }); return "Request submitted for approval."; })}>Submit request</button>
    </Section>}
    <Section title="Requests" right={<select aria-label="Status filter" className={inputCls} value={status} onChange={e => setStatus(e.target.value)}>
      <option value="">All statuses</option>{["PENDING_APPROVAL", "APPROVED", "ORDERED", "RECEIVED", "REJECTED", "CANCELLED"].map(s => <option key={s}>{s}</option>)}</select>}>
      <Table head={["Created", "Printer", "Supply", "Qty", "Est. unit cost", "Reason", "Origin", "Status", "Actions"]}
        rows={(rows.data ?? []).map(x => [ago(x.created_at), <PrinterLink p={{ id: x.printer_id, hostname: x.printer }} />,
          <span>{x.supply_description}{x.part_number && <span className="text-xs text-slate-500"> · {x.part_number}</span>}</span>, x.quantity,
          x.estimated_unit_cost == null ? "—" : `${x.currency} ${x.estimated_unit_cost.toLocaleString()}`,
          <span className="text-xs">{x.reason}</span>, <Pill value={x.origin} tone={x.origin === "AUTOMATIC" ? "info" : "none"} />,
          <span><Pill value={x.status} tone={statusTone(x.status)} />{x.order_reference && <span className="text-xs"> {x.order_reference}</span>}</span>,
          <div className="flex flex-wrap gap-1">
            {x.status === "PENDING_APPROVAL" && canDecide && <>
              <button className={btn2} disabled={act.busy} onClick={() => step(x, "approve")}>Approve</button>
              <button className={btn2} disabled={act.busy} onClick={() => step(x, "reject")}>Reject</button></>}
            {x.status === "APPROVED" && canOperate && <>
              <input aria-label={`PO reference for ${x.supply_description}`} placeholder="PO reference" className={inputCls}
                value={ref[x.id] ?? ""} onChange={e => setRef({ ...ref, [x.id]: e.target.value })} />
              <button className={btn2} disabled={act.busy || !(ref[x.id] ?? "").trim()} onClick={() => step(x, "order", { order_reference: ref[x.id] })}>Mark ordered</button></>}
            {x.status === "ORDERED" && canOperate && <button className={btn2} disabled={act.busy} onClick={() => step(x, "receive")}>Mark received</button>}
            {["PENDING_APPROVAL", "APPROVED"].includes(x.status) && canOperate &&
              <button className={btn2} disabled={act.busy} onClick={() => step(x, "cancel")}>Cancel</button>}
          </div>])}
        empty="No purchase requests." />
    </Section>
  </Shell>;
}

function PurchasePolicyCard({ p, reload }: { p: PurchasePolicy; reload: () => void }) {
  const [v, setV] = useState(p);
  const act = useAction();
  return <Section title="Automatic requests" footer="Runs daily in the worker. Off until enabled. One open request per printer supply; nothing is sent to an external procurement system.">
    <div className="grid gap-2 md:grid-cols-4">
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.automatic} onChange={e => setV({ ...v, automatic: e.target.checked })} />Create requests automatically</label>
      <label className="text-sm">Lead time (days)<input type="number" min={1} max={180} className={inputCls} value={v.lead_time_days} onChange={e => setV({ ...v, lead_time_days: Number(e.target.value) })} /></label>
      <label className="text-sm">Default quantity<input type="number" min={1} max={50} className={inputCls} value={v.default_quantity} onChange={e => setV({ ...v, default_quantity: Number(e.target.value) })} /></label>
    </div>
    <div className="mt-2 flex gap-2">
      <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { await request(`${PP}/policy`, { method: "PUT", body: JSON.stringify(v) }); reload(); return "Saved."; })}>Save</button>
      <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
        const o = await request<{ automatic: boolean; created: number; skipped_open?: number }>(`${PP}/generate`, { method: "POST" });
        return o.automatic ? `${o.created} created; ${o.skipped_open ?? 0} already open.` : "Automatic requests are off."; })}>Run now</button>
    </div>
    <Notice error={act.error} message={act.message} />
  </Section>;
}

/* ---------------- rc17: print-job metadata and document inspection ---------------- */
type PJPolicy = { enabled: boolean; inspection_enabled: boolean; purpose: string; approval_reference: string; retention_days: number;
  pseudonymise_users: boolean; store_document_names: boolean; keyword_rules: { name: string; keywords: string[]; severity: string }[];
  built_in_rules?: string[]; content_storage?: string };
type PJFinding = { id: string; rule_name: string; severity: string; match_count: number; excerpt: string | null; review_state: string; reviewed_by: string | null };
type PJob = { id: string; source: string; printer_ref: string; printer_id: string | null; user_ref: string; user_pseudonymised: boolean;
  document_name: string | null; pages: number | null; copies: number; colour: boolean | null; completed_at: string; inspection_state: string; findings: PJFinding[] };
const PJ = "/print-jobs";

function PrintJobsModule() {
  const r = role();
  const restricted = r === "admin" || r === "auditor";
  const pol = useLoad(() => request<PJPolicy>(`${PJ}/policy`), [], 0, ["printers"]);
  const sum = useLoad(() => r === "viewer" || r === "approver" ? Promise.resolve(null)
    : request<{ days: number; policy_enabled: boolean; printers: { printer_id: string | null; printer_ref: string; jobs: number; pages: number; colour_jobs: number }[] }>(`${PJ}/summary?days=30`), [], 60000, ["printers"]);
  const [state, setState] = useState("");
  const jobs = useLoad(() => restricted ? request<PJob[]>(`${PJ}?days=30${state ? `&state=${state}` : ""}`) : Promise.resolve([] as PJob[]), [state], 0, ["printers"]);
  const act = useAction();
  return <Shell title="Print jobs" subtitle="Print-job metadata from registered print servers and optional document inspection. Off by default; enabling needs a purpose statement and an approval reference. Document content is never stored.">
    <Notice error={act.error || pol.error || sum.error || jobs.error} message={act.message} />
    {pol.data && <Section title="Policy" footer={pol.data.content_storage}>
      <div className="flex flex-wrap gap-2 text-sm">
        <Pill value={pol.data.enabled ? "COLLECTION ON" : "COLLECTION OFF"} tone={pol.data.enabled ? "info" : "none"} />
        <Pill value={pol.data.inspection_enabled ? "INSPECTION ON" : "INSPECTION OFF"} tone={pol.data.inspection_enabled ? "fair" : "none"} />
        <span>Retention {pol.data.retention_days} days · users {pol.data.pseudonymise_users ? "pseudonymised" : "named"} · document names {pol.data.store_document_names ? "kept" : "not kept"}</span>
      </div>
      {pol.data.purpose && <p className="mt-2 text-sm"><b>Purpose:</b> {pol.data.purpose} <span className="text-slate-500">({pol.data.approval_reference})</span></p>}
      {isAdmin() && <PrintJobPolicyForm p={pol.data} reload={pol.reload} />}
    </Section>}
    {isAdmin() && <IngestTokens />}
    {sum.data && <Section title="Last 30 days by printer" footer="Aggregates only: no user or document information.">
      <Table head={["Printer", "Jobs", "Pages", "Colour jobs"]} rows={sum.data.printers.map(p => [p.printer_id ? <PrinterLink p={{ id: p.printer_id, hostname: p.printer_ref }} /> : p.printer_ref, num(p.jobs), num(p.pages), num(p.colour_jobs)])} empty="No print jobs recorded." />
    </Section>}
    {restricted && <Section title="Job records (restricted; every view is audited)" right={<select aria-label="Inspection state" className={inputCls} value={state} onChange={e => setState(e.target.value)}>
      <option value="">All</option>{["FINDINGS", "CLEAN", "SKIPPED", "NOT_INSPECTED"].map(s => <option key={s}>{s}</option>)}</select>}>
      <Table head={["Completed", "Printer", "User", "Document", "Pages × copies", "Inspection", "Findings"]}
        rows={(jobs.data ?? []).map(j => [ago(j.completed_at), j.printer_ref, <span className="font-mono text-xs">{j.user_ref}</span>, j.document_name ?? "—",
          `${j.pages ?? "—"} × ${j.copies}`, <Pill value={j.inspection_state} tone={j.inspection_state === "FINDINGS" ? "fair" : statusTone(j.inspection_state)} />,
          <div className="space-y-1">{j.findings.map(f => <div key={f.id} className="text-xs">
            <Pill value={f.severity} tone={f.severity === "major" ? "poor" : "fair"} /> {f.rule_name} ×{f.match_count} {f.excerpt && <span className="font-mono">{f.excerpt}</span>}
            {f.review_state === "OPEN" ? <> <button className={btn2} onClick={() => act.run(async () => { await request(`${PJ}/findings/${f.id}/review`, jpost({ state: "CONFIRMED" })); jobs.reload(); })}>Confirm</button>
              <button className={btn2} onClick={() => act.run(async () => { await request(`${PJ}/findings/${f.id}/review`, jpost({ state: "DISMISSED" })); jobs.reload(); })}>Dismiss</button></>
              : <span className="text-slate-500"> {f.review_state} by {f.reviewed_by}</span>}</div>)}</div>])}
        empty="No print jobs in the last 30 days." />
    </Section>}
  </Shell>;
}

function PrintJobPolicyForm({ p, reload }: { p: PJPolicy; reload: () => void }) {
  const [v, setV] = useState(p);
  const [rules, setRules] = useState(p.keyword_rules.map(x => `${x.name}|${x.severity}|${x.keywords.join(", ")}`).join("\n"));
  const act = useAction();
  const parsed = () => rules.split("\n").map(l => l.trim()).filter(Boolean).map(l => {
    const [name, severity, kw] = l.split("|"); return { name: (name ?? "").trim(), severity: (severity ?? "minor").trim(), keywords: (kw ?? "").split(",").map(s => s.trim()).filter(Boolean) }; });
  return <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
    <div className="grid gap-2 md:grid-cols-3">
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.enabled} onChange={e => setV({ ...v, enabled: e.target.checked, inspection_enabled: e.target.checked && v.inspection_enabled })} />Collect print-job metadata</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.inspection_enabled} disabled={!v.enabled} onChange={e => setV({ ...v, inspection_enabled: e.target.checked })} />Inspect document text (not stored)</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.pseudonymise_users} onChange={e => setV({ ...v, pseudonymise_users: e.target.checked })} />Pseudonymise user names</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.store_document_names} onChange={e => setV({ ...v, store_document_names: e.target.checked })} />Keep document names</label>
      <label className="text-sm">Retention (days)<input type="number" min={7} max={730} className={inputCls} value={v.retention_days} onChange={e => setV({ ...v, retention_days: Number(e.target.value) })} /></label>
      <label className="text-sm">Approval reference<input className={inputCls} value={v.approval_reference} onChange={e => setV({ ...v, approval_reference: e.target.value })} /></label>
    </div>
    <label className="block text-sm">Purpose statement (required to enable)<textarea className={inputCls} rows={2} value={v.purpose} onChange={e => setV({ ...v, purpose: e.target.value })} /></label>
    <label className="block text-sm">Keyword rules — one per line: NAME|severity|keyword, keyword<textarea className={`${inputCls} font-mono`} rows={3} value={rules} onChange={e => setRules(e.target.value)} /></label>
    <div className="text-xs text-slate-500">Built-in: {(p.built_in_rules ?? []).join(" · ")}</div>
    <button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
      await request(`${PJ}/policy`, { method: "PUT", body: JSON.stringify({ ...v, keyword_rules: parsed(), built_in_rules: undefined, content_storage: undefined }) }); reload(); return "Policy saved and audited."; })}>Save policy</button>
    <Notice error={act.error} message={act.message} />
  </div>;
}

function IngestTokens() {
  const t = useLoad(() => request<{ id: string; name: string; created_by: string; created_at: string; revoked: boolean; last_used_at: string | null }[]>(`${PJ}/ingest-tokens`), [], 0, ["printers"]);
  const [name, setName] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const act = useAction();
  return <Section title="Print-server ingestion tokens" footer="Each print-server agent posts to /api/print-jobs/ingest with header X-INM-Ingest-Token. Tokens are stored hashed and shown once.">
    <div className="flex flex-wrap gap-2"><input aria-label="Token name" placeholder="e.g. cups-hq" className={inputCls} value={name} onChange={e => setName(e.target.value)} />
      <button className={btn} disabled={act.busy || name.trim().length < 3} onClick={() => act.run(async () => {
        const r = await request<{ token: string }>(`${PJ}/ingest-tokens`, jpost({ name })); setShown(r.token); setName(""); t.reload(); })}>Create token</button></div>
    {shown && <p className="mt-2 break-all font-mono text-xs" role="status">Copy now — it will not be shown again: {shown}</p>}
    <Table head={["Name", "Created", "By", "Last used", "State", ""]} rows={(t.data ?? []).map(x => [x.name, ago(x.created_at), x.created_by, ago(x.last_used_at),
      <Pill value={x.revoked ? "REVOKED" : "ACTIVE"} tone={x.revoked ? "none" : "good"} />,
      !x.revoked && <button className={btn2} onClick={() => act.run(async () => { await request(`${PJ}/ingest-tokens/${x.id}`, { method: "DELETE" }); t.reload(); })}>Revoke</button>])} empty="No tokens." />
    <Notice error={act.error} message={act.message} />
  </Section>;
}


/** rc18: purchasing and print-job inspection stay disabled and isolated until explicitly authorised. The API is not
 *  mounted then, so the screen says so instead of showing request errors. */
function AuthorisationGate({ module, title, flag, children }: { module: string; title: string; flag: string; children: React.ReactNode }) {
  const f = useLoad(() => request<{ modules: { key: string; status: string; note?: string }[] }>("/v1/system/features"), [], 0);
  if (!f.data) return <div className="text-sm text-slate-500">{f.error ? String(f.error) : "Checking module authorisation…"}</div>;
  const m = f.data.modules.find(x => x.key === module);
  if (m?.status === "DISABLED_PENDING_AUTHORIZATION") return <div className="space-y-4">
    <PrinterNav />
    <PageHeader title={title} subtitle="This module is disabled and isolated until it is explicitly authorised." />
    <div className="inm-planned-notice" role="status"><b>DISABLED PENDING AUTHORISATION.</b> The server does not expose this module.
      An administrator can enable it only after written authorisation, by setting <code>{flag}=true</code> on the server. Fleet monitoring,
      utilisation, supplies and reports are unaffected.</div>
  </div>;
  return <>{children}</>;
}

export function PrinterPurchasing() {
  return <AuthorisationGate module="printer_purchasing" title="Supply purchasing" flag="INM_FEATURE_PURCHASING"><PrinterPurchasingModule /></AuthorisationGate>;
}

export function PrintJobs() {
  return <AuthorisationGate module="print_jobs" title="Print jobs" flag="INM_FEATURE_PRINT_JOBS"><PrintJobsModule /></AuthorisationGate>;
}
