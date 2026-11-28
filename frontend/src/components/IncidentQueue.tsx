import { Fragment, useState } from "react";
import { Link } from "react-router";
import { getSession } from "../services/api";
import { inc, type Incident } from "../services/wireless";
import { useLoad } from "./useLoad";
import { Pill, ago } from "./viz";

const SEV: Record<string, "poor" | "fair" | "none"> = { critical: "poor", major: "fair" };
const RES_LABEL: Record<string, string> = { AUTO_RECOVERED: "Auto-recovered (self-healed)", NO_TICKET_REQUIRED: "Resolved — no ticket",
  TICKET_RESOLVED: "Resolved — ticket closed" };

/** NOC incident queue: assign → (ticket) → resolve, with auto-recovery from monitoring and a full timeline. */
export default function IncidentQueue() {
  const me = getSession();
  const op = ["operator", "admin"].includes(me?.role ?? "");
  const [view, setView] = useState<{ state: string; mine?: boolean; unassigned?: boolean }>({ state: "OPEN" });
  const list = useLoad(() => inc.list(view), [JSON.stringify(view)], 15000, ["alerts", "devices", "telemetry"]);
  const sum = useLoad(inc.summary, [], 15000, ["alerts"]);
  const people = useLoad(() => op ? inc.assignees() : Promise.resolve([]), [], 0);
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ note: "", system: "Service desk", ref: "", url: "", resolution: "NO_TICKET_REQUIRED" });
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<Incident>) => {
    setErr(null);
    try { await fn(); list.reload(); sum.reload(); setForm({ ...form, note: "", ref: "", url: "" }); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  };
  const s = sum.data;
  const tabs: [string, { state: string; mine?: boolean; unassigned?: boolean }, number | undefined][] = [
    ["Open", { state: "OPEN" }, s?.open], ["Unassigned", { state: "OPEN", unassigned: true }, s?.unassigned],
    ["Mine", { state: "OPEN", mine: true }, s?.mine], ["Resolved", { state: "RESOLVED" }, undefined], ["All", { state: "ALL" }, undefined]];
  return <section className="inm-card"><div className="inm-cardhead"><h2>Incident queue</h2>
    <div className="flex flex-wrap gap-1">{tabs.map(([label, v, n]) => <button key={label}
      className={`rounded-full px-3 py-1 text-sm ${JSON.stringify(v) === JSON.stringify(view) ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-700"}`}
      onClick={() => setView(v)}>{label}{n != null ? ` (${n})` : ""}</button>)}</div></div>
    {s && <div className="flex flex-wrap gap-4 border-b px-5 py-2 text-xs text-slate-600">
      <span>With ticket: <b>{s.with_ticket}</b></span><span>Auto-recovered: <b className="text-emerald-700">{s.auto_recovered}</b></span>
      <span>Manually resolved: <b>{s.manually_resolved}</b></span>
      {s.tickets_to_close > 0 && <span className="text-amber-700">Tickets to close after auto-recovery: <b>{s.tickets_to_close}</b></span>}
      {s.recurrences > 0 && <span className="text-red-700">Recurred after manual resolve: <b>{s.recurrences}</b></span>}</div>}
    {err && <div className="mx-5 mt-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{err}</div>}
    <div className="overflow-x-auto"><table className="inm-table"><thead><tr><th>Opened</th><th>Severity</th><th>Incident</th><th>Owner</th><th>Ticket</th><th>State</th><th></th></tr></thead>
      <tbody>{(list.data ?? []).map(a => <Fragment key={a.id}><tr className={open === a.id ? "bg-blue-50/50" : ""}>
        <td className="text-xs">{ago(a.opened_at)}<div className="text-[10px] text-slate-500">{a.age_minutes} min</div></td>
        <td><Pill value={a.severity} tone={SEV[a.severity] ?? "none"} /></td>
        <td><Link className="inm-link" to={`/devices/${a.device_id}/360`}>{a.message}</Link>
          <div className="text-[10px] text-slate-500">{a.rule_id} · {a.site ?? "no site"}{a.suppressed ? " · maintenance" : ""}{a.recurrence_of ? " · RECURRED" : ""}</div></td>
        <td>{op && a.state !== "RESOLVED" ? <select aria-label="Assignee" className="inm-form-control" value={a.assignee ?? ""}
          onChange={e => act(() => inc.assign(a.id, e.target.value || null))}>
          <option value="">Unassigned</option>{(people.data ?? []).map(p => <option key={p.username} value={p.username}>{p.username}</option>)}</select>
          : (a.assignee ?? "—")}</td>
        <td className="text-xs">{a.ticket ? (a.ticket.url ? <a className="inm-link" href={a.ticket.url} target="_blank" rel="noreferrer">{a.ticket.reference}</a> : a.ticket.reference) : "—"}
          {a.ticket?.closed && <span className="ml-1 text-emerald-700">closed</span>}</td>
        <td>{a.resolution ? <span title={a.resolution.note}><Pill value={RES_LABEL[a.resolution.type] ?? a.resolution.type} tone={a.resolution.type === "AUTO_RECOVERED" ? "good" : "info"} /></span>
          : <Pill value={a.state} tone="fair" />}
          {a.resolution?.ticket_needs_closure && <div className="text-[10px] text-amber-700">close ticket {a.ticket?.reference}</div>}</td>
        <td><button className="inm-btn-secondary" onClick={() => setOpen(open === a.id ? null : a.id)}>{open === a.id ? "Close" : op ? "Work" : "Details"}</button></td></tr>
        {open === a.id && <tr><td colSpan={7} className="bg-slate-50">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-2 text-sm">
              {op && a.state !== "RESOLVED" && <>
                <div className="flex flex-wrap items-end gap-2"><label>Ticket system<input className="inm-form-control" value={form.system} onChange={e => setForm({ ...form, system: e.target.value })} /></label>
                  <label>Reference<input className="inm-form-control" value={form.ref} placeholder="INC0012345" onChange={e => setForm({ ...form, ref: e.target.value })} /></label>
                  <label>URL (optional)<input className="inm-form-control" value={form.url} placeholder="https://…" onChange={e => setForm({ ...form, url: e.target.value })} /></label>
                  <button className="inm-btn-secondary" disabled={!form.ref} onClick={() => act(() => inc.ticket(a.id, form.system, form.ref, form.url))}>{a.ticket ? "Replace ticket" : "Link ticket"}</button></div>
                <label className="block">Note / resolution summary<textarea className="inm-form-control w-full" rows={2} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} /></label>
                <div className="flex flex-wrap gap-2">
                  <button className="inm-btn-secondary" disabled={!form.note} onClick={() => act(() => inc.note(a.id, form.note))}>Add note</button>
                  {!a.ticket && <button className="inm-btn-primary" disabled={form.note.length < 3} onClick={() => act(() => inc.resolve(a.id, "NO_TICKET_REQUIRED", form.note))}>Resolve — no ticket needed</button>}
                  {a.ticket && <button className="inm-btn-primary" disabled={form.note.length < 3} onClick={() => act(() => inc.resolve(a.id, "TICKET_RESOLVED", form.note))}>Resolve — ticket {a.ticket.reference} resolved</button>}
                </div>
                <p className="text-xs text-slate-500">If monitoring measures the condition back within threshold first, this incident closes itself as <b>Auto-recovered</b>.</p></>}
              {op && a.resolution?.ticket_needs_closure && <div className="flex gap-2"><input className="inm-form-control" placeholder="Closure note" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
                <button className="inm-btn-primary" disabled={form.note.length < 3} onClick={() => act(() => inc.resolve(a.id, "TICKET_RESOLVED", form.note))}>Mark ticket closed</button></div>}
              {op && a.state === "RESOLVED" && <button className="inm-link" onClick={() => { const t = window.prompt("Why reopen?"); if (t && t.length >= 1) act(() => inc.reopen(a.id, t)); }}>Reopen</button>}
              {a.recurrence_of && <p className="text-xs text-red-700">This condition came back after it was manually resolved by {a.recurrence_of.resolved_by}.</p>}
              <p className="text-xs text-slate-500">Measured value {a.value ?? "—"} vs threshold {a.threshold ?? "—"} · last seen {ago(a.last_seen_at)}</p>
            </div>
            <div><b className="text-sm">Timeline</b><ol className="mt-1 border-l-2 border-slate-200 pl-3 text-xs">
              <li className="mb-1"><span className="text-slate-500">{new Date(a.opened_at).toLocaleString()}</span> · <b>OPENED</b> by monitoring</li>
              {a.timeline.map((t, i) => <li key={i} className="mb-1"><span className="text-slate-500">{new Date(t.at).toLocaleString()}</span> · <b>{t.kind}</b> by {t.by}: {t.text}</li>)}
              {a.resolution?.type === "AUTO_RECOVERED" && <li><span className="text-slate-500">{a.resolved_at && new Date(a.resolved_at).toLocaleString()}</span> · <b>AUTO-RECOVERED</b>: {a.resolution.note}</li>}
            </ol></div>
          </div></td></tr>}</Fragment>)}
        {list.data && !list.data.length && <tr><td colSpan={7} className="py-4 text-center text-slate-500">No incidents in this view.</td></tr>}</tbody></table></div>
  </section>;
}
