/**
 * Approved full-system wireframe workspaces. All values are retrieved from the
 * live API. Concept-only capabilities are explicitly identified, not simulated.
 * Operational changes are routed into the existing approval-gated workflows.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { PageHeader, Pill, ago, statusTone } from "../components/viz";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { ops, type ChangePlan, type NocTile } from "../services/ops";
import { v1, type Device } from "../services/inm";
import { onboardingApi, type CredentialProfile } from "../services/onboarding";

// rc18: only workspaces that a route renders remain; the former concept pages (integrations, predictive,
// journeys, recommendations, analytics, rf) were replaced by complete screens and are removed.
export type WorkspaceKind = "ports" | "approvals" | "credentials" | "switchconfig" | "ssid" | "device360";

type Definition = { title: string; eyebrow: string; description: string;
  actions: { label: string; to: string }[] };
const CONFIG: Record<WorkspaceKind, Definition> = {
  ports: { title: "Ports, PoE & LLDP", eyebrow: "Monitoring & diagnostics",
    description: "CX switch estate, link health and available port telemetry. Select a switch to open its port faceplate, PoE and LLDP detail.",
    actions: [{ label: "Switch inventory", to: "/switches" }, { label: "Collection status", to: "/collection" }] },
  approvals: { title: "Approvals queue", eyebrow: "Configuration & changes",
    description: "Pending configuration requests and independently approved plans. Review and decide in the controlled Change Workflow; the requester cannot approve their own plan.",
    actions: [{ label: "Open change workflow", to: "/changes" }, { label: "Certification", to: "/certification" }] },
  credentials: { title: "Credential profiles", eyebrow: "Administration",
    description: "Reusable encrypted Aruba management credentials, profile status and verification. Secret values are never displayed in this overview.",
    actions: [{ label: "Open credential setup", to: "/onboarding" }, { label: "Discovery", to: "/discovery" }] },
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
      <th>{mode === "ssid" ? "5 GHz utilization" : mode === "ports" || mode === "switchconfig" ? "Ports up" : "Health"}</th>
      <th>Last persisted</th><th>Details</th></tr></thead>
    <tbody>{rows.map(d => <tr key={d.device_id}>
      <td><Link className="inm-link" to={`/devices/${d.device_id}/360`}>{d.hostname || d.device_id.slice(0, 8)}</Link>
        <div className="text-[10px] text-slate-500">{d.source_type} · {d.family === "switch" ? "CX switch" : "Access point"}</div></td>
      <td>{d.site || "Unassigned"}</td><td>{d.model || "Unknown"}</td>
      <td><Pill value={d.state} tone={statusTone(d.state === "ONLINE" ? "PASS" : d.state === "OFFLINE" ? "FAIL" : d.state === "DEGRADED" ? "WARN" : "STALE")} />
        {d.state !== "ONLINE" && <div className="mt-1 max-w-[220px] text-[10px] text-slate-500">{d.state_reason}</div>}</td>
      <td>{mode === "ssid" ? percent(d.metrics.util_5g_percent)
        : mode === "ports" || mode === "switchconfig" ? display(d.metrics.ports_up, " up")
        : percent(d.health.score)}</td>
      <td>{ago(d.last_persisted_at)}</td>
      <td><Link className="inm-link" to={`/devices/${d.device_id}/360`}>Device 360°</Link>
        {(mode === "ssid" || mode === "switchconfig") && getSession()?.role === "admin" &&
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
  const [fam, setFam] = useState("ALL");
  const [site, setSite] = useState("ALL");
  const needNoc = ["ports", "switchconfig", "ssid", "device360"].includes(kind);
  const noc = useLoad(() => needNoc ? ops.noc() : Promise.resolve(null), [kind], 30000, ["devices", "telemetry", "alerts"]);
  const plans = useLoad(() => kind === "approvals" ? ops.changePlans() : Promise.resolve([] as ChangePlan[]),
    [kind], 30000, ["changes"]);
  const creds = useLoad(() => kind === "credentials" ? onboardingApi.credentials() : Promise.resolve([] as CredentialProfile[]),
    [kind], 60000, ["devices"]);
  const inventory = useLoad(() => kind === "device360" ? v1.devices() : Promise.resolve([] as Device[]),
    [kind], 30000, ["devices"]);
  const all = noc.data?.devices ?? [];
  const filtered = useMemo(() => all.filter(d => (
    (kind === "ssid" ? d.family === "access_point"
      : ["ports", "switchconfig"].includes(kind) ? d.family === "switch" : true) &&
    (state === "ALL" || d.state === state) && (fam === "ALL" || d.family === fam) && (site === "ALL" || (d.site || "Unassigned") === site) &&
    (!search.trim() || [d.hostname, d.site, d.model, d.device_id].some(x => (x || "").toLowerCase().includes(search.toLowerCase())))
  )), [all, state, search, kind, fam, site]);
  const siteNames = useMemo(() => Array.from(new Set(all.map(d => d.site || "Unassigned"))).sort(), [all]);
  const healthy = filtered.filter(x => x.state === "ONLINE").length;
  const atRisk = filtered.filter(x => x.state !== "ONLINE").length;
  const unknown = filtered.filter(x => x.state === "UNKNOWN").length;
  return <div className="space-y-4" data-wireframe-workspace={kind}>
    <PageHeader title={page.title} eyebrow={page.eyebrow} subtitle={page.description}
      right={<div className="inm-actionbar">{page.actions.map(a =>
        <Link key={a.to} to={a.to} className="inm-btn-secondary">{a.label}</Link>)}</div>} />
    <ErrorNote error={noc.error || plans.error || creds.error || inventory.error} />

    {needNoc && <section className="inm-status-overview" aria-label="Device health summary">
      <Kpi label="In scope" value={filtered.length} hint="Managed devices in current scope"/>
      <Kpi label="Online" value={healthy} hint="Persisted state from API"/>
      <Kpi label="Attention needed" value={atRisk} hint={unknown ? `${unknown} not observed (UNKNOWN) · ${atRisk - unknown} offline/degraded/stale` : "Offline, degraded, stale or unknown"}/>
      <Kpi label="Evidence" value={filtered.filter(x => x.last_persisted_at).length} hint="Devices with persisted telemetry"/>
    </section>}
    {needNoc && <section className="inm-card">
      <div className="inm-cardhead"><h2>{kind === "device360" ? "Open a device" : "Managed fleet observations"}</h2>
        <div className="inm-actionbar"><input aria-label="Filter devices" value={search}
          placeholder="Search device, model or site" onChange={e => setSearch(e.target.value)}/>
          <select aria-label="Device state" value={state} onChange={e => setState(e.target.value)}>
            <option value="ALL">All states</option>{["ONLINE", "DEGRADED", "STALE", "OFFLINE", "UNKNOWN"].map(v =>
              <option value={v} key={v}>{v}</option>)}
          </select>
          {kind === "device360" && <select aria-label="Device type" value={fam} onChange={e => setFam(e.target.value)}>
            <option value="ALL">APs and switches</option><option value="access_point">Access points</option><option value="switch">Switches</option></select>}
          <select aria-label="Site" value={site} onChange={e => setSite(e.target.value)}>
            <option value="ALL">All sites</option>{siteNames.map(n => <option key={n}>{n}</option>)}</select></div>
      </div>
      <DeviceRows rows={filtered.slice(0, 300)} mode={kind}/>
      {filtered.length > 300 && <div className="inm-empty">Showing 300 of {filtered.length}. Narrow with search, type or site.</div>}
      <div className="inm-cardfooter">Live management API; DEMO devices remain labelled. Missing data is never substituted with estimates.</div>
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
  </div>;
}
