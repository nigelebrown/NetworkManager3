import { useState } from "react";
import DiagnosisPanel from "../components/DiagnosisPanel";
import { ErrorNote } from "../components/ui";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, btnDanger, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import type { Device } from "../services/inm";
import { ops, rw } from "../services/ops";
import GuidePanel from "../components/GuidePanel";
import ClusterOptimization from "../components/ClusterOptimization";
import WirelessImpactPanel from "../components/WirelessImpactPanel";

export default function Optimization() {
  const [device, setDevice] = useState<Device | undefined>();
  const state = useLoad(() => device ? ops.optState(device.id) : Promise.resolve(null), [device?.id], 5000);
  const control = useLoad(ops.optControl, [], 30000);
  const [mode, setMode] = useState("DEVICE_OPT_IN");
  const [diagnosing,setDiagnosing] = useState(false);
  const [standing, setStanding] = useState(false);
  const sites = useLoad(() => rw.sitePolicies(), [], 0);
  const enrollments = useLoad(() => ops.optEnrollments(), [], 30000);
  const act = useAction();
  const runAct = useAction();
  const me = getSession();
  const selectedState = state.data?.device_id === device?.id ? state.data : null;
  const enr = selectedState?.enrollment as { id: string; status: string; requester_id: string } | null | undefined;
  const workflow = selectedState?.workflow;
  const workflowActive = !!workflow && ["QUEUED","WAIT_ARM","WAIT_ENROLLMENT","WAIT_CHANGE","MONITORING"].includes(workflow.status);
  return <div className="space-y-4">
    <PageHeader title="RF optimization — per-device opt-in" subtitle="Native Aruba ARM selects channels and power. This application uses its per-AP measurements to detect policy problems and persistent client-impacting degradation. Reviewed automatic interventions require an enabled feature and individual DEVICE_OPT_IN enrollment with independent approval, expiry, action limits and cooldown. It is never inherited from a site, model or template. Channel widths are limited to 20/40 MHz. Each automatic change runs through the same certified, backed-up, verified and rollback-protected execution as a manual change; degradation after a change triggers automatic rollback." />
    <div className="rounded-xl border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">AUTOMATIC OPTIMIZATION FEATURE: <b>{control.data?.automatic_optimization_feature ? "ENABLED (per-device opt-in only)" : "OFF (INM_AUTOMATIC_OPTIMIZATION=false)"}</b> · Global write lock: <b>{control.data?.global_write_lock ? "ON" : "OFF"}</b> ·
      Emergency stop: <b>{control.data?.emergency_stop ? "ACTIVE" : "clear"}</b>. Vendor-native features such as Aruba ARM remain as configured on each device.</div>
    <ErrorNote error={control.error || state.error || enrollments.error || sites.error} />
    <GuidePanel guide="optimization" defaultOpen={false} />
    <WirelessImpactPanel />
    <ClusterOptimization onChanged={() => { enrollments.reload(); state.reload(); }} />
    <Card title="Single device">
      <div className="max-w-md"><DevicePicker family="access_point" value={device?.id ?? ""} onChange={(_, d) => { setDevice(d); setDiagnosing(false); }} /></div>
      {device && <div className="mt-3 flex flex-wrap gap-2">
        {me?.role === "admin" && device.source_type === "REAL" && <>
          <button className={btn} disabled={runAct.busy || !selectedState || workflowActive} onClick={() => runAct.run(async () => {
            const job = await ops.optStart(device.id, device.serial_number, standing);
            await state.reload(); enrollments.reload();
            return job.detail;
          })}>Optimize Now</button>
          <button className={btn2} disabled={runAct.busy || !selectedState || workflowActive} onClick={() => runAct.run(async () => {
            const job = await ops.optStart(device.id, device.serial_number, false, true);
            await state.reload(); return job.detail;
          })}>Analyze Now (read-only)</button>
        </>}
        <button className={btn2} onClick={() => setDiagnosing(true)}>View ARM measurements and findings</button>
        <button className={btn2} disabled={state.loading} onClick={() => state.reload()}>Refresh progress</button>
      </div>}
      <p className="mt-2 text-xs text-slate-500">Optimize Now automatically verifies the AP, captures a backup, collects fresh measurements, requests required approvals, applies approved changes and monitors performance. Analysis alone never changes a device.</p>
      <Notice error={runAct.error} message={runAct.message} />
      {workflow && <div className="mt-3 rounded-lg border border-slate-300 p-3 space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2"><b>Optimization workflow</b><Pill value={workflow.status} tone={statusTone(workflow.status)} /></div>
        <p>{workflow.detail}</p>
        <ul className="space-y-1 text-xs">
          <li>{workflow.backup_artifact_id ? "✓" : "○"} Verified identity and encrypted configuration backup</li>
          <li>{workflow.arm_observed_at ? "✓" : "○"} Fresh ARM measurements and recommendations</li>
          <li>{workflow.plan_id ? "✓" : "○"} Configuration change plan prepared (if needed)</li>
          <li>{["MONITORING","COMPLETE","ROLLED_BACK"].includes(workflow.status) ? "✓" : "○"} Approved configuration applied and verified</li>
          <li>{["COMPLETE","ROLLED_BACK"].includes(workflow.status) ? "✓" : "○"} Post-change client experience evaluated</li>
        </ul>
        {workflow.arm_observed_at && <p className="text-xs">Measured: {new Date(workflow.arm_observed_at).toLocaleString()}</p>}
        {workflow.findings.map(f => <div key={f.id} className="rounded border p-2 text-xs"><b>{f.title}</b><p>{f.why}</p></div>)}
        {workflow.proposed_operation && <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(workflow.proposed_operation, null, 2)}</pre>}
        {workflow.plan?.state === "PENDING_APPROVAL" && me && ["admin","approver"].includes(me.role) && me.username.toLowerCase() !== workflow.plan.requester.toLowerCase() && <div className="flex gap-2">
          <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { const p = workflow.plan!; await ops.decideChange(p.id, "approve", p.revision, "Reviewed one-click ARM recommendation"); await state.reload(); return "Approved. The workflow will recheck evidence before applying."; })}>Approve recommended adjustment</button>
          <button className={btnDanger} disabled={act.busy} onClick={() => act.run(async () => { const p = workflow.plan!; await ops.decideChange(p.id, "reject", p.revision, "Rejected one-click ARM recommendation"); await state.reload(); return "Rejected. No configuration change authorized."; })}>Reject adjustment</button></div>}
        {workflow.plan_id && <a className="inm-link" href="/changes">View full change plan</a>}
        {workflow.guards?.filter(g => !g.pass).map(g => <div key={g.guard} className="text-xs text-red-700">Blocked: {g.guard.replace(/_/g, " ")}</div>)}
      </div>}
      {selectedState && <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <Pill value={selectedState.badge} tone={selectedState.badge === "AUTO OFF" ? "none" : "fair"} /><span>Mode <b>{selectedState.mode}</b></span><span>Status <Pill value={selectedState.status} tone={statusTone(selectedState.status)} /></span>
        {selectedState.display && <span className="text-amber-300">{selectedState.display}</span>}</div>}
      {device && selectedState && !enr && !workflowActive && me?.role === "admin" && <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <label>Mode <select aria-label="Enrollment mode" className="rounded bg-slate-950 px-2 py-1" value={mode} onChange={e => setMode(e.target.value)}>
          <option value="SHADOW">SHADOW — simulate only</option><option value="DEVICE_OPT_IN">DEVICE_OPT_IN — automatic changes</option></select></label>
        {mode === "DEVICE_OPT_IN" && <label className="flex items-center gap-1"><input type="checkbox" checked={standing} onChange={e => setStanding(e.target.checked)} />
          Standing approval (optimizer may apply within limits without a per-change approval)</label>}</div>}
      {device && selectedState && !enr && !workflowActive && me?.role === "admin" && <button className={`${btn} mt-3`} disabled={act.busy} onClick={() => act.run(async () => {
        await ops.optEnroll(device.id, { serial: device.serial_number, mode, standing_approval: mode === "DEVICE_OPT_IN" && standing, allowed_operation_types: ["ap.radio.channel", "ap.radio.tx_power"],
          value_constraints: { allowed_widths_mhz: [20, 40] }, cooldown_seconds: 7200, max_actions_per_day: 2,
          expires_at_utc: new Date(Date.now() + 30 * 86400e3).toISOString() });
        state.reload(); enrollments.reload(); return `${mode} enrollment requested for this AP only. An independent approver must approve it.`; })}>Request enrollment for this AP</button>}
      {enr?.status === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== enr.requester_id &&
        <button className={`${btn2} mt-3`} disabled={act.busy} onClick={() => act.run(async () => { await ops.optApprove(enr.id); state.reload(); enrollments.reload(); return "Enrollment approved for this AP."; })}>Approve enrollment</button>}
      {enr && me?.role === "admin" && <button className={`${btnDanger} ml-2 mt-3`} disabled={act.busy} onClick={() => act.run(async () => { await ops.optDisable(device!.id); state.reload(); enrollments.reload(); return "Disabled immediately."; })}>Disable</button>}
      <Notice error={act.error} message={act.message} />
    </Card>
    {me?.role === "admin" && <Card title="Optimizer">
      <p className="mb-2 text-sm text-slate-400">The optimizer evaluates ARM measurements every 5 minutes. Healthy radios stay under native ARM control; static overrides can be returned to ARM through a reviewed change. Run now to evaluate enrolled APs.</p>
      <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await rw.optimizerRun(); enrollments.reload();
        state.reload(); return Object.entries(r).filter(([,v]) => v !== 0).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ") || "No devices evaluated."; })}>Run fleet optimizer (up to 50 APs)</button>
      {!!sites.data?.length && <table className="mt-3 w-full text-xs"><thead><tr className="text-left text-slate-400"><th>Site</th><th>5 GHz channels</th><th>Widths</th><th>TX dBm</th></tr></thead>
        <tbody>{sites.data.map(sp => <tr key={sp.site_id} className="border-t border-slate-800"><td className="font-mono">{sp.site_id.slice(0, 8)}</td>
          <td>{sp.allowed_channels_5g.join(", ")}</td><td>{sp.allowed_widths_mhz.join("/")} MHz</td><td>{sp.min_tx_dbm}–{sp.max_tx_dbm}</td></tr>)}</tbody></table>}
    </Card>}
    {me?.role === "admin" && <Card title="Emergency stop">
      <p className="mb-2 text-sm text-slate-400">Pauses every enrollment and automatic change immediately; each must be reviewed again.</p>
      <button className={control.data?.emergency_stop ? btn2 : btnDanger} onClick={() => act.run(async () => {
        await ops.emergencyStop(!control.data?.emergency_stop, control.data?.emergency_stop ? "Cleared after review" : "Operator emergency stop"); control.reload(); enrollments.reload(); })}>
        {control.data?.emergency_stop ? "Clear emergency stop" : "Activate emergency stop"}</button></Card>}
    {diagnosing && device && <DiagnosisPanel target={{kind:"ap",id:device.id,label:device.hostname || device.serial_number}} onClose={() => setDiagnosing(false)} />}
    <Card title="Enrollments (per device, never inherited)"><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th>Device</th><th>Mode</th><th>Status</th><th>Next action</th><th>Requested by</th><th>Approver</th><th>Expires</th><th></th></tr></thead>
      <tbody>{(enrollments.data ?? []).map(e => { const st = String(e.status); const mine = me?.username === e.requester_id;
        return <tr key={String(e.id)} className="border-t border-slate-200"><td className="py-1"><b>{String(e.hostname ?? String(e.device_id).slice(0, 8))}</b>
          <div className="text-[10px] text-slate-500">{String(e.model ?? "")} · {String(e.immutable_serial)}</div></td>
        <td>{String(e.mode)}</td><td><Pill value={st} tone={statusTone(st)} />{e.status_reason ? <div className="text-[10px] text-slate-500">{String(e.status_reason)}</div> : null}</td>
        <td className="max-w-xs text-xs">{String(e.next_action ?? "")}{String(e.next_action ?? "").startsWith("Blocked") &&
          <> <a className="inm-link" href={`/devices/${String(e.device_id)}/360`}>Open Device 360°</a></>}</td>
        <td>{String(e.requester_id)}</td><td>{String(e.independent_approver_id ?? "—")}</td><td className="text-xs">{String(e.expires_at_utc).slice(0, 10)}</td>
        <td className="whitespace-nowrap">{st === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && !mine &&
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await ops.optApprove(String(e.id)); await state.reload(); enrollments.reload(); return `Approved ${String(e.hostname ?? "")}.`; })}>Approve</button>}
          {st === "PENDING_APPROVAL" && mine && <span className="text-[10px] text-slate-500">another user must approve</span>}
          {["PENDING_APPROVAL", "ARMED", "PAUSED"].includes(st) && me?.role === "admin" &&
            <button className={`${btnDanger} ml-1`} disabled={act.busy} onClick={() => act.run(async () => { await ops.optDisable(String(e.device_id)); await state.reload(); enrollments.reload(); return "Disabled."; })}>Disable</button>}</td></tr>; })}
      </tbody></table></div>
      <div className="mt-2"><Notice error={act.error} message={act.message} /></div>
      {!enrollments.data?.length && <p className="text-sm text-slate-500">No enrollments yet. Pick a cluster above to propose SHADOW enrollments for its APs.</p>}</Card>
  </div>;
}
