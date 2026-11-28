import { useState } from "react";
import { Link } from "react-router";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { notify, type NotifyChannel } from "../services/remediation";

const SEV = ["info", "minor", "major", "critical"];

function RoutingRow({ c, all, admin, reload }: { c: NotifyChannel; all: NotifyChannel[]; admin: boolean; reload: () => void }) {
  const [r, setR] = useState({ min_severity: c.min_severity, escalate: c.escalate_after_minutes ? String(c.escalate_after_minutes) : "",
    esc: c.escalation_channel_id ?? "", demo: c.include_demo });
  const act = useAction();
  const nameOf = (id: string | null) => all.find(x => x.id === id)?.name ?? "—";
  const dirty = r.min_severity !== c.min_severity || (r.escalate || null) !== (c.escalate_after_minutes ? String(c.escalate_after_minutes) : null)
    || (r.esc || null) !== c.escalation_channel_id || r.demo !== c.include_demo;
  return <tr className="border-t border-slate-800 align-top">
    <td className="py-1 font-semibold">{c.name}{!c.enabled && <> <Pill value="disabled" tone="none" /></>}</td><td>{c.kind}</td>
    <td>{admin ? <select aria-label={`Minimum severity for ${c.name}`} className={inputCls} value={r.min_severity} onChange={e => setR({ ...r, min_severity: e.target.value })}>
      {SEV.map(s => <option key={s} value={s}>≥ {s}</option>)}</select> : `≥ ${c.min_severity}`}</td>
    <td>{admin ? <span className="flex gap-1">
      <input aria-label={`Escalate ${c.name} after minutes`} className={`${inputCls} w-20`} placeholder="min" value={r.escalate} onChange={e => setR({ ...r, escalate: e.target.value.replace(/\D/g, "") })} />
      <select aria-label={`Escalation channel for ${c.name}`} className={inputCls} value={r.esc} onChange={e => setR({ ...r, esc: e.target.value })}>
        <option value="">no escalation</option>{all.filter(x => x.id !== c.id && x.kind !== "heartbeat").map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></span>
      : c.escalate_after_minutes ? `${c.escalate_after_minutes} min → ${nameOf(c.escalation_channel_id)}` : "—"}</td>
    <td>{admin ? <input type="checkbox" aria-label={`Include demo alerts for ${c.name}`} checked={r.demo} onChange={e => setR({ ...r, demo: e.target.checked })} /> : c.include_demo ? "yes" : "no"}</td>
    <td>{admin && <button className={btn2} disabled={!dirty || act.busy} onClick={() => act.run(async () => {
      await notify.routing(c.id, { min_severity: r.min_severity, escalate_after_minutes: r.escalate ? Number(r.escalate) : null,
        escalation_channel_id: r.esc || null, include_demo: r.demo }); reload(); return "Routing saved."; })}>Save</button>}
      <Notice error={act.error} message={act.message} /></td></tr>;
}

/** Alert routing (which alerts go to which connection, escalation) and the alert delivery feed (E2E-20).
 *  Connections, credentials and connection tests live on the Integrations screen. */
export default function Notifications() {
  const me = getSession();
  const admin = me?.role === "admin";
  const channels = useLoad(() => notify.channels(), [], 30000);
  const approval = useLoad(() => admin ? notify.approvalChannel() : Promise.resolve(null), [admin], 30000);
  const [filter, setFilter] = useState("");
  const deliveries = useLoad(() => notify.deliveries(filter || undefined), [filter], 15000);
  const act = useAction();
  const routable = (channels.data ?? []).filter(c => c.kind !== "heartbeat");
  const nameOf = (id: string | null) => channels.data?.find(c => c.id === id)?.name ?? "—";
  return <div className="space-y-4">
    <PageHeader title="Alert notifications" subtitle="Route device alerts at or above a severity to your connections, and escalate unacknowledged alerts to a second connection. Each alert transition is queued per connection, retried with backoff (receivers should deduplicate) and shown as DEAD if it cannot be delivered." />
    <p className="text-sm text-slate-400">Demo-data alerts are not sent unless a connection opts in. Acknowledging an alert stops its escalation. To add a connection, change its destination or credentials, or test it, use <Link className="inm-link" to="/integrations">Integrations</Link>.</p>
    <Card title="Alert routing">
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-400"><th>Connection</th><th>Type</th><th>Severity</th><th>Escalation</th><th>Demo alerts</th><th /></tr></thead>
        <tbody>{routable.map(c => <RoutingRow key={c.id} c={c} all={channels.data ?? []} admin={admin} reload={channels.reload} />)}</tbody></table>
      {!routable.length && <p className="text-sm text-slate-500">No alert connections — alerts are shown in the UI only. Add one in <Link className="inm-link" to="/integrations">Integrations</Link>.</p>}
    </Card>
    {admin && <Card title="RF optimization approval notifications">
      <p className="text-sm">Choose an email or webhook destination specifically authorized for independent approvers. No destination means in-app approval only.</p>
      <select aria-label="Optimization approval channel" className={inputCls} value={approval.data?.channel_id ?? ""} disabled={act.busy || approval.loading} onChange={e => {
        const id = e.target.value || null;
        void act.run(async () => { await notify.setApprovalChannel(id); await approval.reload(); return "Approval destination saved."; });
      }}>
        <option value="">In-app approval only</option>
        {(channels.data ?? []).filter(c => c.enabled && (c.kind === "email" || c.kind === "webhook")).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <Notice error={approval.error || act.error} message={act.message} />
    </Card>}
    <Card title="Deliveries">
      <div className="mb-2 flex gap-2 text-xs">{["", "PENDING", "SENDING", "SENT", "DEAD"].map(s => <button key={s} className={filter === s ? btn : btn2} onClick={() => setFilter(s)}>{s || "All"}</button>)}</div>
      <Notice error={act.error} message={act.message} />
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-400"><th>When</th><th>Connection</th><th>Kind</th><th>Alert</th><th>State</th><th>Attempts</th><th>Error</th><th /></tr></thead>
        <tbody>{(deliveries.data ?? []).filter(d => d.kind !== "HEARTBEAT").map(d => <tr key={d.id} className="border-t border-slate-800">
          <td className="py-1 text-xs">{d.created_at?.replace("T", " ").slice(0, 19)}</td><td>{nameOf(d.channel_id)}</td><td>{d.kind}</td>
          <td className="text-xs">{d.severity ? `[${d.severity}] ` : ""}{d.message}</td>
          <td><Pill value={d.state} tone={d.state === "SENT" ? "good" : d.state === "DEAD" ? "poor" : "info"} /></td>
          <td>{d.attempts}</td><td className="text-xs text-rose-300">{d.last_error}</td>
          <td>{d.state === "DEAD" && d.kind !== "TEST" && me && (d.kind === "APPROVAL" ? admin : ["admin", "operator"].includes(me.role)) &&
            <button className={btn2} onClick={() => act.run(async () => { await notify.retry(d.id); deliveries.reload(); return "Queued for redelivery."; })}>Retry</button>}</td></tr>)}</tbody></table>
      {!deliveries.data?.length && <p className="text-sm text-slate-500">No deliveries yet.</p>}
    </Card>
  </div>;
}
