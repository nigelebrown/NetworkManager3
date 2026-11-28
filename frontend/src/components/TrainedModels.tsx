import { useState } from "react";
import { Card } from "./ui";
import { useLoad } from "./useLoad";
import { Notice, Pill, ago, btn2, useAction } from "./viz";
import { getSession } from "../services/api";
import { intel, type MlTarget } from "../services/intel";

const modeTone = (m: string) => m === "PRIMARY" ? "good" : m === "SHADOW" ? "info" : "fair";
const n = (v: unknown) => (v == null ? "—" : String(v));

/** Which engine produces the probability of record for each predictive target (dashboard strip). */
export function EngineOfRecord() {
  const st = useLoad(intel.mlStatus, [], 60000);
  if (!st.data) return null;
  return <section className="inm-card" aria-label="Prediction engine of record">
    <h2 className="mb-2 text-sm font-semibold">Engine of record</h2>
    <ul className="grid gap-2 md:grid-cols-3">{st.data.targets.map((t: MlTarget) => <li key={t.target} className="rounded border p-2 text-xs">
      <div className="flex items-center gap-2"><b>{t.target}</b><Pill value={t.mode.replace(/_/g, " ")} tone={modeTone(t.mode)} /></div>
      <div className="mt-1 text-slate-500">{t.reason}</div>
      {t.model && <div className="mt-1">{t.model.name} · {t.model.dataset_source} · health {t.model.health ?? "—"}</div>}
    </li>)}</ul>
    <p className="mt-2 text-[11px] text-slate-500">{st.data.statement}</p>
  </section>;
}

