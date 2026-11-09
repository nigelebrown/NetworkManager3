import { useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { ops } from "../services/ops";
import { rem, type Batch } from "../services/remediation";

/** Device groups and multi-device template deployments (audit E2E-20 / Central-style group config). */
export default function Deployments() {
  const me = getSession();
  const groups = useLoad(() => rem.groups(), [], 30000, ["devices"]);
  const act = useAction();
  const [name, setName] = useState("");
  const [family, setFamily] = useState("switch");
  const [addTo, setAddTo] = useState("");
  const [dev, setDev] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const [stop, setStop] = useState(true);
  const refresh = async (id: string) => setBatch(await rem.batch(id));
  const plans = useLoad(() => ops.changePlans(), [batch?.batch_id], 10000, ["changes"]);
  return <div className="space-y-4">
    <PageHeader title="Groups & multi-device deployment" subtitle="Group devices, attach group templates (Templates page), then deploy: one change plan per device, each independently approved, executed one device at a time with readback and automatic rollback. A failure stops the rollout by default." />
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Device groups">
        <table className="w-full text-sm"><tbody>{(groups.data ?? []).map(g => <tr key={g.id} className="border-t border-slate-800">
          <td className="py-1 font-semibold">{g.name}</td><td>{g.family}</td><td>{g.members.length} device(s)</td>
          <td className="text-right">{me?.role !== "viewer" && <button className={btn2} disabled={act.busy || !g.members.length} onClick={() => act.run(async () => {
            const r = await rem.deploy({ group_id: g.id }); await refresh(r.batch_id);
            const n = r.devices.filter(x => x.plan_id).length, ok = r.devices.filter(x => x.in_compliance).length;
            return `Deployment ${r.batch_id.slice(0, 8)}: ${n} plan(s) for approval, ${ok} already compliant, ${r.devices.filter(x => x.error).length} not ready.`; })}>Deploy group templates</button>}</td></tr>)}</tbody></table>
        {me?.role === "admin" && <div className="mt-3 grid gap-2 md:grid-cols-3">
          <input aria-label="Group name" className={inputCls} placeholder="New group name" value={name} onChange={e => setName(e.target.value)} />
          <select aria-label="Group family" className={inputCls} value={family} onChange={e => setFamily(e.target.value)}><option value="switch">switch</option><option value="access_point">access point</option></select>
          <button className={btn} disabled={name.trim().length < 2} onClick={() => act.run(async () => { await rem.createGroup({ name: name.trim(), family }); setName(""); groups.reload(); return "Group created."; })}>Create group</button>
          <select aria-label="Add to group" className={inputCls} value={addTo} onChange={e => setAddTo(e.target.value)}>
            <option value="">Add device to group…</option>{(groups.data ?? []).map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select>
          <DevicePicker value={dev} onChange={id => setDev(id)} />
          <button className={btn2} disabled={!addTo || !dev} onClick={() => act.run(async () => { await rem.addMembers(addTo, [dev]); groups.reload(); return "Added."; })}>Add</button>
        </div>}
      </Card>
      <Card title={batch ? `Deployment ${batch.batch_id.slice(0, 8)}` : "Deployment progress"}>
        {!batch && <p className="text-sm text-slate-500">Start a deployment from a group.</p>}
        {batch && <>
          <div className="mb-2 flex flex-wrap gap-2 text-xs">{Object.entries(batch.progress).map(([k, v]) => <Pill key={k} value={`${k.replace(/_/g, " ")}: ${v}`} tone={k === "applied_verified" ? "good" : k.includes("fail") ? (v ? "poor" : "none") : "info"} />)}</div>
          <table className="w-full text-sm"><tbody>{batch.plans.map(p => {
            const full = plans.data?.find(x => x.id === p.plan_id);
            const can = full && full.state === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== full.requester;
            return <tr key={p.plan_id} className="border-t border-slate-800"><td className="py-1">{p.hostname}</td><td>{p.operations} op(s)</td>
              <td><Pill value={p.state} tone={statusTone(p.state === "APPLIED_VERIFIED" ? "PASS" : p.state.includes("FAIL") ? "FAIL" : p.state)} /></td>
              <td>{p.execution?.environment && <Pill value={p.execution.environment} tone={p.execution.environment === "EMULATED" ? "demo" : "info"} />}</td>
              <td>{can && <button className={btn2} onClick={() => act.run(async () => { await ops.decideChange(full!.id, "approve", full!.revision, "Approved in deployment"); await refresh(batch.batch_id); plans.reload(); return "Approved."; })}>Approve</button>}</td></tr>; })}</tbody></table>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={stop} onChange={e => setStop(e.target.checked)} /> Stop at first failure</label>
            {me?.role === "admin" && <button className={btn} disabled={act.busy || !batch.progress.approved} onClick={() => act.run(async () => {
              const r = await rem.executeBatch(batch.batch_id, stop); await refresh(batch.batch_id);
              return `Executed ${r.executed.length}: ${r.executed.map(e => e.outcome ?? e.decision).join(", ")}${r.stopped_at ? " — stopped after a failure." : ""}`; })}>Execute approved plans</button>}
            <button className={btn2} onClick={() => refresh(batch.batch_id)}>Refresh</button>
            <Link className="text-xs text-sky-300" to="/changes">Change workflow →</Link>
          </div></>}
      </Card>
    </div>
    <Notice error={act.error} message={act.message} />
  </div>;
}
