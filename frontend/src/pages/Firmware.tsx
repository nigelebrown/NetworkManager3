import { PageHeader } from "../components/viz";
import { FormEvent, useEffect, useState } from "react";
import FwPlanDialog from "../components/FwPlanDialog";
import { Card, ErrorNote, SimBadge } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { api, fwApi, getSession, type FwChange, type FwPolicy } from "../services/api";

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;
const STATUS: Record<string, string> = {
  compliant: "bg-emerald-900/60 text-emerald-300", outdated: "bg-red-900/60 text-red-300",
  newer: "bg-amber-900/60 text-amber-300", unknown: "bg-slate-800 text-slate-300",
};

export default function Firmware() {
  const isAdmin = getSession()?.role === "admin";
  const canSeeImages = isAdmin || getSession()?.role === "operator";
  const comp = useLoad(() => fwApi.compliance(), [], 0);
  const imgs = useLoad(() => (canSeeImages ? fwApi.images() : Promise.resolve([])), [], 0);
  const { data: sites } = useLoad(() => api.sites(), [], 0);
  const { data: status } = useLoad(() => api.status(), [], 0);
  const liveSites = (sites ?? []).filter((s) => !s.simulated);
  const writable = isAdmin && !!status?.config_changes_enabled;

  const [policy, setPolicy] = useState<FwPolicy | null>(null);
  useEffect(() => { if (comp.data && !policy) setPolicy(comp.data.policy); }, [comp.data, policy]);
  const [siteId, setSiteId] = useState<number | "">("");
  useEffect(() => { if (siteId === "" && liveSites.length) setSiteId(liveSites[0].id); }, [liveSites, siteId]);
  const [picked, setPicked] = useState<number[]>([]);
  const [downgrade, setDowngrade] = useState(false);
  const [certFile, setCertFile] = useState<File | null>(null);
  const [pass, setPass] = useState("");
  const [plan, setPlan] = useState<FwChange | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  const savePolicy = (e: FormEvent) => { e.preventDefault(); if (!policy) return; run(async () => { await fwApi.setPolicy({ ...policy, target: policy.target || null }); setMsg("Compliance policy saved."); comp.reload(); }); };
  const upload = (f: File | undefined) => { if (f) run(async () => { await fwApi.upload(f); setMsg(`Stored ${f.name}.`); imgs.reload(); }); };
  const planUpgrade = () => { if (siteId !== "") run(async () => setPlan(await fwApi.planUpgrade(siteId, picked, downgrade))); };
  const planCert = (e: FormEvent) => { e.preventDefault(); if (siteId !== "" && certFile) run(async () => { setPlan(await fwApi.planCert(siteId, certFile, pass)); setPass(""); }); };

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Administration" title="Firmware lifecycle" subtitle="Firmware compliance, certificates and controlled management tasks." />
      <ErrorNote error={err} />
      {msg && <div className="rounded border border-emerald-800 bg-emerald-950/40 p-2 text-sm text-emerald-300">{msg}</div>}

      <Card title="Firmware compliance">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-400"><tr><th className="py-1">Site</th><th>Running</th><th>Target</th><th>Status</th><th>APs up</th></tr></thead>
          <tbody>{(comp.data?.sites ?? []).map((s) => (
            <tr key={s.site_id} className="border-t border-slate-800">
              <td className="py-1.5">{s.site_name} {s.simulated && <SimBadge />}</td>
              <td>{s.running_parsed ?? <span className="text-slate-500">unavailable</span>}</td>
              <td>{s.target ?? <span className="text-slate-500">not set</span>}</td>
              <td><span className={`rounded px-2 py-0.5 text-xs ${STATUS[s.status]}`}>{s.status}</span></td>
              <td>{s.ap_up}/{s.ap_count}</td>
            </tr>))}</tbody>
        </table>
        <p className="mt-2 text-xs text-slate-500">The version is what the Virtual Controller reports for its whole cluster. SIMS only reports and alerts; it never upgrades on its own.</p>
        {isAdmin && policy && (
          <form onSubmit={savePolicy} className="mt-3 flex flex-wrap items-end gap-3 border-t border-slate-800 pt-3 text-sm">
            <label>Target version<input placeholder="8.10.0.21" value={policy.target ?? ""} onChange={(e) => setPolicy({ ...policy, target: e.target.value })} className="mt-1 block w-36 rounded border border-slate-600 bg-slate-950 px-2 py-1" /></label>
            <label>Rule<select value={policy.mode} onChange={(e) => setPolicy({ ...policy, mode: e.target.value as FwPolicy["mode"] })} className="mt-1 block rounded border border-slate-600 bg-slate-950 px-2 py-1">
              <option value="minimum">At least the target</option><option value="exact">Exactly the target</option></select></label>
            <button disabled={busy} className="rounded bg-sky-600 px-3 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40">Save policy</button>
          </form>)}
      </Card>

      {canSeeImages && (
        <Card title="Firmware images stored in SIMS" right={isAdmin ? <label className="cursor-pointer rounded border border-slate-600 px-3 py-1 text-sm hover:bg-slate-800">Upload image
          <input type="file" className="hidden" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} /></label> : undefined}>
          {(imgs.data ?? []).length === 0 ? <p className="text-sm text-slate-400">No images yet. Upload the file exactly as downloaded from Aruba, e.g. ArubaInstant_Gemini_8.10.0.21_91450.</p> : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-400"><tr><th /><th>File</th><th>Class</th><th>Version</th><th>Size</th><th /></tr></thead>
              <tbody>{imgs.data!.map((i) => (
                <tr key={i.id} className="border-t border-slate-800">
                  <td className="py-1.5">{isAdmin && <input type="checkbox" aria-label={`Select ${i.filename}`} checked={picked.includes(i.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, i.id] : picked.filter((x) => x !== i.id))} />}</td>
                  <td className="break-all">{i.filename}</td><td>{i.ap_class}</td><td>{i.version}</td><td>{mb(i.size_bytes)}</td>
                  <td>{isAdmin && <button className="rounded border border-red-800 px-2 py-0.5 text-xs text-red-300 hover:bg-red-950" onClick={() => { if (window.confirm(`Delete ${i.filename} from SIMS?`)) run(async () => { await fwApi.deleteImage(i.id); setPicked(picked.filter((x) => x !== i.id)); imgs.reload(); }); }}>Delete</button>}</td>
                </tr>))}</tbody>
            </table>)}
        </Card>)}

      {isAdmin && (
        <Card title="Stage a firmware upgrade or upload a certificate">
          {!writable && <p className="mb-2 text-sm text-amber-300">Configuration changes are disabled. Set SIMS_ALLOW_CONFIG_CHANGES=true in .env and restart to plan changes.</p>}
          <label className="text-sm">Site<select value={siteId} onChange={(e) => setSiteId(Number(e.target.value))} className="ml-2 rounded border border-slate-600 bg-slate-950 px-2 py-1">
            {liveSites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>

          <div className="mt-4 border-t border-slate-800 pt-3 text-sm">
            <h3 className="font-medium">Firmware (stage only, no reboot)</h3>
            <p className="text-xs text-slate-400">Tick the image(s) above (one per AP class), then plan. The Virtual Controller downloads them from SIMS and syncs them to the other APs. You reboot the cluster yourself when ready.</p>
            <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={downgrade} onChange={(e) => setDowngrade(e.target.checked)} /> Allow downgrade</label>
            <button disabled={!writable || busy || picked.length === 0 || siteId === ""} onClick={planUpgrade} className="mt-2 rounded bg-sky-600 px-3 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40">Plan firmware staging</button>
          </div>

          <form onSubmit={planCert} className="mt-4 border-t border-slate-800 pt-3 text-sm">
            <h3 className="font-medium">TLS certificate (WebUI / REST server certificate)</h3>
            <p className="text-xs text-slate-400">One PEM file: certificate (plus intermediates) followed by the passphrase-protected private key.</p>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <label>PEM file<input type="file" accept=".pem,.crt,.cer,.txt" onChange={(e) => setCertFile(e.target.files?.[0] ?? null)} className="mt-1 block text-xs" /></label>
              <label>Key passphrase<input type="password" autoComplete="off" value={pass} onChange={(e) => setPass(e.target.value)} className="mt-1 block w-48 rounded border border-slate-600 bg-slate-950 px-2 py-1" /></label>
              <button disabled={!writable || busy || !certFile || !pass || siteId === ""} className="rounded bg-sky-600 px-3 py-1.5 font-medium hover:bg-sky-500 disabled:opacity-40">Plan certificate upload</button>
            </div>
          </form>
        </Card>)}

      {plan && <FwPlanDialog change={plan} onClose={() => { setPlan(null); comp.reload(); }} onDone={() => comp.reload()} />}
    </div>
  );
}
