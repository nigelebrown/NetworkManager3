import { useState } from "react";
import { Link } from "react-router";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { ops } from "../services/ops";
import { rem, type Batch, type Impact } from "../services/remediation";

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
  const [waveSize, setWaveSize] = useState(5);
  const [oneSite, setOneSite] = useState(true);
  const [review, setReview] = useState("");
  const [releaseNote, setReleaseNote] = useState("");
  const plans = useLoad(() => ops.changePlans(), [], 10000, ["changes"]);
  const recent = useLoad(() => rem.batches(), [], 30000, ["changes"]);
  const refresh = async (id: string) => { setBatch(await rem.batch(id)); recent.reload(); plans.reload(); };
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
        {(recent.data ?? []).length > 0 && <label className="mb-2 block text-xs">Open a deployment
          <select aria-label="Open deployment" className={inputCls} value={batch?.batch_id ?? ""} onChange={e => e.target.value && refresh(e.target.value)}>
            <option value="">Select…</option>
            {(recent.data ?? []).map(b => <option key={b.batch_id} value={b.batch_id}>
              {b.batch_id.slice(0, 8)} · {b.created_at?.slice(0, 16).replace("T", " ")} · {b.progress.total} plan(s){b.risk === "HIGH" ? (b.release.approved ? " · HIGH (released)" : " · HIGH (release needed)") : ""}</option>)}
          </select></label>}
        {!batch && <p className="text-sm text-slate-500">Start a deployment from a group, or open one above.</p>}
        {batch && <>
          <div className="mb-2 flex flex-wrap gap-2 text-xs">{Object.entries(batch.progress).map(([k, v]) => <Pill key={k} value={`${k.replace(/_/g, " ")}: ${v}`} tone={k === "applied_verified" ? "good" : k.includes("fail") ? (v ? "poor" : "none") : "info"} />)}</div>
          <table className="w-full text-sm"><tbody>{batch.plans.map(p => {
            const full = plans.data?.find(x => x.id === p.plan_id);
            const can = full && full.state === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== full.requester;
            return <tr key={p.plan_id} className="border-t border-slate-800"><td className="py-1">{p.hostname}</td><td>{p.operations} op(s)</td>
              <td><Pill value={p.state} tone={statusTone(p.state === "APPLIED_VERIFIED" ? "PASS" : p.state.includes("FAIL") ? "FAIL" : p.state)} /></td>
              <td>{p.execution?.environment && <Pill value={p.execution.environment} tone={p.execution.environment === "EMULATED" ? "demo" : "info"} />}</td>
              <td>{can && <button className={btn2} onClick={() => act.run(async () => { await ops.decideChange(full!.id, "approve", full!.revision, "Approved in deployment"); await refresh(batch.batch_id); plans.reload(); return "Approved."; })}>Approve</button>}</td></tr>; })}</tbody></table>
          {batch.impact && <ImpactPanel impact={batch.impact} />}
          {batch.release?.required && <div className={`mt-2 rounded border p-2 text-xs ${batch.release.approved ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-amber-300 bg-amber-50 text-amber-900"}`} data-testid="release-gate">
            <b>Release approval {batch.release.approved ? "recorded" : batch.release.stale ? "no longer valid (the batch changed)" : "required"}.</b>{" "}
            {batch.release.approved && batch.release.approval ? `Approved by ${batch.release.approval.approved_by}: ${batch.release.approval.note}` : batch.impact?.policy}
            {!batch.release.approved && me && ["approver", "admin"].includes(me.role) && me.username !== batch.created_by
              && !batch.plans.some(p => p.approver === me.username) && <div className="mt-2 flex flex-wrap items-center gap-2">
              <input aria-label="Release review note" className={inputCls} value={releaseNote} onChange={e => setReleaseNote(e.target.value)} placeholder="Impact, rollback and window reviewed (20+ characters)" />
              <button className={btn} disabled={act.busy || releaseNote.trim().length < 20 || batch.progress.pending_approval > 0} onClick={() => act.run(async () => {
                await rem.releaseBatch(batch.batch_id, batch.impact!.digest, releaseNote.trim()); setReleaseNote(""); await refresh(batch.batch_id);
                return "High-risk deployment released for execution."; })}>Approve release of this impact</button>
              {batch.progress.pending_approval > 0 && <span>Every plan must be approved first.</span>}</div>}
          </div>}
          <div className="mt-2 text-xs">Wave status: <b>{batch.wave?.state ?? "READY"}</b>. Hardware starts with one canary and always stays within one site and pauses on failure.</div>
          {["PAUSED", "RUNNING"].includes(batch.wave?.state ?? "") && <label className="block text-xs mt-2">Failure / interruption review (required to resume)<input className={inputCls} value={review} onChange={e => setReview(e.target.value)} placeholder="Record recovery and review outcome" /></label>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="text-xs">Wave size (1–25)<input className={inputCls} type="number" min={1} max={25} value={waveSize} onChange={e => setWaveSize(Number(e.target.value))} /></label>
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={oneSite} onChange={e => setOneSite(e.target.checked)} /> One site per wave</label>
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={stop} onChange={e => setStop(e.target.checked)} /> Stop at first failure</label>
            {me?.role === "admin" && <button className={btn} disabled={act.busy || !batch.progress.approved || (batch.release?.required && !batch.release.approved) || !Number.isInteger(waveSize) || waveSize < 1 || waveSize > 25 || (["PAUSED", "RUNNING"].includes(batch.wave?.state ?? "") && review.trim().length < 10)} onClick={() => act.run(async () => {
              const r = await rem.executeBatch(batch.batch_id, stop, waveSize, oneSite, ["PAUSED", "RUNNING"].includes(batch.wave?.state ?? ""), review); setReview(""); await refresh(batch.batch_id);
              return `Executed ${r.executed.length}: ${r.executed.map(e => e.outcome ?? e.decision).join(", ")}${r.stopped_at ? " — stopped after a failure." : ""}`; })}>Execute approved wave</button>}
            <button className={btn2} onClick={() => refresh(batch.batch_id)}>Refresh</button>
            <Link className="text-xs text-sky-300" to="/changes">Change workflow →</Link>
          </div></>}
      </Card>
    </div>
    <Notice error={act.error} message={act.message} />
  </div>;
}

