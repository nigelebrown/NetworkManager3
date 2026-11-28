import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Pill, ago } from "../components/viz";
import { request } from "../services/api";
import { onboardingApi } from "../services/onboarding";

type Subnet = { id: string; name: string; cidr: string; site_name: string; notes: string; used_by: string[]; batches: number };
type RunSummary = { id: string; state: string; started_at: string; finished_at?: string; summary?: Record<string, number>; steps?: string[];
  errors?: { scope?: string; subnet?: string; error: string }[]; progress?: { done: number; total: number; found: number } | null;
  jobs: { id: string; status: string; scope: string }[] };
type Rule = { id: string; name: string; subnet_ids: string[]; credential_ids: string[]; register_found: boolean; enable_monitoring: boolean;
  onboard_ap_clusters: boolean; schedule: string; enabled: boolean; next_run_at: string | null;
  subnets: { id: string; name: string; cidr: string }[]; credentials: { id: string; name: string; family: string; enabled: boolean }[];
  last_run: RunSummary | null };
const post = <T,>(p: string, b?: unknown, method = "POST") => request<T>(p, { method, body: b === undefined ? undefined : JSON.stringify(b) });
const EMPTY_RULE = { name: "", subnet_ids: [] as string[], credential_ids: [] as string[], register_found: true, enable_monitoring: true,
  onboard_ap_clusters: true, schedule: "manual", enabled: true };

