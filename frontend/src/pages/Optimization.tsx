import { useState } from "react";
import DiagnosisPanel from "../components/DiagnosisPanel";
import { ErrorNote } from "../components/ui";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, btnDanger, statusTone, useAction } from "../components/viz";
import { getSession, request } from "../services/api";
import { v1 } from "../services/inm";
import type { Device } from "../services/inm";
import { ops, rw } from "../services/ops";
import GuidePanel from "../components/GuidePanel";
import ClusterOptimization from "../components/ClusterOptimization";

export default function Optimization() {
  const [device, setDevice] = useState<Device | undefined>();
  const state = useLoad(() => device ? ops.optState(device.id) : Promise.resolve(null), [device?.id], 30000);
  const control = useLoad(ops.optControl, [], 30000);
  const [mode, setMode] = useState("DEVICE_OPT_IN");
  const [diagnosing,setDiagnosing] = useState(false);
  const [standing, setStanding] = useState(false);
  const sites = useLoad(() => rw.sitePolicies(), [], 0);
  const enrollments = useLoad(() => ops.optEnrollments(), [], 30000);
  const act = useAction();
  const me = getSession();
  const enr = state.data?.enrollment as { id: string; status: string; requester_id: string } | null | undefined;
  return <div className="space-y-4">
    <PageHeader title="RF optimization — per-device opt-in" subtitle="Native Aruba ARM selects channels and power. This application uses its per-AP measurements to detect policy problems and persistent client-impacting degradation. Reviewed automatic interventions require an enabled feature and individual DEVICE_OPT_IN enrollment with independent approval, expiry, action limits and cooldown. It is never inherited from a site, model or template; a site batch enrolls only the APs present when it is requested, each with its own record. Channel widths are limited to 20/40 MHz. Each automatic change runs through the same certified, backed-up, verified and rollback-protected execution as a manual change; degradation after a change triggers automatic rollback." />
    <div className="rounded-xl border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">AUTOMATIC OPTIMIZATION FEATURE: <b>{control.data?.automatic_optimization_feature ? "ENABLED (per-device opt-in only)" : "OFF (INM_AUTOMATIC_OPTIMIZATION=false)"}</b> · Global write lock: <b>{control.data?.global_write_lock ? "ON" : "OFF"}</b> ·
      Emergency stop: <b>{control.data?.emergency_stop ? "ACTIVE" : "clear"}</b>. Vendor-native features such as Aruba ARM remain as configured on each device.</div>
    <ErrorNote error={control.error || state.error || enrollments.error || sites.error} />
    <GuidePanel guide="optimization" defaultOpen={false} />
    <ClusterOptimization onChanged={() => { enrollments.reload(); state.reload(); }} />
    <Card title="Single device">
      <div className="max-w-md"><DevicePicker family="access_point" value={device?.id ?? ""} onChange={(_, d) => setDevice(d)} /></div>
      {device && <button className={btn2+" mt-3"} onClick={() => setDiagnosing(true)}>Review live ARM measurements and proposed adjustments</button>}
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
    <SiteBatches mode={mode} onChanged={() => { enrollments.reload(); state.reload(); }} />
    {me?.role === "admin" && <Card title="Optimizer">
      <p className="mb-2 text-sm text-slate-400">The optimizer evaluates ARM measurements every 5 minutes. Healthy radios stay under native ARM control; static overrides can be returned to ARM through a reviewed change. Run now to evaluate enrolled APs.</p>
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
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await ops.optApprove(String(e.id)); enrollments.reload(); return `Approved ${String(e.hostname ?? "")}.`; })}>Approve</button>}
          {st === "PENDING_APPROVAL" && mine && <span className="text-[10px] text-slate-500">another user must approve</span>}
          {["PENDING_APPROVAL", "ARMED", "PAUSED"].includes(st) && me?.role === "admin" &&
            <button className={`${btnDanger} ml-1`} disabled={act.busy} onClick={() => act.run(async () => { await ops.optDisable(String(e.device_id)); enrollments.reload(); return "Disabled."; })}>Disable</button>}</td></tr>; })}
      </tbody></table></div>
      <div className="mt-2"><Notice error={act.error} message={act.message} /></div>
      {!enrollments.data?.length && <p className="text-sm text-slate-500">No enrollments yet. Pick a cluster above to propose SHADOW enrollments for its APs.</p>}</Card>
  </div>;
}


type Batch = { id: string; site_id: string; requester_id: string; created_at: string; status: string; approved_by: string | null;
  skipped: { device_id: string; reason: string }[]; members: { id: string; device_id: string; immutable_serial: string; status: string; status_reason: string | null }[];
  results?: { device_id: string; armed: boolean; reason: string }[] };

