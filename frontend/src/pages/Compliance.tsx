import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Gauge, PageHeader, Pill, SourceTag, TT, scoreColor, statusTone } from "../components/viz";
import { ops, type DeviceCompliance } from "../services/ops";

export function RuleTable({ c }: { c: DeviceCompliance }) {
  const [show, setShow] = useState<"issues" | "all">("issues");
  const rows = c.results.filter(r => show === "all" || ["FAIL", "WARN"].includes(r.status));
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-3 text-sm">
      {!c.config_available && <span className="text-amber-300">No captured configuration — every rule is UNKNOWN. Capture one in <Link className="underline" to="/vault">Config vault</Link>.</span>}
      {c.config_integrity === "FAIL" && <Pill value="CONFIG INTEGRITY FAILED" tone="poor" />}
      {c.config_captured_at && <span className="text-slate-400">Evaluated against the configuration captured {new Date(c.config_captured_at).toLocaleString()}</span>}
      <div className="ml-auto flex gap-1">{(["issues", "all"] as const).map(s =>
        <button key={s} onClick={() => setShow(s)} className={`rounded px-2 py-1 text-xs ${show === s ? "bg-sky-600" : "bg-slate-800"}`}>{s === "issues" ? "Issues only" : "All rules"}</button>)}</div>
    </div>
    <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500">
      <th className="py-1">Rule</th><th>Status</th><th>Severity</th><th>Finding</th><th>Recommended configuration</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.id} className="border-t border-slate-800 align-top">
        <td className="py-2 pr-2"><div className="font-medium">{r.title}</div><div className="font-mono text-[11px] text-slate-500">{r.id} · {r.category}</div></td>
        <td><Pill value={r.status} tone={statusTone(r.status)} /></td>
        <td><Pill value={r.severity} tone={r.severity === "critical" ? "poor" : r.severity === "high" ? "fair" : "none"} /></td>
        <td className="pr-2 text-xs text-slate-400">{r.why}{r.evidence && <div className="mt-1 font-mono text-[11px] text-slate-300">found: {r.evidence}</div>}</td>
        <td className="font-mono text-[11px] text-sky-200">{r.fix}</td></tr>)}
        {!rows.length && <tr><td colSpan={5} className="py-4 text-center text-emerald-300">No issues found.</td></tr>}</tbody></table>
    <div><h3 className="mb-1 text-sm font-semibold">Intended vs installed</h3>
      <p className="text-xs text-slate-400">Compares the inherited template configuration ({c.intended_sources.map(s => s.name).join(", ") || "no template applies"}) with what is actually configured on the device.</p>
      <div className="mt-2 flex items-center gap-2"><Pill value={c.intended_vs_installed.status} tone={statusTone(c.intended_vs_installed.status)} />
        {c.intended_vs_installed.note && <span className="text-xs text-slate-500">{c.intended_vs_installed.note}</span>}</div>
      {c.intended_vs_installed.differences.length > 0 && <table className="mt-2 w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Field</th><th>Intended</th><th>Installed</th></tr></thead>
        <tbody>{c.intended_vs_installed.differences.map(d => <tr key={d.field} className="border-t border-slate-800">
          <td className="py-1 font-mono text-xs">{d.field}</td><td className="text-emerald-300">{JSON.stringify(d.intended)}</td><td className="text-red-300">{JSON.stringify(d.installed)}</td></tr>)}</tbody></table>}
    </div>
  </div>;
}

export default function Compliance() {
  const fleet = useLoad(ops.fleetCompliance, [], 60000);
  const [sel, setSel] = useState<string | null>(null);
  const detail = useLoad(() => sel ? ops.deviceCompliance(sel) : Promise.resolve(null), [sel], 0);
  const f = fleet.data;
  const cats = Object.entries(f?.failures_by_category ?? {}).map(([category, failures]) => ({ category, failures }));
  return <div className="space-y-4">
    <PageHeader title="Configuration best practice & compliance" subtitle="Evaluates what is actually configured on each device (from its captured configuration) against Aruba AOS-CX and Instant best practice, and against your intended templates." />
    <ErrorNote error={fleet.error} />
    {f && <div className="grid gap-3 md:grid-cols-5">
      <Card><Gauge value={f.fleet_score} label="Fleet score" /></Card>
      <Stat label="Grade A devices" value={f.grades.A ?? 0} tone="ok" />
      <Stat label="Grade C/D devices" value={(f.grades.C ?? 0) + (f.grades.D ?? 0)} tone={(f.grades.C ?? 0) + (f.grades.D ?? 0) ? "bad" : "ok"} />
      <Stat label="Template drift" value={f.drift.DRIFT ?? 0} tone={f.drift.DRIFT ? "warn" : "ok"} sub="installed ≠ intended" />
      <Stat label="No configuration captured" value={f.devices_without_config} tone={f.devices_without_config ? "warn" : "ok"} />
    </div>}
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Failures by category">{cats.length ? <ResponsiveContainer width="100%" height={200}><BarChart data={cats}>
        <CartesianGrid stroke="#1e293b" /><XAxis dataKey="category" stroke="#64748b" fontSize={11} /><YAxis allowDecimals={false} stroke="#64748b" fontSize={11} />
        <Tooltip {...TT} /><Bar dataKey="failures" fill={COLORS.poor} radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer>
        : <p className="py-8 text-center text-sm text-slate-500">No failures.</p>}</Card>
      <Card title="Most common failing rules"><ul className="space-y-1.5 text-sm">{(f?.top_failures ?? []).map(r =>
        <li key={r.id} className="flex gap-2"><Pill value={r.severity} tone={r.severity === "critical" ? "poor" : "fair"} />
          <span className="flex-1">{r.title} <span className="font-mono text-[11px] text-slate-500">{r.id}</span></span><span className="text-slate-400">{r.devices} device(s)</span></li>)}</ul></Card>
    </div>
    <Card title="Devices — click a row for findings and fixes">
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">Device</th><th>Site</th><th>Model</th><th>Score</th><th>Grade</th><th>Fail/Warn</th><th>Template</th><th></th></tr></thead>
        <tbody>{(f?.devices ?? []).map(d => <tr key={d.device_id} onClick={() => setSel(d.device_id)}
          className={`cursor-pointer border-t border-slate-800 hover:bg-slate-800/40 ${sel === d.device_id ? "bg-slate-800/60" : ""}`}>
          <td className="py-1.5">{d.hostname} <SourceTag source={d.source_type} /></td><td>{d.site ?? "—"}</td><td>{d.model}</td>
          <td><span className="font-semibold" style={{ color: scoreColor(d.score) }}>{d.score ?? "—"}</span></td>
          <td><Pill value={d.grade} tone={statusTone(d.grade)} /></td><td className="text-xs">{d.critical_failures ? <Pill value="critical" tone="poor" /> : null}</td>
          <td><Pill value={d.drift} tone={statusTone(d.drift)} /></td>
          <td><Link onClick={e => e.stopPropagation()} className="text-xs text-sky-300" to={`/devices/${d.device_id}/360`}>360°</Link></td></tr>)}</tbody></table>
    </Card>
    {sel && detail.data && <Card title={`Findings — ${detail.data.hostname}`} right={<span className="text-sm">Score <b style={{ color: scoreColor(detail.data.score) }}>{detail.data.score ?? "—"}</b> · Grade {detail.data.grade}</span>}>
      <RuleTable c={detail.data} /></Card>}
  </div>;
}
