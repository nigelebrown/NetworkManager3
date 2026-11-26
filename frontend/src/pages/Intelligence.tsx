/** Network Intelligence workspaces: Recommendations, Predictive health, Operational analytics, AI rules. */
import { ReactNode, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { exportAnalytics, intel, type IntelRec, type Rule } from "../services/intel";
import { EngineOfRecord } from "../components/TrainedModels";

const role = () => getSession()?.role ?? "viewer";
const canOperate = () => ["admin", "operator"].includes(role());
const canApprove = () => ["admin", "approver"].includes(role());
const riskTone = (r: string) => r === "high" ? "poor" : r === "medium" ? "fair" : "info";
const stateTone = (s: string) => s === "RESOLVED" ? "good" : s === "DISMISSED" ? "none" : s === "ACKNOWLEDGED" ? "info" : "fair";
const pct = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v * 100)}%`;

function Section({ title, right, children, footer }: { title: string; right?: ReactNode; children: ReactNode; footer?: string }) {
  return <section className="inm-card"><div className="inm-cardhead"><h2>{title}</h2>{right}</div>
    <div className="inm-cardbody">{children}</div>{footer && <div className="inm-cardfooter">{footer}</div>}</section>;
}
function Table({ head, rows, empty }: { head: string[]; rows: ReactNode[][]; empty: string }) {
  return <div className="inm-tablewrap"><table className="inm-table"><thead><tr>{head.map(h => <th key={h}>{h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, k) => <td key={k}>{c}</td>)}</tr>)}</tbody></table>
    {!rows.length && <div className="inm-empty">{empty}</div>}</div>;
}
function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="inm-kpi"><span className="inm-kpi-label">{label}</span><span className="inm-kpi-value">{value}</span>
    {hint && <span className="inm-kpi-sub">{hint}</span>}</div>;
}

// ---------------------------------------------------------------------------------- Recommendations
export function IntelRecommendations() {
  const [f, setF] = useState({ state: "ADVISORY,ACKNOWLEDGED", risk: "", engine: "", source: "", q: "" });
  const recs = useLoad(() => intel.recs(f), [f.state, f.risk, f.engine, f.source, f.q], 30000, ["recommendations", "learning"]);
  const act = useAction();
  const [note, setNote] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  return <div className="space-y-4">
    <PageHeader eyebrow="Intelligence & automation" title="Recommendations"
      subtitle="Every evidence-backed finding from site rules, device insights and validated predictions — deduplicated, with freshness, reason codes and triage. Nothing here changes a device."
      right={canOperate() && <div className="inm-actionbar">
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.analyzeDevices(); recs.reload();
          return `Device insights: ${r.created} new, ${r.updated} refreshed, ${r.suppressed} suppressed on ${r.evaluated_devices} device(s).`; })}>Analyze devices</button>
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.analyzeSites(); recs.reload();
          return `Site rules ${r.ruleset_version}: ${r.created} new, ${r.updated ?? 0} refreshed, ${r.suppressed ?? 0} suppressed.`; })}>Analyze sites</button></div>} />
    <Notice error={act.error || recs.error} message={act.message} />
    <section className="inm-status-overview">
      {["ADVISORY", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"].map(s => <Kpi key={s} label={s.toLowerCase()} value={recs.data?.counts[s] ?? 0} />)}
    </section>
    <div className="inm-actionbar">
      <select aria-label="State" value={f.state} onChange={e => setF({ ...f, state: e.target.value })}>
        <option value="ADVISORY,ACKNOWLEDGED">Open</option><option value="">All</option>
        {["ADVISORY", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"].map(s => <option key={s}>{s}</option>)}</select>
      <select aria-label="Risk" value={f.risk} onChange={e => setF({ ...f, risk: e.target.value })}><option value="">Any risk</option>
        {["high", "medium", "low"].map(s => <option key={s}>{s}</option>)}</select>
      <select aria-label="Engine" value={f.engine} onChange={e => setF({ ...f, engine: e.target.value })}><option value="">Any engine</option>
        <option value="RULE">Rules</option><option value="PREDICTIVE">Predictive</option></select>
      <select aria-label="Source" value={f.source} onChange={e => setF({ ...f, source: e.target.value })}><option value="">Live & demo</option>
        <option value="REAL">Live (REAL)</option><option value="DEMO">Demo</option></select>
      <input aria-label="Search recommendations" placeholder="Search" value={f.q} onChange={e => setF({ ...f, q: e.target.value })} />
    </div>
    <div className="space-y-3">{(recs.data?.items ?? []).map((r: IntelRec) => <section key={r.id} className="inm-card" data-rec={r.rule_id}>
      <div className="inm-cardbody space-y-1 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Pill value={r.risk} tone={riskTone(r.risk)} /><b>{r.title}</b><Pill value={r.state} tone={stateTone(r.state)} />
          <Pill value={r.freshness} tone={r.freshness === "FRESH" ? "good" : "none"} /><Pill value={r.source_type} tone={r.source_type === "DEMO" ? "demo" : "none"} />
          <span className="text-xs text-slate-500">{r.engine} · {r.rule_id} v{r.rule_version} · seen {r.occurrences}× · last {ago(r.last_seen_at)}</span>
        </div>
        <p>{r.explanation}</p>
        <div className="flex flex-wrap gap-3 text-xs">{r.evidence_links.map(l => <Link key={l.href} className="inm-link" to={l.href}>{l.label}</Link>)}
          {r.site && <span>Site: {r.site}</span>}<span>Reasons: {r.reason_codes.join(", ")}</span>
          <button className="inm-link" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide evidence" : "Evidence"}</button></div>
        {open === r.id && <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 text-[11px]">{JSON.stringify(r.evidence, null, 1)}</pre>}
        {r.reviewed_by && <div className="text-xs">Triage by {r.reviewed_by}: {r.review_note}</div>}
        {r.outcomes.length > 0 && <div className="text-xs">Outcome: {r.outcomes.map(o => String(o.status)).join(", ")}</div>}
        {canOperate() && <div className="flex flex-wrap gap-2">
          <input aria-label="Triage note" className="grow" placeholder="Triage note (required)" value={note[r.id] ?? ""} onChange={e => setNote({ ...note, [r.id]: e.target.value })} />
          {(r.state === "ADVISORY" ? ["ACKNOWLEDGE", "RESOLVE", "DISMISS"] : r.state === "ACKNOWLEDGED" ? ["RESOLVE", "DISMISS"] : ["REOPEN"]).map(a =>
            <button key={a} className={btn2} disabled={act.busy || (note[r.id] ?? "").length < 3}
              onClick={() => act.run(async () => { await intel.triage(r.id, a, note[r.id]); recs.reload(); return `${a.toLowerCase()}d.`; })}>{a.toLowerCase()}</button>)}</div>}
      </div></section>)}
      {!recs.data?.items.length && <div className="inm-empty">No recommendations match. Run an analysis or change the filters.</div>}</div>
  </div>;
}

// ---------------------------------------------------------------------------------- Predictive health
export function PredictiveHealth() {
  const [state, setState] = useState("");
  const p = useLoad(() => intel.predictive(state || undefined), [state], 60000, ["recommendations"]);
  const act = useAction();
  const run = p.data?.run;
  return <div className="space-y-4">
    <PageHeader eyebrow="Intelligence & automation" title="Predictive health" subtitle={p.data?.statement ?? ""}
      right={canOperate() && <div className="inm-actionbar">
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.scorePredictive(); p.reload();
          return `${r.scored} finished window(s) scored against observed data.`; })}>Score past predictions</button>
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.runPredictive(); p.reload();
          return `Run ${r.status}: ${r.validation_state}.`; })}>Run predictions</button></div>} />
    <Notice error={act.error || p.error} message={act.message} />
    <EngineOfRecord />
    {run && <section className="inm-status-overview">
      <Kpi label="Model" value={run.model_version} hint={`run ${ago(run.created_at)} by ${run.requested_by}`} />
      <Kpi label="Validation" value={<Pill value={run.validation_state} tone={run.validation_state === "VALIDATED" ? "good" : run.validation_state === "PARTIALLY_VALIDATED" ? "info" : "fair"} />} hint="per rule below (walk-forward backtest)" />
      <Kpi label="At risk" value={run.summary?.predictions?.AT_RISK ?? 0} hint={`${run.summary?.predictions?.INSUFFICIENT_DATA ?? 0} insufficient data`} />
      <Kpi label="Surfaced" value={run.summary?.surfaced_recommendations ?? 0} hint="validated rules only" />
    </section>}
    {run?.evaluation && <Section title="Backtest evaluation per rule" footer="A rule is OPERATIONAL only with ≥30 evaluated windows, Brier skill > 0 and calibration gap ≤ 0.25. Otherwise its predictions are EXPERIMENTAL and never become recommendations.">
      <Table head={["Rule", "State", "Windows", "Base rate", "Brier skill", "Precision", "Recall", "False alerts", "Calibration"]}
        rows={Object.entries(run.evaluation).map(([rid, e]) => [rid, <span><Pill value={e.state} tone={e.state === "VALIDATED" ? "good" : "fair"} />{e.reason && <div className="text-[10px] text-slate-500">{e.reason}</div>}</span>,
          e.windows, pct(e.base_rate), e.brier_skill ?? "—", pct(e.precision), pct(e.recall), pct(e.false_alert_rate),
          <span className="text-[10px]">{(e.calibration ?? []).map(c => `${c.range}: ${pct(c.mean_predicted)}→${pct(c.observed_rate)} (n${c.n})`).join(" · ") || "—"}</span>])} empty="" />
    </Section>}
    {!!run?.summary?.drift?.length && <Section title="Drift detected"><ul className="text-xs">{run.summary.drift.map((d: any, i: number) =>  // eslint-disable-line @typescript-eslint/no-explicit-any
      <li key={i}>{d.metric} on {d.device_id.slice(0, 8)}: recent mean {d.recent_mean} vs {d.reference_mean} (z {d.z})</li>)}</ul></Section>}
    <Section title="Predictions" right={<select aria-label="Prediction state" value={state} onChange={e => setState(e.target.value)}>
      <option value="">All</option><option value="AT_RISK">At risk</option><option value="NO_RISK_EXPECTED">No risk expected</option><option value="INSUFFICIENT_DATA">Insufficient data</option></select>}>
      <Table head={["Device", "Metric", "Window", "State", "Probability", "Predicted peak", "Confidence", "Label", "Outcome"]}
        rows={(p.data?.predictions ?? []).map(x => [<Link className="inm-link" to={`/devices/${x.device_id}/360`}>{x.hostname ?? x.device_id.slice(0, 8)}</Link>,
          `${x.metric} ≥ ${x.threshold}`, `${new Date(x.window_start).toLocaleString()} + ${Math.round((+new Date(x.window_end) - +new Date(x.window_start)) / 36e5)} h`,
          <Pill value={x.state} tone={x.state === "AT_RISK" ? "poor" : x.state === "NO_RISK_EXPECTED" ? "good" : "none"} />,
          x.probability == null ? <span title={x.reason ?? ""}>—</span> : pct(x.probability), x.predicted_peak ?? "—", x.confidence,
          <span className={x.label === "OPERATIONAL" ? "text-emerald-600" : "text-amber-600"}>{x.label}</span>,
          x.outcome ? `${x.outcome}${x.observed_peak != null ? ` (${x.observed_peak})` : ""}` : "pending"])}
        empty="No predictions yet. Run predictions after at least 7 days of recorded telemetry." />
    </Section>
  </div>;
}

// ---------------------------------------------------------------------------------- Operational analytics
export function OperationalAnalytics() {
  const [f, setF] = useState({ window: "24h", source: "REAL", family: "" });
  const a = useLoad(() => intel.analytics({ ...f, family: f.family || undefined }), [f.window, f.source, f.family], 60000, ["telemetry", "devices"]);
  const [site, setSite] = useState<string | null>(null);
  const drill = useLoad(() => site ? intel.analyticsDevices({ ...f, family: f.family || undefined, site_name: site }) : Promise.resolve(null),
    [site, f.window, f.source, f.family], 0);
  const act = useAction();
  const t = a.data?.totals;
  const m = (s: { metrics: Record<string, { avg: number | null; p95: number | null; n: number }> }, k: string) =>
    s.metrics[k]?.n ? `${s.metrics[k].avg} / ${s.metrics[k].p95}` : "—";
  return <div className="space-y-4">
    <PageHeader eyebrow="Intelligence & automation" title="Operational analytics"
      subtitle="Aggregated measured telemetry by site and device, with freshness, coverage and reconciliation. Live and demo data are never mixed."
      right={<div className="inm-actionbar">{["csv", "xlsx", "pdf"].map(x => <button key={x} className={btn2} disabled={act.busy}
        onClick={() => act.run(async () => { await exportAnalytics({ ...f, family: f.family || undefined }, x); return `Exported ${x.toUpperCase()}.`; })}>Export {x.toUpperCase()}</button>)}</div>} />
    <Notice error={act.error || a.error} message={act.message} />
    <div className="inm-actionbar">
      <select aria-label="Window" value={f.window} onChange={e => setF({ ...f, window: e.target.value })}>{["1h", "24h", "7d", "30d"].map(w => <option key={w}>{w}</option>)}</select>
      <select aria-label="Data source" value={f.source} onChange={e => setF({ ...f, source: e.target.value })}>
        <option value="REAL">Live (REAL)</option><option value="DEMO">Demo</option><option value="ALL">Both (per-device provenance kept)</option></select>
      <select aria-label="Family" value={f.family} onChange={e => setF({ ...f, family: e.target.value })}>
        <option value="">APs & switches</option><option value="access_point">Access points</option><option value="switch">Switches</option></select>
    </div>
    {t && <section className="inm-status-overview">
      <Kpi label="Devices" value={t.devices} hint={`${t.reporting} reporting · ${t.no_data} no data`} />
      <Kpi label="Stale" value={t.stale} hint="newest sample > 30 min" />
      <Kpi label="Samples" value={t.samples.toLocaleString()} hint={`window ${a.data?.window}`} />
      <Kpi label="Reconciled" value={<Pill value={a.data?.reconciliation.reconciled ? "YES" : "NO"} tone={a.data?.reconciliation.reconciled ? "good" : "poor"} />}
        hint={`${t.alerts_opened} alerts · ${t.changes_requested} change requests`} />
    </section>}
    {a.data?.telemetry_store === "NOT_CONFIGURED" && <div className="inm-planned-notice">The telemetry database is not configured; no measured data can be shown.</div>}
    <Section title="By site" footer={(a.data?.notes ?? []).join(" ")}>
      <Table head={["Site", "Devices", "Reporting", "Stale", "No data", "Samples", "CPU avg / p95", "Memory avg / p95", "5 GHz util avg / p95", "Alerts", "Last observed", ""]}
        rows={(a.data?.sites ?? []).map(s => [s.site, s.devices, s.reporting, s.stale, s.no_data, s.samples, m(s, "cpu_percent"), m(s, "memory_percent"),
          m(s, "util_5g_percent"), s.alerts_opened, ago(s.last_observed), <button className={btn2} onClick={() => setSite(s.site)}>Drill down</button>])}
        empty="No devices in scope." />
    </Section>
    {site && <Section title={`Devices at ${site}`} right={<button className={btn2} onClick={() => setSite(null)}>Close</button>}>
      <Table head={["Device", "Family", "Freshness", "Samples", "Coverage", "CPU avg / p95", "Memory avg / p95", "Clients avg", "Alerts", "Changes"]}
        rows={(drill.data?.devices ?? []).map(d => [<Link className="inm-link" to={`/devices/${d.device_id}/360`}>{d.hostname ?? d.device_id.slice(0, 8)}</Link>,
          d.family, <Pill value={d.freshness} tone={statusTone(d.freshness === "FRESH" ? "PASS" : d.freshness === "STALE" ? "WARN" : "FAIL")} />,
          d.expected_samples ? `${d.samples}/${d.expected_samples}` : d.samples, pct(d.coverage), m(d, "cpu_percent"), m(d, "memory_percent"),
          d.metrics.clients?.avg ?? "—", d.alerts_opened, d.changes_requested])} empty="No devices." />
    </Section>}
  </div>;
}

// ---------------------------------------------------------------------------------- AI rules
export function AiRules() {
  const [sp] = useSearchParams();
  const rules = useLoad(intel.rules, [], 60000, ["recommendations"]);
  const [sel, setSel] = useState<string | null>(sp.get("rule"));
  const detail = useLoad(() => sel ? intel.rule(sel) : Promise.resolve(null), [sel], 0);
  const act = useAction();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const r = detail.data;
  const me = getSession()?.username;
  const groups = (rules.data?.rules ?? []).reduce<Record<string, Rule[]>>((g, x) => { (g[x.engine] ??= []).push(x); return g; }, {});
  return <div className="space-y-4">
    <PageHeader eyebrow="Intelligence & automation" title="AI rules" subtitle={rules.data?.governance ?? ""} />
    <Notice error={act.error || rules.error} message={act.message} />
    <p className="text-xs text-slate-500">{rules.data?.precedence}</p>
    {Object.entries(groups).map(([eng, rows]) => <Section key={eng} title={eng.replace("_", " ").toLowerCase()}>
      <Table head={["Precedence", "Rule", "Condition", "Parameters", "Suppression", "Version", "State", "Open / total", ""]}
        rows={rows.map(x => [x.precedence, <span><b>{x.title}</b><div className="text-[10px] text-slate-500">{x.rule_id}</div></span>,
          <span className="text-xs">{x.condition}</span>, <code className="text-[11px]">{JSON.stringify(x.params)}</code>, `${x.suppression_minutes} min`,
          `v${x.version}`, <span><Pill value={x.enabled ? "ENABLED" : "DISABLED"} tone={x.enabled ? "good" : "none"} />{x.pending && <Pill value="PENDING REVIEW" tone="fair" />}</span>,
          `${x.open_findings} / ${x.triggered_total}`, <button className={btn2} onClick={() => { setSel(x.rule_id); setDraft({}); setNote(""); }}>Open</button>])} empty="" />
    </Section>)}
    {r && <Section title={`${r.title} (v${r.version})`} right={<button className={btn2} onClick={() => setSel(null)}>Close</button>}>
      <dl className="grid gap-1 text-sm md:grid-cols-2"><dt>Inputs</dt><dd>{r.inputs.join(", ")}</dd><dt>Condition</dt><dd>{r.condition}</dd>
        <dt>Output</dt><dd>{r.output}</dd><dt>Risk</dt><dd>{r.risk}</dd><dt>Defaults</dt><dd><code>{JSON.stringify(r.default_params)}</code></dd>
        <dt>Last change</dt><dd>{r.updated_by} {ago(r.updated_at)}</dd></dl>
      {r.pending ? <div className="mt-3 rounded border border-amber-300 p-2 text-sm">
        <b>Pending change</b> by {r.pending_by}: <code>{JSON.stringify(r.pending)}</code> — {r.pending_note}
        {canApprove() && r.pending_by !== me && <div className="mt-2 flex flex-wrap gap-2">
          <input aria-label="Decision note" className="grow" placeholder="Decision note" value={note} onChange={e => setNote(e.target.value)} />
          <button className={btn} disabled={note.length < 3 || act.busy} onClick={() => act.run(async () => { await intel.decide(r.rule_id, true, note); detail.reload(); rules.reload(); setNote(""); return "Approved; new version active."; })}>Approve</button>
          <button className={btn2} disabled={note.length < 3 || act.busy} onClick={() => act.run(async () => { await intel.decide(r.rule_id, false, note); detail.reload(); rules.reload(); setNote(""); return "Rejected."; })}>Reject</button></div>}
        {r.pending_by === me && <p className="text-xs text-amber-700">Awaiting an independent approver.</p>}
      </div> : canOperate() && <div className="mt-3 space-y-2">
        <div className="grid gap-2 md:grid-cols-4">{Object.entries(r.schema).map(([k, s]) => <label key={k} className="text-sm">{k} ({s.min}–{s.max})
          <input aria-label={`Parameter ${k}`} className={inputCls} placeholder={String(r.params[k])} value={draft[k] ?? ""} onChange={e => setDraft({ ...draft, [k]: e.target.value })} /></label>)}
          <label className="text-sm">Suppression (min)<input aria-label="Suppression minutes" className={inputCls} placeholder={String(r.suppression_minutes)} value={draft.__supp ?? ""} onChange={e => setDraft({ ...draft, __supp: e.target.value })} /></label>
          <label className="text-sm">Enabled<select aria-label="Enabled" className={inputCls} value={draft.__enabled ?? ""} onChange={e => setDraft({ ...draft, __enabled: e.target.value })}>
            <option value="">unchanged</option><option value="true">enabled</option><option value="false">disabled</option></select></label></div>
        <div className="flex gap-2"><input aria-label="Change reason" className="grow" placeholder="Reason for change" value={note} onChange={e => setNote(e.target.value)} />
          <button className={btn} disabled={note.length < 3 || act.busy} onClick={() => act.run(async () => {
            const params = Object.fromEntries(Object.entries(draft).filter(([k, v]) => !k.startsWith("__") && v !== "").map(([k, v]) => [k, Number(v)]));
            await intel.propose(r.rule_id, { params, note, ...(draft.__supp ? { suppression_minutes: Number(draft.__supp) } : {}),
              ...(draft.__enabled ? { enabled: draft.__enabled === "true" } : {}) });
            detail.reload(); rules.reload(); setDraft({}); setNote(""); return "Change proposed; an independent approver must approve it."; })}>Propose change</button></div>
      </div>}
      <h3 className="mt-4 text-sm font-semibold">Version history</h3>
      <Table head={["Version", "Parameters", "Enabled", "Suppression", "Proposed by", "Approved by", "Note", "When"]}
        rows={(r.versions ?? []).map(v => [`v${v.version}`, <code className="text-[11px]">{JSON.stringify(v.params)}</code>, v.enabled ? "yes" : "no",
          `${v.suppression_minutes} min`, v.proposed_by, v.approved_by, v.note, ago(v.at)])} empty="" />
      <h3 className="mt-4 text-sm font-semibold">Recent findings from this rule</h3>
      <Table head={["Finding", "Rule version", "State", "When"]} rows={(r.recent_findings ?? []).map(x => [x.title, x.rule_version,
        <Pill value={x.state} tone={stateTone(x.state)} />, ago(x.created_at)])} empty="No findings yet." />
      <Link className="inm-link text-sm" to={`/recommendations`}>Open in Recommendations →</Link>
    </Section>}
  </div>;
}