/** Site batch enrollment (owner directive 2026-11-25): one approval, one record per AP, no later inheritance. */
function SiteBatches({ mode, onChanged }: { mode: string; onChanged: () => void }) {
  const me = getSession();
  const canSee = me && ["admin", "approver", "auditor"].includes(me.role);
  const batches = useLoad(() => canSee ? request<Batch[]>("/v1/optimization/site-batches") : Promise.resolve([]), [canSee], 30000);
  const sites = useLoad(() => me?.role === "admin" ? v1.physicalSites() : Promise.resolve({ sites: [], clusters: [] }), [me?.role], 0);
  const [site, setSite] = useState("");
  const act = useAction();
  if (!canSee) return null;
  const siteName = (id: string) => (sites.data?.sites ?? []).find((x: { id: string; name: string }) => x.id === id)?.name ?? id;
  const run = (fn: () => Promise<string>) => act.run(async () => { const m = await fn(); batches.reload(); onChanged(); return m; });
  return <Card title="Site batch enrollment">
    <p className="text-sm text-slate-600">Creates one enrollment per access point currently registered at the site, bound to its serial number.
      One independent approval arms each AP that passes its own checks (identity, firmware, expiry, backup). APs added later are not included.</p>
    <Notice error={act.error || batches.error || sites.error} message={act.message} />
    {me?.role === "admin" && <div className="mt-2 flex flex-wrap items-end gap-2 text-sm">
      <label htmlFor="opt-batch-site">Site</label>
      <select id="opt-batch-site" className="rounded border px-2 py-1" value={site} onChange={e => setSite(e.target.value)}>
        <option value="">Choose a site</option>{(sites.data?.sites ?? []).map((x: { id: string; name: string }) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
      <button className={btn} disabled={act.busy || !site} onClick={() => run(async () => {
        const b = await request<Batch>("/v1/optimization/site-batches", { method: "POST", body: JSON.stringify({ site_id: site, template: {
          serial: "-", mode, allowed_operation_types: ["ap.radio.channel", "ap.radio.tx_power"], value_constraints: { allowed_widths_mhz: [20, 40] },
          cooldown_seconds: 7200, max_actions_per_day: 2, expires_at_utc: new Date(Date.now() + 30 * 86400e3).toISOString() } }) });
        return `${b.members.length} AP enrollment(s) requested (${mode}); ${b.skipped.length} skipped. An independent approver must approve the batch.`;
      })}>Request {mode} for all APs at this site</button></div>}
    <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>
      {["Site", "Requested", "APs", "Status", "Approver", ""].map(h => <th key={h} scope="col" className="p-1">{h}</th>)}</tr></thead>
      <tbody>{(batches.data ?? []).map(b => <tr key={b.id} className="border-t">
        <td className="p-1">{siteName(b.site_id)}</td><td className="p-1">{new Date(b.created_at).toLocaleString()} by {b.requester_id}</td>
        <td className="p-1">{b.members.length} ({b.members.filter(m => m.status === "ARMED").length} armed){b.skipped.length ? ` · ${b.skipped.length} skipped` : ""}
          {b.results?.some(r => !r.armed) && <details><summary className="cursor-pointer text-xs">Not armed</summary>
            <ul className="text-xs">{b.results.filter(r => !r.armed).map(r => <li key={r.device_id}>{r.device_id.slice(0, 8)}: {r.reason}</li>)}</ul></details>}</td>
        <td className="p-1"><Pill value={b.status} tone={statusTone(b.status)} /></td><td className="p-1">{b.approved_by ?? "—"}</td>
        <td className="p-1 space-x-2">
          {b.status === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== b.requester_id &&
            <button className={btn2} disabled={act.busy} onClick={() => run(async () => {
              const r = await request<Batch>(`/v1/optimization/site-batches/${b.id}/approve`, { method: "POST" });
              return `Batch ${r.status.toLowerCase().replace("_", " ")}.`; })}>Approve batch</button>}
          {me?.role === "admin" && !["DISABLED", "REJECTED"].includes(b.status) && <button className={btnDanger} disabled={act.busy}
            onClick={() => run(async () => { await request(`/v1/optimization/site-batches/${b.id}/disable`, { method: "POST" }); return "Batch disabled; all members revoked."; })}>Disable batch</button>}
        </td></tr>)}</tbody></table>
      {!batches.data?.length && <p className="text-sm text-slate-500">No site batches.</p>}</div>
  </Card>;
}
