import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, useAction } from "../components/viz";
import { ops } from "../services/ops";

type Dev = { device_id: string; hostname: string; model: string; family: string; evidence_status: string;
  latest_observation: string | null; metrics: Record<string, { value: number }>; findings: { rule_id: string; title: string }[] };
type Rec = { id: string; device_id: string; title: string; explanation: string; risk: string; approval_state: string; created_at: string };

export default function Phase4Insights() {
  const ov = useLoad(ops.phase4, [], 30000);
  const recs = useLoad(ops.phase4Recs, [], 30000);
  const act = useAction();
  const devices = (ov.data?.devices ?? []) as unknown as Dev[];
  return <div className="space-y-4">
    <PageHeader title="Device insights" subtitle="Rule analysis of recorded live telemetry. A finding requires two independent recent REST observations; manual readings are shown but never used as evidence. Advisories only — no automatic changes."
      right={<button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
        const r = await ops.phase4Analyze(); recs.reload(); return `Analysed ${r.evaluated_devices} device(s); ${r.created} new advisory(ies).`; })}>Analyze now</button>} />
    <ErrorNote error={ov.error} /><Notice error={act.error} message={act.message} />
    <Card title="Live devices"><table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">Device</th><th>Evidence</th><th>Latest</th><th>Metrics</th><th>Findings</th></tr></thead>
      <tbody>{devices.map(d => <tr key={d.device_id} className="border-t border-slate-800"><td className="py-1.5"><Link className="text-sky-300" to={`/devices/${d.device_id}/360`}>{d.hostname}</Link> <span className="text-xs text-slate-500">{d.model}</span></td>
        <td><Pill value={d.evidence_status} tone={d.evidence_status === "REST_VERIFIED_RECENT" ? "good" : d.evidence_status === "NO_TELEMETRY" ? "none" : "fair"} /></td>
        <td className="text-xs">{ago(d.latest_observation)}</td><td className="text-xs">{Object.entries(d.metrics).slice(0, 4).map(([k, v]) => `${k}=${v.value}`).join(" · ")}</td>
        <td>{d.findings.map(f => <Pill key={f.rule_id} value={f.title} tone="fair" />)}</td></tr>)}
        {!devices.length && <tr><td colSpan={5} className="py-3 text-center text-slate-500">No live Aruba devices registered.</td></tr>}</tbody></table></Card>
    <Card title="Advisories"><ul className="space-y-2 text-sm">{((recs.data ?? []) as unknown as Rec[]).map(r => <li key={r.id} className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-2">
      <Pill value={r.risk} tone={r.risk === "high" ? "poor" : "fair"} /><b>{r.title}</b><span className="text-xs text-slate-400">{r.explanation}</span>
      <span className="ml-auto text-xs text-slate-500">{ago(r.created_at)}</span><Pill value={r.approval_state} tone="none" />
      {r.approval_state === "ADVISORY" && <><button className={btn2} onClick={() => act.run(async () => { await ops.phase4Review(r.id, "ACKNOWLEDGED"); recs.reload(); })}>Acknowledge</button>
        <button className={btn2} onClick={() => act.run(async () => { await ops.phase4Review(r.id, "DISMISSED"); recs.reload(); })}>Dismiss</button></>}</li>)}
      {!recs.data?.length && <li className="text-slate-500">No advisories.</li>}</ul></Card>
  </div>;
}
