import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Gauge, PageHeader, Pill, SourceTag, TT, ago, statusTone } from "../components/viz";
import { RuleTable } from "./Compliance";
import Faceplate from "../components/Faceplate";
import { Notice, btn2, useAction } from "../components/viz";
import { rw, type DeviceOriginLogs, type DeviceDiagnostic } from "../services/ops";
import { api, getSession } from "../services/api";
import { rem } from "../services/remediation";
import { v1 } from "../services/inm";
import { ops } from "../services/ops";
import DiagnosisPanel from "../components/DiagnosisPanel";
import { STATE_TONE, TIER_TONE, pct, wl } from "../services/wireless";

const METRICS: Record<string, string[]> = {
  access_point: ["clients", "util_5g_percent", "util_2g_percent", "retry_percent", "noise_floor_dbm", "cpu_percent", "memory_percent"],
  switch: ["ports_up", "port_errors_delta", "max_port_util_percent", "poe_power_w", "cpu_percent", "memory_percent", "temp_max_c"],
};
const TABS = ["Performance", "Radios & clients", "Ports", "Incidents", "Best practice", "Changes", "Configuration", "Capabilities", "Operations"] as const;

const AP_TRENDS = [
  ["cpu_percent", "CPU utilization", "%"],
  ["memory_percent", "Memory utilization", "%"],
  ["client_count", "Connected clients", ""],
  ["util_2g_percent", "2.4 GHz channel utilization", "%"],
  ["util_5g_percent", "5 GHz channel utilization", "%"],
] as const;

function DeviceLogArchive({ deviceId }: { deviceId: string }) {
  const schedule = useLoad(() => rw.deviceLogSchedule(deviceId), [deviceId], 30000);
  const history = useLoad(() => rw.deviceLogHistory(deviceId), [deviceId], 30000);
  const act = useAction();
  const [minutes, setMinutes] = useState("");
  const s = schedule.data?.device_id === deviceId ? schedule.data : undefined;
  const records = history.data?.device_id === deviceId ? history.data.records : [];
  const interval = minutes === "" ? (s?.interval_minutes ?? 15) : Number(minutes);
  const admin = getSession()?.role === "admin";
  const save = (enabled: boolean) => act.run(async () => {
    await rw.setDeviceLogSchedule(deviceId, enabled, interval);
    await schedule.reload();
    return enabled ? "Schedule saved. Collection also requires the worker's live log-collection setting." : "Schedule paused; previously collected records retained.";
  });
  return <Card title="Scheduled device log archive">
    <p className="text-xs text-slate-500">Read-only, opt-in collection. Timestamps indicate collection time, not verified event time.
      AP records describe the controller's log view, not necessarily the selected member AP.</p>
    <ErrorNote error={schedule.error || history.error} />
    <Notice error={act.error} message={act.message} />
    {s && <div className="mt-2 space-y-1 text-sm">
      <p>Schedule: <b>{s.enabled ? "Enabled" : "Not enabled"}</b> · last result: {s.last_result?.state ?? "Not yet collected"}</p>
      {s.last_result?.reason && <p>Last failure: {s.last_result.reason}</p>}
      {s.last_completed_at && <p>Last completion: {new Date(s.last_completed_at).toLocaleString()}</p>}
      {Object.entries(s.last_result?.categories ?? {}).map(([kind, value]) =>
        <p key={kind}>{kind}: {value.state} · {value.received ?? 0} received · {value.added ?? 0} added
          {value.reason ? " · " + value.reason : ""}{value.possibly_truncated ? " · possibly truncated" : ""}</p>)}
      {admin && s.eligible && <div className="flex flex-wrap items-center gap-2">
        <label>Interval (minutes) <input type="number" min={5} max={1440}
          value={minutes === "" ? (s.interval_minutes ?? 15) : minutes}
          onChange={e => setMinutes(e.target.value)} className="inm-form-control w-24" /></label>
        <button className={btn2} disabled={act.busy || !Number.isInteger(interval) || interval < 5 || interval > 1440}
          onClick={() => save(true)}>Enable / save schedule</button>
        {s.enabled && <button className={btn2} disabled={act.busy} onClick={() => save(false)}>Pause schedule</button>}
      </div>}
    </div>}
    <div className="mt-3 max-h-80 overflow-auto"><table className="inm-table">
      <thead><tr><th>Collected</th><th>Category / scope</th><th>Reported actor</th><th>Message</th></tr></thead>
      <tbody>{records.map(r => <tr key={r.id}>
        <td>{new Date(r.collected_at).toLocaleString()}</td><td>{r.kind} / {r.scope} / {r.level}</td>
        <td>{r.reported_actor ?? "Unknown"}</td><td className="font-mono text-xs break-words">{r.message}</td>
      </tr>)}</tbody></table>
      {!records.length && <p className="inm-empty">No archived records for this device.</p>}</div>
  </Card>;
}

