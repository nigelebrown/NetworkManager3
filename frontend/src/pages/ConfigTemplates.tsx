import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { v1 } from "../services/inm";
import { templateApi, type Template, type TemplateDoc, type TemplateFamily, type TemplateScope, type Preview } from "../services/configTemplates";

const input = "mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm";
const primary = "rounded bg-sky-600 px-4 py-2 text-sm font-semibold disabled:opacity-50";
const secondary = "rounded border border-slate-700 px-3 py-2 text-sm disabled:opacity-50";
const empty = { name: "", family: "switch" as TemplateFamily, scope: "global" as TemplateScope,
  site_id: "", device_id: "", model_contains: "", priority: 100,
  vlans: "100,200,300,400,500", ssid: "", ssidVlan: "100", width: "20" as "20" | "40",
  port: "", portVlan: "100", trunkVlans: "", enabled: true };
type Form = typeof empty;
function toForm(t: Template): Form {
  const ssid = t.config.ssids?.[0];
  const port = t.config.interfaces?.[0];
  return {
    name: t.name, family: t.family, scope: t.scope,
    site_id: t.site_id ?? "", device_id: t.device_id ?? "",
    model_contains: t.model_contains ?? "", priority: t.priority,
    vlans: t.config.vlans.join(","),
    ssid: ssid?.essid ?? "", ssidVlan: String(ssid?.vlan ?? 100),
    width: String(ssid?.channel_width_mhz ?? 20) as "20" | "40",
    port: port?.name ?? "", portVlan: String(port?.access_vlan ?? 100),
    trunkVlans: port?.trunk_vlans?.join(",") ?? "", enabled: t.enabled,
  };
}
function ids(text: string): number[] {
  const values = text.trim() ? text.split(/[\s,]+/).filter(Boolean).map(Number) : [];
  if (values.some(n => !Number.isInteger(n) || n < 1 || n > 4094)) {
    throw new Error("VLAN IDs must be whole numbers from 1 through 4094.");
  }
  if (new Set(values).size !== values.length) throw new Error("Duplicate VLAN ID.");
  return values;
}
function configOf(f: Form): TemplateDoc {
  const config: TemplateDoc = { vlans: ids(f.vlans) };
  if (f.family === "access_point" && f.ssid.trim()) {
    const vlan = ids(f.ssidVlan);
    if (vlan.length !== 1) throw new Error("Enter exactly one SSID VLAN.");
    config.ssids = [{
      profile: f.ssid.trim(), essid: f.ssid.trim(), vlan: vlan[0], rf_band: "all",
      channel_width_mhz: Number(f.width) as 20 | 40, enabled: true,
    }];
  }
  if (f.family === "switch" && f.port.trim()) {
    const vlan = ids(f.portVlan);
    if (vlan.length !== 1) throw new Error("Enter exactly one port access VLAN.");
    config.interfaces = [{
      name: f.port.trim(), admin_state: "up", access_vlan: vlan[0],
      trunk_vlans: ids(f.trunkVlans),
    }];
  }
  return config;
}

