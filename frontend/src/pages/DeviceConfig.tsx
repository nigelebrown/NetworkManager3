import { PageHeader } from "../components/viz";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { v1 } from "../services/inm";
import { onboardingApi } from "../services/onboarding";
import { rem } from "../services/remediation";

type SsidRow = { profile: string; essid: string; vlan: string; rf_band: string; width: "20" | "40"; enabled: boolean };
type PortRow = { name: string; admin_state: string; access_vlan: string };
const input = "mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm";
const button = "rounded bg-sky-600 px-4 py-2 text-sm font-semibold disabled:opacity-50";
const secondary = "rounded border border-slate-700 px-3 py-2 text-sm disabled:opacity-50";

export default function DeviceConfig() {
  const { id = "" } = useParams<{ id: string }>();
  const device = useLoad(() => v1.getDevice(id), [id], 30000, ["devices"]);
  const config = useLoad(() => onboardingApi.deviceConfig(id), [id], 30000, ["device-config"]);
  const acceptance = useLoad(() => onboardingApi.acceptance(id), [id], 30000, ["device-config"]);
  const caps = useLoad(() => rem.capabilities(id), [id], 30000, ["device-config", "devices"]);
  const profiles = useLoad(onboardingApi.credentials, [], 30000, ["onboarding-credentials"]);
  const [credentialId, setCredentialId] = useState("");
  const [vlanText, setVlanText] = useState("");
  const [ssids, setSsids] = useState<SsidRow[]>([]);
  const [ports, setPorts] = useState<PortRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!config.data) return;
    const raw = config.data.baseline?.config;
    setCredentialId(config.data.credential_id ?? "");
    setVlanText((raw?.vlans ?? []).map(v => typeof v === "number" ? String(v) : String(v.id)).join(", "));
    setSsids((raw?.ssids ?? []).filter(x => x && typeof x === "object").map(x => ({
      profile: String(x.profile ?? ""), essid: String(x.essid ?? ""),
      vlan: x.vlan == null ? "" : String(x.vlan),
      rf_band: String(x.rf_band ?? "all"),
      width: "20" as const, enabled: x.enabled !== false,
    })));
    setPorts((raw?.interfaces ?? []).filter(x => x && typeof x === "object").map(x => ({
      name: String(x.name ?? ""), admin_state: String(x.admin_state ?? "unknown"),
      access_vlan: x.access_vlan == null ? "" : String(x.access_vlan),
    })));
  }, [config.data]);

  const perform = async (fn: () => Promise<unknown>, result: string) => {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); config.reload(); acceptance.reload(); setMessage(result); }
    catch (e) { setError(e instanceof Error ? e.message : "Operation failed"); }
    finally { setBusy(false); }
  };
  const capture = () => perform(() => onboardingApi.capture(id, credentialId || undefined),
    "The read-only baseline was captured. Proposed edits remain separate from the physical device.");
  const save = () => {
    const numbers = vlanText.split(/[\s,]+/).filter(Boolean).map(Number);
    if (numbers.some(v => !Number.isInteger(v) || v < 1 || v > 4094)) {
      setError("Every VLAN must be an integer between 1 and 4094."); return;
    }
    const proposal = {
      vlans: Array.from(new Set(numbers)),
      ...(device.data?.family === "access_point"
        ? { ssids: ssids.map(x => ({
            profile: x.profile, essid: x.essid,
            vlan: x.vlan ? Number(x.vlan) : null,
            rf_band: x.rf_band,
            channel_width_mhz: Number(x.width),
            enabled: x.enabled,
          })) }
        : { interfaces: ports.map(x => ({
            name: x.name, admin_state: x.admin_state,
            access_vlan: x.access_vlan ? Number(x.access_vlan) : null,
          })) }),
    };
    perform(() => onboardingApi.saveDraft(id, proposal),
      "Configuration draft saved in the application. No changes were sent to the device.");
  };
  const updateSsid = (i: number, field: keyof SsidRow, value: string | boolean) =>
    setSsids(v => v.map((r, j) => i === j ? { ...r, [field]: value } : r));
  const updatePort = (i: number, field: keyof PortRow, value: string) =>
    setPorts(v => v.map((r, j) => i === j ? { ...r, [field]: value } : r));

  if (getSession()?.role !== "admin") return <Card>Administrator access required to review configuration baselines and drafts.</Card>;
  return <div className="space-y-4">
    <Link className="text-sm text-sky-400 underline" to={`/devices/${id}`}>← Device details</Link>
    <PageHeader eyebrow="Configuration & changes" title={`Device configuration — ${device.data?.hostname || "Loading…"}`} subtitle="Inspect current settings and prepare reviewed, certified change plans." />
    <p className="text-sm text-amber-300">
      This page edits application-side configuration drafts only; saving a draft never writes to hardware.
      Approved change plans and the certified read/write execution engine are available separately in Change Management.
    </p>
    <ErrorNote error={error ?? config.error ?? device.error} />
    {message && <p role="status" className="rounded border border-emerald-800 p-3 text-sm text-emerald-200">{message}</p>}
    <Card title="Phase 2 — device acceptance evidence">
      <p className="text-sm text-slate-300">
        This checks what was actually recorded. It does not contact the network or certify physical equipment.
      </p>
      <ErrorNote error={acceptance.error} />
      {acceptance.data && <>
        <div className="mt-3 flex flex-wrap gap-3 text-sm">
          <span>Stage: <b>{acceptance.data.stage.replace(/_/g, " ")}</b></span>
          <span>Model: <b>{acceptance.data.recognized_pilot_model ?? "Not in certified pilot model list"}</b></span>
          <span>Identity: <b>{acceptance.data.inventory_evidence.replace(/_/g, " ")}</b></span>
          <span>Credential: <b>{acceptance.data.credential.enabled ? "Assigned and enabled" : "Missing or disabled"}</b></span>
        </div>
        <p className="mt-2 text-xs text-amber-300">Physical-device certified: {caps.data ? (caps.data.hardware_certified ? "YES" : "NO") : "Checking…"}
          · Device writes: {caps.data?.write_posture.hardware_writes ?? acceptance.data.device_writes}
          · Adapter: {caps.data?.adapter ?? "Checking…"}</p>
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {acceptance.data.field_checks.map(field => <div key={field.field}
            className="rounded border border-slate-700 p-3 text-xs">
            <div className="font-semibold">{field.field.replace(/_/g, " ")}:
              <span className={field.status === "OBSERVED" ? "ml-2 text-emerald-300" : "ml-2 text-amber-300"}>
                {field.status.replace(/_/g, " ")}
              </span>
            </div>
            <p className="mt-1 text-slate-400">{field.detail}</p>
          </div>)}
        </div>
        <details className="mt-3 text-sm"><summary className="cursor-pointer">Remaining acceptance actions ({acceptance.data.remaining_actions.length})</summary>
          <ul className="ml-5 mt-2 list-disc space-y-1 text-slate-300">
            {acceptance.data.remaining_actions.map((action, i) => <li key={i}>{action}</li>)}
          </ul>
        </details>
      </>}
    </Card>
    <Card title="1. Capture or refresh the existing configuration">
      <label className="block text-sm">Credential profile (shared across devices)
        <select className={input} value={credentialId} onChange={e => setCredentialId(e.target.value)}>
          <option value="">Select a saved credential profile</option>
          {profiles.data?.filter(p => p.enabled && p.device_family === device.data?.family).map(p =>
            <option value={p.id} key={p.id}>{p.name} — {p.username}</option>)}
        </select>
      </label>
      <button className={`mt-3 ${button}`} disabled={busy || !credentialId} onClick={capture}>
        {busy ? "Reading…" : "Read existing configuration (no writes)"}
      </button>
      {config.data?.baseline
        ? <p className="mt-3 text-sm text-emerald-200">Baseline captured {fmtTime(config.data.baseline.captured_at)} · {config.data.baseline.completeness}
            <span className="block text-xs text-amber-300">{config.data.baseline.warning}</span>
          </p>
        : <p className="mt-3 text-sm text-slate-400">No baseline has been captured. Select a working credential and read the device before editing.</p>}
    </Card>
    {config.data?.baseline && <>
      <Card title="2. Review and propose VLAN settings">
        <p className="mb-2 text-xs text-slate-400">Current baseline: {(config.data.baseline.config.vlans ?? []).map(v => typeof v === "number" ? v : v.id).join(", ") || "No VLANs reported"}</p>
        <label className="text-sm">Proposed VLAN IDs (comma-separated)
          <input className={input} value={vlanText} onChange={e => setVlanText(e.target.value)}
            placeholder="100, 200, 300, 400, 500"/>
        </label>
      </Card>
      {device.data?.family === "access_point" && <Card title="3. Proposed SSID profiles">
        <div className="space-y-3">{ssids.map((row, i) => <div key={i} className="grid gap-2 rounded border border-slate-800 p-3 md:grid-cols-3">
          {([["profile", "Profile"], ["essid", "SSID"], ["vlan", "VLAN"]] as const).map(([k, name]) =>
            <label className="text-xs" key={k}>{name}<input className={input} value={row[k]}
              onChange={e => updateSsid(i, k, e.target.value)}/></label>)}
          <label className="text-xs">Radio band
            <select className={input} value={row.rf_band}
              onChange={e => updateSsid(i, "rf_band", e.target.value)}>
              {["all", "2.4", "5.0"].map(v => <option value={v} key={v}>{v}</option>)}
            </select>
          </label>
          <label className="text-xs">Channel width
            <select className={input} value={row.width}
              onChange={e => updateSsid(i, "width", e.target.value as "20" | "40")}>
              <option value="20">20 MHz</option><option value="40">40 MHz</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={row.enabled} onChange={e => updateSsid(i, "enabled", e.target.checked)}/>Enabled (draft)
          </label>
          <button type="button" className={secondary} onClick={() => setSsids(v => v.filter((_, j) => i !== j))}>Remove draft row</button>
        </div>)}</div>
        <button className={`mt-3 ${secondary}`} onClick={() => setSsids(v => [...v, {
          profile: "", essid: "", vlan: "", rf_band: "all", width: "20", enabled: true,
        }])}>Add an SSID draft row</button>
        <p className="mt-3 text-xs text-amber-300">The approved widths are 20/40 MHz only. SSID passwords are not read, edited or stored in this draft.</p>
      </Card>}
      {device.data?.family === "switch" && <Card title="3. Proposed switch interface settings">
        <p className="mb-2 text-xs text-slate-400">Interface state and VLAN information depends on the CX REST firmware; unavailable values are not inferred.</p>
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {ports.map((row, i) => <div className="grid gap-2 rounded border border-slate-800 p-2 md:grid-cols-3" key={i}>
            <label className="text-xs">Interface<input className={input} value={row.name}
              onChange={e => updatePort(i, "name", e.target.value)}/></label>
            <label className="text-xs">Administrative state
              <select className={input} value={row.admin_state}
                onChange={e => updatePort(i, "admin_state", e.target.value)}>
                {[...new Set([row.admin_state, "up", "down", "unknown"])].map(v => <option value={v} key={v}>{v}</option>)}
              </select>
            </label>
            <label className="text-xs">Access VLAN<input className={input} type="number" min="1" max="4094"
              value={row.access_vlan} onChange={e => updatePort(i, "access_vlan", e.target.value)}/></label>
          </div>)}
        </div>
      </Card>}
      <Card title="4. Review and save the proposed configuration">
        <p className="text-sm text-slate-300">
          This action only saves an auditable draft. For an actual configuration write, use
          Change Management: build and review a change plan, obtain independent approval,
          run the per-device execution gate, then verify and audit the readback. Uncertified or globally locked devices remain blocked.
        </p>
        <button className={`mt-3 ${button}`} disabled={busy} onClick={save}>Save configuration draft (no hardware changes)</button>
        <Link className="ml-3 text-xs text-sky-300 underline" to="/changes">Open Change Management →</Link>
      </Card>
    </>}
    <Card title="Saved drafts">
      {config.data?.drafts.map(d =>
        <div key={d.id} className="border-t border-slate-800 py-2 text-sm">
          <b>{d.state}</b> · {fmtTime(d.created_at)} · <code>{d.id}</code>
        </div>)}
      {!config.data?.drafts.length && <p className="text-sm text-slate-500">No drafts saved yet.</p>}
    </Card>
  </div>;
}