function ApTrends({ apId }: { apId: number }) {
  const [hours, setHours] = useState(6);
  const history = useLoad(() => api.apHistory(apId, hours), [apId, hours], 60000, ["telemetry"]);
  const data = (history.data ?? []).map(row => ({...row, time: Date.parse(row.ts)}));
  const timeLabel = (time: number) => new Date(time).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  return <Card title="AP and radio history" right={<select className="rounded border px-2 py-1"
    aria-label="AP history range" value={hours} onChange={e => setHours(Number(e.target.value))}>
    {[1, 6, 24].map(h => <option key={h} value={h}>{h} h</option>)}
  </select>}>
    <ErrorNote error={history.error} />
    <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
      {AP_TRENDS.map(([metric, title, unit]) => {
        const count = data.filter(row => row[metric] != null).length;
        return <div className="min-w-0" key={metric}>
          <h3 className="mb-2 text-sm font-semibold">{title}</h3>
          {count ? <ResponsiveContainer width="100%" height={155}><LineChart data={data}>
            <CartesianGrid stroke="#e6edf6" />
            <XAxis dataKey="time" type="number" domain={["dataMin", "dataMax"]} tickFormatter={timeLabel} minTickGap={30} fontSize={11} />
            <YAxis width={42} unit={unit} domain={unit ? [0, 100] : [0, "auto"]} fontSize={11} allowDecimals={false} />
            <Tooltip labelFormatter={timeLabel} />
            <Line type="linear" dataKey={metric} name={title} unit={unit} stroke={COLORS.info}
              connectNulls={false} dot={count === 1} isAnimationActive={false} />
          </LineChart></ResponsiveContainer> : <p className="text-xs text-slate-500">{history.loading ? "Loading readings…" : "No readings for this window."}</p>}
        </div>;
      })}
    </div>
  </Card>;
}

