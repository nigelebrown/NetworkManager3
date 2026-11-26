import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router";
import DiagnosisPanel from "../components/DiagnosisPanel";
import GuidePanel from "../components/GuidePanel";
import { ErrorNote, fmtUptime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Pill, ago } from "../components/viz";
import { getSession } from "../services/api";
import { STATE_TONE, TIER_TONE, pct, wl, type FleetAp, type FleetCluster } from "../services/wireless";

function Kpi({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) {
  return <div className="inm-kpi"><span className="inm-kpi-label">{label}</span>
    <span className={`inm-kpi-value ${tone ?? ""}`}>{value}</span>{sub && <span className="inm-kpi-sub">{sub}</span>}</div>;
}
const Radio = ({ r, warn }: { r: FleetAp["radio_2g"]; warn: number }) => r.channel == null && r.util_percent == null
  ? <span className="text-slate-400">—</span>
  : <span className="whitespace-nowrap"><b>ch {r.channel ?? "?"}</b>
    {r.util_percent != null && <span className={r.util_percent >= warn ? "ml-1 font-semibold text-red-600" : "ml-1 text-slate-600"}>{pct(r.util_percent)}</span>}
    {r.noise_floor_dbm != null && <span className={r.noise_floor_dbm > -85 ? "ml-1 text-amber-700" : "ml-1 text-slate-400"}>{r.noise_floor_dbm} dBm</span>}</span>;

function AutoOnboard({ onDone }: { onDone: () => void }) {
  const status = useLoad(wl.autodiscoverStatus, [], 4000);
  const policy = useLoad(wl.onboardingPolicy, [], 0);
  const profiles = useLoad(wl.onboardingProfiles, [], 0);
  const pending = useLoad(wl.pendingKeys, [], 0);
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [autoRest, setAutoRest] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = status.data;
  const running = s?.state === "RUNNING" || s?.state === "QUEUED";
  useEffect(() => {
    if (policy.data && profiles.data) {
      setEnabled(policy.data.enabled);
      setAutoRest(policy.data.auto_enable_rest);
      setSelected(policy.data.credential_ids ?? profiles.data.filter(p => p.enabled).map(p => p.id));
    }
  }, [policy.data, profiles.data]);
  useEffect(() => { if (s?.state === "SUCCEEDED" || s?.clusters_saved) onDone(); },
    [s?.state, s?.finished_at, s?.clusters_saved]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null);
    try { setMsg(await fn()); }
    catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  };
  const changed = (pending.data ?? []).filter(k => k.changed_from);
  const available = (profiles.data ?? []).filter(p => p.enabled);
  const exceptions = (s?.results ?? []).filter(r =>
    !["LINKED", "CLUSTER_REGISTERED", "ALREADY_MONITORED", "SWITCH_MONITORING_ENABLED"].includes(r.result));
  return <section className="inm-card" id="autodiscover">
    <div className="inm-cardhead"><h2>Automatic AP and switch onboarding</h2></div>
    <div className="inm-cardbody space-y-3 text-sm">
      <p>Choose saved credentials once. New or imported devices are checked in the background.
        A successful login links the working profile, registers the AP cluster or switch monitoring plan,
        and starts collection. Progress survives API and worker restarts.</p>
      <ErrorNote error={policy.error || profiles.error || status.error} />
      <label className="flex items-center gap-2 font-semibold">
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Automatically onboard new and imported APs and switches
      </label>
      <fieldset className="rounded-lg border p-3"><legend>Saved credentials to try</legend>
        <div className="grid gap-2 md:grid-cols-2">{available.map(p =>
          <label key={p.id} className="flex items-center gap-2">
            <input type="checkbox" checked={selected.includes(p.id)} onChange={e =>
              setSelected(e.target.checked ? [...selected, p.id] : selected.filter(id => id !== p.id))} />
            {p.name} <span className="text-xs text-slate-500">{p.device_family.replace(/_/g, " ")}</span>
          </label>)}</div>
        {!available.length && <p>No enabled device credential profiles are available.</p>}
      </fieldset>
      {policy.data?.lockout_guard && <p className="text-xs text-slate-600" data-testid="lockout-guard">Account lockout guard:
        an account rejected {policy.data.lockout_guard.max_failures} time(s) within {policy.data.lockout_guard.window_minutes} minutes
        (any device, any run) is not tried again until the window ends; a REST rejection is never retried over SSH.
        {policy.data.lockout_guard.accounts.filter(a => a.held).length > 0 && <b> Held now: {policy.data.lockout_guard.accounts.filter(a => a.held).map(a => a.account).join(", ")}.</b>}</p>}
      <p className="text-xs text-slate-600">New SSH keys are recorded after a saved credential authenticates
        and a device read succeeds. A changed known key is listed for review.</p>
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" checked={autoRest}
        onChange={e => setAutoRest(e.target.checked)} />
        Try the existing REST-enablement bootstrap where permitted. APs can also be monitored through SSH.
      </label>
      <div className="flex flex-wrap gap-2">
        <button className="inm-btn-primary" disabled={busy || !selected.length} onClick={() => run(async () => {
          await wl.saveOnboardingPolicy({ enabled, credential_ids: selected, auto_trust_new_hosts: true, auto_enable_rest: autoRest });
          policy.reload(); return enabled ? "Automatic onboarding enabled." : "Automatic onboarding disabled.";
        })}>Save automation</button>
        <button className="inm-btn-secondary" disabled={busy || running || !selected.length} onClick={() => run(async () => {
          await wl.autodiscover(autoRest, undefined, selected); status.reload();
          return "Onboarding queued. The worker will continue even if you leave this page.";
        })}>Retry onboarding now</button>
      </div>
      {msg && <p role="status" className="rounded bg-slate-50 p-2">{msg}</p>}
      {running && <p role="status">{s?.state === "QUEUED" ? "Queued for worker" : "Onboarding"}:
        {" "}{s?.done ?? 0}/{s?.total ?? "?"} devices checked · {s?.clusters_saved ?? 0} clusters saved</p>}
      {s && s.state !== "NEVER_RUN" && <details open={s.state === "FAILED" || exceptions.length > 0}>
        <summary className="font-semibold">Latest run: {s.state}{s.finished_at && " · " + ago(s.finished_at)}</summary>
        {s.error && <p className="text-red-700">{s.error}</p>}
        {s.counts && <p>{Object.entries(s.counts).map(([k, v]) => k.replace(/_/g, " ").toLowerCase() + ": " + v).join(" · ")}</p>}
        {!!exceptions.length && <table className="inm-table mt-2"><thead><tr><th>Device</th><th>IP</th><th>Result</th><th>Detail</th></tr></thead>
          <tbody>{exceptions.slice(0,100).map(r => <tr key={r.device_id}><td>{r.hostname}</td><td>{r.ip ?? "—"}</td>
            <td>{r.result.replace(/_/g, " ")}</td><td>{r.detail ?? "Check credentials, identity and reachability."}</td></tr>)}</tbody></table>}
      </details>}
      {!!changed.length && <details><summary>{changed.length} changed SSH key(s) need review</summary>
        {changed.map(k => <div className="mt-2 flex flex-wrap gap-2" key={k.ip}>
          <code>{k.ip} · {k.fingerprint}</code>
          <button className="inm-btn-secondary" disabled={busy} onClick={() => run(async () => {
            if (!window.confirm("Replace the saved SSH key for " + k.ip + "? Verify the device identity before continuing.")) return "Cancelled.";
            await wl.approveKeys([{ip:k.ip,fingerprint:k.fingerprint}]); pending.reload();
            return "Replacement key saved. Retry onboarding when ready.";
          })}>Approve replacement key</button>
        </div>)}
      </details>}
    </div>
  </section>;
}