/** Trained breach-risk model lifecycle: train, review metrics, independent promotion, drift, rollback, settings. */
export default function TrainedModels() {
  const st = useLoad(intel.mlStatus, [], 60000);
  const models = useLoad(intel.mlModels, [], 60000);
  const runs = useLoad(intel.mlRuns, [], 60000);
  const act = useAction();
  const me = getSession();
  const admin = me?.role === "admin";
  const approver = !!me && ["admin", "approver"].includes(me.role);
  const [target, setTarget] = useState("PRED_CPU_BREACH");
  const [cmp, setCmp] = useState<Awaited<ReturnType<typeof intel.mlCompare>> | null>(null);
  const reload = () => { st.reload(); models.reload(); runs.reload(); };
  const trained = (models.data ?? []).filter(m => m.target);
  return <Card title="Trained breach-risk models" right={admin && <span className="flex flex-wrap gap-1">
    <select aria-label="Model target" value={target} onChange={e => setTarget(e.target.value)}>
      {(st.data?.targets ?? []).map(t => <option key={t.target}>{t.target}</option>)}</select>
    <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.mlTrain(target, false); reload();
      return r.run.status === "SUCCEEDED" ? `Candidate ${r.model?.name} trained on live telemetry.` : `Not trained: ${String(r.run.summary.reason ?? r.run.status)}`; })}>Train on live telemetry</button>
    <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.mlTrain(target, true); reload();
      return `Synthetic pipeline run ${r.run.status}: ${r.model?.name ?? ""} (never production-validated).`; })}>Train on synthetic data</button></span>}>
    <Notice error={act.error} message={act.message} />
    <p className="text-xs text-slate-500">Logistic regression on 12 features computed only from history before each forecast time; temporal train / validation / holdout split with a horizon embargo. Holdout metrics are compared with the climatological base rate and the statistical engine on the same windows. Models trained on synthetic or demo data are never production-validated.</p>
    <table className="inm-table mt-2 text-xs"><thead><tr><th>Model</th><th>Data</th><th>Holdout AUC</th><th>Brier (stat / base)</th><th>Validation</th><th>Status · health</th><th></th></tr></thead>
      <tbody>{trained.map(m => { const h = m.metrics.holdout ?? {}; return <tr key={m.id}>
        <td><b>{m.name}</b><div className="text-[10px] text-slate-500">{m.trained_by} · {ago(m.trained_at)} · split {JSON.stringify(m.split)}</div></td>
        <td><Pill value={m.dataset_source ?? "—"} tone={m.dataset_source === "LIVE" ? "good" : "fair"} /></td>
        <td>{n(h.auc)}</td><td>{n(h.brier)} ({n(m.metrics.statistical_holdout_brier)} / {n(m.metrics.holdout_baseline_brier)})</td>
        <td><Pill value={m.production_validated ? "PRODUCTION VALIDATED" : "NOT VALIDATED"} tone={m.production_validated ? "good" : "fair"} />
          <div className="max-w-xs text-[10px] text-slate-500">{m.validation_note}</div></td>
        <td><Pill value={m.status} tone={m.status === "PROMOTED" ? "good" : m.status === "CANDIDATE" ? "info" : "none"} /> {m.health && <span className="text-[10px]">{m.health}</span>}</td>
        <td className="space-x-1 whitespace-nowrap">
          {m.status === "CANDIDATE" && approver && me?.username !== m.trained_by && <>
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await intel.mlDecide(m.id, "promote"); reload(); return `${m.name} promoted.`; })}>Promote</button>
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await intel.mlDecide(m.id, "reject"); reload(); return `${m.name} rejected.`; })}>Reject</button></>}
          {m.status === "PROMOTED" && <>
            {admin && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.mlDrift(m.id); reload(); return `Drift ${r.state} (max PSI ${r.max_psi ?? "—"}, ${r.samples} samples); health ${r.health}.`; })}>Check drift</button>}
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.mlPerformance(m.id); return `Live performance ${r.state}: ${r.scored} scored window(s).`; })}>Live performance</button>
            {approver && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await intel.rollback(m.id); reload(); return r.mode === "RULES_ONLY" ? "Rolled back: rules and statistical engine only." : "Previous model restored."; })}>Roll back</button>}</>}
        </td></tr>; })}
        {!trained.length && <tr><td colSpan={7} className="py-3 text-center text-slate-500">No trained breach-risk models yet.</td></tr>}</tbody></table>
    {st.data && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
      <label className="flex items-center gap-1"><input type="checkbox" disabled={!admin || act.busy} checked={st.data.settings.inference_enabled}
        onChange={e => act.run(async () => { await intel.mlSettings({ inference_enabled: e.target.checked }); reload(); return e.target.checked ? "ML inference enabled." : "ML inference disabled; statistical engine in use."; })} /> ML inference enabled</label>
      <label className="flex items-center gap-1"><input type="checkbox" disabled={!admin || act.busy} checked={st.data.settings.retrain_on_drift}
        onChange={e => act.run(async () => { await intel.mlSettings({ retrain_on_drift: e.target.checked }); reload(); })} /> Retrain on drift</label>
      <span>Scheduled retraining every {st.data.settings.retrain_interval_days} days on {st.data.settings.history_days} days of history (candidates only).</span>
    </div>}
    <div className="mt-3 text-xs">
      <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { setCmp(await intel.mlCompare(target)); })}>Compare versions on the same observations</button>
      {cmp && <div className="mt-2">{cmp.reason ? <p>{cmp.reason}</p> : <>
        <p>{cmp.windows} most recent windows ({cmp.positives} breaches) · statistical engine Brier {n(cmp.statistical_brier)} · base rate Brier {n(cmp.climatology_brier)} · best: <b>{cmp.best_version ?? "—"}</b></p>
        <table className="inm-table"><thead><tr><th>Version</th><th>Status</th><th>Brier</th><th>Brier skill</th><th>AUC</th></tr></thead>
          <tbody>{cmp.versions.map(v => <tr key={v.model}><td>{v.model}</td><td>{v.status}</td>
            <td>{v.comparable ? n(v.brier) : v.reason}</td><td>{n(v.brier_skill)}</td><td>{n(v.auc)}</td></tr>)}</tbody></table></>}</div>}
    </div>
    {!!runs.data?.length && <details className="mt-2 text-xs"><summary>Training runs ({runs.data.length})</summary>
      <ul>{runs.data.slice(0, 15).map(r => <li key={r.id}>{ago(r.started_at)} · {r.target} · {r.trigger} · {r.dataset_source} · <b>{r.status}</b> {r.summary.reason ? `— ${String(r.summary.reason)}` : ""}</li>)}</ul></details>}
  </Card>;
}

