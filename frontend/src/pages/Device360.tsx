import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Gauge, PageHeader, Pill, SourceTag, TT, ago, statusTone } from "../components/viz";
import { RuleTable } from "./Compliance";
import Faceplate from "../components/Faceplate";
import { Notice, btn2, useAction } from "../components/viz";
import { rw } from "../services/ops";
import { v1 } from "../services/inm";
import { ops } from "../services/ops";

const METRICS: Record<string, string[]> = {
  access_point: ["clients", "util_5g_percent", "util_2g_percent", "retry_percent", "noise_floor_dbm", "cpu_percent", "memory_percent"],
  switch: ["ports_up", "port_errors_delta", "max_port_util_percent", "poe_power_w", "cpu_percent", "memory_percent", "temp_max_c"],
};
const TABS = ["Performance", "Ports", "Best practice", "Changes", "Configuration", "Capabilities", "Operations"] as const;

export default function Device360() {
  const { id = "" } = useParams();
  const device = useLoad(() => v1.getDevice(id), [id], 30000, ["devices"]);
  const noc = useLoad(ops.noc, [], 30000);
  const d = device.data;
  const [metric, setMetric] = useState("cpu_percent");
  const [hours, setHours] = useState(24);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Performance");
  const series = useLoad(() => ops.series(id, metric, hours), [id, metric, hours], 60000, ["telemetry"]);
  const comp = useLoad(() => ops.deviceCompliance(id), [id], 120000);
  const tl = useLoad(() => ops.timeline(id), [id], 60000);
  const ver = useLoad(() => ops.versions(id), [id], 120000);
  const ad = useLoad(() => ops.deviceAdapter(id), [id], 0);
  const opt = useLoad(() => ops.optState(id), [id], 60000);
  const tile = noc.data?.devices.find(x => x.device_id === id);
  const ports = useLoad(() => rw.ports(id), [id], 30000, ["telemetry"]);
  const cert = useLoad(() => rw.deviceCert(id), [id], 60000);
  const act = useAction();
  const [fp, setFp] = useState("");
  const [vc, setVc] = useState("");
  const metricsFor = METRICS[d?.family ?? "switch"] ?? METRICS.switch;
  const data = (series.data?.points ?? []).map(p => ({ t: new Date(p.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), v: p.v }));
  return <div className="space-y-4">
    <PageHeader title={d ? `${d.hostname ?? d.serial_number}` : "Device"} subtitle={d ? `${d.vendor} ${d.model ?? ""} · serial ${d.serial_number} · firmware ${d.firmware ?? "unknown"}` : undefined}
      right={d && <div className="flex items-center gap-2"><SourceTag source={d.source_type} /><Pill value={d.status} tone={statusTone(d.status)} />
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/changes`}>Plan a change</Link>
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/devices/${id}`}>Inventory record</Link></div>} />
    <ErrorNote error={device.error} />
    <div className="grid gap-3 md:grid-cols-4">
      <Card><Gauge value={tile?.health.score} label="Experience" /><p className="mt-1 text-center text-xs text-slate-500">{tile?.health.reasons.join(" · ") || "No issues detected"}</p></Card>
      <Card><Gauge value={comp.data?.score} label="Best practice" /><p className="mt-1 text-center text-xs text-slate-500">Grade {comp.data?.grade ?? "—"}</p></Card>
      <Card title="Telemetry"><div className="space-y-1 text-sm"><div>Freshness <Pill value={tile?.freshness ?? "—"} tone={statusTone(tile?.freshness ?? "")} /></div>
        {Object.entries(tile?.metrics ?? {}).slice(0, 5).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-slate-400">{k}</span><b>{v}</b></div>)}</div></Card>
      <Card title="Optimization"><div className="space-y-1 text-sm"><Pill value={opt.data?.badge ?? "AUTO OFF"} tone="none" />
        <p className="text-xs text-slate-400">Recommendations only. Automatic optimization is disabled by policy; changes go through engineer change plans.</p>
        <Link to="/optimization" className="text-xs text-sky-300">Shadow simulation →</Link></div></Card>
    </div>
    <div className="flex gap-1 border-b border-slate-800">{TABS.map(t => <button key={t} onClick={() => setTab(t)}
      className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-sky-500 text-sky-200" : "text-slate-400"}`}>{t}</button>)}</div>
    {tab === "Performance" && <Card title="History" right={<div className="flex gap-2">
      <select aria-label="Metric" className="rounded bg-slate-950 px-2 py-1 text-sm" value={metric} onChange={e => setMetric(e.target.value)}>{metricsFor.map(m => <option key={m}>{m}</option>)}</select>
      <select aria-label="Range" className="rounded bg-slate-950 px-2 py-1 text-sm" value={hours} onChange={e => setHours(Number(e.target.value))}>{[1, 6, 24, 168].map(h => <option key={h} value={h}>{h < 24 ? `${h} h` : `${h / 24} d`}</option>)}</select></div>}>
      {data.length > 1 ? <ResponsiveContainer width="100%" height={260}><AreaChart data={data}>
        <defs><linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.info} stopOpacity={0.45} /><stop offset="100%" stopColor={COLORS.info} stopOpacity={0} /></linearGradient></defs>
        <CartesianGrid stroke="#1e293b" /><XAxis dataKey="t" stroke="#64748b" fontSize={11} minTickGap={30} /><YAxis stroke="#64748b" fontSize={11} /><Tooltip {...TT} />
        <Area dataKey="v" name={metric} stroke={COLORS.info} fill="url(#m)" connectNulls={false} /></AreaChart></ResponsiveContainer>
        : <p className="py-10 text-center text-sm text-slate-500">No {metric} readings in this window. Create a collection plan under <Link className="text-sky-300" to="/collection">Monitoring → Collection</Link>.</p>}
    </Card>}
    {tab === "Ports" && <Card title="Switch faceplate">{d?.family === "switch" && ports.data
      ? <Faceplate data={ports.data} deviceId={id} /> : <p className="text-sm text-slate-500">Port faceplate applies to switches.</p>}</Card>}
    {tab === "Operations" && <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Configuration authority">
        <p className="text-sm text-slate-400">Before configuring, confirm Aruba Central / AirWave is not the configuration authority for this device.</p>
        <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
          const a = await rw.authority(id); return `${a.authority}: ${a.detail} (${a.environment})`; })}>Check management authority</button>
      </Card>
      <Card title="Configuration backup">
        <div className="flex flex-wrap gap-2">
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
            const r = await rw.captureDevice(id); ver.reload(); comp.reload(); return `Captured full configuration (${r.size} bytes, SHA-256 ${r.sha256.slice(0, 12)}…) into the encrypted vault.`; })}>Capture configuration from device</button>
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await rw.schedule(id, 24); return "Daily scheduled capture enabled (drift and compliance alerts after each capture)."; })}>Schedule daily backup</button>
        </div>
      </Card>
      <Card title="Templates → deployment">
        <p className="text-sm text-slate-400">Compare the inherited template with what is installed and create an approvable change plan containing only the needed operations.</p>
        <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
          const r = await rw.deployPlan(id); return r.in_compliance ? "Installed configuration already matches the templates." : `Change plan created with ${r.operations?.length} operation(s) — review it in Change workflow.`; })}>Create deployment plan from templates</button>
      </Card>
      <Card title={`Write certification — ${cert.data?.model_family ?? ""} firmware ${cert.data?.firmware_train ?? ""}`}>
        <ul className="grid grid-cols-1 gap-1 text-xs md:grid-cols-2">{Object.entries(cert.data?.operations ?? {}).map(([op, st]) => <li key={op} className="flex justify-between gap-2">
          <span className="font-mono">{op}</span><Pill value={st} tone={st === "CERTIFIED_WRITE" ? "good" : st === "FAILED" ? "poor" : "fair"} /></li>)}</ul>
        {!Object.keys(cert.data?.operations ?? {}).length && <p className="text-sm text-amber-300">Uncertified: change plans for this model/firmware are refused until certified on a lab device.</p>}
        <Link className="mt-2 inline-block text-xs text-sky-300" to="/certification">Certification →</Link>
      </Card>
      {d?.family === "access_point" && <Card title="SSH host key (needed for ARM/radio changes)">
        <div className="flex flex-wrap items-end gap-2">
          <button className={btn2} onClick={() => act.run(async () => { const r = await rw.scanKey(id); setFp(r.fingerprint ?? r.sha256 ?? ""); return "Compare this fingerprint with the AP console before pinning."; })}>Scan host key</button>
          <input aria-label="Fingerprint" className="rounded bg-slate-950 px-2 py-1 text-xs" value={fp} onChange={e => setFp(e.target.value)} placeholder="SHA256:…" />
          <input aria-label="Virtual Controller IP" className="rounded bg-slate-950 px-2 py-1 text-xs" value={vc} onChange={e => setVc(e.target.value)} placeholder="VC IP (optional)" />
          <button className={btn2} disabled={!fp} onClick={() => act.run(async () => { await rw.pinKey(id, fp, vc); return "Host key pinned."; })}>Pin</button>
        </div>
      </Card>}
      <div className="lg:col-span-2"><Notice error={act.error} message={act.message} /></div>
    </div>}
    {tab === "Best practice" && comp.data && <Card><RuleTable c={comp.data} /></Card>}
    {tab === "Changes" && <Card title="Change timeline — who changed what" right={tl.data && <span className="text-xs text-slate-400">Evidence sources: {tl.data.coverage.present.join(", ")}{tl.data.coverage.missing.length ? ` · missing ${tl.data.coverage.missing.join(", ")}` : ""}</span>}>
      <ul className="space-y-2 text-sm">{(tl.data?.attributions ?? []).map(a => <li key={a.id} className={`flex flex-wrap items-center gap-2 ${a.current === false ? "opacity-50" : ""}`}>
        <span className="w-24 text-xs text-slate-500">{ago(a.change_at)}</span><Pill value={a.level} tone={a.level === "VERIFIED" ? "good" : a.level === "CORRELATED" ? "fair" : "poor"} />
        <span>{a.change_type === "APPLICATION" ? "Change via this platform" : "Direct device change"}</span>
        <b>{a.actor ?? "operator unknown"}</b>{a.candidates.length > 0 && !a.actor && <span className="text-xs text-slate-500">candidates: {a.candidates.join(", ")}</span>}
        {a.supersedes_id && <Pill value="correction" tone="info" />}{a.note && <span className="text-xs text-slate-400">“{a.note}”</span>}</li>)}
        {!tl.data?.attributions.length && <li className="text-slate-500">No configuration changes detected.</li>}</ul>
      <h3 className="mt-4 text-sm font-semibold">Device log</h3>
      <ul className="mt-1 max-h-60 space-y-1 overflow-auto text-xs">{(tl.data?.log_events ?? []).map(e => <li key={e.id} className="flex gap-2">
        <span className="w-20 text-slate-500">{ago(e.observed_at)}</span><Pill value={e.severity} tone="none" /><span>{e.message}</span></li>)}</ul></Card>}
    {tab === "Configuration" && <Card title="Configuration versions" right={<Link className="text-sm text-sky-300" to="/vault">Open config vault →</Link>}>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Captured</th><th>Trigger</th><th>Source</th><th>SHA-256</th><th>Restore</th></tr></thead>
        <tbody>{(ver.data?.full_fidelity ?? []).map(a => <tr key={a.id} className="border-t border-slate-800"><td className="py-1">{new Date(a.created_at).toLocaleString()}</td>
          <td>{a.trigger}</td><td>{a.capture_source}</td><td className="font-mono text-xs">{a.plaintext_sha256.slice(0, 16)}…</td><td><Pill value={a.restore_state} tone={statusTone(a.restore_state)} /></td></tr>)}</tbody></table>
      <p className="mt-2 text-xs text-slate-500">{ver.data?.redacted_baselines.length ?? 0} redacted onboarding baseline(s) also exist; they are not restorable.</p></Card>}
    {tab === "Capabilities" && ad.data && <Card title={`Adapter ${ad.data.adapter.key} — ${ad.data.adapter.certification}`}>
      <div className="grid gap-2 md:grid-cols-2">{ad.data.operations.map(o => <div key={o.op} className="flex items-center justify-between rounded border border-slate-800 px-3 py-1.5 text-sm">
        <span className="font-mono text-xs">{o.op}</span><Pill value={o.status} tone={o.status === "AVAILABLE_READ_ONLY" ? "good" : o.status === "UNSUPPORTED" ? "none" : "fair"} /></div>)}</div>
      <p className="mt-2 text-xs text-slate-500">{ad.data.adapter.notes}</p></Card>}
  </div>;
}
