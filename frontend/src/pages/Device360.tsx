import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Gauge, PageHeader, Pill, SourceTag, TT, ago, statusTone } from "../components/viz";
import { RuleTable } from "./Compliance";
import Faceplate from "../components/Faceplate";
import { Notice, btn2, useAction } from "../components/viz";
import { rw, type DeviceOriginLogs, type DeviceDiagnostic } from "../services/ops";
import { getSession } from "../services/api";
import { rem } from "../services/remediation";
import { v1 } from "../services/inm";
import { ops } from "../services/ops";

const METRICS: Record<string, string[]> = {
  access_point: ["clients", "util_5g_percent", "util_2g_percent", "retry_percent", "noise_floor_dbm", "cpu_percent", "memory_percent"],
  switch: ["ports_up", "port_errors_delta", "max_port_util_percent", "poe_power_w", "cpu_percent", "memory_percent", "temp_max_c"],
};
const TABS = ["Performance", "Ports", "Best practice", "Changes", "Configuration", "Capabilities", "Operations"] as const;

export default function Device360() {
  const { id = "" } = useParams();
  const device = useLoad(() => v1.getDevice(id), [id], 30000, ["devices"]);
  const noc = useLoad(ops.noc, [], 30000);
  const d = device.data;
  const [metric, setMetric] = useState("cpu_percent");
  const [hours, setHours] = useState(24);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Performance");
  const series = useLoad(() => ops.series(id, metric, hours), [id, metric, hours], 60000, ["telemetry"]);
  const comp = useLoad(() => ops.deviceCompliance(id), [id], 120000);
  const tl = useLoad(() => ops.timeline(id), [id], 60000);
  const ver = useLoad(() => ops.versions(id), [id], 120000);
  const ad = useLoad(() => ops.deviceAdapter(id), [id], 0);
  const opt = useLoad(() => ops.optState(id), [id], 60000);
  const tile = noc.data?.devices.find(x => x.device_id === id);
  const ports = useLoad(() => rw.ports(id), [id], 30000, ["telemetry"]);
  const caps = useLoad(() => rem.capabilities(id), [id], 60000, ["devices"]);
  const cert = useLoad(() => rw.deviceCert(id), [id], 60000);
  const act = useAction();
  const [fp, setFp] = useState("");
  const [vc, setVc] = useState("");
  const [logKind, setLogKind] = useState<"audit" | "event" | "system" | "security" | "user">("audit");
  const [deviceLogs, setDeviceLogs] = useState<DeviceOriginLogs | null>(null);
  const [diagnosticKind, setDiagnosticKind] = useState("port_auth");
  const [diagnostic, setDiagnostic] = useState<DeviceDiagnostic | null>(null);
  const mayReadHardware = ["admin", "operator", "auditor"].includes(getSession()?.role ?? "");
  const metricsFor = METRICS[d?.family ?? "switch"] ?? METRICS.switch;
  const data = (series.data?.points ?? []).map(p => ({ t: new Date(p.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), v: p.v }));
  return <div className="space-y-4">
    <PageHeader eyebrow="Monitoring & diagnostics · Device 360°" title={d ? `${d.hostname ?? d.serial_number}` : "Device"} subtitle={d ? `${d.vendor} ${d.model ?? ""} · serial ${d.serial_number} · firmware ${d.firmware ?? "unknown"}` : undefined}
      right={d && <div className="flex items-center gap-2"><SourceTag source={d.source_type} />
        <span title={(d as { state_reason?: string }).state_reason}><Pill value={(d as { state?: string }).state ?? d.status}
          tone={statusTone((d as { state?: string }).state === "ONLINE" ? "PASS" : (d as { state?: string }).state ?? d.status)} /></span>
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/changes`}>Plan a change</Link>
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/devices/${id}`}>Inventory record</Link></div>} />
    <ErrorNote error={device.error} />
    <div className="grid gap-3 md:grid-cols-4">
      <Card><Gauge value={tile?.health.score} label="Experience" /><p className="mt-1 text-center text-xs text-slate-500">{tile?.health.reasons.join(" · ") || "No issues detected"}</p></Card>
      <Card><Gauge value={comp.data?.score} label="Best practice" /><p className="mt-1 text-center text-xs text-slate-500">Grade {comp.data?.grade ?? "—"}</p></Card>
      <Card title="Telemetry"><div className="space-y-1 text-sm"><div>Freshness <Pill value={tile?.freshness ?? "—"} tone={statusTone(tile?.freshness ?? "")} /></div>
        {Object.entries(tile?.metrics ?? {}).slice(0, 5).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-slate-400">{k}</span><b>{v}</b></div>)}</div></Card>
      <Card title="Optimization"><div className="space-y-1 text-sm"><Pill value={opt.data?.badge ?? "AUTO OFF"} tone="none" />
        <p className="text-xs text-slate-400">Recommendations only. Automatic optimization is disabled by policy; changes go through engineer change plans.</p>
        <Link to="/optimization" className="text-xs text-sky-300">Shadow simulation →</Link></div></Card>
    </div>
    <div className="inm-tabs" role="tablist" aria-label="Device 360 sections">{TABS.map(t => <button
      key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}
      className={`inm-tab ${tab === t ? "active" : ""}`}>{t}</button>)}</div>
    {tab === "Performance" && <Card title="History" right={<div className="flex gap-2">
      <select aria-label="Metric" className="rounded bg-slate-950 px-2 py-1 text-sm" value={metric} onChange={e => setMetric(e.target.value)}>{metricsFor.map(m => <option key={m}>{m}</option>)}</select>
      <select aria-label="Range" className="rounded bg-slate-950 px-2 py-1 text-sm" value={hours} onChange={e => setHours(Number(e.target.value))}>{[1, 6, 24, 168].map(h => <option key={h} value={h}>{h < 24 ? `${h} h` : `${h / 24} d`}</option>)}</select></div>}>
      {data.length > 1 ? <ResponsiveContainer width="100%" height={260}><AreaChart data={data}>
        <defs><linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.info} stopOpacity={0.45} /><stop offset="100%" stopColor={COLORS.info} stopOpacity={0} /></linearGradient></defs>
        <CartesianGrid stroke="#e6edf6" /><XAxis dataKey="t" stroke="#74879b" fontSize={11} minTickGap={30} /><YAxis stroke="#74879b" fontSize={11} /><Tooltip {...TT} />
        <Area dataKey="v" name={metric} stroke={COLORS.info} fill="url(#m)" connectNulls={false} /></AreaChart></ResponsiveContainer>
        : <p className="py-10 text-center text-sm text-slate-500">No {metric} readings in this window. Create a collection plan under <Link className="text-sky-300" to="/collection">Monitoring → Collection</Link>.</p>}
    </Card>}
    {tab === "Ports" && <Card title="Switch faceplate">{d?.family === "switch" && ports.data
      ? <Faceplate data={ports.data} deviceId={id} /> : <p className="text-sm text-slate-500">Port faceplate applies to switches.</p>}</Card>}
    {tab === "Operations" && <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Configuration authority">
        <p className="text-sm text-slate-400">Before configuring, confirm Aruba Central / AirWave is not the configuration authority for this device.</p>
        <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
          const a = await rw.authority(id); return `${a.authority}: ${a.detail} (${a.environment})`; })}>Check management authority</button>
      </Card>
      <Card title="Configuration backup">
        <div className="flex flex-wrap gap-2">
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
            const r = await rw.captureDevice(id); ver.reload(); comp.reload(); return `Captured full configuration (${r.size} bytes, SHA-256 ${r.sha256.slice(0, 12)}…) into the encrypted vault.`; })}>Capture configuration from device</button>
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await rw.schedule(id, 24); return "Daily scheduled capture enabled (drift and compliance alerts after each capture)."; })}>Schedule daily backup</button>
        </div>
      </Card>
      <Card title="Device-originated logs (read-only)">
        <p className="text-xs text-slate-500">Retrieve authenticated device accounting, security or system logs.
          Responses are bounded and credential-redacted. These are distinct from the platform audit trail.
          Hardware log formats still require device-specific verification.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs" htmlFor="device-log-kind">Log category</label>
          <select id="device-log-kind" className="inm-form-control"
            value={(d?.family === "switch" ? ["audit", "event"] : ["system", "security", "user"]).includes(logKind)
              ? logKind : d?.family === "switch" ? "audit" : "system"}
            onChange={e => { setLogKind(e.target.value as typeof logKind); setDeviceLogs(null); }}>
            {(d?.family === "switch" ? ["audit", "event"] : ["system", "security", "user"]).map(kind =>
              <option key={kind} value={kind}>{kind}</option>)}
          </select>
          {mayReadHardware && <button className={btn2} disabled={!d || act.busy}
            onClick={() => act.run(async () => {
              const kind = d?.family === "switch"
                ? (["audit", "event"].includes(logKind) ? logKind : "audit")
                : (["system", "security", "user"].includes(logKind) ? logKind : "system");
              const result = await rw.deviceLogs(id, kind, 50);
              setDeviceLogs(result);
              return `Read ${result.count} device log record(s) from ${result.environment}. No configuration writes occurred.`;
            })}>Fetch device logs</button>}
        </div>
        {deviceLogs?.device_id === id && <div className="mt-3 max-h-64 overflow-auto">
          <div className="mb-2 text-xs text-slate-500">{deviceLogs.count} records · {deviceLogs.environment} ·
            hardware-verified: {deviceLogs.hardware_verified ? "yes" : "not yet"}</div>
          <table className="inm-table"><thead><tr><th>Time</th><th>Actor</th><th>Message</th></tr></thead>
            <tbody>{deviceLogs.records.map(item => <tr key={item.id}>
              <td className="text-xs">{item.timestamp || "—"}</td>
              <td className="text-xs">{item.actor || "Unknown"}</td>
              <td className="font-mono text-xs break-words">{item.message}</td></tr>)}</tbody></table>
          {!deviceLogs.count && <div className="inm-empty">No log entries returned by the device.</div>}
        </div>}
      </Card>
      <Card title="Live device diagnostics (read-only)">
        <p className="text-xs text-slate-500">Run a fixed, documented device show command.
          No arbitrary CLI input, no configuration operations, and no automatic changes.
          Results are from the selected device and may vary by firmware and model.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs" htmlFor="device-diagnostic-kind">Diagnostic</label>
          <select id="device-diagnostic-kind" className="inm-form-control"
            value={(d?.family === "switch"
              ? ["port_auth", "vsf", "poe", "qos", "lacp"] : ["rf", "clients", "system", "wireless"]).includes(diagnosticKind)
              ? diagnosticKind : d?.family === "switch" ? "port_auth" : "rf"}
            onChange={e => { setDiagnosticKind(e.target.value); setDiagnostic(null); }}>
            {(d?.family === "switch"
              ? ["port_auth", "vsf", "poe", "qos", "lacp"] : ["rf", "clients", "system", "wireless"]).map(key =>
                <option key={key} value={key}>{key.replace(/_/g, " ")}</option>)}
          </select>
          {mayReadHardware && <button className={btn2} disabled={!d || act.busy}
            onClick={() => act.run(async () => {
              const allowed = d?.family === "switch" ? ["port_auth", "vsf", "poe", "qos", "lacp"] :
                ["rf", "clients", "system", "wireless"];
              const response = await rw.diagnostics(id, allowed.includes(diagnosticKind)
                ? diagnosticKind : allowed[0]);
              setDiagnostic(response);
              return `Read-only ${response.command} completed. Device writes: ${response.device_writes}.`;
            })}>Run read-only diagnostic</button>}
        </div>
        {diagnostic?.device_id === id && <div className="mt-3">
          <p className="mb-2 text-xs text-slate-500">{diagnostic.source} ·
            {diagnostic.environment} · {diagnostic.command}</p>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border p-3 text-xs"
            aria-label="Sanitized device diagnostic output">{diagnostic.output}</pre>
        </div>}
        {!mayReadHardware && <p className="mt-2 text-xs text-slate-500">
          Device-originated diagnostics require an operator, auditor or administrator account.</p>}
      </Card>
      <Card title="Templates → deployment">
        <p className="text-sm text-slate-400">Compare the inherited template with what is installed and create an approvable change plan containing only the needed operations.</p>
        <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
          const r = await rw.deployPlan(id); return r.in_compliance ? "Installed configuration already matches the templates." : `Change plan created with ${r.operations?.length} operation(s) — review it in Change workflow.`; })}>Create deployment plan from templates</button>
      </Card>
      <Card title={`Write certification — ${cert.data?.model_family ?? ""} firmware ${cert.data?.firmware_train ?? ""}`}>
        <ul className="grid grid-cols-1 gap-1 text-xs md:grid-cols-2">{Object.entries(cert.data?.operations ?? {}).map(([op, st]) => <li key={op} className="flex justify-between gap-2">
          <span className="font-mono">{op}</span><Pill value={st} tone={st === "CERTIFIED_WRITE" ? "good" : st === "FAILED" ? "poor" : "fair"} /></li>)}</ul>
        {!Object.keys(cert.data?.operations ?? {}).length && <p className="text-sm text-amber-300">Uncertified: change plans for this model/firmware are refused until certified on a lab device.</p>}
        <Link className="mt-2 inline-block text-xs text-sky-300" to="/certification">Certification →</Link>
      </Card>
      {d?.family === "access_point" && <Card title="SSH host key (needed for ARM/radio changes)">
        <div className="flex flex-wrap items-end gap-2">
          <button className={btn2} onClick={() => act.run(async () => { const r = await rw.scanKey(id); setFp(r.fingerprint ?? r.sha256 ?? ""); return "Compare this fingerprint with the AP console before pinning."; })}>Scan host key</button>
          <input aria-label="Fingerprint" className="rounded bg-slate-950 px-2 py-1 text-xs" value={fp} onChange={e => setFp(e.target.value)} placeholder="SHA256:…" />
          <input aria-label="Virtual Controller IP" className="rounded bg-slate-950 px-2 py-1 text-xs" value={vc} onChange={e => setVc(e.target.value)} placeholder="VC IP (optional)" />
          <button className={btn2} disabled={!fp} onClick={() => act.run(async () => { await rw.pinKey(id, fp, vc); return "Host key pinned."; })}>Pin</button>
        </div>
      </Card>}
      <div className="lg:col-span-2"><Notice error={act.error} message={act.message} /></div>
    </div>}
    {tab === "Best practice" && comp.data && <Card><RuleTable c={comp.data} /></Card>}
    {tab === "Changes" && <Card title="Change timeline — who changed what" right={tl.data && <span className="text-xs text-slate-400">Evidence sources: {tl.data.coverage.present.join(", ")}{tl.data.coverage.missing.length ? ` · missing ${tl.data.coverage.missing.join(", ")}` : ""}</span>}>
      <ul className="space-y-2 text-sm">{(tl.data?.attributions ?? []).map(a => <li key={a.id} className={`flex flex-wrap items-center gap-2 ${a.current === false ? "opacity-50" : ""}`}>
        <span className="w-24 text-xs text-slate-500">{ago(a.change_at)}</span><Pill value={a.level} tone={a.level === "VERIFIED" ? "good" : a.level === "CORRELATED" ? "fair" : "poor"} />
        <span>{a.change_type === "APPLICATION" ? "Change via this platform" : "Direct device change"}</span>
        <b>{a.actor ?? "operator unknown"}</b>{a.candidates.length > 0 && !a.actor && <span className="text-xs text-slate-500">candidates: {a.candidates.join(", ")}</span>}
        {a.supersedes_id && <Pill value="correction" tone="info" />}{a.note && <span className="text-xs text-slate-400">“{a.note}”</span>}</li>)}
        {!tl.data?.attributions.length && <li className="text-slate-500">No configuration changes detected.</li>}</ul>
      <h3 className="mt-4 text-sm font-semibold">Device log</h3>
      <ul className="mt-1 max-h-60 space-y-1 overflow-auto text-xs">{(tl.data?.log_events ?? []).map(e => <li key={e.id} className="flex gap-2">
        <span className="w-20 text-slate-500">{ago(e.observed_at)}</span><Pill value={e.severity} tone="none" /><span>{e.message}</span></li>)}</ul></Card>}
    {tab === "Configuration" && <Card title="Configuration versions" right={<Link className="text-sm text-sky-300" to="/vault">Open config vault →</Link>}>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Captured</th><th>Trigger</th><th>Source</th><th>SHA-256</th><th>Restore</th></tr></thead>
        <tbody>{(ver.data?.full_fidelity ?? []).map(a => <tr key={a.id} className="border-t border-slate-800"><td className="py-1">{new Date(a.created_at).toLocaleString()}</td>
          <td>{a.trigger}</td><td>{a.capture_source}</td><td className="font-mono text-xs">{a.plaintext_sha256.slice(0, 16)}…</td><td><Pill value={a.restore_state} tone={statusTone(a.restore_state)} /></td></tr>)}</tbody></table>
      <p className="mt-2 text-xs text-slate-500">{ver.data?.redacted_baselines.length ?? 0} redacted onboarding baseline(s) also exist; they are not restorable.</p></Card>}
    {tab === "Capabilities" && caps.data && <Card title={`${caps.data.adapter} · ${caps.data.model_family} firmware ${caps.data.firmware_train} · ${caps.data.environment}`}>
      <p className="mb-2 text-xs text-slate-400">Software = what the platform implements. Certification = proven for THIS model + firmware train + environment
        (emulated certification is never hardware certification). Hardware writes: <b>{caps.data.write_posture.hardware_writes}</b>.
        {!caps.data.adapter_implemented && <b className="text-amber-300"> Placeholder adapter — not implemented.</b>}</p>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Operation</th><th>Software</th><th>Certification</th><th>Executable now</th><th>Why</th></tr></thead>
        <tbody>{caps.data.operations.map(o => <tr key={o.operation} className="border-t border-slate-800">
          <td className="py-1 font-mono text-xs">{o.operation}</td><td><Pill value={o.software} tone={o.software === "IMPLEMENTED" ? "good" : "fair"} /></td>
          <td><Pill value={o.certification} tone={o.certification === "CERTIFIED_WRITE" ? "good" : o.certification === "FAILED" ? "poor" : "fair"} /></td>
          <td>{o.executable_now ? "yes" : "no"}</td><td className="text-xs text-slate-400">{o.reason}</td></tr>)}</tbody></table>
      <div className="mt-3 flex flex-wrap gap-2">{caps.data.read_operations.map(o => <Pill key={o.op} value={`${o.op}: ${o.status === "AVAILABLE_READ_ONLY" ? "read OK" : o.status}`} tone={o.status === "AVAILABLE_READ_ONLY" ? "good" : "none"} />)}</div>
      {ad.data && <p className="mt-2 text-xs text-slate-500">{ad.data.adapter.notes}</p>}</Card>}
  </div>;
}
