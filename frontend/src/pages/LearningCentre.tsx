import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { ops, type LearningItem } from "../services/ops";

function Row({ r, reload }: { r: LearningItem; reload: () => void }) {
  const act = useAction();
  const [reason, setReason] = useState("");
  return <tr className="border-t border-slate-800 align-top">
    <td className="py-2 text-xs text-slate-500">{ago(r.created_at)}</td>
    <td><div className="font-medium">{r.title}</div><div className="text-xs text-slate-400">{r.why}</div>
      {r.device_id && <Link className="text-xs text-sky-300" to={`/devices/${r.device_id}/360`}>device 360°</Link>}</td>
    <td><Pill value={r.kind} tone="info" /><div className="mt-1 font-mono text-[10px] text-slate-500">{r.analyzer_version}</div></td>
    <td><Pill value={r.evidence_status} tone={r.evidence_status === "OK" ? "good" : "fair"} /><div className="text-[10px] text-slate-500">{r.observed_samples} samples · {r.source_type}</div></td>
    <td>{r.feedback ? <div className="text-xs"><Pill value={r.feedback.decision} tone={statusTone(r.feedback.decision === "ACCEPTED" ? "PASS" : "FAIL")} /><div className="text-slate-400">{r.feedback.operator}: {r.feedback.reason}</div></div>
      : <div className="flex flex-col gap-1"><input aria-label="Feedback reason" className="rounded bg-slate-950 px-2 py-1 text-xs" placeholder="reason" value={reason} onChange={e => setReason(e.target.value)} />
        <div className="flex gap-1">{["ACCEPTED", "REJECTED", "DEFERRED"].map(d => <button key={d} className="rounded bg-slate-800 px-2 py-0.5 text-[11px]" disabled={act.busy || reason.length < 3}
          onClick={() => act.run(async () => { await ops.feedback(r.id, d, reason); reload(); })}>{d.toLowerCase()}</button>)}</div>
        <Notice error={act.error} message={null} /></div>}</td>
    <td className="text-xs">{r.outcomes.map((o, i) => <div key={i}><Pill value={String(o.status)} tone={o.status === "IMPROVED" ? "good" : o.status === "DEGRADED" ? "poor" : "none"} /></div>)}
      {!r.outcomes.length && <span className="text-slate-500">not evaluated</span>}</td>
  </tr>;
}

export default function LearningCentre() {
  const hist = useLoad(ops.learning, [], 30000);
  const models = useLoad(ops.models, [], 60000);
  const act = useAction();
  const items = hist.data?.items ?? [];
  const me = getSession();
  const [sim, setSim] = useState<{ at: string; op: string; result: string }[] | null>(null);
  return <div className="space-y-4">
    <PageHeader title="AI learning centre" subtitle="What the analyser recommended, why, on which evidence, what engineers decided and what actually happened afterwards. Models are only called trained when fitted on labelled outcomes with a time-separated holdout and promoted by an independent approver." />
    <ErrorNote error={hist.error} />
    <div className="grid gap-3 md:grid-cols-4">
      <Stat label="Recommendations" value={items.length} /><Stat label="With engineer feedback" value={items.filter(i => i.feedback).length} />
      <Stat label="Outcomes measured" value={items.filter(i => i.outcomes.length).length} />
      <Stat label="Active model" value={hist.data?.active_model?.name ?? "Rules only"} sub={hist.data?.active_model ? "promoted" : "no trained model promoted"} />
    </div>
    <Card title="Learning timeline"><table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th>When</th><th>Recommendation</th><th>Engine</th><th>Evidence</th><th>Engineer decision</th><th>Outcome</th></tr></thead>
      <tbody>{items.map(r => <Row key={r.id} r={r} reload={hist.reload} />)}{!items.length && <tr><td colSpan={6} className="py-4 text-center text-slate-500">No recommendations yet. Run analysis from <Link className="text-sky-300" to="/phase4-insights">Device insights</Link>.</td></tr>}</tbody></table></Card>
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Model registry" right={me?.role === "admin" && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
        const r = await ops.train(); models.reload(); return r.status === "CANDIDATE" ? "Candidate model trained; it needs independent promotion." : `Not trained: ${String(r.reason)}`; })}>Train candidate</button>}>
        <ul className="space-y-2 text-sm">{(models.data ?? []).map(m => <li key={m.id} className="rounded border border-slate-800 p-2">
          <div className="flex gap-2"><b>{m.name}</b><Pill value={m.status} tone={m.status === "PROMOTED" ? "good" : m.status === "REJECTED" ? "poor" : "fair"} /></div>
          <div className="font-mono text-[11px] text-slate-400">{JSON.stringify(m.metrics)}</div>
          {m.status === "CANDIDATE" && me && ["approver", "admin"].includes(me.role) && me.username !== m.trained_by && <div className="mt-1 flex gap-2">
            <button className={btn2} onClick={() => act.run(async () => { await ops.decideModel(m.id, "promote"); models.reload(); hist.reload(); })}>Promote</button>
            <button className={btn2} onClick={() => act.run(async () => { await ops.decideModel(m.id, "reject"); models.reload(); })}>Reject</button></div>}</li>)}
          {!models.data?.length && <li className="text-slate-500">No models. Training requires at least 20 measured, labelled outcomes.</li>}</ul>
        <Notice error={act.error} message={act.message} />
      </Card>
      <Card title="Shadow simulation — what a policy would do (no writes)">
        <p className="text-xs text-slate-400">Simulates 8 proposals 10 minutes apart with a 30-minute cooldown and a degradation after the 3rd action, to show cooldown and stop behaviour.</p>
        <button className={`${btn} mt-2`} onClick={() => act.run(async () => {
          const now = Date.now(); const r = await ops.shadow({ cooldown_seconds: 1800, degrade_after: 3,
            proposals: Array.from({ length: 8 }, (_, i) => ({ at: new Date(now + i * 600e3).toISOString(), op: "ap.radio.channel" })) });
          setSim(r.timeline); return `Simulation complete — device writes: ${r.device_writes}.`; })}>Run simulation</button>
        {sim && <ol className="mt-2 space-y-0.5 text-xs">{sim.map((s, i) => <li key={i} className="flex gap-2"><span className="w-16 text-slate-500">{new Date(s.at).toLocaleTimeString()}</span>
          <Pill value={s.result} tone={s.result.startsWith("WOULD") ? "info" : s.result.startsWith("STOP") ? "poor" : "none"} /></li>)}</ol>}
      </Card>
    </div>
  </div>;
}