export default function ApFleet({ mode = "aps" }: { mode?: "aps" | "rf" }) {
  const fleet = useLoad(wl.fleet, [], 30000, ["devices", "telemetry", "sites"]);
  const loc = useLocation();
  const [cluster, setCluster] = useState("ALL");
  const [q, setQ] = useState("");
  const [state, setState] = useState("ALL");
  const [tier, setTier] = useState("ALL");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [diag, setDiag] = useState<FleetAp | null>(null);
  const admin = getSession()?.role === "admin";
  const d = fleet.data;
  const rows = useMemo(() => (d?.aps ?? []).filter(a =>
    (cluster === "ALL" || a.cluster_key === cluster) && (state === "ALL" || a.state === state) && (tier === "ALL" || a.tier === tier) &&
    (!q.trim() || [a.name, a.ip, a.serial, a.site, a.model, a.cluster].some(x => (x ?? "").toLowerCase().includes(q.toLowerCase())))), [d, cluster, state, tier, q]);
  const groups = useMemo(() => (d?.clusters ?? []).map(c => ({ c, aps: rows.filter(a => a.cluster_key === c.key)
    .sort((x, y) => mode === "rf" ? (y.radio_5g.util_percent ?? -1) - (x.radio_5g.util_percent ?? -1) : x.name.localeCompare(y.name)) }))
    .filter(g => g.aps.length), [d, rows, mode]);
  useEffect(() => { if (loc.hash === "#autodiscover") document.getElementById("autodiscover")?.scrollIntoView(); }, [loc.hash, d]);
  const s = d?.summary;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3"><div>
      <div className="inm-section-label">{mode === "rf" ? "Monitoring & diagnostics" : "Sites & inventory"}</div>
      <h1 className="inm-pagetitle">{mode === "rf" ? "Wireless & RF health" : "Access points"}</h1>
      <p className="inm-subtitle">{mode === "rf" ? "Radio load, noise and channel plan per cluster, busiest radios first. Click Diagnose on any AP for the cause and fix plan."
        : "Every AP grouped by its Instant cluster, with both radios, health tier and what to do when an AP is not reporting."}</p></div>
      <div className="flex gap-2"><Link className="inm-btn-secondary" to="/ap-health">AP health report</Link>
        {mode === "aps" && <Link className="inm-btn-secondary" to="/aps/controller">Controller actions</Link>}</div></div>
    <ErrorNote error={fleet.error} />
    {s && <section className="inm-status-overview">
      <Kpi label="Access points" value={s.aps_total} sub={`${s.monitored} in polled clusters · ${s.unlinked} not linked`} />
      <Kpi label="Online" value={s.states.ONLINE ?? 0} sub="fresh cluster observation" />
      <Kpi label="Attention needed" value={s.attention} sub={Object.entries(s.states).filter(([k]) => k !== "ONLINE").map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "none"} tone={s.attention ? "text-amber-600" : ""} />
      <Kpi label="Health tiers" value={`${s.tiers.GOLD ?? 0} / ${s.tiers.SILVER ?? 0} / ${s.tiers.BRONZE ?? 0}`} sub={`Gold / Silver / Bronze · ${s.tiers.UNRATED ?? 0} unrated`} />
      <Kpi label="Connected wireless clients" value={s.clients} sub={`${s.clusters_polled_ok} cluster(s) polling OK · ${s.clusters_failing} failing`} />
    </section>}
    {mode === "aps" && s && s.unlinked > 0 && <GuidePanel guide="monitoring" defaultOpen={false} />}
    {mode === "aps" && admin && <AutoOnboard onDone={fleet.reload} />}
    <section className="inm-card"><div className="inm-cardhead"><h2>By cluster ({rows.length} APs)</h2>
      <div className="inm-actionbar flex-wrap">
        <select aria-label="Cluster" className="inm-form-control" value={cluster} onChange={e => setCluster(e.target.value)}>
          <option value="ALL">All clusters ({d?.clusters.length ?? 0})</option>{(d?.clusters ?? []).map(c => <option key={c.key} value={c.key}>{c.name} ({c.aps_total})</option>)}</select>
        <input aria-label="Search" className="inm-form-control" placeholder="Name, IP, serial, site" value={q} onChange={e => setQ(e.target.value)} />
        <select aria-label="State" className="inm-form-control" value={state} onChange={e => setState(e.target.value)}>
          {["ALL", "ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN"].map(x => <option key={x}>{x}</option>)}</select>
        <select aria-label="Tier" className="inm-form-control" value={tier} onChange={e => setTier(e.target.value)}>
          {["ALL", "BRONZE", "SILVER", "GOLD", "UNRATED"].map(x => <option key={x}>{x}</option>)}</select></div></div>
      <div className="inm-cardbody space-y-4">{groups.map(({ c, aps }) => <ClusterBlock key={c.key} c={c} aps={aps} mode={mode}
        collapsed={!!collapsed[c.key]} toggle={() => setCollapsed({ ...collapsed, [c.key]: !collapsed[c.key] })}
        open={open} setOpen={setOpen} onDiagnose={setDiag} />)}
        {!groups.length && <div className="inm-empty">No APs match these filters.</div>}</div></section>
    {diag && <DiagnosisPanel target={{ kind: "ap", id: diag.device_id, label: diag.name }} onClose={() => setDiag(null)} />}
  </div>;
}

