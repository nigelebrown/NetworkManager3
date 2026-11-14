/**
 * Approved full-system wireframe workspaces. All values are retrieved from the
 * live API. Concept-only capabilities are explicitly identified, not simulated.
 * Operational changes are routed into the existing approval-gated workflows.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, Pill, ago, statusTone } from "../components/viz";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { ops, type ChangePlan, type NocTile, type LearningItem } from "../services/ops";
import { v1, type Device } from "../services/inm";
import { onboardingApi, type CredentialProfile } from "../services/onboarding";

export type WorkspaceKind = "rf" | "ports" | "journeys" | "approvals" | "recommendations" |
  "predictive" | "analytics" | "credentials" | "integrations" | "switchconfig" |
  "ssid" | "device360";

type Definition = { title: string; eyebrow: string; description: string; concept?: boolean;
  actions: { label: string; to: string }[] };
const CONFIG: Record<WorkspaceKind, Definition> = {
  rf: { title: "Wireless & RF health", eyebrow: "Monitoring & diagnostics",
    description: "Current radio load, AP state and observed wireless performance. Device-level RF history and configuration remain in Device 360°.",
    actions: [{ label: "Access points", to: "/aps" }, { label: "RF optimization", to: "/optimization" }] },
  ports: { title: "Ports, PoE & LLDP", eyebrow: "Monitoring & diagnostics",
    description: "CX switch estate, link health and available port telemetry. Select a switch to open its port faceplate, PoE and LLDP detail.",
    actions: [{ label: "Switch inventory", to: "/switches" }, { label: "Collection status", to: "/collection" }] },
  journeys: { title: "Client connectivity", eyebrow: "Monitoring & diagnostics", concept: true,
    description: "A unified connectivity-journey view is not yet instrumented end to end. Available Wi-Fi clients and AP observations are linked below; no authentication-path evidence is invented.",
    actions: [{ label: "Connected clients", to: "/clients" }, { label: "User experience", to: "/experience" }] },
  approvals: { title: "Approvals queue", eyebrow: "Configuration & changes",
    description: "Pending configuration requests and independently approved plans. Review and decide in the controlled Change Workflow; the requester cannot approve their own plan.",
    actions: [{ label: "Open change workflow", to: "/changes" }, { label: "Certification", to: "/certification" }] },
  recommendations: { title: "Recommendations", eyebrow: "Intelligence & automation",
    description: "Evidence-backed operational recommendations and analyst decisions. Recommendations do not automatically write to hardware.",
    actions: [{ label: "AI learning centre", to: "/learning" }, { label: "RF optimization", to: "/optimization" }] },
  predictive: { title: "Predictive health", eyebrow: "Intelligence & automation", concept: true,
    description: "Forecast-driven failure prediction has not been certified. The live device-health observations below are descriptive, not an ML failure forecast.",
    actions: [{ label: "Device insights", to: "/phase4-insights" }, { label: "Operations analytics", to: "/analytics" }] },
  analytics: { title: "Operational analytics", eyebrow: "Intelligence & automation",
    description: "Fleet trend reporting based on measured telemetry, change activity and source-aware quality. Unmeasured indicators remain unavailable.",
    actions: [{ label: "Executive overview", to: "/executive" }, { label: "Live collection", to: "/collection" }] },
  credentials: { title: "Credential profiles", eyebrow: "Administration",
    description: "Reusable encrypted Aruba management credentials, profile status and verification. Secret values are never displayed in this overview.",
    actions: [{ label: "Open credential setup", to: "/onboarding" }, { label: "Discovery", to: "/discovery" }] },
  integrations: { title: "Integrations", eyebrow: "Administration", concept: true,
    description: "Service integrations and external-system connectors are not all configurable in one module yet. These status readings come from live service health checks.",
    actions: [{ label: "Platform settings", to: "/platform" }, { label: "System health", to: "/system" }] },
  switchconfig: { title: "Switch configuration", eyebrow: "Configuration & changes",
    description: "Select a CX 6100 or CX 6200 to inspect configuration, prepare typed changes and submit them for independent approval.",
    actions: [{ label: "Configuration templates", to: "/config-templates" }, { label: "Change workflow", to: "/changes" }] },
  ssid: { title: "WLAN & SSID policies", eyebrow: "Configuration & changes",
    description: "Access-point SSID, VLAN, RF and authentication policies. Channel widths remain restricted to 20/40 MHz; real changes require approval and certification.",
    actions: [{ label: "Legacy WLAN settings", to: "/config" }, { label: "Configuration templates", to: "/config-templates" }] },
  device360: { title: "Device 360°", eyebrow: "Monitoring & diagnostics",
    description: "Choose a managed AP or switch for performance, port faceplate, configuration history, compliance, capabilities and operations.",
    actions: [{ label: "Device inventory", to: "/inventory" }, { label: "Discover devices", to: "/onboarding" }] },
};
const percent = (v: number | null | undefined) => v == null ? "Not measured" : `${Math.round(v)}%`;
const display = (v: number | null | undefined, suffix = "") => v == null ? "—" : `${Math.round(v * 10) / 10}${suffix}`;

function DeviceRows({ rows, mode }: { rows: NocTile[]; mode: WorkspaceKind }) {
  return <div className="inm-tablewrap"><table className="inm-table">
    <thead><tr><th>Device</th><th>Site</th><th>Model</th><th>State</th>
      <th>{mode === "rf" || mode === "ssid" ? "5 GHz utilization" : mode === "ports" || mode === "switchconfig" ? "Ports up" : "Health"}</th>
      <th>Last persisted</th><th>Details</th></tr></thead>
    <tbody>{rows.map(d => <tr key={d.device_id}>
      <td><Link className="inm-link" to={`/devices/${d.device_id}/360`}>{d.hostname || d.device_id.slice(0, 8)}</Link>
        <div className="text-[10px] text-slate-500">{d.source_type} · {d.family === "switch" ? "CX switch" : "Access point"}</div></td>
      <td>{d.site || "Unassigned"}</td><td>{d.model || "Unknown"}</td>
      <td><Pill value={d.state} tone={statusTone(d.state === "ONLINE" ? "PASS" : d.state === "OFFLINE" ? "FAIL" : d.state === "DEGRADED" ? "WARN" : "STALE")} /></td>
      <td>{mode === "rf" || mode === "ssid" ? percent(d.metrics.util_5g_percent)
        : mode === "ports" || mode === "switchconfig" ? display(d.metrics.ports_up, " up")
        : percent(d.health.score)}</td>
      <td>{ago(d.last_persisted_at)}</td>
      <td><Link className="inm-link" to={`/devices/${d.device_id}/360`}>Device 360°</Link>
        {(mode === "ssid" || mode === "switchconfig") &&
          <Link className="inm-link ml-3" to={`/devices/${d.device_id}/config`}>Configure</Link>}
      </td></tr>)}</tbody>
  </table>{!rows.length && <div className="inm-empty">No matching managed devices or observations are available.</div>}</div>;
}
function Kpi({ label, value, hint }: { label: string; value: string | number; hint: string }) {
  return <div className="inm-kpi"><span className="inm-kpi-label">{label}</span>
    <span className="inm-kpi-value">{value}</span><span className="inm-kpi-sub">{hint}</span></div>;
}

export default function WireframeWorkspace({ kind }: { kind: WorkspaceKind }) {
  const page = CONFIG[kind];
  const [search, setSearch] = useState("");
  const [state, setState] = useState("ALL");
  const needNoc = ["rf", "ports", "journeys", "predictive", "switchconfig", "ssid", "device360"].includes(kind);
  const noc = useLoad(() => needNoc ? ops.noc() : Promise.resolve(null), [kind], 30000, ["devices", "telemetry", "alerts"]);
  const plans = useLoad(() => kind === "approvals" ? ops.changePlans() : Promise.resolve([] as ChangePlan[]),
    [kind], 30000, ["changes"]);
  const suggestions = useLoad(() => kind === "recommendations" ? ops.learning() : Promise.resolve(null),
    [kind], 30000, ["learning"]);
  const creds = useLoad(() => kind === "credentials" ? onboardingApi.credentials() : Promise.resolve([] as CredentialProfile[]),
    [kind], 60000, ["devices"]);
  const services = useLoad(() => kind === "integrations" ? v1.services() : Promise.resolve(null),
    [kind], 30000, ["system"]);
  const trends = useLoad(() => kind === "analytics" ? ops.trends() : Promise.resolve(null),
    [kind], 60000, ["telemetry", "changes"]);
  const inventory = useLoad(() => kind === "device360" ? v1.devices() : Promise.resolve([] as Device[]),
    [kind], 30000, ["devices"]);
  const all = noc.data?.devices ?? [];
  const filtered = useMemo(() => all.filter(d => (
    (["rf", "ssid", "journeys"].includes(kind) ? d.family === "access_point"
      : ["ports", "switchconfig"].includes(kind) ? d.family === "switch" : true) &&
    (state === "ALL" || d.state === state) &&
    (!search.trim() || [d.hostname, d.site, d.model, d.device_id].some(x => (x || "").toLowerCase().includes(search.toLowerCase())))
  ), [all, state, search, kind]);
  const healthy = filtered.filter(x => x.state === "ONLINE").length;
  const atRisk = filtered.filter(x => ["OFFLINE", "DEGRADED", "STALE"].includes(x.state)).length;
  return <div className="space-y-4" data-wireframe-workspace={kind}>
    <PageHeader title={page.title} eyebrow={page.eyebrow} subtitle={page.description}
      right={<div className="inm-actionbar">{page.actions.map(a =>
        <Link key={a.to} to={a.to} className="inm-btn-secondary">{a.label}</Link>)}</div>} />
    {page.concept && <div className="inm-planned-notice" role="status">
      <b>DESIGN CONCEPT — NOT FULLY IMPLEMENTED.</b> The linked live information below is available now; no unsupported capability is represented as operational.
    </div>}
    <ErrorNote error={noc.error || plans.error || suggestions.error || creds.error || services.error || trends.error || inventory.error} />

    {needNoc && <section className="inm-status-overview" aria-label="Device health summary">
      <Kpi label="In scope" value={filtered.length} hint="Managed devices in current scope"/>
      <Kpi label="Online" value={healthy} hint="Persisted state from API"/>
      <Kpi label="Attention needed" value={atRisk} hint="Offline, degraded or stale"/>
      <Kpi label="Evidence" value={filtered.filter(x => x.last_persisted_at).length} hint="Devices with persisted telemetry"/>
    </section>}
    {needNoc && <section className="inm-card">
      <div className="inm-cardhead"><h2>{kind === "device360" ? "Open a device" : "Managed fleet observations"}</h2>
        <div className="inm-actionbar"><input aria-label="Filter devices" value={search}
          placeholder="Search device, model or site" onChange={e => setSearch(e.target.value)}/>
          <select aria-label="Device state" value={state} onChange={e => setState(e.target.value)}>
            <option value="ALL">All states</option>{["ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN"].map(v =>
              <option value={v} key={v}>{v}</option>)}
          </select></div>
      </div>
      <DeviceRows rows={filtered} mode={kind}/>
      <div className="inm-cardfooter">Live management API; DEMO devices remain labelled. Missing data is never substituted with estimates.</div>
    </section>}
    {kind === "device360" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Registered inventory</h2></div>
      <div className="inm-cardbody inm-workspace-actions">{(inventory.data ?? []).map(d =>
        <Link key={d.id} to={`/devices/${d.id}/360`}>{d.hostname || d.serial_number} · {d.model || d.family}</Link>)}
        {!inventory.data?.length && <p>No devices registered.</p>}
      </div>
    </section>}

    {kind === "approvals" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Change requests awaiting independent review</h2>
        <Pill value={`${(plans.data ?? []).filter(p => p.state === "PENDING_APPROVAL").length} pending`} tone="fair"/></div>
      <div className="inm-tablewrap"><table className="inm-table"><thead>
        <tr><th>Change</th><th>Requester</th><th>Device</th><th>State</th><th>Approver</th><th>Created</th><th>Review</th></tr>
      </thead><tbody>{(plans.data ?? []).filter(p => ["PENDING_APPROVAL", "APPROVED", "REJECTED"].includes(p.state))
        .map(p => <tr key={p.id}><td>{p.title}</td><td>{p.requester}</td><td>{p.device_id.slice(0, 10)}</td>
          <td><Pill value={p.state} tone={statusTone(p.state)}/></td><td>{p.approver || "—"}</td>
          <td>{ago(p.created_at)}</td><td><Link className="inm-link" to="/changes">Review in change workflow</Link></td>
        </tr>)}</tbody></table>
        {!plans.data?.length && <div className="inm-empty">No configuration plans have been submitted.</div>}
      </div>
      <div className="inm-cardfooter">Approval actions, audit records and execution remain in the existing controlled Change Workflow.</div>
    </section>}
    {kind === "recommendations" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Evidence-backed recommendations</h2><Pill value={`${suggestions.data?.items.length ?? 0} records`}/></div>
      <div className="inm-tablewrap"><table className="inm-table"><thead>
        <tr><th>Recommendation</th><th>Evidence</th><th>Decision</th><th>Observed</th><th>Review</th></tr>
      </thead><tbody>{(suggestions.data?.items ?? []).map((r: LearningItem) => <tr key={r.id}>
        <td>{r.title}<div className="text-[10px] text-slate-500">{r.why}</div></td>
        <td><Pill value={r.evidence_status} tone={statusTone(r.evidence_status)}/></td>
        <td>{r.feedback?.decision || "Awaiting review"}</td><td>{ago(r.created_at)}</td>
        <td><Link className="inm-link" to="/learning">Decision & outcome</Link></td>
      </tr>)}</tbody></table>{!suggestions.data?.items.length &&
        <div className="inm-empty">No recommendations available for the current inventory.</div>}</div>
    </section>}
    {kind === "credentials" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Credential library (metadata only)</h2></div>
      <div className="inm-tablewrap"><table className="inm-table"><thead>
        <tr><th>Profile</th><th>Family</th><th>Protocol</th><th>Username</th><th>Enabled</th><th>Last test</th><th>Manage</th></tr>
      </thead><tbody>{(creds.data ?? []).map(c => <tr key={c.id}>
        <td>{c.name}</td><td>{c.device_family}</td><td>{c.protocol}</td><td>{c.username}</td>
        <td><Pill value={c.enabled ? "Enabled" : "Disabled"} tone={c.enabled ? "good" : "poor"}/></td>
        <td>{c.last_test_status || "Not tested"}</td><td><Link className="inm-link" to="/onboarding">Manage profile</Link></td>
      </tr>)}</tbody></table>{!creds.data?.length &&
        <div className="inm-empty">No profiles configured. Create a credential from guided onboarding.</div>}</div>
    </section>}
    {kind === "integrations" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Observed platform service connections</h2><Pill value={services.data?.overall || "UNKNOWN"}/></div>
      <div className="inm-tablewrap"><table className="inm-table"><thead>
        <tr><th>Service role</th><th>Status</th><th>Latency</th><th>Detail</th></tr>
      </thead><tbody>{(services.data?.services ?? []).map(s => <tr key={s.name}>
        <td>{s.role}</td><td><Pill value={s.status} tone={statusTone(s.status === "UP" ? "PASS" : "FAIL")}/></td>
        <td>{s.latency_ms == null ? "—" : `${s.latency_ms} ms`}</td><td>{s.detail}</td>
      </tr>)}</tbody></table></div>
    </section>}
    {kind === "analytics" && <section className="inm-card">
      <div className="inm-cardhead"><h2>Measured 24-hour operational trend</h2></div>
      <div className="inm-cardbody">
        <div className="inm-status-overview">
          <Kpi label="Hourly samples" value={trends.data?.hourly.length ?? 0} hint="Aggregated observations"/>
          <Kpi label="Change days" value={trends.data?.changes_by_day.length ?? 0} hint="Days with recorded plans"/>
          <Kpi label="Audit events (24h)" value={trends.data?.audit_events_24h ?? "—"} hint="Reported by API"/>
          <Kpi label="Source" value="Persisted" hint="No fabricated series"/>
        </div>
        <div className="inm-tablewrap mt-4"><table className="inm-table"><thead>
          <tr><th>Hour</th><th>Clients</th><th>5 GHz utilization</th><th>CPU</th></tr>
        </thead><tbody>{(trends.data?.hourly ?? []).slice(-24).map((r,i) => <tr key={`${r.hour}-${i}`}>
          <td>{r.hour}</td><td>{display(r.clients)}</td><td>{percent(r.util_5g_percent)}</td>
          <td>{percent(r.cpu_percent)}</td></tr>)}</tbody></table></div>
      </div>
    </section>}
    {(kind === "journeys" || kind === "predictive") && <section className="inm-workspace-summary">
      <b>{kind === "predictive" ? "Health observations, not a failure forecast" :
          "Available wireless-client observations"}</b>
      <p className="mt-2 text-sm text-slate-600">{kind === "predictive"
        ? "Use current device health, recent errors, collection freshness and trend history to investigate risk. Trained predictive-maintenance alerts are not yet available."
        : "Client RSSI, SNR, access point, SSID and band are available through the client pages where the device reports them. Authentication and roaming-hop timelines require additional collectors."}</p>
      <div className="inm-workspace-actions"><Link to={kind === "predictive" ? "/device-alerts" : "/clients"}>
        {kind === "predictive" ? "Review real alerts" : "Investigate connected clients"}</Link>
      </div>
    </section>}
  </div>;
}
