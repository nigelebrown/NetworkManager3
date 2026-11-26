import { useState } from "react";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession, request } from "../services/api";
import { v1 } from "../services/inm";
import { ops } from "../services/ops";

const tone = (s: string) => s.startsWith("IMPLEMENTED") ? "good" : s === "GUIDED" ? "fair" : s === "SCAFFOLD" ? "demo" : "none";

export default function Platform() {
  const ad = useLoad(ops.adapters, [], 0);
  const topo = useLoad(() => ops.topology().catch(() => null), [], 60000);
  const rules = useLoad(ops.rules, [], 0);
  const feats = useLoad(v1.features, [], 0);
  const ftone = (s: string) => s.startsWith("LIVE") || s === "WORKFLOW" || s === "CERTIFIED_WRITE_ENGINE" ? "good"
    : s === "NOT_IMPLEMENTED" || s === "PLACEHOLDER" ? "poor" : s === "DEMO" ? "demo" : "fair";
  return <div className="space-y-4">
    <PageHeader title="Platform: vendors, roles & rules" subtitle="Which device families each adapter supports, how the seven service roles are deployed, and the best-practice rule catalogue." />
    <ErrorNote error={ad.error} />
    <Card title="Module status" right={<span className="text-xs text-slate-500">Hardware writes: {feats.data?.device_writes}</span>}>
      <table className="w-full text-xs"><tbody>{(feats.data?.modules ?? []).map(m => <tr key={m.key} className="border-t border-slate-800">
        <td className="py-1 font-mono">{m.key}</td><td><Pill value={m.status.replace(/_/g, " ")} tone={ftone(m.status)} /></td>
        <td className="text-slate-400">{m.note}</td></tr>)}</tbody></table>
    </Card>
    <Card title="Vendor adapter capability matrix" right={<span className="text-xs text-slate-500">{ad.data?.write_policy}</span>}>
      <div className="overflow-auto"><table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="py-1">Adapter</th><th>Models</th><th>Protocols</th>
        {(ad.data?.operations ?? []).map(o => <th key={o} className="px-1">{o.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>{(ad.data?.adapters ?? []).map(a => <tr key={a.key} className="border-t border-slate-800">
          <td className="py-1.5"><b>{a.vendor}</b> {a.family}<div className="font-mono text-[10px] text-slate-500">{a.key} · {a.certification}</div></td>
          <td>{a.models.join(", ") || "any"}</td><td>{a.protocols.join(", ")}</td>
          {(ad.data?.operations ?? []).map(o => <td key={o} className="px-1"><Pill value={(a.operations[o] ?? "UNSUPPORTED").replace("IMPLEMENTED_", "")} tone={tone(a.operations[o])} /></td>)}</tr>)}</tbody></table></div>
    </Card>
    {topo.data && <Card title={`Service roles — ${topo.data.deployment.replace("_", " ").toLowerCase()}`}>
      <div className="grid gap-2 md:grid-cols-4">{topo.data.roles.map(r => <div key={r.role} className="rounded-lg border border-slate-800 p-2 text-sm">
        <div className="font-medium">{r.role}</div><div className="text-xs text-slate-400">{r.configured ? `${r.host ?? "local"}${r.port ? ":" + r.port : ""}` : "in-process / not configured"}</div>
        <div className="mt-1 flex gap-1">{r.configured && <Pill value={r.co_located ? "co-located" : "remote"} tone="info" />}{r.tls && <Pill value="TLS" tone="good" />}</div></div>)}</div>
      <p className="mt-2 text-xs text-slate-500">Moving a role to its own VM is a configuration change (URL/TLS/secret); no code changes.</p></Card>}
    <Card title={`Best-practice rule catalogue (${rules.data?.length ?? 0})`}><table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th>ID</th><th>Family</th><th>Category</th><th>Severity</th><th>Rule</th><th>Recommended</th></tr></thead>
      <tbody>{(rules.data ?? []).map(r => <tr key={r.id} className="border-t border-slate-800"><td className="py-1 font-mono">{r.id}</td><td>{r.family === "switch" ? "Switch" : "AP"}</td><td>{r.category}</td>
        <td><Pill value={r.severity} tone={r.severity === "critical" ? "poor" : r.severity === "high" ? "fair" : "none"} /></td><td>{r.title}</td><td className="font-mono text-sky-200">{r.fix}</td></tr>)}</tbody></table></Card>
    {["admin", "auditor"].includes(getSession()?.role ?? "") && <LegacySsh />}
    {getSession()?.role === "admin" && <EmergencyAccess />}
  </div>;
}

type Approval = { ip: string; approved_by: string; reason: string; expires_at: string };
function LegacySsh() {
  const d = useLoad(() => request<{ server_enabled: boolean; approvals: Approval[] }>("/v1/ssh-legacy-approvals"), [], 0);
  const [f, setF] = useState({ ip: "", reason: "", days: 30 });
  const act = useAction();
  const admin = getSession()?.role === "admin";
  return <Card title="SSH legacy compatibility (ssh-rsa / SHA-1)">
    <p className="text-sm">Modern SSH algorithms only by default. A device that cannot negotiate them is refused unless it is
      approved here <b>and</b> the server sets <code>INM_SSH_LEGACY_COMPAT=true</code>. Every legacy session and refusal is audited; the host key stays pinned.</p>
    <p className="mt-1 text-sm">Server setting: <Pill value={d.data?.server_enabled ? "ENABLED" : "DISABLED"} tone={d.data?.server_enabled ? "fair" : "good"} /></p>
    <ErrorNote error={d.error} /><Notice error={act.error} message={act.message} />
    <table className="mt-2 w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Device IP</th><th>Approved by</th><th>Reason</th><th>Expires</th><th></th></tr></thead>
      <tbody>{(d.data?.approvals ?? []).map(a => <tr key={a.ip} className="border-t border-slate-800"><td className="font-mono">{a.ip}</td><td>{a.approved_by}</td><td>{a.reason}</td>
        <td>{new Date(a.expires_at).toLocaleString()}</td><td>{admin && <button className={btn2} onClick={() => act.run(async () => {
          await request(`/v1/ssh-legacy-approvals/${encodeURIComponent(a.ip)}`, { method: "DELETE" }); d.reload(); return "Approval revoked."; })}>Revoke</button>}</td></tr>)}
        {!d.data?.approvals.length && <tr><td colSpan={5} className="py-2 text-slate-500">No device is approved for legacy SSH.</td></tr>}</tbody></table>
    {admin && <div className="mt-3 grid gap-2 md:grid-cols-4">
      <label className="text-sm">Device IP<input className={inputCls} value={f.ip} onChange={e => setF({ ...f, ip: e.target.value })} /></label>
      <label className="text-sm md:col-span-2">Reason (why it cannot be upgraded)<input className={inputCls} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></label>
      <label className="text-sm">Days (max 90)<input type="number" min={1} max={90} className={inputCls} value={f.days} onChange={e => setF({ ...f, days: Number(e.target.value) })} /></label>
      <button className={btn} disabled={act.busy || !f.ip || f.reason.length < 10} onClick={() => act.run(async () => {
        await request("/v1/ssh-legacy-approvals", { method: "POST", body: JSON.stringify(f) }); d.reload(); setF({ ip: "", reason: "", days: 30 }); return "Approved and audited."; })}>Approve device</button>
    </div>}
  </Card>;
}



type Emergency = { id: string; holder: string; reason: string; ticket: string; started_at: string; expires_at: string;
  ended_at: string | null; reviewed_by: string | null; review_note: string | null; state: string };
function EmergencyAccess() {
  const d = useLoad(() => request<{ sessions: Emergency[]; mine_active: boolean; overdue_reviews: number; policy: string }>("/v1/emergency-access"), [], 30000);
  const [f, setF] = useState({ reason: "", ticket: "", minutes: 60 });
  const [note, setNote] = useState<Record<string, string>>({});
  const act = useAction();
  const me = getSession();
  return <Card title="Emergency (break-glass) access">
    <Notice error={act.error || d.error} message={act.message} />
    <p className="text-xs text-slate-500">{d.data?.policy} Opening a session alerts every connection at critical severity; another administrator must review it.</p>
    {!!d.data?.overdue_reviews && <p role="alert" className="text-sm text-rose-700">{d.data.overdue_reviews} session(s) overdue for review.</p>}
    {!d.data?.mine_active && <form className="mt-2 grid gap-2 md:grid-cols-4" aria-label="Open emergency session" onSubmit={e => { e.preventDefault(); act.run(async () => {
      await request("/v1/emergency-access", { method: "POST", body: JSON.stringify(f) }); setF({ reason: "", ticket: "", minutes: 60 }); d.reload();
      return "Emergency session open. Self-approval is now possible only with the explicit emergency approval button."; }); }}>
      <input aria-label="Emergency reason" className={`${inputCls} md:col-span-2`} placeholder="Reason (min 20 characters)" value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} />
      <input aria-label="Incident ticket" className={inputCls} placeholder="Incident ticket" value={f.ticket} onChange={e => setF({ ...f, ticket: e.target.value })} />
      <span className="flex gap-1"><input aria-label="Emergency minutes" className={`${inputCls} w-20`} value={f.minutes} onChange={e => setF({ ...f, minutes: Number(e.target.value.replace(/\D/g, "")) || 5 })} />
        <button className={btn} type="submit" disabled={act.busy || f.reason.length < 20 || f.ticket.length < 3}>Open session</button></span>
    </form>}
    <table className="mt-2 w-full text-xs"><thead><tr className="text-left text-slate-500"><th>Holder</th><th>Ticket</th><th>Window</th><th>State</th><th>Review</th></tr></thead>
      <tbody>{(d.data?.sessions ?? []).map(s => <tr key={s.id} className="border-t border-slate-800 align-top"><td>{s.holder}</td><td>{s.ticket}<div className="text-slate-500">{s.reason}</div></td>
        <td>{s.started_at.slice(0, 16).replace("T", " ")} → {(s.ended_at ?? s.expires_at).slice(0, 16).replace("T", " ")}</td>
        <td><Pill value={s.state.replace(/_/g, " ")} tone={s.state === "ACTIVE" ? "poor" : s.state === "REVIEWED" ? "good" : "fair"} /></td>
        <td>{s.state === "ACTIVE" && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await request(`/v1/emergency-access/${s.id}/end`, { method: "POST" }); d.reload(); return "Session ended."; })}>End now</button>}
          {s.reviewed_by ? `${s.reviewed_by}: ${s.review_note}` : s.state !== "ACTIVE" && me?.username !== s.holder && <span className="flex gap-1">
            <input aria-label={`Review note for ${s.ticket}`} className={inputCls} value={note[s.id] ?? ""} onChange={e => setNote({ ...note, [s.id]: e.target.value })} />
            <button className={btn2} disabled={act.busy || (note[s.id] ?? "").length < 10} onClick={() => act.run(async () => {
              await request(`/v1/emergency-access/${s.id}/review`, { method: "POST", body: JSON.stringify({ note: note[s.id] }) }); d.reload(); return "Review recorded."; })}>Record review</button></span>}</td></tr>)}</tbody></table>
  </Card>;
}