function ClusterBlock({ c, aps, mode, collapsed, toggle, open, setOpen, onDiagnose }: { c: FleetCluster; aps: FleetAp[]; mode: string;
  collapsed: boolean; toggle: () => void; open: string | null; setOpen: (s: string | null) => void; onDiagnose: (a: FleetAp) => void }) {
  const unlinked = c.key === "unlinked";
  return <div className="rounded-xl border border-slate-200">
    <button className="flex w-full flex-wrap items-center gap-3 rounded-t-xl bg-slate-50 px-4 py-3 text-left" onClick={toggle} aria-expanded={!collapsed}>
      <span className="text-slate-400">{collapsed ? "▸" : "▾"}</span><b className="text-slate-800">{c.name}</b>
      {!unlinked && <span className="text-xs text-slate-500">VC {c.vc_host ?? "—"} · read via {c.transport === "ssh_cli" ? "SSH CLI (REST off)" : "REST"}</span>}
      {!unlinked && (c.last_poll_ok ? <Pill value={`polled ${ago(c.last_poll_at)}`} tone="good" /> : c.last_poll_ok === false
        ? <span title={c.last_poll_error ?? ""}><Pill value="poll failing" tone="poor" /></span> : <Pill value="not polled yet" tone="none" />)}
      <span className="ml-auto text-xs text-slate-600">{c.aps_online}/{c.aps_total} online · {c.clients} clients · 2.4 GHz {pct(c.util_2g_avg)} · 5 GHz {pct(c.util_5g_avg)}
        {" · "}<span className="text-emerald-700">{c.tiers.GOLD ?? 0}G</span> <span className="text-amber-700">{c.tiers.SILVER ?? 0}S</span> <span className="text-red-700">{c.tiers.BRONZE ?? 0}B</span></span>
    </button>
    {!collapsed && unlinked && <p className="px-4 pt-2 text-xs text-amber-800">These APs are registered but no polled cluster reports them, so nothing observes them. Use <a className="underline" href="#autodiscover">Auto-onboard</a>.</p>}
    {!collapsed && c.last_poll_ok === false && <p className="px-4 pt-2 text-xs text-red-700">Last poll error: {c.last_poll_error}</p>}
    {!collapsed && <div className="overflow-x-auto"><table className="inm-table">
      <thead><tr><th>AP</th><th>State</th><th>Health</th><th>Clients</th><th>2.4 GHz</th><th>5 GHz</th>{mode === "aps" && <><th>CPU / Mem</th><th>Uptime</th><th>Site</th></>}<th></th></tr></thead>
      <tbody>{aps.map(a => <Fragment key={a.device_id}><tr>
        <td><Link className="inm-link" to={`/devices/${a.device_id}/360`}>{a.name}</Link>{a.is_vc && <span className="ml-1 rounded bg-blue-100 px-1 text-[10px] text-blue-800">VC</span>}
          <div className="text-[10px] text-slate-500">{a.model ?? "—"} · {a.ip ?? "no IP"}</div></td>
        <td><button title={a.state_reason} onClick={() => setOpen(open === a.device_id ? null : a.device_id)}><Pill value={a.state} tone={STATE_TONE(a.state)} /></button></td>
        <td><span title={[...a.condition_triggers, a.event_trigger, a.client_experience?.detail].filter(Boolean).join("; ")}>
          <Pill value={a.state === "OFFLINE" ? "OFFLINE" : a.tier} tone={a.state === "OFFLINE" ? "poor" : TIER_TONE[a.tier]} /></span></td>
        <td>{a.clients ?? "—"}</td><td><Radio r={a.radio_2g} warn={70} /></td><td><Radio r={a.radio_5g} warn={80} /></td>
        {mode === "aps" && <><td>{pct(a.cpu_percent)} / {pct(a.memory_percent)}</td><td>{fmtUptime(a.uptime_seconds)}</td><td className="text-xs">{a.site ?? "—"}</td></>}
        <td className="whitespace-nowrap"><button className="inm-btn-secondary" onClick={() => onDiagnose(a)}>Diagnose</button>
          <button className="inm-link ml-2" onClick={() => setOpen(open === a.device_id ? null : a.device_id)}>{open === a.device_id ? "less" : "why?"}</button></td></tr>
        {open === a.device_id && <tr><td colSpan={mode === "aps" ? 10 : 7} className="bg-slate-50 text-sm">
          <b>{a.state}:</b> {a.state_reason}. <b>What to do:</b> {a.guidance}
          {!!a.condition_triggers.length && <> · <b>Health trigger:</b> {a.condition_triggers.join("; ")}</>}
          {a.event_trigger && <> · <b>Events:</b> {a.event_trigger}</>}
          <div className="text-xs text-slate-600">{a.client_experience?.detail ?? "Client experience unavailable"}</div>
          {!!a.unavailable_inputs.length && a.state === "ONLINE" && <div className="text-xs text-slate-500">Not measured: {a.unavailable_inputs.join(", ")}</div>}
        </td></tr>}</Fragment>)}</tbody></table></div>}
  </div>;
}
