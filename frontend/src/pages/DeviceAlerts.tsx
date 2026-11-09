import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { v1 } from "../services/inm";
import { rw } from "../services/ops";
import { rem } from "../services/remediation";

const sevTone = (s: string) => s === "critical" ? "poor" : s === "major" ? "fair" : "none";

export default function DeviceAlerts() {
  const [state, setState] = useState("OPEN");
  const alerts = useLoad(() => rw.alerts(state), [state], 15000);
  const rules = useLoad(rw.alertRules, [], 0);
  const windows = useLoad(rw.windows, [], 30000);
  const sites = useLoad(v1.physicalSites, [], 60000);
  const act = useAction();
  const admin = getSession()?.role === "admin";
  const [edit, setEdit] = useState<{ rule: string; scope: string; threshold: number; severity: string } | null>(null);
  const [win, setWin] = useState({ site_id: "", hours: 2, reason: "" });
  const rows = alerts.data ?? [];
  const n = (s: string) => rows.filter(a => a.severity === s && a.state !== "RESOLVED").length;
  return <div className="space-y-4">
    <PageHeader title="Device alerts" subtitle="Rule-based alerts on every managed AP and switch, evaluated after each poll and configuration capture. Duplicates are merged, alerts clear only when the metric is measured again below threshold, and maintenance windows suppress noise." />
    <ErrorNote error={alerts.error} />
    <div className="grid gap-3 md:grid-cols-4"><Stat label="Critical" value={n("critical")} tone={n("critical") ? "bad" : "ok"} />
      <Stat label="Major" value={n("major")} tone={n("major") ? "warn" : "ok"} /><Stat label="Minor" value={n("minor")} />
      <Stat label="Suppressed (maintenance)" value={rows.filter(a => a.suppressed).length} /></div>
    <Card title="Alerts" right={<select aria-label="Alert state" className={`${inputCls} mt-0 w-40`} value={state} onChange={e => setState(e.target.value)}>
      {["OPEN", "RESOLVED", "ALL"].map(s => <option key={s}>{s}</option>)}</select>}>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">Opened</th><th>Severity</th><th>Alert</th><th>Rule</th><th>State</th><th></th></tr></thead>
        <tbody>{rows.map(a => <tr key={a.id} className="border-t border-slate-800">
          <td className="py-1.5 text-xs">{ago(a.opened_at)}</td><td><Pill value={a.severity} tone={sevTone(a.severity)} /></td>
          <td><Link className="text-sky-300" to={`/devices/${a.device_id}/360`}>{a.message}</Link>{a.suppressed && <> <Pill value="maintenance" tone="info" /></>}{a.source_type === "DEMO" && <> <Pill value="DEMO" tone="demo" /></>}</td>
          <td className="font-mono text-[11px]">{a.rule_id}{a.entity && <div className="text-sky-300">{a.entity}</div>}</td><td><Pill value={a.state} tone={a.state === "RESOLVED" ? "good" : "fair"} /></td>
          <td className="space-x-1 whitespace-nowrap">{a.state === "OPEN" && <button className={btn2} onClick={() => act.run(async () => { await rw.ack(a.id); alerts.reload(); })}>Acknowledge</button>}
            <button className={btn2} onClick={() => act.run(async () => { const r = await rem.relatedChanges(a.id);
              return r.candidates.length ? `Changes in the ${r.window_minutes} min before this alert: ` + r.candidates.map(x => `${x.title ?? x.plan_id.slice(0, 8)} (${x.outcome}, ${x.minutes_before_alert} min before, ${x.same_device ? "same device" : "LLDP neighbour"})`).join("; ") + ". " + r.method
                : `No configuration change on this device or its LLDP neighbours in the ${r.window_minutes} minutes before this alert.`; })}>Related changes</button></td></tr>)}
          {!rows.length && <tr><td colSpan={6} className="py-4 text-center text-slate-500">No alerts in this state.</td></tr>}</tbody></table>
    </Card>
    <Notice error={act.error} message={act.message} />
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Rule catalogue & thresholds">
        <table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th>Rule</th><th>Applies to</th><th>Default</th><th>Overrides</th><th></th></tr></thead>
          <tbody>{(rules.data?.catalogue ?? []).map(r => <tr key={r.id} className="border-t border-slate-800">
            <td className="py-1">{r.title}<div className="font-mono text-[10px] text-slate-500">{r.id}</div></td><td>{r.family}</td>
            <td>{r.metric} ≥ {r.threshold} <Pill value={r.severity} tone={sevTone(r.severity)} /></td>
            <td>{(rules.data?.policies ?? []).filter(p => p.rule_id === r.id).map(p => <div key={p.scope}>{p.scope === "global" ? "global" : "site"}: {p.threshold} {p.enabled ? "" : "(off)"}</div>)}</td>
            <td>{admin && <button className="text-sky-300" onClick={() => setEdit({ rule: r.id, scope: "global", threshold: r.threshold, severity: r.severity })}>edit</button>}</td></tr>)}</tbody></table>
        {edit && <div className="mt-3 grid gap-2 rounded border border-slate-700 p-3 md:grid-cols-4">
          <label className="text-sm">Scope<select className={inputCls} value={edit.scope} onChange={e => setEdit({ ...edit, scope: e.target.value })}>
            <option value="global">Global</option>{(sites.data?.sites ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="text-sm">Threshold<input type="number" className={inputCls} value={edit.threshold} onChange={e => setEdit({ ...edit, threshold: Number(e.target.value) })} /></label>
          <label className="text-sm">Severity<select className={inputCls} value={edit.severity} onChange={e => setEdit({ ...edit, severity: e.target.value })}>{["critical", "major", "minor", "info"].map(s => <option key={s}>{s}</option>)}</select></label>
          <div className="flex items-end gap-2"><button className={btn} onClick={() => act.run(async () => { await rw.setRule(edit.rule, edit); setEdit(null); rules.reload(); return "Threshold saved."; })}>Save</button>
            <button className={btn2} onClick={() => setEdit(null)}>Cancel</button></div></div>}
      </Card>
      <Card title="Maintenance windows">
        {admin && <div className="grid gap-2 md:grid-cols-4">
          <label className="text-sm">Site<select className={inputCls} value={win.site_id} onChange={e => setWin({ ...win, site_id: e.target.value })}>
            <option value="">All sites</option>{(sites.data?.sites ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="text-sm">Hours from now<input type="number" min={1} className={inputCls} value={win.hours} onChange={e => setWin({ ...win, hours: Number(e.target.value) })} /></label>
          <label className="text-sm">Reason<input className={inputCls} value={win.reason} onChange={e => setWin({ ...win, reason: e.target.value })} /></label>
          <div className="flex items-end"><button className={btn} disabled={win.reason.length < 3} onClick={() => act.run(async () => {
            const now = new Date(); await rw.addWindow({ site_id: win.site_id || null, starts_at: now.toISOString(),
              ends_at: new Date(now.getTime() + win.hours * 3600e3).toISOString(), reason: win.reason });
            windows.reload(); return "Maintenance window scheduled."; })}>Schedule</button></div></div>}
        <ul className="mt-3 space-y-1 text-sm">{(windows.data ?? []).map(w => <li key={w.id} className="flex gap-2">
          <span className="text-xs text-slate-400">{new Date(w.starts_at).toLocaleString()} → {new Date(w.ends_at).toLocaleString()}</span>
          <span>{w.reason}</span><span className="text-xs text-slate-500">{w.site_id ? "site" : "all sites"} · {w.created_by}</span></li>)}</ul>
      </Card>
    </div>
  </div>;
}