export default function ConfigTemplates() {
  const templates = useLoad(templateApi.list, [], 30000, ["templates"]);
  const sites = useLoad(v1.physicalSites, [], 30000, ["sites"]);
  const devices = useLoad(v1.devices, [], 30000, ["devices"]);
  const [form, setForm] = useState<Form>(empty);
  const [editing, setEditing] = useState<Template | null>(null);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const patch = (updates: Partial<Form>) => setForm(v => ({ ...v, ...updates }));
  const realDevices = (devices.data ?? []).filter(d => d.source_type === "REAL" && d.vendor === "Aruba");
  const realSites = (sites.data?.sites ?? []).filter(s => s.source_type === "REAL");
  const work = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Operation failed"); }
    finally { setBusy(false); }
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    work(async () => {
      const config = configOf(form);
      const payload = {
        name: form.name.trim(), family: form.family, scope: form.scope,
        site_id: form.scope === "site" ? form.site_id : null,
        device_id: form.scope === "device" ? form.device_id : null,
        model_contains: form.model_contains.trim() || null, priority: form.priority,
        enabled: form.enabled, config,
      };
      if (editing) {
        await templateApi.update(editing.id, editing.revision, { config, priority: form.priority, enabled: form.enabled });
        setMessage("Configuration template revised. No device configuration was changed.");
      } else {
        await templateApi.create(payload);
        setMessage("Configuration template saved. It is available for inheritance previews.");
      }
      setEditing(null); setForm(empty); setPreview(null); templates.reload();
    });
  };
  const inspect = () => work(async () => {
    if (!selectedDevice) throw new Error("Choose a registered real Aruba device.");
    const result = await templateApi.preview(selectedDevice);
    setPreview(result);
    setMessage("Effective configuration calculated. No device was contacted or modified.");
  });
  const draft = () => work(async () => {
    const result = await templateApi.saveDraft(selectedDevice);
    setMessage("Inherited configuration draft saved: " + result.draft_id + ". Hardware writes LOCKED.");
  });
  if (getSession()?.role !== "admin") return <Card>Administrator role required.</Card>;
  return <div className="space-y-4">
    <div><h1 className="text-xl font-semibold">Configuration inheritance — Phase 3</h1>
      <p className="text-sm text-slate-400">Define a base template, add site differences, then add per-device overrides. Review the effective configuration before drafting. No device writes.</p>
    </div>
    <ErrorNote error={error ?? templates.error ?? devices.error ?? sites.error}/>
    {message && <p role="status" className="rounded border border-emerald-800 p-2 text-sm text-emerald-200">{message}</p>}
    <Card title="Step 1 — Create or revise a configuration template">
      <form className="space-y-3" onSubmit={save}>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">Template name<input required className={input} value={form.name}
            disabled={!!editing} onChange={e => patch({name:e.target.value})} placeholder="CX 6200 24-port base" /></label>
          <label className="text-sm">Device family<select className={input} value={form.family} disabled={!!editing}
            onChange={e => patch({family:e.target.value as TemplateFamily})}>
            <option value="switch">Aruba CX switch</option><option value="access_point">Aruba Instant AP</option>
          </select></label>
          <label className="text-sm">Apply template at<select className={input} value={form.scope} disabled={!!editing}
            onChange={e => patch({scope:e.target.value as TemplateScope,site_id:"",device_id:""})}>
            <option value="global">Global device base</option><option value="site">Physical site</option>
            <option value="device">Specific device</option>
          </select></label>
          <label className="text-sm">Model contains (optional)<input className={input} value={form.model_contains}
            disabled={!!editing} placeholder="e.g. 6200F" onChange={e => patch({model_contains:e.target.value})}/></label>
          {form.scope === "site" && <label className="text-sm">Physical site<select className={input} disabled={!!editing}
            required value={form.site_id} onChange={e => patch({site_id:e.target.value})}>
            <option value="">Select physical site</option>{realSites.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}
          </select></label>}
          {form.scope === "device" && <label className="text-sm">Device override target<select className={input} disabled={!!editing}
            value={form.device_id} required onChange={e => patch({device_id:e.target.value})}>
            <option value="">Select a device</option>{realDevices.filter(d => d.family === form.family).map(d =>
              <option value={d.id} key={d.id}>{d.hostname} ({d.serial_number})</option>)}
          </select></label>}
          <label className="text-sm">Priority (higher wins within layer)<input type="number" min={0} max={1000}
            className={input} value={form.priority} onChange={e => patch({priority:Number(e.target.value)})}/></label>
          <label className="flex items-center gap-2 pt-6 text-sm">
            <input type="checkbox" checked={form.enabled} onChange={e => patch({enabled:e.target.checked})}/>Enabled
          </label>
        </div>
        <div className="border-t border-slate-700 pt-3">
          <h2 className="mb-2 font-semibold">Step 2 — Set standard configuration</h2>
          <label className="block text-sm">Mandatory and additional VLAN IDs (comma-separated)
            <input className={input} value={form.vlans} onChange={e => patch({vlans:e.target.value})}/>
          </label>
          <p className="mt-1 text-xs text-slate-400">Global VLANs cannot be removed by a site or device override. SSID and port rows override matching names.</p>
          {form.family === "access_point" ? <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <label className="text-sm">Optional SSID/profile<input className={input} value={form.ssid} onChange={e => patch({ssid:e.target.value})}/></label>
            <label className="text-sm">SSID VLAN<input className={input} value={form.ssidVlan} onChange={e => patch({ssidVlan:e.target.value})}/></label>
            <label className="text-sm">Channel width<select className={input} value={form.width} onChange={e => patch({width:e.target.value as "20" | "40"})}>
              <option value="20">20 MHz</option><option value="40">40 MHz</option>
            </select></label>
          </div> : <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <label className="text-sm">Optional switch interface<input className={input} placeholder="1/1/1" value={form.port} onChange={e => patch({port:e.target.value})}/></label>
            <label className="text-sm">Access VLAN<input className={input} value={form.portVlan} onChange={e => patch({portVlan:e.target.value})}/></label>
            <label className="text-sm">Trunk VLAN IDs<input className={input} value={form.trunkVlans} onChange={e => patch({trunkVlans:e.target.value})}/></label>
          </div>}
        </div>
        <button className={primary} disabled={busy}>{editing ? "Save template revision" : "Save configuration template"}</button>
        {editing && <button type="button" className={"ml-2 "+secondary} onClick={() => {setEditing(null);setForm(empty);}}>Cancel edit</button>}
      </form>
    </Card>
    <Card title="Current inheritance templates">
      {!templates.data?.length && <p className="text-sm text-slate-400">No templates. Create the global base first.</p>}
      {templates.data?.map(t => <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-700 py-2 text-sm" key={t.id}>
        <span><b>{t.name}</b> · {t.scope} · {t.family} · priority {t.priority} · revision {t.revision}
          {!t.enabled && <span className="ml-2 text-amber-300">Disabled</span>}</span>
        <button type="button" className={secondary} onClick={() => {setEditing(t);setForm(toForm(t));}}>Edit settings</button>
      </div>)}
    </Card>
    <Card title="Step 3 — Preview inherited settings for an onboarded device">
      <label className="block text-sm">Registered real Aruba device<select className={input} value={selectedDevice}
        onChange={e => {setSelectedDevice(e.target.value);setPreview(null);}}>
        <option value="">Choose device</option>{realDevices.map(d => <option value={d.id} key={d.id}>{d.hostname} — {d.model}</option>)}
      </select></label>
      <button type="button" disabled={busy || !selectedDevice} className={"mt-3 "+primary} onClick={inspect}>Preview effective configuration</button>
      {preview && <div className="mt-3 space-y-3 text-sm">
        <p className="text-amber-300">Preview only — applied to hardware: NO · Device writes LOCKED</p>
        <p>Inherited templates: {preview.templates.map(t => t.scope + ": " + t.name).join(" → ") || "None"}</p>
        <p>Effective VLANs: <b>{preview.effective.vlans.join(", ") || "None"}</b></p>
        <p>SSID profiles: {preview.effective.ssids?.map(s=>s.essid).join(", ") || "None"}</p>
        <p>Switch interfaces: {preview.effective.interfaces?.map(p=>p.name).join(", ") || "None"}</p>
        <details><summary className="cursor-pointer">Show setting provenance</summary>
          <pre className="mt-2 overflow-auto rounded bg-slate-950 p-3 text-xs">{JSON.stringify(preview.provenance,null,2)}</pre>
        </details>
        <div className="flex flex-wrap gap-3">
          <button type="button" className={primary} disabled={busy || !preview.templates.length} onClick={draft}>Save inherited draft (no device writes)</button>
          <Link className="text-sky-400 underline" to={"/devices/"+selectedDevice+"/config"}>Open device configuration</Link>
        </div>
        <p className="text-xs text-slate-400">A real-device baseline must be captured first. Saving never applies settings to a switch or AP.</p>
      </div>}
    </Card>
  </div>;
}