export default function Device360() {
  const { id = "" } = useParams();
  const device = useLoad(() => v1.getDevice(id), [id], 30000, ["devices"]);
  const insight = useLoad(() => wl.insight(id), [id], 30000, ["telemetry", "devices", "alerts"]);
  const [diag, setDiag] = useState(false);
  const d = device.data;
  const ins = insight.data;
  const [metric, setMetric] = useState("cpu_percent");
  const [hours, setHours] = useState(24);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Performance");
  const series = useLoad(() => ops.series(id, metric, hours), [id, metric, hours], 60000, ["telemetry"]);
  const comp = useLoad(() => ops.deviceCompliance(id), [id], 120000);
  const tl = useLoad(() => ops.timeline(id), [id], 60000);
  const ver = useLoad(() => ops.versions(id), [id], 120000);
  const ad = useLoad(() => ops.deviceAdapter(id), [id], 0);
  const opt = useLoad(() => ops.optState(id), [id], 60000);
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
  const metricsFor = ins?.series_available.length ? ins.series_available : (METRICS[d?.family ?? "switch"] ?? METRICS.switch);
  const label = (m: string) => ins?.metrics.find(x => x.metric === m)?.label ?? m.replace(/_/g, " ");
  const data = (series.data?.points ?? []).map(p => ({ t: new Date(p.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), v: p.v }));
  return <div className="space-y-4">
    <PageHeader eyebrow="Monitoring & diagnostics · Device 360°" title={d ? `${d.hostname ?? d.serial_number}` : "Device"} subtitle={d ? `${d.vendor} ${d.model ?? ""} · serial ${d.serial_number} · firmware ${d.firmware ?? "unknown"}` : undefined}
      right={d && <div className="flex items-center gap-2"><SourceTag source={d.source_type} />
        <span title={(d as { state_reason?: string }).state_reason}><Pill value={(d as { state?: string }).state ?? d.status}
          tone={statusTone((d as { state?: string }).state === "ONLINE" ? "PASS" : (d as { state?: string }).state ?? d.status)} /></span>
        {d.family === "access_point" && <button className="inm-btn-primary" onClick={() => setDiag(true)}>Diagnose</button>}
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/changes`}>Plan a change</Link>
        <Link className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm" to={`/devices/${id}`}>Inventory record</Link></div>} />
    <ErrorNote error={device.error} />
    {ins && ins.state.state !== "ONLINE" && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      <b>{ins.state.state}:</b> {ins.state.state_reason}.{ins.ap && <> <b>What to do:</b> {ins.ap.guidance}</>}</div>}
    <div className="grid gap-3 md:grid-cols-4">
      <Card><Gauge value={ins?.health.score} label={d?.family === "switch" ? "Health" : "Client experience"}
        tone={ins?.ap ? ({ good: "#39966d", fair: "#d69923", poor: "#dc7425", unavailable: "#94a3b8" }[ins.health.band] ?? "#94a3b8") : undefined} />
        <p className="mt-1 text-center text-xs text-slate-500">{ins ? (ins.health.reasons.join(" · ") || (ins.health.score == null ? "No measured telemetry" : "No threshold breached")) : "…"}</p>
        {!!ins?.health.inputs.length && <p className="text-center text-[10px] text-slate-400">checked: {ins.health.inputs.map(label).join(", ")}</p>}
        {ins?.ap && <p className="mt-1 text-center"><Pill value={ins.state.state === "OFFLINE" ? "OFFLINE" : `Tier ${ins.ap.tier}`}
          tone={ins.state.state === "OFFLINE" ? "poor" : TIER_TONE[ins.ap.tier]} /></p>}</Card>
      <Card><Gauge value={comp.data?.score} label="Best practice" />
        {ins && !ins.config.captured ? <p className="mt-1 text-center text-xs text-amber-700">No configuration captured yet, so rules cannot be checked.
          <button className="ml-1 underline" onClick={() => setTab("Operations")}>Capture it</button></p>
          : <p className="mt-1 text-center text-xs text-slate-500">Grade {comp.data?.grade ?? "—"}</p>}</Card>
      <Card title="Latest telemetry"><div className="space-y-1 text-sm">
        <div>Freshness <Pill value={ins?.state.freshness ?? "—"} tone={statusTone(ins?.state.freshness ?? "")} /></div>
        {(ins?.metrics ?? []).slice(0, 8).map(m => <div key={m.metric} className="flex justify-between gap-2"><span className="text-slate-500">{m.label}</span>
          <b>{Math.round(m.value * 10) / 10}{m.unit}</b></div>)}
        {ins && !ins.metrics.length && <p className="text-xs text-slate-500">No readings in 24 h. {d?.family === "access_point" ? "Link this AP to a polled cluster (Access points → Auto-onboard)." : "Enable read-only monitoring (Live collection)."}</p>}</div></Card>
      <Card title="Optimization"><div className="space-y-1 text-sm"><Pill value={opt.data?.badge ?? "AUTO OFF"} tone="none" />
        <p className="text-xs text-slate-400">Recommendations only unless this AP is individually enrolled and approved.</p>
        {d?.family === "access_point" && <button className="block text-xs text-sky-700 underline" onClick={() => setDiag(true)}>See recommended RF changes →</button>}
        <Link to="/optimization" className="text-xs text-sky-700">Optimization →</Link></div></Card>
    </div>
    <div className="inm-tabs" role="tablist" aria-label="Device 360 sections">{TABS.map(t => <button
      key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}
      className={`inm-tab ${tab === t ? "active" : ""}`}>{t}</button>)}</div>
    {tab === "Performance" && <Card title="History" right={<div className="flex gap-2">
      <select aria-label="Metric" className="rounded bg-slate-950 px-2 py-1 text-sm" value={metric} onChange={e => setMetric(e.target.value)}>{metricsFor.map(m => <option key={m} value={m}>{label(m)}</option>)}</select>
      <select aria-label="Range" className="rounded bg-slate-950 px-2 py-1 text-sm" value={hours} onChange={e => setHours(Number(e.target.value))}>{[1, 6, 24, 168].map(h => <option key={h} value={h}>{h < 24 ? `${h} h` : `${h / 24} d`}</option>)}</select></div>}>
      {data.length > 1 ? <ResponsiveContainer width="100%" height={260}><AreaChart data={data}>
        <defs><linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.info} stopOpacity={0.45} /><stop offset="100%" stopColor={COLORS.info} stopOpacity={0} /></linearGradient></defs>
        <CartesianGrid stroke="#e6edf6" /><XAxis dataKey="t" stroke="#74879b" fontSize={11} minTickGap={30} /><YAxis stroke="#74879b" fontSize={11} /><Tooltip {...TT} />
        <Area dataKey="v" name={metric} stroke={COLORS.info} fill="url(#m)" connectNulls={false} /></AreaChart></ResponsiveContainer>
        : data.length === 1 ? <p className="py-10 text-center text-sm text-slate-600">One reading so far: <b>{data[0].v}</b> at {data[0].t}. The trend appears after the next poll.</p>
        : <p className="py-10 text-center text-sm text-slate-500">No {label(metric)} readings in this window. {d?.family === "access_point"
          ? <>Link this AP to a polled cluster under <Link className="text-sky-700" to="/aps#autodiscover">Access points → Auto-onboard</Link>.</>
          : <>Enable read-only monitoring under <Link className="text-sky-700" to="/collection">Live collection</Link>.</>}</p>}
    </Card>}
    {tab === "Radios & clients" && <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Radios">{ins?.ap ? <table className="inm-table"><thead><tr><th>Band</th><th>Channel</th><th>Busy</th><th>Noise</th><th>TX power</th></tr></thead><tbody>
        {([["2.4 GHz", ins.ap.radio_2g], ["5 GHz", ins.ap.radio_5g]] as const).map(([b, r]) => <tr key={b}><td>{b}</td><td>{r.channel ?? "—"}</td><td>{pct(r.util_percent)}</td>
          <td>{r.noise_floor_dbm != null ? `${r.noise_floor_dbm} dBm` : "—"}</td><td>{r.tx_power_dbm != null ? `${r.tx_power_dbm} dBm` : "—"}</td></tr>)}</tbody></table>
        : <p className="text-sm text-slate-500">{d?.family === "switch" ? "Radios apply to access points." : "No radio data — the AP is not in a polled cluster."}</p>}
        {ins?.ap && <p className="mt-2 text-xs text-slate-500">Cluster {ins.ap.cluster ?? "not linked"} · read via {ins.ap.transport ?? "—"} · <Pill value={ins.ap.state} tone={STATE_TONE(ins.ap.state)} /></p>}</Card>
      {ins?.ap_extras && <ApExtrasCard x={ins.ap_extras} />}
      <Card title={`Connected clients (${ins?.clients.length ?? 0})`}>{ins?.client_bands && <p className="mb-2 text-xs text-slate-500">{Object.entries(ins.client_bands).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>}
        <div className="max-h-80 overflow-auto"><table className="inm-table"><thead><tr><th>Client</th><th>SSID</th><th>Band</th><th>SNR</th><th>Rate</th><th>Quality</th></tr></thead>
          <tbody>{(ins?.clients ?? []).map(c => <tr key={c.id}><td className="font-mono text-xs">{c.mac}<div className="text-[10px] text-slate-500">{c.hostname ?? ""}</div></td><td>{c.ssid ?? "—"}</td>
            <td>{c.band ?? "—"}</td><td>{c.snr_db ?? "—"}</td><td>{c.speed_mbps ?? "—"}</td><td><Pill value={c.quality ?? "—"} tone={c.quality === "poor" ? "poor" : c.quality === "fair" ? "fair" : "good"} /></td></tr>)}</tbody></table></div>
        {ins && !ins.clients.length && <p className="text-sm text-slate-500">No connected clients reported.</p>}
        <Link className="mt-2 inline-block text-xs text-sky-700" to="/client-journeys">Client connectivity →</Link></Card>
      {d?.family === "access_point" && d.legacy_ap_id != null &&
        <div className="lg:col-span-2"><ApTrends apId={d.legacy_ap_id} /></div>}
    </div>}
    {tab === "Incidents" && <Card title="Incidents on this device">
      <table className="inm-table"><thead><tr><th>Opened</th><th>Severity</th><th>Incident</th><th>Owner</th><th>Ticket</th><th>State</th></tr></thead><tbody>
        {(ins?.incidents ?? []).map(a => <tr key={a.id}><td className="text-xs">{ago(a.opened_at)}</td><td><Pill value={a.severity} tone={a.severity === "critical" ? "poor" : "fair"} /></td>
          <td>{a.message}</td><td>{a.assignee ?? "—"}</td><td>{a.ticket?.reference ?? "—"}</td>
          <td><Pill value={a.resolution?.type?.replace(/_/g, " ") ?? a.state} tone={a.state === "RESOLVED" ? "good" : "fair"} /></td></tr>)}</tbody></table>
      {ins && !ins.incidents.length && <p className="text-sm text-slate-500">No incidents recorded.</p>}
      <Link className="mt-2 inline-block text-xs text-sky-700" to="/device-alerts">Work incidents →</Link></Card>}
    {tab === "Ports" && <Card title="Switch faceplate">{d?.family === "switch" && ports.data
      ? <Faceplate data={ports.data} deviceId={id} /> : <p className="text-sm text-slate-500">Port faceplate applies to switches.</p>}</Card>}
    {tab === "Operations" && <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Configuration authority">
        <p className="text-sm text-slate-400">Before configuring, confirm Aruba Central / AirWave is not the configuration authority for this device.</p>
        <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
          const a = await rw.authority(id); return `${a.authority}: ${a.detail} (${a.environment})`; })}>Check management authority</button>
      </Card>
      {d?.family === "switch" && mayReadHardware && <Card title="Switch firmware and boot images">
        <p className="text-xs text-slate-500">Read-only verification; no image upload, staging or reboot.</p>
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
          const f = await rw.switchFirmwareStatus(id);
          return `${f.environment} / ${f.state}: running ${f.current_version ?? "unknown"}; primary ${f.primary_version ?? "unknown"}; secondary ${f.secondary_version ?? "unknown"}; booted ${f.booted_image ?? "unknown"}; default ${f.default_image ?? "unknown"}.`;
        })}>Check running and alternate firmware</button>
      </Card>}
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
      {mayReadHardware && d && <DeviceLogArchive key={id} deviceId={id} />}
      <Card title="Live device diagnostics (read-only)">
        <p className="text-xs text-slate-500">Run a fixed, documented device show command.
          No arbitrary CLI input, no configuration operations, and no automatic changes.
          Results are from the selected device and may vary by firmware and model.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="text-xs" htmlFor="device-diagnostic-kind">Diagnostic</label>
          <select id="device-diagnostic-kind" className="inm-form-control"
            value={(d?.family === "switch"
              ? ["port_auth", "vsf", "poe", "qos", "lacp", "transceivers", "optical_dom"] : ["rf", "clients", "system", "wireless"]).includes(diagnosticKind)
              ? diagnosticKind : d?.family === "switch" ? "port_auth" : "rf"}
            onChange={e => { setDiagnosticKind(e.target.value); setDiagnostic(null); }}>
            {(d?.family === "switch"
              ? ["port_auth", "vsf", "poe", "qos", "lacp", "transceivers", "optical_dom"] : ["rf", "clients", "system", "wireless"]).map(key =>
                <option key={key} value={key}>{key.replace(/_/g, " ")}</option>)}
          </select>
          {mayReadHardware && <button className={btn2} disabled={!d || act.busy}
            onClick={() => act.run(async () => {
              const allowed = d?.family === "switch" ? ["port_auth", "vsf", "poe", "qos", "lacp", "transceivers", "optical_dom"] :
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
    {diag && d && <DiagnosisPanel target={{ kind: "ap", id, label: d.hostname ?? d.serial_number }} onClose={() => setDiag(false)} />}
  </div>;
}


const bps = (v: number | null) => v == null ? null : v >= 1e6 ? `${(v / 1e6).toFixed(2)} Mb/s` : `${(v / 1e3).toFixed(1)} kb/s`;

function ApExtrasCard({ x }: { x: import("../services/wireless").ApExtras }) {
  const na = (why?: string | null) => <span className="text-slate-500" title={why ?? ""}>not available</span>;
  return <Card title="AP resources & radio neighbourhood">
    <div className="grid gap-3 text-sm md:grid-cols-4">
      <div><div className="text-xs text-slate-500">Free memory</div>{x.memory_free_mib == null ? na(x.memory_basis) : <b>{x.memory_free_mib} MiB</b>}
        {x.memory_free_mib != null && <div className="text-[10px] text-slate-500">{x.memory_basis}</div>}</div>
      <div><div className="text-xs text-slate-500">Uplink {x.uplink.interface ?? ""} rx / tx</div>
        {x.uplink.rx_bps == null ? na(x.uplink.quality) : <b>{bps(x.uplink.rx_bps)} / {bps(x.uplink.tx_bps)}</b>}
        <div className="text-[10px] text-slate-500">{x.uplink.quality}{x.uplink.interval_s ? ` over ${x.uplink.interval_s}s` : ""}</div></div>
      <div><div className="text-xs text-slate-500">Neighbouring APs heard</div>{x.neighbors.aps.total == null ? na(x.neighbors.aps.reason) : <b>{x.neighbors.aps.total}</b>}
        <div className="text-[10px] text-slate-500">{Object.entries(x.neighbors.aps.by_band ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</div></div>
      <div><div className="text-xs text-slate-500">Stations heard (monitor)</div>{x.neighbors.stations.total == null ? na(x.neighbors.stations.reason) : <b>{x.neighbors.stations.total}</b>}
        <div className="text-[10px] text-slate-500">{Object.entries(x.neighbors.stations.by_band ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</div></div>
    </div>
    <div className="mt-3 text-sm"><div className="text-xs text-slate-500">Signal of connected stations by band</div>
      {Object.keys(x.signal_distribution.by_band).length === 0 ? <span className="text-slate-500">No recently observed connected stations</span> :
        Object.entries(x.signal_distribution.by_band).map(([band, b]) => <div key={band} className="text-xs"><b>{band}</b>: {Object.entries(b).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>)}</div>
    <p className="mt-2 text-xs text-slate-500">Client Match: {x.client_match.state} — {x.client_match.reason}</p>
    {!!x.gaps.length && <p className="text-xs text-amber-600">Gaps: {x.gaps.join("; ")}</p>}
    <p className="text-[10px] text-slate-500">{x.neighbors.source}. {x.format_verification}.</p>
  </Card>;
}