/** Build discovery from reusable parts: saved subnets + credential profiles → a rule with follow-up actions and a schedule. */
export default function DiscoveryRules() {
  const subnets = useLoad(() => request<Subnet[]>("/v1/discovery/subnets"), [], 0);
  const creds = useLoad(onboardingApi.credentials, [], 0);
  const rules = useLoad(() => request<Rule[]>("/v1/discovery/rules"), [], 10000);
  const [sub, setSub] = useState({ id: "", name: "", cidr: "", site_name: "", notes: "" });
  const [cred, setCred] = useState({ name: "", device_family: "switch" as "switch" | "access_point", username: "", password: "" });
  const [rule, setRule] = useState<typeof EMPTY_RULE & { id?: string }>(EMPTY_RULE);
  const [msg, setMsg] = useState<{ ok?: string; err?: string }>({});
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string>) => { setBusy(true); setMsg({});
    try { setMsg({ ok: await fn() }); } catch (e) { setMsg({ err: e instanceof Error ? e.message : "Failed" }); } finally { setBusy(false); } };
  const running = (rules.data ?? []).some(r => r.last_run?.state === "SCANNING");
  useEffect(() => { if (!running) return; const t = setInterval(rules.reload, 4000); return () => clearInterval(t); }, [running]); // eslint-disable-line react-hooks/exhaustive-deps
  const step = (n: number, title: string, done: boolean, hint: string) => <div className="flex items-center gap-2">
    <span className={`flex h-7 w-7 items-center justify-center rounded-full text-sm font-bold ${done ? "bg-emerald-100 text-emerald-700" : "bg-blue-600 text-white"}`}>{done ? "✓" : n}</span>
    <div><b>{title}</b><div className="text-xs text-slate-500">{hint}</div></div></div>;
  const s = subnets.data ?? [], c = creds.data ?? [], r = rules.data ?? [];
  return <div className="space-y-4">
    <div><div className="inm-section-label">Sites & inventory</div><h1 className="inm-pagetitle">Discovery</h1>
      <p className="inm-subtitle">Save subnets and credential profiles once, then join them in a discovery rule. A rule scans its subnets read-only, tries the profiles in order, registers what it verifies, binds the credential that worked, switches on monitoring and onboards AP clusters — on demand or on a schedule.</p>
      <Link className="inm-link text-sm" to="/discovery/planner">Zero-traffic range planner</Link></div>
    <div className="grid gap-3 md:grid-cols-3">{step(1, "Save subnets", s.length > 0, `${s.length} saved`)}{step(2, "Save credential profiles", c.length > 0, `${c.length} saved`)}
      {step(3, "Create a rule and run it", r.some(x => x.last_run?.state === "COMPLETED"), `${r.length} rule(s)`)}</div>
    {msg.err && <ErrorNote error={msg.err} />}{msg.ok && <p className="rounded bg-emerald-50 p-2 text-sm text-emerald-800" role="status">{msg.ok}</p>}
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="inm-card"><div className="inm-cardhead"><h2>1 · Discovery subnets</h2></div><div className="inm-cardbody space-y-2 text-sm">
        <div className="grid gap-2 md:grid-cols-2">
          <input className="inm-form-control" placeholder="Name, e.g. HQ switch mgmt" value={sub.name} onChange={e => setSub({ ...sub, name: e.target.value })} />
          <input className="inm-form-control" placeholder="CIDR, e.g. 10.190.97.0/24 (max /22)" value={sub.cidr} onChange={e => setSub({ ...sub, cidr: e.target.value })} />
          <input className="inm-form-control" placeholder="Site for found devices" value={sub.site_name} onChange={e => setSub({ ...sub, site_name: e.target.value })} />
          <input className="inm-form-control" placeholder="Notes (optional)" value={sub.notes} onChange={e => setSub({ ...sub, notes: e.target.value })} /></div>
        <div className="flex gap-2"><button className="inm-btn-primary" disabled={busy || !sub.name || !sub.cidr || !sub.site_name} onClick={() => run(async () => {
          const { id, ...b } = sub; await post(id ? `/v1/discovery/subnets/${id}` : "/v1/discovery/subnets", b, id ? "PUT" : "POST");
          setSub({ id: "", name: "", cidr: "", site_name: "", notes: "" }); subnets.reload(); return id ? "Subnet updated." : "Subnet saved."; })}>{sub.id ? "Update subnet" : "Save subnet"}</button>
          {sub.id && <button className="inm-link" onClick={() => setSub({ id: "", name: "", cidr: "", site_name: "", notes: "" })}>Cancel edit</button>}</div>
        <table className="inm-table"><thead><tr><th>Name</th><th>CIDR</th><th>Site</th><th>Used by</th><th></th></tr></thead><tbody>
          {s.map(x => <tr key={x.id}><td>{x.name}</td><td className="font-mono text-xs">{x.cidr}<div className="text-[10px] text-slate-500">{x.batches} × /24</div></td><td>{x.site_name}</td>
            <td className="text-xs">{x.used_by.join(", ") || "—"}</td>
            <td className="whitespace-nowrap"><button className="inm-link" onClick={() => setSub({ id: x.id, name: x.name, cidr: x.cidr, site_name: x.site_name, notes: x.notes })}>edit</button>
              <button className="inm-link ml-2" onClick={() => run(async () => { await post(`/v1/discovery/subnets/${x.id}`, undefined, "DELETE"); subnets.reload(); return "Subnet deleted."; })}>delete</button></td></tr>)}</tbody></table>
        {!s.length && <p className="text-slate-500">No subnets yet. Add the management ranges where your APs and switches live.</p>}
      </div></section>
      <section className="inm-card"><div className="inm-cardhead"><h2>2 · Credential profiles</h2></div><div className="inm-cardbody space-y-2 text-sm">
        <div className="grid gap-2 md:grid-cols-2">
          <input className="inm-form-control" placeholder="Profile name" value={cred.name} onChange={e => setCred({ ...cred, name: e.target.value })} />
          <select className="inm-form-control" value={cred.device_family} onChange={e => setCred({ ...cred, device_family: e.target.value as "switch" | "access_point" })}>
            <option value="switch">CX switches (REST :443)</option><option value="access_point">Instant APs (REST :4343 / SSH)</option></select>
          <input className="inm-form-control" placeholder="Username" value={cred.username} onChange={e => setCred({ ...cred, username: e.target.value })} />
          <input className="inm-form-control" type="password" placeholder="Password (encrypted at rest)" value={cred.password} onChange={e => setCred({ ...cred, password: e.target.value })} /></div>
        <button className="inm-btn-primary" disabled={busy || cred.name.length < 2 || !cred.username || !cred.password} onClick={() => run(async () => {
          await onboardingApi.createCredential(cred); setCred({ name: "", device_family: cred.device_family, username: "", password: "" }); creds.reload(); return "Credential profile saved."; })}>Save profile</button>
        <table className="inm-table"><thead><tr><th>Name</th><th>Family</th><th>User</th><th>Status</th></tr></thead><tbody>
          {c.map(p => <tr key={p.id}><td>{p.name}</td><td>{p.device_family === "switch" ? "CX switch" : "Instant AP"}</td><td>{p.username}</td>
            <td><Pill value={p.enabled ? "enabled" : "disabled"} tone={p.enabled ? "good" : "none"} /></td></tr>)}</tbody></table>
        <p className="text-xs text-slate-500">Rotate or disable profiles under <Link className="inm-link" to="/onboarding">Add & configure devices</Link>.</p>
      </div></section>
    </div>
    <section className="inm-card"><div className="inm-cardhead"><h2>3 · Discovery rules</h2></div><div className="inm-cardbody space-y-3 text-sm">
      <div className="grid gap-3 rounded-lg border border-slate-200 p-3 lg:grid-cols-4">
        <label className="lg:col-span-1">Rule name<input className="inm-form-control w-full" value={rule.name} onChange={e => setRule({ ...rule, name: e.target.value })} placeholder="e.g. Kingston HQ nightly" /></label>
        <div><b>Subnets</b>{!s.length && <p className="text-xs text-amber-700">Save a subnet in step 1 first.</p>}{s.map(x => <label key={x.id} className="flex gap-1"><input type="checkbox" checked={rule.subnet_ids.includes(x.id)}
          onChange={e => setRule({ ...rule, subnet_ids: e.target.checked ? [...rule.subnet_ids, x.id] : rule.subnet_ids.filter(i => i !== x.id) })} />{x.name} <span className="text-slate-400">{x.cidr}</span></label>)}</div>
        <div><b>Credential profiles</b> <span className="text-xs text-slate-500">(tried in the order ticked)</span>
          {!c.some(p => p.enabled) && <p className="text-xs text-amber-700">Save a credential profile in step 2 first.</p>}
          {c.filter(p => p.enabled).map(p => { const pos = rule.credential_ids.indexOf(p.id);
            return <label key={p.id} className="flex gap-1"><input type="checkbox" checked={pos >= 0}
              onChange={e => setRule({ ...rule, credential_ids: e.target.checked ? [...rule.credential_ids, p.id] : rule.credential_ids.filter(i => i !== p.id) })} />
              {pos >= 0 && <span className="rounded bg-blue-100 px-1 text-[10px] text-blue-800">{pos + 1}</span>}{p.name}</label>; })}</div>
        <div><b>Then</b>
          <label className="flex gap-1"><input type="checkbox" checked={rule.register_found} onChange={e => setRule({ ...rule, register_found: e.target.checked })} />Register verified devices + bind working credential</label>
          <label className="flex gap-1"><input type="checkbox" checked={rule.enable_monitoring} onChange={e => setRule({ ...rule, enable_monitoring: e.target.checked })} />Turn on read-only switch monitoring</label>
          <label className="flex gap-1"><input type="checkbox" checked={rule.onboard_ap_clusters} onChange={e => setRule({ ...rule, onboard_ap_clusters: e.target.checked })} />Onboard AP clusters (cluster-aware)</label>
          <label className="mt-1 block">Schedule<select className="inm-form-control" value={rule.schedule} onChange={e => setRule({ ...rule, schedule: e.target.value })}>
            <option value="manual">Manual only</option><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label></div>
        <div className="flex gap-2 lg:col-span-4"><button className="inm-btn-primary" disabled={busy || !rule.name || !rule.subnet_ids.length || !rule.credential_ids.length} onClick={() => run(async () => {
          const { id, ...b } = rule; await post(id ? `/v1/discovery/rules/${id}` : "/v1/discovery/rules", b, id ? "PUT" : "POST"); setRule(EMPTY_RULE); rules.reload(); subnets.reload();
          return id ? "Rule updated." : "Rule saved — press Run now, or let the schedule run it."; })}>{rule.id ? "Update rule" : "Save rule"}</button>
          {rule.id && <button className="inm-link" onClick={() => setRule(EMPTY_RULE)}>Cancel edit</button>}</div>
      </div>
      {r.map(x => <div key={x.id} className="rounded-lg border border-slate-200 p-3">
        <div className="flex flex-wrap items-center gap-2"><b className="text-base">{x.name}</b><Pill value={x.schedule} tone="info" />
          <span className="text-xs text-slate-500">{x.subnets.map(y => y.cidr).join(", ")} · profiles {x.credentials.map((y, i) => `${i + 1}. ${y.name}`).join(" → ")}
            {x.next_run_at && ` · next run ${new Date(x.next_run_at).toLocaleString()}`}</span>
          <div className="ml-auto flex gap-2"><button className="inm-btn-primary" disabled={busy || x.last_run?.state === "SCANNING"} onClick={() => run(async () => {
            const res = await post<RunSummary>(`/v1/discovery/rules/${x.id}/run`); rules.reload();
            return res.state === "COMPLETED" ? "Run complete." : "Run started — progress updates below."; })}>Run now</button>
            <button className="inm-link" onClick={() => setRule({ ...EMPTY_RULE, ...x })}>edit</button>
            <button className="inm-link" onClick={() => run(async () => { await post(`/v1/discovery/rules/${x.id}`, undefined, "DELETE"); rules.reload(); subnets.reload(); return "Rule deleted."; })}>delete</button></div></div>
        {x.last_run && <div className="mt-2 rounded bg-slate-50 p-2 text-xs">
          <div>Last run <Pill value={x.last_run.state} tone={x.last_run.state === "COMPLETED" ? "good" : x.last_run.state === "FAILED" ? "poor" : "fair"} /> started {ago(x.last_run.started_at)}
            {x.last_run.progress && ` · scanned ${x.last_run.progress.done}/${x.last_run.progress.total} · found ${x.last_run.progress.found}`}</div>
          {x.last_run.steps?.length ? <ol className="mt-1 list-decimal pl-5">{x.last_run.steps.map(t => <li key={t}>{t}</li>)}</ol> : null}
          {x.last_run.errors?.map((e, i) => <div key={i} className="text-red-700">{e.subnet} {e.scope}: {e.error}</div>)}
          {x.last_run.state === "COMPLETED" && <div className="mt-1 flex gap-3"><Link className="inm-link" to="/inventory">Inventory</Link><Link className="inm-link" to="/collection">Monitoring</Link><Link className="inm-link" to="/aps">Access points</Link></div>}
        </div>}
      </div>)}
      {!r.length && <p className="text-slate-500">No rules yet. Tick at least one subnet and one credential profile above.</p>}
    </div></section>
  </div>;
}
