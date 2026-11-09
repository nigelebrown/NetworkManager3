import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, Steps, ago, btn, inputCls, statusTone, useAction } from "../components/viz";
import { ops, rw } from "../services/ops";

type R = { id: string; state: string; blockers: string[]; identity_map: Record<string, { old: unknown; new: unknown }>;
  plan: { steps: string[]; current_step: string; note: string }; created_at: string; old_device_id: string; new_device_id: string };

export default function Replacement() {
  const [oldId, setOld] = useState("");
  const [newId, setNew] = useState("");
  const [ipOld, setIpOld] = useState("");
  const [ipNew, setIpNew] = useState("");
  const [result, setResult] = useState<R | null>(null);
  const list = useLoad(ops.replacements, [], 30000);
  const act = useAction();
  return <div className="space-y-4">
    <PageHeader title="Replace a failed device" subtitle="Validates that the replacement is the same or an explicitly compatible model and firmware train, maps identity fields, and prepares a lab restore from the vault. The successor always starts with optimization OFF." />
    <Card title="Replacement wizard">
      <div className="grid gap-3 md:grid-cols-2">
        <DevicePicker label="Failed device" value={oldId} onChange={id => setOld(id)} />
        <DevicePicker label="Replacement device (registered first)" value={newId} onChange={id => setNew(id)} />
        <label className="text-sm">Old management IP<input className={inputCls} value={ipOld} onChange={e => setIpOld(e.target.value)} /></label>
        <label className="text-sm">New management IP<input className={inputCls} value={ipNew} onChange={e => setIpNew(e.target.value)} /></label>
      </div>
      <button className={`${btn} mt-3`} disabled={!oldId || !newId || act.busy} onClick={() => act.run(async () => {
        const r = await ops.planReplacement({ old_device_id: oldId, new_device_id: newId,
          identity_map: ipOld || ipNew ? { mgmt_ip: { old: ipOld, new: ipNew } } : {} }) as unknown as R;
        setResult(r); list.reload(); return r.state === "BLOCKED" ? "Plan blocked — see reasons." : "Compatible. Next step: lab restore and readback."; })}>Validate replacement</button>
      <Notice error={act.error} message={act.message} />
      {result && <div className="mt-4 space-y-3">
        <Steps steps={result.plan.steps.map(s => s.replace(/_/g, " ").toLowerCase())} current={result.plan.steps.indexOf(result.plan.current_step)} />
        {result.blockers.map(b => <div key={b} className="text-sm text-red-300">✗ {b}</div>)}
        <table className="text-sm"><tbody>{Object.entries(result.identity_map).map(([k, v]) => <tr key={k}><td className="pr-4 text-slate-400">{k}</td>
          <td className="pr-2 text-red-300">{String(v.old)}</td><td>→</td><td className="pl-2 text-emerald-300">{String(v.new)}</td></tr>)}</tbody></table>
        <p className="text-xs text-slate-500">{result.plan.note}</p></div>}
    </Card>
    <Card title="Replacement history"><ul className="space-y-1 text-sm">{((list.data ?? []) as unknown as R[]).map(r => <li key={r.id} className="flex gap-2">
      <span className="w-20 text-xs text-slate-500">{ago(r.created_at)}</span><Pill value={r.state} tone={statusTone(r.state)} />
      <span className="font-mono text-xs">{r.old_device_id.slice(0, 8)} → {r.new_device_id.slice(0, 8)}</span><span className="text-xs text-slate-400">{r.blockers[0] ?? ""}</span>
      {r.state !== "BLOCKED" && <button className="text-xs text-sky-300 underline" disabled={act.busy} onClick={() => act.run(async () => { const p = await rw.replacementRestore(r.id);
        return `Restore plan ${p.plan_id.slice(0, 8)} created for the new device — approve and apply it in Change workflow.`; })}>Create restore plan</button>}</li>)}</ul></Card>
  </div>;
}
