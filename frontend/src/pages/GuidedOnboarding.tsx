import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Card } from "../components/ui";
import DeviceRestBootstrap from "../components/DeviceRestBootstrap";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, btnDanger, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { onboardingApi } from "../services/onboarding";
import { ops, rw } from "../services/ops";
import { rem, wf, type DevicePreview, type Workflow } from "../services/remediation";

const LABEL: Record<string, string> = {
  SITE: "Site", CREDENTIAL: "Credentials", DISCOVER: "Discover", REGISTER: "Identify & register",
  BASELINE: "Import & back up", TEMPLATES: "Templates & group", PREVIEW: "Preview", DECIDE: "Now or later",
  APPROVAL: "Independent approval", EXECUTE: "Apply & read back", MONITOR: "Monitor", DONE: "Done",
};

/** One resumable workflow from site selection to a configured, verified and monitored device (audit E2E-06). */
export default function GuidedOnboarding() {
  const [params, setParams] = useSearchParams();
  const id = params.get("id");
  const me = getSession();
  const act = useAction();
  const list = useLoad(() => wf.list(), [], 15000, ["workflows"]);
  const [w, setW] = useState<Workflow | null>(null);
  const load = async () => { if (id) setW(await wf.get(id)); };
  useEffect(() => { setW(null); load().catch(() => {}); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);
  // live discovery progress
  useEffect(() => {
    if (!w?.discovery || !["QUEUED", "SCANNING"].includes(w.discovery.status)) return;
    const t = setInterval(() => load().catch(() => {}), 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w?.discovery?.status, id]);
  const run = (fn: () => Promise<Workflow | void>, ok: string) => act.run(async () => { const r = await fn(); if (r) setW(r); return ok; });

  if (!id || !w) return <div className="space-y-4">
    <PageHeader title="Add & configure devices" subtitle="One guided workflow: site → credentials → discovery → import & backup → templates → preview → independent approval → apply with readback → monitoring. Progress is saved; you can leave and resume." />
    <Start onStarted={(nw) => { setParams({ id: nw.id }); setW(nw); }} />
    <Card title="Resume a workflow">
      <table className="w-full text-sm"><tbody>{(list.data ?? []).map(x => <tr key={x.id} className="border-t border-slate-800">
        <td className="py-1"><Link className="text-sky-300" to={`/onboarding?id=${x.id}`}>{x.title}</Link></td><td>{x.site}</td>
        <td><Pill value={LABEL[x.step] ?? x.step} tone="info" /></td><td><Pill value={x.state} tone={statusTone(x.state === "DONE" ? "PASS" : x.state)} /></td>
        <td className="text-xs text-slate-500">{x.devices} device(s) · {ago(x.updated_at)}</td></tr>)}</tbody></table>
      {!list.data?.length && <p className="text-sm text-slate-500">No workflows yet.</p>}
    </Card>
    <p className="text-xs text-slate-500">Single-device manual entry and CSV import remain under <Link className="underline" to="/onboarding/manual">Manual / CSV import</Link>.</p>
  </div>;

  const cur = w.step === "DONE" ? w.steps.length - 1 : w.steps.findIndex(s => s.status === "current");
  return <div className="space-y-4">
    <PageHeader title={w.title} subtitle={`${w.site.name} · ${w.device_family === "switch" ? "switches" : "access points"} · workflow ${w.id.slice(0, 8)} · ${w.state}`}
      right={<div className="flex gap-2"><button className={btn2} onClick={() => setParams({})}>All workflows</button>
        {w.state === "ACTIVE" && <button className={btnDanger} onClick={() => run(() => wf.cancel(w.id), "Workflow cancelled; devices, backups and plans are kept.")}>Cancel</button>}</div>} />
    <ol className="grid grid-cols-3 gap-1 md:grid-cols-6 lg:grid-cols-12" aria-label="Workflow steps">
      {w.steps.map((s, i) => <li key={s.step} className={`rounded border px-2 py-1 text-[11px] ${s.status === "current" ? "border-sky-500 text-sky-200" : s.status === "done" ? "border-emerald-900 text-emerald-300" : "border-slate-700 text-slate-500"}`}>
        {i + 1}. {LABEL[s.step]}</li>)}
    </ol>
    <Notice error={act.error} message={act.message} />
    {w.state !== "CANCELLED" && <>
      {cur <= 1 && <CredentialStep w={w} run={run} />}
      {cur >= 2 && cur <= 3 && <DiscoverStep w={w} run={run} reload={load} />}
      {cur === 4 && <Card title="5 · Import the current configuration and back it up">
        <p className="text-sm text-slate-400">Reads each device's full running configuration (read-only) and seals it encrypted in the vault. This is the restore point and the basis for the preview.</p>
        <DeviceTable w={w} />
        <button className={`${btn} mt-2`} disabled={act.busy} onClick={() => run(() => wf.baseline(w.id), "Configuration imported and backed up.")}>Import & back up configuration</button>
        {w.outcome.baseline && <ul className="mt-2 text-xs">{Object.entries(w.outcome.baseline as Record<string, { ok: boolean; error?: string; sha256?: string }>).map(([d, r]) =>
          <li key={d}>{d.slice(0, 8)}: {r.ok ? <span className="text-emerald-300">backed up {r.sha256?.slice(0, 12)}…</span> : <span className="text-red-300">{r.error}</span>}</li>)}</ul>}
      </Card>}
      {cur === 5 && <TemplateStep w={w} run={run} />}
      {(cur === 6 || cur === 7) && <PreviewStep w={w} run={run} />}
      {(cur === 8 || cur === 9) && <ApproveApplyStep w={w} run={run} me={me} reload={load} />}
      {cur >= 10 && <Card title={cur !== 11 ? "11 · Start monitoring and verify the result"
        : w.mode === "LATER" ? "Monitored — configuration deferred"
        : w.plans.some(p => p.state !== "APPLIED_VERIFIED") ? `Monitored — ${w.plans.filter(p => p.state !== "APPLIED_VERIFIED").length} change plan(s) not applied (approve, certify or unlock, then apply)`
        : "Done — devices are configured, verified and monitored"}>
        <DeviceTable w={w} />
        {w.mode === "LATER" && <p className="mt-2 text-sm text-amber-300">Configuration was deferred. Resume configuration to review the current state, submit new change plans and obtain independent approval.</p>}
        <div className="mt-2 flex gap-2">
          <button className={btn} disabled={act.busy} onClick={() => run(() => wf.monitor(w.id), "Monitoring active; first poll done.")}>Start monitoring & poll now</button>
          {w.mode === "LATER" && <button className={btn2} onClick={() => run(() => wf.resume(w.id), "Configuration resumed; preview is required.")}>Resume configuration</button>}
        </div>
      </Card>}
    </>}
    <Card title="History"><ol className="space-y-1 text-xs">{[...w.history].reverse().map((h, i) => <li key={i}>
      <span className="text-slate-500">{ago(h.at)}</span> <b>{LABEL[h.step] ?? h.step}</b> · {h.actor} — {h.detail}</li>)}</ol></Card>
  </div>;
}

function Start({ onStarted }: { onStarted: (w: Workflow) => void }) {
  const act = useAction();
  const [title, setTitle] = useState("");
  const [family, setFamily] = useState("switch");
  const [site, setSite] = useState("");
  return <Card title="1 · Start: site and device type">
    <div className="grid gap-3 md:grid-cols-3">
      <label className="text-sm">Task name<input aria-label="Task name" className={inputCls} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Clinic A access switches" /></label>
      <label className="text-sm">Device type<select aria-label="Device type" className={inputCls} value={family} onChange={e => setFamily(e.target.value)}>
        <option value="switch">Aruba CX switches (6100 / 6200)</option><option value="access_point">Aruba Instant APs (505 / 535)</option></select></label>
      <label className="text-sm">Site (existing or new)<input aria-label="Site" className={inputCls} value={site} onChange={e => setSite(e.target.value)} placeholder="Site name" /></label>
    </div>
    <button className={`${btn} mt-3`} disabled={act.busy || title.trim().length < 3 || !site.trim()} onClick={() => act.run(async () => {
      onStarted(await wf.start({ title: title.trim(), device_family: family, site_name: site.trim() })); })}>Start workflow</button>
    <Notice error={act.error} message={act.message} />
  </Card>;
}

type Run = (fn: () => Promise<Workflow | void>, ok: string) => Promise<void>;

function CredentialStep({ w, run }: { w: Workflow; run: Run }) {
  const creds = useLoad(onboardingApi.credentials, [], 0);
  const mine = (creds.data ?? []).filter(c => c.device_family === w.device_family && c.enabled);
  const [pick, setPick] = useState("");
  const [nf, setNf] = useState({ name: "", username: "", password: "" });
  return <Card title="2 · Credentials (create once, reuse for every device)">
    <div className="grid gap-4 md:grid-cols-2">
      <div><label className="text-sm">Use an existing profile<select aria-label="Credential profile" className={inputCls} value={pick} onChange={e => setPick(e.target.value)}>
        <option value="">Select…</option>{mine.map(c => <option key={c.id} value={c.id}>{c.name} ({c.username})</option>)}</select></label>
        <button className={`${btn} mt-2`} disabled={!pick} onClick={() => run(() => wf.credential(w.id, pick), "Credentials selected.")}>Use this profile</button></div>
      <div className="space-y-2"><p className="text-sm">Or create one (encrypted; never shown again)</p>
        <input aria-label="Profile name" className={inputCls} placeholder="Profile name" value={nf.name} onChange={e => setNf({ ...nf, name: e.target.value })} />
        <input aria-label="Username" className={inputCls} placeholder="Username" value={nf.username} onChange={e => setNf({ ...nf, username: e.target.value })} />
        <input aria-label="Password" type="password" className={inputCls} placeholder="Password" value={nf.password} onChange={e => setNf({ ...nf, password: e.target.value })} />
        <button className={btn2} disabled={!nf.name || !nf.username || nf.password.length < 4} onClick={() => run(async () => {
          const p = await onboardingApi.createCredential({ ...nf, device_family: w.device_family }); creds.reload();
          return wf.credential(w.id, p.id); }, "Credential profile created and selected.")}>Create & use</button></div>
    </div>
  </Card>;
}

function DiscoverStep({ w, run, reload }: { w: Workflow; run: Run; reload: () => Promise<void> }) {
  const [scope, setScope] = useState("");
  const [sel, setSel] = useState<number[]>([]);
  const [restTarget, setRestTarget] = useState("");
  const d = w.discovery;
  const act = useAction();
  return <Card title="3–4 · Discover, identify and register">
    <div className="flex flex-wrap items-end gap-2">
      <label className="text-sm">Management IP or CIDR (up to /24)<input aria-label="Discovery scope" className={inputCls} value={scope} onChange={e => setScope(e.target.value)} placeholder="10.10.20.0/24" /></label>
      <button className={btn} disabled={!scope.trim()} onClick={() => run(() => wf.discover(w.id, scope.trim()), "Discovery started.")}>Discover</button>
      {w.credential_id && /^(?:\d{1,3}\.){3}\d{1,3}$/.test(scope.trim()) &&
        <button className={btn2} onClick={() => setRestTarget(scope.trim())}>Check REST on this IP</button>}
    </div>
    {d && <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm"><Pill value={d.status} tone={statusTone(d.status === "SCANNED" ? "PASS" : d.status)} />
        {d.progress && <><div className="h-2 w-48 rounded bg-slate-800"><div className="h-2 rounded bg-sky-500" style={{ width: `${d.progress.percent}%` }} /></div>
          <span>{d.progress.done}/{d.progress.total} addresses · {d.progress.found} found · {d.progress.errors} errors</span></>}
        {d.progress && ["QUEUED", "SCANNING"].includes(d.status) && <button className={btn2} onClick={() => act.run(async () => { await wf.discoveryControl(d.id, "pause"); await reload(); })}>Pause</button>}
        {d.status === "PAUSED" && <button className={btn2} onClick={() => act.run(async () => { await wf.discoveryControl(d.id, "resume"); await reload(); })}>Resume</button>}
        {d.progress && ["QUEUED", "SCANNING", "PAUSED"].includes(d.status) && <button className={btn2} onClick={() => act.run(async () => { await wf.discoveryControl(d.id, "cancel"); await reload(); })}>Cancel scan</button>}
        {["FAILED", "CANCELLED"].includes(d.status) && <button className={btn2} onClick={() => act.run(async () => { await wf.discoveryControl(d.id, "retry"); await reload(); })}>Retry</button>}
      </div>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th></th><th>Address</th><th>Result</th><th>Hostname</th><th>Model / firmware</th><th>Serial</th><th>Note</th></tr></thead>
        <tbody>{d.results.map((r, i) => <tr key={r.management_ip + i} className="border-t border-slate-800">
          <td>{r.enrollable && r.status === "FOUND" && <input type="checkbox" aria-label={`Select ${r.management_ip}`} checked={sel.includes(i)} onChange={e => setSel(s => e.target.checked ? [...s, i] : s.filter(x => x !== i))} />}</td>
          <td className="font-mono text-xs">{r.management_ip}</td><td><Pill value={r.status} tone={r.status === "FOUND" ? "good" : r.status === "DUPLICATE" ? "fair" : "poor"} /></td>
          <td>{r.hostname}</td><td>{r.model} {r.firmware}</td><td className="font-mono text-xs">{r.serial_number}</td>
          <td className="text-xs text-slate-400">{r.existing_device_id ? "Already in inventory — will be updated, not duplicated. " : ""}{r.result_note}
            {!r.enrollable && r.status !== "FOUND" && <button className="ml-2 text-sky-300 underline"
              onClick={() => setRestTarget(r.management_ip)}>Check / enable REST</button>}
          </td></tr>)}</tbody></table>
      <button className={btn} disabled={!sel.length || d.status !== "SCANNED"} onClick={() => run(() => wf.register(w.id, sel), "Devices registered.")}>Register selected ({sel.length})</button>
      <Notice error={act.error} message={act.message} />
    </div>}
    {restTarget && w.credential_id && <DeviceRestBootstrap
      key={`${w.credential_id}-${restTarget}`} credentialId={w.credential_id} address={restTarget}
      family={w.device_family} onEnabled={async () => {
        const scan = d?.scope || scope.trim() || restTarget;
        await wf.discover(w.id, scan);
        await reload(); setRestTarget("");
      }} />}
  </Card>;
}

function DeviceTable({ w }: { w: Workflow }) {
  return <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Device</th><th>Model / firmware</th><th>State</th><th></th></tr></thead>
    <tbody>{w.devices.map(d => <tr key={d.device_id} className="border-t border-slate-800">
      <td className="py-1"><Link className="text-sky-300" to={`/devices/${d.device_id}/360`}>{d.hostname ?? d.serial_number}</Link> {d.source_type === "DEMO" && <Pill value="EMULATED" tone="demo" />}</td>
      <td>{d.model} {d.firmware}</td><td><Pill value={d.state} tone={statusTone(d.state === "ONLINE" ? "PASS" : d.state)} /> <span className="text-xs text-slate-500">{d.state_reason}</span></td><td></td></tr>)}</tbody></table>;
}

function TemplateStep({ w, run }: { w: Workflow; run: Run }) {
  const groups = useLoad(() => rem.groups(), [], 0);
  const [g, setG] = useState("");
  const [ng, setNg] = useState("");
  return <Card title="6 · Configuration scope: global → group → site → model → device">
    <p className="text-sm text-slate-400">Global, site, model and device templates always apply. Put these devices in a group to apply that group's templates (for example port roles for a 24- or 48-port access switch). <Link className="text-sky-300" to="/config-templates">Edit templates</Link></p>
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <label className="text-sm">Existing group<select aria-label="Group" className={inputCls} value={g} onChange={e => setG(e.target.value)}>
        <option value="">None</option>{(groups.data ?? []).filter(x => x.family === w.device_family).map(x => <option key={x.id} value={x.id}>{x.name} ({x.members.length})</option>)}</select></label>
      <label className="text-sm">…or new group<input aria-label="New group name" className={inputCls} value={ng} onChange={e => setNg(e.target.value)} /></label>
      <button className={btn} onClick={() => run(() => wf.templates(w.id, { group_id: g || null, new_group_name: ng || null }), "Scope saved.")}>Continue to preview</button>
    </div>
  </Card>;
}

function PreviewStep({ w, run }: { w: Workflow; run: Run }) {
  const pv = useLoad(() => wf.preview(w.id), [w.id], 0);
  return <Card title="7–8 · Preview the effective configuration and choose when to apply">
    {(pv.data?.devices ?? []).map((d: DevicePreview) => <div key={d.device_id} className="mb-3 rounded border border-slate-800 p-2">
      <div className="text-sm font-semibold">{d.hostname} <span className="text-xs text-slate-500">templates: {d.templates.map(t => `${t.name} (${t.scope})`).join(" → ") || "none"}</span></div>
      {d.reason && <p className="text-sm text-amber-300">{d.reason}</p>}
      {Object.keys(d.port_roles ?? {}).length > 0 && <p className="text-xs text-slate-400">Port roles: {Object.entries(
        Object.entries(d.port_roles).reduce((a, [, r]) => ({ ...a, [r]: (a[r] ?? 0) + 1 }), {} as Record<string, number>)).map(([r, n]) => `${r} ×${n}`).join(", ")}</p>}
      {d.operations && (d.operations.length ? <ul className="mt-1 max-h-48 overflow-y-auto text-xs">{d.operations.map((o, i) => <li key={i} className="flex gap-2">
        <span className="font-mono">{String(o.op)}</span><span className="text-slate-400">{o.port ? String(o.port) : ""} {JSON.stringify(o.value ?? "")}</span>
        <Pill value={d.certification[String(o.op)] ?? "UNCERTIFIED"} tone={d.certification[String(o.op)] === "CERTIFIED_WRITE" ? "good" : "fair"} /></li>)}</ul>
        : <p className="text-sm text-emerald-300">Already matches the intended configuration.</p>)}
    </div>)}
    <div className="flex gap-2">
      <button className={btn} onClick={() => run(() => wf.decide(w.id, "NOW"), "Change plans submitted for independent approval.")}>Configure now (submit for approval)</button>
      <button className={btn2} onClick={() => run(() => wf.decide(w.id, "LATER"), "Configuration deferred; monitoring will still be set up.")}>Configure later</button>
    </div>
    <p className="mt-2 text-xs text-slate-500">Uncertified operations are refused at execution until the model/firmware is certified on a lab device; hardware writes also require the platform write lock to be off.</p>
  </Card>;
}

function ApproveApplyStep({ w, run, me, reload }: { w: Workflow; run: Run; me: ReturnType<typeof getSession>; reload: () => Promise<void> }) {
  const act = useAction();
  const plans = useLoad(() => ops.changePlans(), [w.id], 10000, ["changes"]);
  const [ex, setEx] = useState<Record<string, Awaited<ReturnType<typeof rw.executions>>>>({});
  useEffect(() => {
    w.plans.filter(p => p.execution).forEach(p => rw.executions(p.plan_id).then(e => setEx(x => ({ ...x, [p.plan_id]: e }))).catch(() => {}));
  }, [w.plans]);
  return <Card title="9–10 · Independent approval, then apply with readback">
    <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Device</th><th>Plan</th><th>State</th><th>Approver</th><th>Result</th><th></th></tr></thead>
      <tbody>{w.plans.map(p => {
        const full = plans.data?.find(x => x.id === p.plan_id);
        const canApprove = full && full.state === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== full.requester;
        return <tr key={p.plan_id} className="border-t border-slate-800 align-top">
          <td className="py-1">{p.hostname}</td><td className="text-xs">{p.operations} operation(s)</td>
          <td><Pill value={p.state} tone={statusTone(p.state === "APPLIED_VERIFIED" ? "PASS" : p.state.includes("FAIL") ? "FAIL" : p.state)} /></td>
          <td className="text-xs">{p.approver ?? (full?.state === "PENDING_APPROVAL" ? "awaiting a second person" : "—")}</td>
          <td className="text-xs">{p.execution && <>{p.execution.outcome} <Pill value={p.execution.environment} tone={p.execution.environment === "EMULATED" ? "demo" : "info"} />
            <ul className="mt-1">{(ex[p.plan_id]?.[0]?.steps ?? []).filter(s => s.step === "apply_readback").map((s, i) =>
              <li key={i} className={s.ok ? "text-emerald-300" : "text-red-300"}>{s.ok ? "✓" : "✗"} {String(s.op?.op)} readback {JSON.stringify(s.readback)}</li>)}</ul></>}</td>
          <td>{canApprove && <button className={btn2} onClick={() => act.run(async () => { await ops.decideChange(full!.id, "approve", full!.revision, "Approved in onboarding workflow"); await reload(); plans.reload(); return "Approved."; })}>Approve</button>}</td>
        </tr>; })}</tbody></table>
    {!w.plans.length && <p className="text-sm text-slate-400">No change was needed — the devices already match their templates.</p>}
    <div className="mt-2 flex gap-2">
      {me?.role === "admin" && <button className={btn} disabled={!w.plans.some(p => p.state === "APPROVED")} onClick={() => run(() => wf.execute(w.id), "Execution finished — see each device's readback.")}>Apply to device</button>}
      <span className="self-center text-xs text-slate-400">Monitoring unlocks only after every selected device is verified as configured.</span>
    </div>
    {w.outcome.execution && <ul className="mt-2 text-xs">{(w.outcome.execution as { plan_id: string; decision: string; outcome: string | null; denial_reason: string | null }[]).map(r =>
      <li key={r.plan_id} className={r.outcome === "APPLIED_VERIFIED" ? "text-emerald-300" : "text-amber-300"}>{r.plan_id.slice(0, 8)}: {r.outcome ?? `${r.decision} — ${r.denial_reason}`}</li>)}</ul>}
    <Notice error={act.error} message={act.message} />
  </Card>;
}
