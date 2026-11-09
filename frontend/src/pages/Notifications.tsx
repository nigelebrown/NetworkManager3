import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { notify } from "../services/remediation";

const HINT: Record<string, string> = { webhook: "https://hooks.example/inm (no credentials in the URL)",
  email: "noc@example.org, oncall@example.org (SMTP relay set by INM_SMTP_HOST)", syslog: "siem.example.org:514" };

/** Alert notification channels, escalation and delivery history (audit E2E-20). */
export default function Notifications() {
  const me = getSession();
  const admin = me?.role === "admin";
  const channels = useLoad(() => notify.channels(), [], 30000);
  const [filter, setFilter] = useState("");
  const deliveries = useLoad(() => notify.deliveries(filter || undefined), [filter], 15000);
  const act = useAction();
  const [f, setF] = useState({ name: "", kind: "webhook", target: "", min_severity: "major", escalate: "", esc: "", demo: false });
  const nameOf = (id: string | null) => channels.data?.find(c => c.id === id)?.name ?? "—";
  return <div className="space-y-4">
    <PageHeader title="Alert notifications" subtitle="Send device alerts at or above a severity to webhooks, email or syslog. Each alert transition is queued per channel, retried with backoff on failure (receivers should deduplicate repeat deliveries) and shown as DEAD if it cannot be delivered. Unacknowledged alerts can escalate to a second channel." />
    <p className="text-sm text-slate-400">Demo-data alerts are not sent unless a channel opts in. Acknowledging an alert stops its escalation.</p>
    <Card title="Channels">
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-400"><th>Name</th><th>Type</th><th>Target</th><th>Severity ≥</th><th>Escalation</th><th /></tr></thead>
        <tbody>{(channels.data ?? []).map(c => <tr key={c.id} className="border-t border-slate-800">
          <td className="py-1 font-semibold">{c.name}{!c.enabled && <Pill value="disabled" tone="none" />}</td><td>{c.kind}</td>
          <td className="font-mono text-xs">{c.target}</td><td>{c.min_severity}</td>
          <td>{c.escalate_after_minutes ? `${c.escalate_after_minutes} min → ${nameOf(c.escalation_channel_id)}` : "—"}</td>
          <td className="text-right">{admin && <>
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
              const r = await notify.test(c.id); deliveries.reload();
              if (!r.delivered) throw new Error(`Test notification NOT delivered: ${r.error}`);
              return "Test notification delivered."; })}>Send test</button>{" "}
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await notify.remove(c.id); channels.reload(); return "Channel removed."; })}>Remove</button></>}</td></tr>)}
        </tbody></table>
      {!channels.data?.length && <p className="text-sm text-slate-500">No channels configured — alerts are shown in the UI only.</p>}
      {admin && <div className="mt-3 grid gap-2 md:grid-cols-4">
        <input aria-label="Channel name" className={inputCls} placeholder="Name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
        <select aria-label="Channel type" className={inputCls} value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })}>
          <option value="webhook">webhook</option><option value="email">email</option><option value="syslog">syslog</option></select>
        <input aria-label="Channel target" className={`${inputCls} md:col-span-2`} placeholder={HINT[f.kind]} value={f.target} onChange={e => setF({ ...f, target: e.target.value })} />
        <select aria-label="Minimum severity" className={inputCls} value={f.min_severity} onChange={e => setF({ ...f, min_severity: e.target.value })}>
          {["info", "minor", "major", "critical"].map(s => <option key={s} value={s}>severity ≥ {s}</option>)}</select>
        <input aria-label="Escalate after minutes" className={inputCls} placeholder="Escalate after (min, optional)" value={f.escalate} onChange={e => setF({ ...f, escalate: e.target.value.replace(/\D/g, "") })} />
        <select aria-label="Escalation channel" className={inputCls} value={f.esc} onChange={e => setF({ ...f, esc: e.target.value })}>
          <option value="">Escalate to…</option>{(channels.data ?? []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={f.demo} onChange={e => setF({ ...f, demo: e.target.checked })} /> include demo alerts</label>
        <button className={btn} disabled={act.busy || !f.name.trim() || !f.target.trim()} onClick={() => act.run(async () => {
          await notify.create({ name: f.name.trim(), kind: f.kind as any, target: f.target.trim(), min_severity: f.min_severity,
            escalate_after_minutes: f.escalate ? Number(f.escalate) : null, escalation_channel_id: f.esc || null, include_demo: f.demo });
          setF({ ...f, name: "", target: "", escalate: "", esc: "" }); channels.reload(); return "Channel added."; })}>Add channel</button>
      </div>}
      <div className="mt-2"><Notice error={act.error} message={act.message} /></div>
    </Card>
    <Card title="Deliveries">
      <div className="mb-2 flex gap-2 text-xs">{["", "PENDING", "SENDING", "SENT", "DEAD"].map(s => <button key={s} className={filter === s ? btn : btn2} onClick={() => setFilter(s)}>{s || "All"}</button>)}</div>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-400"><th>When</th><th>Channel</th><th>Kind</th><th>Alert</th><th>State</th><th>Attempts</th><th>Error</th><th /></tr></thead>
        <tbody>{(deliveries.data ?? []).map(d => <tr key={d.id} className="border-t border-slate-800">
          <td className="py-1 text-xs">{d.created_at?.replace("T", " ").slice(0, 19)}</td><td>{nameOf(d.channel_id)}</td><td>{d.kind}</td>
          <td className="text-xs">{d.severity ? `[${d.severity}] ` : ""}{d.message}</td>
          <td><Pill value={d.state} tone={d.state === "SENT" ? "good" : d.state === "DEAD" ? "poor" : "info"} /></td>
          <td>{d.attempts}</td><td className="text-xs text-rose-300">{d.last_error}</td>
          <td>{d.state === "DEAD" && d.kind !== "TEST" && me && ["admin", "operator"].includes(me.role) &&
            <button className={btn2} onClick={() => act.run(async () => { await notify.retry(d.id); deliveries.reload(); return "Queued for redelivery."; })}>Retry</button>}</td></tr>)}</tbody></table>
      {!deliveries.data?.length && <p className="text-sm text-slate-500">No deliveries yet.</p>}
    </Card>
  </div>;
}
