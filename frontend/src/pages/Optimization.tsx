import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, btnDanger, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import type { Device } from "../services/inm";
import { ops, rw } from "../services/ops";

export default function Optimization() {
  const [device, setDevice] = useState<Device | undefined>();
  const state = useLoad(() => device ? ops.optState(device.id) : Promise.resolve(null), [device?.id], 30000);
  const control = useLoad(ops.optControl, [], 30000);
  const [mode, setMode] = useState("SHADOW");
  const [standing, setStanding] = useState(false);
  const sites = useLoad(() => rw.sitePolicies().catch(() => []), [], 0);
  const enrollments = useLoad(() => ops.optEnrollments().catch(() => []), [], 30000);
  const act = useAction();
  const me = getSession();
  const enr = state.data?.enrollment as { id: string; status: string; requester_id: string } | null | undefined;
  return <div className="space-y-4">
    <PageHeader title="RF optimization — per-device opt-in" subtitle="Automatic optimization is off unless the platform feature is enabled AND an individual AP is enrolled in DEVICE_OPT_IN with independent approval, scope, expiry, daily limit and cooldown. It is never inherited from a site, model or template. Channel widths are limited to 20/40 MHz. Each automatic change runs through the same certified, backed-up, verified and rollback-protected execution as a manual change; degradation after a change triggers automatic rollback." />
    <div className="rounded-xl border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">AUTOMATIC OPTIMIZATION FEATURE: <b>{control.data?.automatic_optimization_feature ? "ENABLED (per-device opt-in only)" : "OFF (INM_AUTOMATIC_OPTIMIZATION=false)"}</b> · Global write lock: <b>{control.data?.global_write_lock ? "ON" : "OFF"}</b> ·
      Emergency stop: <b>{control.data?.emergency_stop ? "ACTIVE" : "clear"}</b>. Vendor-native features such as Aruba ARM remain as configured on each device.</div>
    <Card title="Device">
      <div className="max-w-md"><DevicePicker family="access_point" value={device?.id ?? ""} onChange={(_, d) => setDevice(d)} /></div>
      {state.data && <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        <Pill value={state.data.badge} tone={state.data.badge === "AUTO OFF" ? "none" : "fair"} /><span>Mode <b>{state.data.mode}</b></span><span>Status <Pill value={state.data.status} tone={statusTone(state.data.status)} /></span>
        {state.data.display && <span className="text-amber-300">{state.data.display}</span>}</div>}
      {device && !enr && me?.role === "admin" && <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <label>Mode <select aria-label="Enrollment mode" className="rounded bg-slate-950 px-2 py-1" value={mode} onChange={e => setMode(e.target.value)}>
          <option value="SHADOW">SHADOW — simulate only</option><option value="DEVICE_OPT_IN">DEVICE_OPT_IN — automatic changes</option></select></label>
        {mode === "DEVICE_OPT_IN" && <label className="flex items-center gap-1"><input type="checkbox" checked={standing} onChange={e => setStanding(e.target.checked)} />
          Standing approval (optimizer may apply within limits without a per-change approval)</label>}</div>}
      {device && !enr && me?.role === "admin" && <button className={`${btn} mt-3`} disabled={act.busy} onClick={() => act.run(async () => {
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
      <p className="mb-2 text-sm text-slate-400">The optimizer runs every 5 minutes. Run it now to evaluate enrolled APs immediately.</p>
      <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await rw.optimizerRun(); enrollments.reload();
        return Object.entries(r).map(([k, v]) => `${k}: ${v}`).join(" · "); })}>Run optimizer now</button>
      {!!sites.data?.length && <table className="mt-3 w-full text-xs"><thead><tr className="text-left text-slate-400"><th>Site</th><th>5 GHz channels</th><th>Widths</th><th>TX dBm</th></tr></thead>
        <tbody>{sites.data.map(sp => <tr key={sp.site_id} className="border-t border-slate-800"><td className="font-mono">{sp.site_id.slice(0, 8)}</td>
          <td>{sp.allowed_channels_5g.join(", ")}</td><td>{sp.allowed_widths_mhz.join("/")} MHz</td><td>{sp.min_tx_dbm}–{sp.max_tx_dbm}</td></tr>)}</tbody></table>}
    </Card>}
    {me?.role === "admin" && <Card title="Emergency stop">
      <p className="mb-2 text-sm text-slate-400">Pauses every enrollment and automatic change immediately; each must be reviewed again.</p>
      <button className={control.data?.emergency_stop ? btn2 : btnDanger} onClick={() => act.run(async () => {
        await ops.emergencyStop(!control.data?.emergency_stop, control.data?.emergency_stop ? "Cleared after review" : "Operator emergency stop"); control.reload(); enrollments.reload(); })}>
        {control.data?.emergency_stop ? "Clear emergency stop" : "Activate emergency stop"}</button></Card>}
    <Card title="Enrollments (per device, never inherited)"><table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th>Device</th><th>Serial</th><th>Mode</th><th>Status</th><th>Requested by</th><th>Approver</th><th>Expires</th></tr></thead>
      <tbody>{(enrollments.data ?? []).map(e => <tr key={String(e.id)} className="border-t border-slate-800"><td className="py-1 font-mono text-xs">{String(e.device_id).slice(0, 8)}</td>
        <td>{String(e.immutable_serial)}</td><td>{String(e.mode)}</td><td><Pill value={String(e.status)} tone={statusTone(String(e.status))} /></td>
        <td>{String(e.requester_id)}</td><td>{String(e.independent_approver_id ?? "—")}</td><td className="text-xs">{String(e.expires_at_utc).slice(0, 10)}</td></tr>)}</tbody></table></Card>
  </div>;
}
