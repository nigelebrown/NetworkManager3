import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Donut, Notice, PageHeader, Pill, ago, btn, btn2, inputCls, useAction } from "../components/viz";
import { ops } from "../services/ops";

export default function Accountability() {
  const drift = useLoad(ops.drift, [], 30000);
  const act = useAction();
  const [aaa, setAaa] = useState({ device_serial: "", username: "", source: "TACACS+", start_at: "", end_at: "" });
  const [sys, setSys] = useState({ source_ip: "", lines: "" });
  const [corr, setCorr] = useState<{ id: string; level: string; actor: string; note: string } | null>(null);
  const rows = drift.data ?? [];
  const current = rows.filter(r => !rows.some(x => x.supersedes_id === r.id));
  const n = (l: string) => current.filter(r => r.level === l).length;
  return <div className="space-y-4">
    <PageHeader title="Change accountability" subtitle="Every configuration change is attributed using device syslog plus AAA accounting: VERIFIED only with a matching named session; otherwise CORRELATED or UNKNOWN. The actor is never guessed." />
    <ErrorNote error={drift.error} />
    <div className="grid gap-3 md:grid-cols-4">
      <Card><Donut height={130} data={[{ name: "Verified", value: n("VERIFIED"), color: COLORS.good }, { name: "Correlated", value: n("CORRELATED"), color: COLORS.fair }, { name: "Unknown", value: n("UNKNOWN"), color: COLORS.poor }]} /></Card>
      <Stat label="Direct device changes" value={current.length} /><Stat label="Unknown actor" value={n("UNKNOWN")} tone={n("UNKNOWN") ? "bad" : "ok"} sub="enable AAA accounting on the device" />
      <Stat label="Verified technician" value={n("VERIFIED")} tone="ok" />
    </div>
    <Card title="Out-of-band changes (drift)">
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">When</th><th>Device</th><th>Attribution</th><th>Actor</th><th>Candidates</th><th>Note</th><th></th></tr></thead>
        <tbody>{rows.map(a => <tr key={a.id} className={`border-t border-slate-800 ${current.includes(a) ? "" : "opacity-50"}`}>
          <td className="py-1.5">{ago(a.change_at)}</td><td><Link className="text-sky-300" to={`/devices/${a.device_id}/360`}>{a.device_id.slice(0, 8)}</Link></td>
          <td><Pill value={a.level} tone={a.level === "VERIFIED" ? "good" : a.level === "CORRELATED" ? "fair" : "poor"} /></td>
          <td>{a.actor ?? <span className="text-slate-500">operator unknown</span>}</td><td className="text-xs">{a.candidates.join(", ")}</td>
          <td className="text-xs text-slate-400">{a.supersedes_id ? "correction · " : ""}{a.note}</td>
          <td>{current.includes(a) && <button className={btn2} onClick={() => setCorr({ id: a.id, level: "CORRELATED", actor: "", note: "" })}>Annotate</button>}</td></tr>)}
          {!rows.length && <tr><td colSpan={7} className="py-3 text-center text-slate-500">No direct device changes detected.</td></tr>}</tbody></table>
      {corr && <div className="mt-3 grid gap-2 rounded-lg border border-slate-700 p-3 md:grid-cols-4">
        <label className="text-sm">Level<select className={inputCls} value={corr.level} onChange={e => setCorr({ ...corr, level: e.target.value })}><option>CORRELATED</option><option>UNKNOWN</option></select></label>
        <label className="text-sm">Actor (must be a candidate)<input className={inputCls} value={corr.actor} onChange={e => setCorr({ ...corr, actor: e.target.value })} /></label>
        <label className="text-sm md:col-span-2">Investigation note<input className={inputCls} value={corr.note} onChange={e => setCorr({ ...corr, note: e.target.value })} /></label>
        <div className="md:col-span-4"><button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
          await ops.correct(corr.id, { level: corr.level, actor: corr.actor || null, note: corr.note }); setCorr(null); drift.reload();
          return "Superseding record created; original evidence preserved."; })}>Save superseding record</button>
          <span className="ml-2 text-xs text-slate-500">VERIFIED can only be earned from matching evidence, never set by hand.</span></div></div>}
    </Card>
    <Notice error={act.error} message={act.message} />
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Record AAA accounting session (RADIUS / TACACS+ export)">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Device serial<input className={inputCls} value={aaa.device_serial} onChange={e => setAaa({ ...aaa, device_serial: e.target.value })} /></label>
          <label className="text-sm">Username<input className={inputCls} value={aaa.username} onChange={e => setAaa({ ...aaa, username: e.target.value })} /></label>
          <label className="text-sm">Session start<input type="datetime-local" className={inputCls} value={aaa.start_at} onChange={e => setAaa({ ...aaa, start_at: e.target.value })} /></label>
          <label className="text-sm">Session end<input type="datetime-local" className={inputCls} value={aaa.end_at} onChange={e => setAaa({ ...aaa, end_at: e.target.value })} /></label>
          <label className="text-sm">Source<select className={inputCls} value={aaa.source} onChange={e => setAaa({ ...aaa, source: e.target.value })}>{["TACACS+", "RADIUS", "CX_ACCOUNTING"].map(s => <option key={s}>{s}</option>)}</select></label>
        </div>
        <button className={`${btn} mt-2`} disabled={act.busy || !aaa.device_serial || !aaa.username || !aaa.start_at} onClick={() => act.run(async () => {
          await ops.aaa({ ...aaa, start_at: new Date(aaa.start_at).toISOString(), end_at: aaa.end_at ? new Date(aaa.end_at).toISOString() : null }); return "AAA session recorded."; })}>Record session</button>
      </Card>
      <Card title="Import device syslog lines">
        <p className="mb-2 text-xs text-slate-400">Devices normally send syslog to the collector (UDP/TCP 1514). Paste lines here to import archived logs. The source IP must be a registered management address.</p>
        <label className="text-sm">Device management IP<input className={inputCls} value={sys.source_ip} onChange={e => setSys({ ...sys, source_ip: e.target.value })} /></label>
        <textarea aria-label="Syslog lines" className={`${inputCls} h-24 font-mono text-xs`} value={sys.lines} onChange={e => setSys({ ...sys, lines: e.target.value })} />
        <button className={`${btn} mt-2`} disabled={act.busy || !sys.source_ip || !sys.lines.trim()} onClick={() => act.run(async () => {
          const r = await ops.syslog({ source_ip: sys.source_ip, lines: sys.lines.split("\n").filter(Boolean) }); drift.reload();
          return `Stored ${r.stored}, duplicates ${r.duplicates}, configuration changes attributed ${r.config_changes_attributed}.`; })}>Import</button>
      </Card>
    </div>
  </div>;
}
