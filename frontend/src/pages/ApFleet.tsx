import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
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
  const pending = useLoad(wl.pendingKeys, [], 0);
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [autoRest, setAutoRest] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = status.data;
  useEffect(() => { if (s?.state === "SUCCEEDED") onDone(); }, [s?.state, s?.finished_at]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<string>) => { setBusy(true); setMsg(null);
    try { setMsg(await fn()); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); } };
  const keys = pending.data ?? [];
  return <section className="inm-card" id="autodiscover"><div className="inm-cardhead"><h2>Auto-onboard access points (cluster-aware)</h2></div>
    <div className="inm-cardbody space-y-3 text-sm">
      <p className="text-slate-600">One run finds each AP's Instant cluster from its own IP, registers the Virtual Controller once and links every member AP —
        no per-AP onboarding. Where a cluster's REST API is off, it is read over a read-only SSH CLI session; credentials are only sent to hosts whose SSH key you approved.</p>
      <ol className="grid gap-3 md:grid-cols-3">
        <li className="rounded-lg border p-3"><b>1 · Scan SSH host keys</b><p className="text-xs text-slate-500">Unauthenticated: no password is sent. Only APs not yet in a polled cluster.</p>
          <button className="inm-btn-secondary mt-2" disabled={busy} onClick={() => run(async () => { const r = await wl.scanKeys(); pending.reload();
            const changed = r.results.filter(x => x.status === "CHANGED").length;
            return `Scanned ${r.scanned} host(s): ${r.results.filter(x => x.status === "NEW").length} new key(s), ${r.results.filter(x => x.status === "ALREADY_APPROVED").length} already approved, ${r.results.filter(x => x.error).length} unreachable on TCP/22.` +
              (changed ? ` ${changed} host key(s) CHANGED since approval — polling of those controllers is stopped until you verify and re-approve.` : ""); })}>Scan host keys</button></li>
        <li className="rounded-lg border p-3"><b>2 · Approve fingerprints</b><p className="text-xs text-slate-500">{keys.length} pending. Compare with the AP console if in doubt.</p>
          {keys.length > 0 && <div className="mt-2 max-h-40 overflow-auto text-xs">
            <label className="flex gap-1 font-semibold"><input type="checkbox" checked={keys.every(k => sel[k.ip])}
              onChange={e => setSel(Object.fromEntries(keys.map(k => [k.ip, e.target.checked])))} />Select all</label>
            {keys.map(k => <label key={k.ip} className="flex gap-1 font-mono"><input type="checkbox" checked={!!sel[k.ip]}
              onChange={e => setSel({ ...sel, [k.ip]: e.target.checked })} />{k.ip} {k.fingerprint.slice(0, 22)}…
              {k.changed_from && <span className="ml-1 rounded bg-red-100 px-1 font-sans font-bold text-red-700" title={`Previously approved: ${k.changed_from}`}>KEY CHANGED — verify before approving</span>}</label>)}</div>}
          <button className="inm-btn-secondary mt-2" disabled={busy || !Object.values(sel).some(Boolean)} onClick={() => run(async () => {
            const items = keys.filter(k => sel[k.ip]).map(k => ({ ip: k.ip, fingerprint: k.fingerprint }));
            if (!window.confirm(`Approve ${items.length} SSH host key(s)? Credentials will be sent to these hosts for read-only commands.`)) return "Cancelled.";
            const r = await wl.approveKeys(items); pending.reload(); setSel({}); return `${r.filter(x => x.approved).length} key(s) approved.`; })}>Approve selected</button></li>
        <li className="rounded-lg border p-3"><b>3 · Run auto-onboarding</b>
          <label className="mt-1 flex items-start gap-2 text-xs"><input className="mt-0.5" type="checkbox" checked={autoRest} onChange={e => setAutoRest(e.target.checked)} />
            <span>Also try to enable REST on clusters where it is off (fixed 4-command <code>allow-rest-api</code> bootstrap; only runs where the write policy and /32 approval permit — otherwise the cluster is read over SSH).</span></label>
          <button className="inm-btn-primary mt-2" disabled={busy || s?.state === "RUNNING"} onClick={() => run(async () => { await wl.autodiscover(autoRest); status.reload(); return "Auto-onboarding started."; })}>
            {s?.state === "RUNNING" ? `Running… ${s.done ?? 0}/${s.total ?? "?"}` : "Run auto-onboarding"}</button></li>
      </ol>
      {msg && <p className="rounded bg-slate-50 p-2" role="status">{msg}</p>}
      {s && s.state !== "NEVER_RUN" && s.state !== "RUNNING" && <details open={s.state === "FAILED"}><summary className="cursor-pointer font-semibold">
        Last run: {s.state} {s.finished_at && `· ${ago(s.finished_at)}`} {s.counts && "· " + Object.entries(s.counts).map(([k, v]) => `${k.replace(/_/g, " ").toLowerCase()} ${v}`).join(" · ")}</summary>
        {s.error && <p className="text-red-700">{s.error}</p>}
        {!!s.clusters?.length && <table className="inm-table mt-2"><thead><tr><th>Cluster</th><th>VC</th><th>Members</th><th>Read via</th><th>REST</th><th>First poll</th></tr></thead>
          <tbody>{s.clusters.map(c => <tr key={c.name}><td>{c.name}</td><td>{c.vc_ip}</td><td>{c.members}</td><td>{c.transport}</td><td className="text-xs">{c.rest_state}</td>
            <td>{c.first_poll_ok ? <Pill value="OK" tone="good" /> : <span className="text-xs text-red-700">{c.first_poll_error ?? "failed"}</span>}</td></tr>)}</tbody></table>}
        {!!s.results?.filter(r => !["LINKED", "CLUSTER_REGISTERED", "ALREADY_MONITORED"].includes(r.result)).length && <>
          <p className="mt-2 font-semibold">Needs attention</p>
          <table className="inm-table"><thead><tr><th>AP</th><th>IP</th><th>Result</th><th>What to do</th></tr></thead><tbody>
            {s.results.filter(r => !["LINKED", "CLUSTER_REGISTERED", "ALREADY_MONITORED"].includes(r.result)).slice(0, 100).map(r =>
              <tr key={r.device_id}><td>{r.hostname}</td><td>{r.ip ?? "—"}</td><td><Pill value={r.result} tone="fair" /></td>
                <td className="text-xs">{r.detail ?? {
                  NO_CREDENTIAL: "Create an AP credential profile (it is tried automatically).", NO_MANAGEMENT_IP: "Add the AP's IP in inventory.",
                  TARGET_NOT_ALLOWED: "Add the management range to the approved CIDRs.", AUTH_REJECTED: "Credential refused — check the profile.",
                  NEEDS_HOSTKEY_APPROVAL: "Approve its SSH key in step 2, then run again.", UNREACHABLE: "No route / TCP 4343 and 22 closed.",
                }[r.result] ?? r.result}</td></tr>)}</tbody></table></>}
      </details>}
    </div></section>;
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
      <Kpi label="Clients" value={s.clients} sub={`${s.clusters_polled_ok} cluster(s) polling OK · ${s.clusters_failing} failing`} />
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
        <td><span title={[...a.condition_triggers, a.event_trigger].filter(Boolean).join("; ") || "no breach"}><Pill value={a.tier} tone={TIER_TONE[a.tier]} /></span></td>
        <td>{a.clients ?? "—"}</td><td><Radio r={a.radio_2g} warn={70} /></td><td><Radio r={a.radio_5g} warn={80} /></td>
        {mode === "aps" && <><td>{pct(a.cpu_percent)} / {pct(a.memory_percent)}</td><td>{fmtUptime(a.uptime_seconds)}</td><td className="text-xs">{a.site ?? "—"}</td></>}
        <td className="whitespace-nowrap"><button className="inm-btn-secondary" onClick={() => onDiagnose(a)}>Diagnose</button>
          <button className="inm-link ml-2" onClick={() => setOpen(open === a.device_id ? null : a.device_id)}>{open === a.device_id ? "less" : "why?"}</button></td></tr>
        {open === a.device_id && <tr><td colSpan={mode === "aps" ? 10 : 7} className="bg-slate-50 text-sm">
          <b>{a.state}:</b> {a.state_reason}. <b>What to do:</b> {a.guidance}
          {!!a.condition_triggers.length && <> · <b>Health trigger:</b> {a.condition_triggers.join("; ")}</>}
          {a.event_trigger && <> · <b>Events:</b> {a.event_trigger}</>}
          {!!a.unavailable_inputs.length && a.state === "ONLINE" && <div className="text-xs text-slate-500">Not measured: {a.unavailable_inputs.join(", ")}</div>}
        </td></tr>}</Fragment>)}</tbody></table></div>}
  </div>;
}
