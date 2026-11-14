import { PageHeader } from "../components/viz";
import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorNote, SimBadge, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import ConfirmDialog from "../components/ConfirmDialog";
import RestApiDialog from "../components/RestApiDialog";
import { api, getSession, type Site } from "../services/api";

export default function Sites() {
  const { data, error, reload } = useLoad(() => api.sites(), []);
  const platform = useLoad(() => api.status(), [], 0);
  const isAdmin = getSession()?.role === "admin";
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toRemove, setToRemove] = useState<Site | null>(null);
  const [restSite, setRestSite] = useState<Site | null>(null);
  const [form, setForm] = useState({ name: "", group: "", vc_host: "", vc_port: 4343, api_username: "", api_password: "" });

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setMsg(`${label}…`); setErr(null);
    try { const r = await fn(); setMsg(`${label}: ${JSON.stringify(r)}`); reload(); }
    catch (e) { setMsg(null); setErr(e instanceof Error ? e.message : "Failed"); }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run("Create site", () => api.createSite({ ...form, group: form.group || null })).then(() => setForm({ ...form, api_password: "" }));
  };
  const SiteRow = ({ s }: { s: Site }) => (
    <tr>
      <td className="px-2 py-2 font-medium">{s.name} {s.simulated && <SimBadge />}</td>
      <td className="px-2 py-2">{s.group ?? "—"}</td>
      <td className="px-2 py-2">{s.vc_host ? `${s.vc_host}:${s.vc_port}` : "—"}</td>
      <td className="px-2 py-2">{s.ap_count}</td>
      <td className="px-2 py-2">{s.last_poll_ok == null ? "—" : <Badge value={s.last_poll_ok ? "up" : "down"} />}</td>
      <td className="px-2 py-2 text-xs text-slate-400">{fmtTime(s.last_poll_at)}{s.last_poll_error && <div className="text-amber-400">{s.last_poll_error}</div>}</td>
      <td className="px-2 py-2 text-sm">
        {!s.simulated && isAdmin && <button className="mr-2 rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" onClick={() => run("Test", () => api.testSite(s.id))}>Test connection</button>}
        {!s.simulated && isAdmin && <button className="mr-2 rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" onClick={() => setRestSite(s)}>REST API</button>}
        {getSession()?.role !== "viewer" && <button className="mr-2 rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" onClick={() => run("Poll", () => api.pollSite(s.id))}>Poll now</button>}
        {!s.simulated && isAdmin && <button className="rounded border border-red-800 px-2 py-1 text-red-300 hover:bg-red-950" onClick={() => setToRemove(s)}>Remove</button>}
      </td>
    </tr>
  );
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Sites & inventory" title="Sites & formations" subtitle="Aruba Instant cluster administration and site monitoring. Physical site assignments are also shown in Device Inventory." />
      {restSite && <RestApiDialog site={restSite} onClose={() => { setRestSite(null); reload(); }} />}
      {toRemove && (
        <ConfirmDialog title={`Remove site "${toRemove.name}"?`} confirmLabel="Remove site" onCancel={() => setToRemove(null)}
          onConfirm={() => { const id = toRemove.id; setToRemove(null); run("Remove site", () => api.deleteSite(id)); }}>
          <p>This deletes the site, its {toRemove.ap_count} access point record(s), clients, history and alerts from SIMS.</p>
          <p className="text-slate-400">Nothing is changed on the Aruba devices. This cannot be undone.</p>
        </ConfirmDialog>
      )}
      <ErrorNote error={error ?? err} />
      {msg && <div className="rounded-lg border border-slate-700 bg-slate-900 p-3 font-mono text-xs break-all">{msg}</div>}
      <Card>
        <div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left text-sm">
          <thead className="text-xs uppercase text-slate-400"><tr>{["Site", "Group", "Virtual Controller", "APs", "Last poll", "Details", "Actions"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-800">{data?.map((s) => <SiteRow key={s.id} s={s} />)}</tbody>
        </table></div>
      </Card>
      {isAdmin && platform.data?.mode === "demo" && <Card title="Add a real location or device">
        <p className="text-sm text-slate-300">Demo Instant clusters appear above. For actual access points and switches, use the guided Add device workflow; it stores the physical site and keeps real data separate from simulated data.</p>
        <Link to="/onboarding" className="mt-3 inline-block rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500">Add device</Link>
      </Card>}
      {isAdmin && platform.data?.mode === "live" && (
        <Card title="Register a site (live mode)">
          <form onSubmit={submit} className="grid gap-3 md:grid-cols-3">
            {([["name", "Site name"], ["group", "Group (optional)"], ["vc_host", "Virtual Controller IP / host"], ["api_username", "API username"]] as const).map(([k, label]) => (
              <label key={k} className="text-sm">{label}
                <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required={k !== "group"} />
              </label>
            ))}
            <label className="text-sm">Port
              <input type="number" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5" value={form.vc_port} onChange={(e) => setForm({ ...form, vc_port: Number(e.target.value) })} />
            </label>
            <label className="text-sm">API password (stored encrypted)
              <input type="password" autoComplete="off" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5" value={form.api_password} onChange={(e) => setForm({ ...form, api_password: e.target.value })} required />
            </label>
            <div className="md:col-span-3"><button className="rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500">Register site</button></div>
          </form>
          <p className="mt-2 text-xs text-slate-500">Registration is only available when SIMS_MODE=live. If a new site fails to connect, use “REST API” to check whether the REST API is on (it is off by default on Instant) and enable it.</p>
        </Card>
      )}
    </div>
  );
}
