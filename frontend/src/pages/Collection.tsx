import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, Steps, ago, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { v1, type Device } from "../services/inm";
import { getSession, request } from "../services/api";
import { onboardingApi } from "../services/onboarding";
import { ops } from "../services/ops";

const FAMILIES: Record<string, { key: string; label: string }[]> = {
  access_point: [{ key: "system", label: "System (CPU, memory, uptime)" }, { key: "radios", label: "Radios (channel utilisation, noise)" },
    { key: "clients", label: "Clients (count, experience)" }, { key: "rf_quality", label: "RF quality (retries)" }],
  switch: [{ key: "system", label: "System (CPU, memory, uptime)" }, { key: "interfaces", label: "Interfaces (up/down, errors)" },
    { key: "poe", label: "PoE power budget" }, { key: "lldp", label: "LLDP neighbours" }],
};

export default function Collection() {
  const plans = useLoad(ops.plans, [], 15000);
  const jobs = useLoad(() => ops.jobs(), [], 15000);
  const admin = getSession()?.role === "admin";
  const profiles = useLoad(() => admin ? onboardingApi.credentials() : Promise.resolve([]), [admin]);
  const inventory = useLoad(() => admin ? v1.devices() : Promise.resolve([]), [admin]);
  const [repair, setRepair] = useState<{id: string; profile: string; verified?: boolean; message?: string}>();
  const [testing, setTesting] = useState(false);
  const [device, setDevice] = useState<Device | undefined>();
  const [fams, setFams] = useState<string[]>(["system"]);
  const [interval, setIntervalS] = useState(300);
  const [preview, setPreview] = useState<Record<string, unknown> & { warnings: string[] } | null>(null);
  const act = useAction();
  const step = !device ? 0 : !preview ? 1 : 2;
  const body = () => ({ device_id: device?.id, metric_families: fams, interval_seconds: interval });
  const p = plans.data ?? [];
  const states = (s: string) => p.filter(x => x.freshness === s).length;
  return <div className="space-y-4">
    <PageHeader title="Live monitoring collection" subtitle="Schedule read-only polling of each AP and switch with its saved encrypted credential. Failures are retried with backoff and shown as stale — never as zero." />
    <ErrorNote error={plans.error} />
    <div className="grid gap-3 md:grid-cols-4"><Stat label="Collection plans" value={p.length} /><Stat label="Fresh" value={states("FRESH")} tone="ok" />
      <Stat label="Stale / gap" value={states("STALE") + states("GAP")} tone={states("STALE") + states("GAP") ? "warn" : "ok"} />
      <Stat label="Failing" value={p.filter(x => x.consecutive_failures > 0).length} tone={p.some(x => x.consecutive_failures) ? "bad" : "ok"} /></div>
    <Card title="Start monitoring a device">
      <div className="space-y-4">
        <Steps steps={["Choose device", "Choose metrics & interval", "Preview & approve"]} current={step} />
        <div className="grid gap-4 md:grid-cols-3">
          <DevicePicker value={device?.id ?? ""} onChange={(_, d) => { setDevice(d); setPreview(null); setFams(["system"]); }} />
          <label className="block text-sm text-slate-300">Polling interval
            <select className={inputCls} value={interval} onChange={e => { setIntervalS(Number(e.target.value)); setPreview(null); }}>
              {[60, 120, 300, 600, 900, 3600].map(s => <option key={s} value={s}>{s < 3600 ? `every ${s / 60} min` : "hourly"}</option>)}</select></label>
          <div className="text-sm text-slate-300">Metrics<div className="mt-1 space-y-1">{(FAMILIES[device?.family ?? "access_point"]).map(f =>
            <label key={f.key} className="flex items-center gap-2"><input type="checkbox" checked={fams.includes(f.key)} disabled={!device}
              onChange={e => { setPreview(null); setFams(e.target.checked ? [...fams, f.key] : fams.filter(x => x !== f.key)); }} />{f.label}</label>)}</div></div>
        </div>
        <div className="flex gap-2">
          <button className={btn2} disabled={!device || !fams.length || act.busy} onClick={() => act.run(async () => { setPreview(await ops.previewPlan(body())); })}>Preview</button>
          <button className={btn} disabled={!preview || act.busy} onClick={() => act.run(async () => {
            await ops.createPlan(body()); setPreview(null); plans.reload(); return "Collection plan approved. The worker will poll this device read-only on schedule."; })}>Approve plan</button>
        </div>
        {preview && <div className="rounded-lg border border-slate-700 p-3 text-sm">
          <div>Adapter <b>{String((preview.adapter as { key: string }).key)}</b> · {String(preview.polls_per_day)} polls/day · mode <Pill value="READ ONLY" tone="info" /></div>
          {preview.warnings.map(w => <div key={w} className="mt-1 text-amber-300">⚠ {w}</div>)}
          {!preview.warnings.length && <div className="mt-1 text-emerald-300">Credential bound and address recorded. Ready to collect.</div>}</div>}
        <Notice error={act.error} message={act.message} />
      </div>
    </Card>
    <Card title="Collection plans">
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">Device</th><th>Metrics</th><th>Every</th><th>State</th><th>Freshness</th><th>Last success</th><th>Last failure</th><th></th></tr></thead>
        <tbody>{p.map(x => {
          const target = inventory.data?.find(d => d.id === x.device_id);
          const ip = target?.management_addresses?.[0];
          const selected = repair?.id === x.id ? repair : undefined;
          const compatible = profiles.data?.filter(profile => profile.enabled && profile.device_family === target?.family) ?? [];
          return <tr key={x.id} className="border-t border-slate-800">
          <td className="py-1.5"><Link className="text-sky-300" to={`/devices/${x.device_id}/360`}>{x.hostname ?? x.device_id.slice(0, 8)}</Link>{x.source_type === "DEMO" && <> <Pill value="DEMO" tone="demo" /></>}</td>
          <td className="text-xs">{x.metric_families.join(", ")}</td><td>{x.interval_seconds / 60} min</td>
          <td><Pill value={x.state} tone={statusTone(x.state)} /></td><td><Pill value={x.freshness} tone={statusTone(x.freshness)} /></td>
          <td className="text-xs">{ago(x.last_success_at)}</td>
          <td className="text-xs">{x.last_failure_category ? <>
            <span className={x.consecutive_failures ? "text-red-300" : "text-slate-500"}>
              {x.last_failure_category}{x.consecutive_failures ? ` ×${x.consecutive_failures}` : " (historical)"}
            </span>
            {admin && x.source_type === "REAL" && ip && <>
              <div className="flex flex-wrap gap-1">
                <select aria-label={`Authentication profile for ${x.hostname ?? ip}`} className={inputCls}
                  disabled={testing} value={selected?.profile ?? ""}
                  onChange={e => setRepair({id: x.id, profile: e.target.value})}>
                  <option value="">Select authentication profile</option>
                  {compatible.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
                <button className={btn2} disabled={!selected?.profile || testing} onClick={async () => {
                  if (!selected) return;
                  setTesting(true);
                  try {
                    const checked = await onboardingApi.checkRestApi(selected.profile, ip);
                    setRepair({...selected, verified: checked.state === "ENABLED", message: checked.detail});
                  } catch (error) {
                    setRepair({...selected, verified: false, message: error instanceof Error ? error.message : "Test failed."});
                  } finally { setTesting(false); }
                }}>{testing && selected ? "Testing…" : "Test"}</button>
                {selected?.verified && <button className={btn2} disabled={testing} onClick={async () => {
                  setTesting(true);
                  try {
                    await request("/v1/onboarding/credentials/assign", {
                      method: "POST", body: JSON.stringify({credential_id: selected.profile, device_ids: [x.device_id]}),
                    });
                    setRepair({...selected, verified: false, message: "Profile applied."});
                    plans.reload();
                  } catch (error) {
                    setRepair({...selected, message: error instanceof Error ? error.message : "Could not apply profile."});
                  } finally { setTesting(false); }
                }}>Apply profile</button>}
              </div>
              {selected?.message && <p role="status">{selected.message}</p>}
            </>}
          </> : "—"}</td>
          <td className="space-x-1 whitespace-nowrap text-right">
            <button className={btn2} disabled={act.busy || x.state !== "ACTIVE"} onClick={() => act.run(async () => {
              const r = await ops.planAction(x.id, "run-now"); plans.reload(); jobs.reload(); return `Poll result: ${JSON.stringify(r.summary)}`; })}>Poll now</button>
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await ops.planAction(x.id, x.state === "ACTIVE" ? "pause" : "resume"); plans.reload(); })}>
              {x.state === "ACTIVE" ? "Pause" : "Resume"}</button></td></tr>;
        })}
          {!p.length && <tr><td colSpan={8} className="py-4 text-center text-slate-500">No plans yet.</td></tr>}</tbody></table>
    </Card>
    <Card title="Recent poll jobs"><table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th>Started</th><th>Device</th><th>Metric family</th><th>Attempt</th><th>State</th><th>Failure</th><th>Points</th></tr></thead>
      <tbody>{(jobs.data ?? []).slice(0, 40).map(j => <tr key={j.id} className="border-t border-slate-800"><td className="py-1">{ago(j.started_at)}</td><td>{j.device_id.slice(0, 8)}</td>
        <td>{j.metric_family}</td><td>{j.attempt}</td><td><Pill value={j.state} tone={statusTone(j.state)} /></td><td>{j.failure_category ?? ""}</td><td>{j.points_written}</td></tr>)}</tbody></table></Card>
  </div>;
}
