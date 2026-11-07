import { FormEvent, useEffect, useState } from "react";
import PlanDialog from "../components/PlanDialog";
import { Badge, Card, ErrorNote, SimBadge, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { api, configApi, getSession, type BackupMeta, type Change, type SsidInput, type SsidRow } from "../services/api";

const OPMODES = [["opensystem", "Open (no encryption)"], ["wpa2-psk-aes", "WPA2-Personal (AES)"], ["wpa3-sae-aes", "WPA3-Personal (SAE)"]];
const empty = { profile: "", essid: "", vlan: "", opmode: "wpa2-psk-aes", passphrase: "", rf_band: "", enabled: true };

export default function Configuration() {
  const isAdmin = getSession()?.role === "admin";
  const { data: sites } = useLoad(() => api.sites(), [], 0);
  const { data: status } = useLoad(() => api.status(), [], 0);
  const [siteId, setSiteId] = useState<number | null>(null);
  useEffect(() => { if (siteId == null && sites?.length) setSiteId(sites[0].id); }, [sites, siteId]);
  const site = sites?.find((s) => s.id === siteId);
  const writable = !!status?.config_changes_enabled && !!site && !site.simulated && isAdmin;

  const view = useLoad(() => (siteId ? configApi.ssids(siteId) : Promise.resolve(null)), [siteId], 0);
  const changes = useLoad(() => (siteId && isAdmin ? configApi.changes(siteId) : Promise.resolve([])), [siteId], 0);
  const backups = useLoad(() => (siteId && isAdmin ? configApi.backups(siteId) : Promise.resolve([])), [siteId], 0);

  const [form, setForm] = useState<typeof empty | null>(null);
  const [editing, setEditing] = useState(false);
  const [plan, setPlan] = useState<Change | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState<{ title: string; body: string } | null>(null);

  const refreshAll = () => { view.reload(); changes.reload(); backups.reload(); };
  const pull = async () => {
    if (!siteId) return; setBusy(true); setErr(null);
    try { await configApi.pull(siteId); refreshAll(); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  const startEdit = (r: SsidRow) => { setEditing(true); setForm({ ...empty, profile: r.profile, essid: r.essid ?? "", vlan: String(r.vlan ?? ""), opmode: r.opmode ?? "wpa2-psk-aes", rf_band: r.rf_band ?? "", enabled: r.enabled }); };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); if (!siteId || !form) return; setErr(null); setBusy(true);
    const body: SsidInput = { profile: form.profile };
    if (form.essid) body.essid = form.essid;
    if (form.vlan) body.vlan = Number(form.vlan);
    if (form.opmode) body.opmode = form.opmode;
    if (form.passphrase) body.passphrase = form.passphrase;
    if (form.rf_band) body.rf_band = form.rf_band;
    body.enabled = form.enabled;
    try { setPlan(await configApi.planSsid(siteId, body)); setForm({ ...form, passphrase: "" }); }
    catch (e2) { setErr(e2 instanceof Error ? e2.message : "Failed"); } finally { setBusy(false); }
  };
  const showBackup = async (b: BackupMeta) => { try { const r = await configApi.backup(b.id); setText({ title: `Backup #${b.id} (${b.kind}) — secrets redacted`, body: r.text }); } catch (e) { setErr(String(e)); } };
  const diffPrev = async (i: number) => {
    const list = backups.data ?? []; if (i + 1 >= list.length) return;
    try { const r = await configApi.diff(list[i + 1].id, list[i].id); setText({ title: `Changes: #${list[i + 1].id} → #${list[i].id}`, body: r.identical ? "(identical)" : r.diff }); } catch (e) { setErr(String(e)); }
  };

  const input = "mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Configuration</h1>
        <select aria-label="Site" className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={siteId ?? ""} onChange={(e) => { setSiteId(Number(e.target.value)); setForm(null); }}>
          {sites?.map((s) => <option key={s.id} value={s.id}>{s.name}{s.simulated ? " (simulated)" : ""}</option>)}
        </select>
      </div>
      <ErrorNote error={view.error ?? err} />

      {!isAdmin && <div className="rounded-lg border border-slate-700 bg-slate-900 p-3 text-sm text-slate-300">Read-only: configuration changes need an administrator.</div>}
      {isAdmin && status && !status.config_changes_enabled && (
        <div className="rounded-lg border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200">
          Configuration changes are <b>disabled</b> (the safe default). To enable them, set <code className="rounded bg-slate-800 px-1">SIMS_ALLOW_CONFIG_CHANGES=true</code> in <code>.env</code> and restart SIMS. Reading configuration still works.
        </div>
      )}
      {isAdmin && site?.simulated && <div className="rounded-lg border border-fuchsia-800 bg-fuchsia-950/30 p-3 text-sm text-fuchsia-200">Simulated sites are read-only. Switch to live mode and register a real site to make changes.</div>}

      <Card title="SSIDs and VLANs" right={
        <div className="flex items-center gap-2">
          {view.data?.simulated && <SimBadge />}
          <span className="text-xs text-slate-500">{view.data?.taken_at ? `pulled ${fmtTime(view.data.taken_at)}` : "never pulled"}</span>
          <button disabled={busy || !siteId} onClick={pull} className="rounded border border-slate-700 px-2 py-1 text-sm hover:bg-slate-800 disabled:opacity-50">{busy ? "Pulling…" : "Pull config now"}</button>
        </div>}>
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-xs uppercase text-slate-400"><tr>{["Profile", "ESSID", "VLAN", "Security", "Band", "State", ""].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-800">
            {view.data?.ssids.map((r) => (
              <tr key={r.profile}>
                <td className="px-2 py-2 font-medium">{r.profile}</td><td className="px-2 py-2">{r.essid ?? "—"}</td><td className="px-2 py-2">{r.vlan ?? "—"}</td>
                <td className="px-2 py-2">{r.opmode ?? "—"}</td><td className="px-2 py-2">{r.rf_band ?? "—"}</td>
                <td className="px-2 py-2"><Badge value={r.enabled ? "up" : "down"} /></td>
                <td className="px-2 py-2 text-right">{writable && <button className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800" onClick={() => startEdit(r)}>Edit</button>}</td>
              </tr>
            ))}
            {view.data && view.data.ssids.length === 0 && <tr><td colSpan={7} className="px-2 py-6 text-center text-slate-500">{view.data.backup_id ? "No SSID profiles recognized in the pulled configuration." : "No configuration pulled yet. Click “Pull config now”."}</td></tr>}
          </tbody>
        </table></div>
        {view.data && view.data.vlans.length > 0 && <p className="mt-2 text-xs text-slate-400">VLANs in use: {view.data.vlans.join(", ")}</p>}
        {writable && !form && <button className="mt-3 rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500" onClick={() => { setEditing(false); setForm({ ...empty }); }}>Add SSID</button>}
      </Card>

      {writable && form && (
        <Card title={editing ? `Edit SSID “${form.profile}”` : "Add SSID"}>
          <form onSubmit={submit} className="grid gap-3 md:grid-cols-3">
            <label className="text-sm">Profile name<input className={input} value={form.profile} disabled={editing} required onChange={(e) => setForm({ ...form, profile: e.target.value })} /></label>
            <label className="text-sm">ESSID (network name)<input className={input} value={form.essid} maxLength={32} required={!editing} onChange={(e) => setForm({ ...form, essid: e.target.value })} /></label>
            <label className="text-sm">VLAN (1–4094)<input type="number" min={1} max={4094} className={input} value={form.vlan} required={!editing} onChange={(e) => setForm({ ...form, vlan: e.target.value })} /></label>
            <label className="text-sm">Security<select className={input} value={form.opmode} onChange={(e) => setForm({ ...form, opmode: e.target.value })}>{OPMODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            {form.opmode !== "opensystem" && <label className="text-sm">Passphrase (8–63){editing && " — leave blank to keep"}<input type="password" autoComplete="new-password" className={input} value={form.passphrase} minLength={8} maxLength={63} onChange={(e) => setForm({ ...form, passphrase: e.target.value })} /></label>}
            <label className="text-sm">RF band<select className={input} value={form.rf_band} onChange={(e) => setForm({ ...form, rf_band: e.target.value })}><option value="">(unchanged / default)</option><option value="all">All</option><option value="2.4">2.4 GHz</option><option value="5.0">5 GHz</option></select></label>
            <label className="flex items-center gap-2 text-sm md:col-span-3"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Enabled</label>
            <div className="flex gap-2 md:col-span-3">
              <button disabled={busy} className="rounded bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50">Review change…</button>
              <button type="button" className="rounded border border-slate-600 px-4 py-2 text-sm hover:bg-slate-800" onClick={() => setForm(null)}>Close</button>
            </div>
            <p className="text-xs text-slate-500 md:col-span-3">Nothing is sent to the AP yet. Reviewing takes a safety backup and shows exactly what will change. Tip: try a new throw-away SSID before editing a production one.</p>
          </form>
        </Card>
      )}

      {isAdmin && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Change history">
            <ul className="divide-y divide-slate-800 text-sm">
              {changes.data?.map((c) => (
                <li key={c.id} className="py-2"><div className="flex items-center gap-2"><Badge value={c.status === "applied" ? "up" : c.status === "failed" ? "down" : c.status} /><span>{c.summary}</span></div>
                  <div className="text-xs text-slate-500">#{c.id} by {c.created_by} · {fmtTime(c.created_at)}{c.result && ` · ${c.result}`}</div></li>
              ))}
              {changes.data?.length === 0 && <li className="py-4 text-center text-slate-500">No changes yet.</li>}
            </ul>
          </Card>
          <Card title="Configuration backups (secrets redacted)">
            <ul className="divide-y divide-slate-800 text-sm">
              {backups.data?.map((b, i) => (
                <li key={b.id} className="flex items-center justify-between gap-2 py-2">
                  <span>#{b.id} <span className="text-slate-400">{b.kind}</span> · {fmtTime(b.taken_at)} · {b.ssid_count} SSIDs {b.simulated && <SimBadge />}</span>
                  <span className="flex gap-1"><button className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800" onClick={() => showBackup(b)}>View</button>
                    {i + 1 < (backups.data?.length ?? 0) && <button className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800" onClick={() => diffPrev(i)}>Diff vs previous</button>}</span>
                </li>
              ))}
              {backups.data?.length === 0 && <li className="py-4 text-center text-slate-500">No backups yet.</li>}
            </ul>
          </Card>
        </div>
      )}

      {plan && <PlanDialog change={plan} onClose={() => { setPlan(null); refreshAll(); }} onDone={refreshAll} />}
      {text && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setText(null)} role="presentation">
          <div className="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-xl border border-slate-700 bg-slate-900 p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold">{text.title}</h2><button className="rounded border border-slate-600 px-2 py-1 text-xs" onClick={() => setText(null)}>Close</button></div>
            <pre className="whitespace-pre-wrap text-xs text-slate-200">{text.body}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