/** Scope of a deployment: devices, sites, Instant clusters reached through their virtual controller, operations. */
function ImpactPanel({ impact }: { impact: Impact }) {
  return <div className="mt-2 rounded border border-slate-200 p-2 text-xs" data-testid="deployment-impact">
    <div className="flex flex-wrap items-center gap-2"><b>Impact</b>
      <Pill value={impact.risk === "HIGH" ? "HIGH RISK" : "STANDARD"} tone={impact.risk === "HIGH" ? "poor" : "info"} />
      <span>{impact.devices_in_batch} device(s) in batch, {impact.devices_reached} reached; sites: {impact.sites.map(s => s.name).join(", ") || "none"}</span></div>
    {impact.reasons.length > 0 && <ul className="mt-1 list-disc pl-5">{impact.reasons.map(r => <li key={r.code}><b>{r.code.replace(/_/g, " ").toLowerCase()}</b>: {r.detail}</li>)}</ul>}
    {impact.clusters.map(c => <div key={c.cluster_id} className="mt-1">Cluster <b>{c.name}</b>: {c.member_count} member(s){c.cluster_wide ? " — the change is applied to every member" : ""}
      {c.also_affected.length > 0 && <> (also affected, not in this batch: {c.also_affected.map(m => m.hostname ?? m.device_id.slice(0, 8)).join(", ")})</>}</div>)}
    {Object.keys(impact.operations).length > 0 && <div className="mt-1">Operations: {Object.entries(impact.operations).map(([k, v]) => `${k} × ${v}`).join(", ")}</div>}
    {impact.warnings.map(w => <div key={w} className="mt-1 text-amber-700">{w}</div>)}
  </div>;
}
