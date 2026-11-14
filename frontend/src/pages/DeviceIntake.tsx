import { PageHeader } from "../components/viz";
import { FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { v1, type Device } from "../services/inm";
import { getSession } from "../services/api";
import BulkDeviceImport from "../components/BulkDeviceImport";

type Family = "access_point" | "switch";
type Method = "manual" | "probe";
const rowClass = "mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm";
const metricsByFamily: Record<Family, { key: string; label: string; placeholder: string }[]> = {
  access_point: [
    { key: "clients", label: "Connected clients", placeholder: "e.g. 18" },
    { key: "util_5g_percent", label: "5 GHz utilization (%)", placeholder: "e.g. 35" },
    { key: "util_2g_percent", label: "2.4 GHz utilization (%)", placeholder: "e.g. 20" },
    { key: "cpu_percent", label: "CPU (%)", placeholder: "e.g. 26" },
    { key: "memory_percent", label: "Memory (%)", placeholder: "e.g. 54" },
  ],
  switch: [
    { key: "ports_total", label: "Total interfaces", placeholder: "e.g. 24" },
    { key: "ports_up", label: "Interfaces up", placeholder: "e.g. 18" },
    { key: "poe_power_w", label: "PoE used (W)", placeholder: "e.g. 86" },
    { key: "cpu_percent", label: "CPU (%)", placeholder: "e.g. 14" },
    { key: "memory_percent", label: "Memory (%)", placeholder: "e.g. 39" },
  ],
};
const empty = { site_name: "", hostname: "", serial_number: "", model: "", firmware: "",
                management_ip: "", username: "", password: "" };

export default function DeviceIntake() {
  const [family, setFamily] = useState<Family>("access_point");
  const [method, setMethod] = useState<Method>("manual");
  const [form, setForm] = useState(empty);
  const [numbers, setNumbers] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"up" | "down" | "unknown">("unknown");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const canAdd = getSession()?.role === "admin";
  const devices = useLoad(() => v1.devices(), [], 30000, ["devices"]);
  const physicalSites = useLoad(() => v1.physicalSites(), [], 30000, ["sites"]);
  const real = useMemo(() => (devices.data ?? []).filter((d) => d.source_type === "REAL"), [devices.data]);

  const editDevice = async (device: Device) => {
    // A newly imported physical site may not yet be in the cached /v1/sites list.
    // Fetch it before filling the required location rather than blanking the site
    // or accidentally assigning a different location on the next Save.
    let siteName = physicalSites.data?.sites.find(site => site.id === device.site_id)?.name;
    if (!siteName && device.site_id) {
      try {
        const fresh = await v1.physicalSites();
        siteName = fresh.sites.find(site => site.id === device.site_id)?.name;
        physicalSites.reload();
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Unable to fetch existing physical site");
        return;
      }
    }
    if (!siteName) {
      setError("This device has no verified physical site. Set its location before editing.");
      return;
    }
    setFamily(device.family);
    setMethod("manual");
    setForm({
      site_name: siteName,
      hostname: device.hostname ?? "",
      serial_number: device.serial_number,
      model: device.model ?? "",
      firmware: device.firmware ?? "",
      management_ip: device.management_addresses?.[0] ?? "",
      username: "", password: "",
    });
    setStatus(["up", "down"].includes(device.status) ? device.status as "up" | "down" : "unknown");
    setNumbers({});
    setError(null);
    setSuccess("Editing existing real device. Saving will update its inventory details and mark this observation as MANUAL.");
    document.getElementById("single-device-form")?.scrollIntoView({ behavior: "smooth" });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null); setSuccess(null);
    const basic = {
      family, site_name: form.site_name.trim(), hostname: form.hostname.trim(),
      serial_number: form.serial_number.trim(), model: form.model.trim(),
      management_ip: form.management_ip.trim() || null,
    };
    try {
      const result = method === "manual"
        ? await v1.addQuickDevice({
            ...basic, firmware: form.firmware.trim() || null, status,
            metrics: Object.fromEntries(Object.entries(numbers)
              .filter(([, v]) => v.trim() !== "")
              .map(([k, v]) => [k, Number(v)]))
          })
        : await v1.probeQuickDevice({
            ...basic, management_ip: form.management_ip.trim(),
            username: form.username.trim(), password: form.password,
          });
      setSuccess(result.evidence === "PROBED"
        ? `Device read successfully using the Aruba REST API: ${result.hostname}. No configuration was modified.`
        : `Real device snapshot saved: ${result.hostname}. Values are manually entered, not live polling.`);
      setForm((f) => ({ ...f, password: "" }));
      devices.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to add device");
    } finally {
      setBusy(false);
    }
  };

  const field = (key: keyof typeof empty, label: string, required = false, type = "text", hint = "") => (
    <label className="text-sm" key={key}>{label}
      <input className={rowClass} type={type} required={required} autoComplete={type === "password" ? "new-password" : "off"}
        value={form[key]} placeholder={hint}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
    </label>
  );
  return (
    <div className="space-y-4">
      <div>
        <PageHeader eyebrow="Sites & inventory" title="Manual / CSV intake" subtitle="Register devices without changing hardware. Use guided onboarding for discovery, credentials and baseline capture." />
        <p className="mt-1 text-sm text-slate-400">Add an Aruba access point or Aruba CX switch to your real inventory. Demo fixtures stay separate. No device configuration is changed.</p>
      </div>
      {!canAdd && <Card>Administrator access is required to register devices.</Card>}
      {canAdd && <Card title="1. Select the device">
        <div className="flex flex-wrap gap-2">
          {([["access_point", "Access point (AP-505 / AP-535)"], ["switch", "Aruba CX switch (6100 / 6200)"]] as const).map(([id, label]) => (
            <button key={id} type="button"
              className={`rounded-lg border px-3 py-2 text-sm ${family === id ? "border-sky-400 bg-sky-900/40 text-sky-200" : "border-slate-700 text-slate-300"}`}
              onClick={() => { setFamily(id); setNumbers({}); setError(null); }}>{label}</button>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {([["manual", "Enter device details & readings"], ["probe", "Read directly from device (REST API)"]] as const).map(([id, label]) => (
            <button key={id} type="button"
              className={`rounded border px-3 py-2 text-sm ${method === id ? "border-emerald-500 bg-emerald-900/30 text-emerald-200" : "border-slate-700"}`}
              onClick={() => { setMethod(id); setError(null); }}>{label}</button>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-400">
          {method === "manual" ? "Works immediately without device connectivity. You can copy facts from show commands or device labels. Metrics are labelled MANUAL."
            : "Requires an approved management IP range, network reachability and valid HTTPS credentials. API requests are read-only and never change a device. If TLS or REST is not configured, use manual entry first."}
        </p>
      </Card>}
      {canAdd && <Card title="2. Device identity and location">
        <form id="single-device-form" onSubmit={submit} className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {field("site_name", "Site / physical location", true, "text", "e.g. Head Office")}
            {field("hostname", "Device name", true, "text", "e.g. HQ-AP-01")}
            {field("serial_number", "Device serial number", true)}
            {field("model", "Model", true, "text", family === "access_point" ? "AP-535" : "CX 6200F")}
            {method === "manual" && field("firmware", "Firmware / OS version")}
            {field("management_ip", "Management IPv4 address" + (method === "probe" ? " (required)" : " (optional)"), method === "probe", "text", "e.g. 10.101.12.20")}
          </div>
          {method === "probe"
            ? <div className="grid gap-3 md:grid-cols-2">
                {field("username", "Device REST API username", true)}
                {field("password", "Device REST API password (not saved)", true, "password")}
              </div>
            : <>
                <label className="block text-sm">Operational status
                  <select className={rowClass} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                    <option value="up">Up / online (operator-reported)</option>
                    <option value="down">Down</option>
                    <option value="unknown">Not yet verified</option>
                  </select>
                </label>
                <h3 className="text-sm font-semibold">3. Enter observed readings (optional)</h3>
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                  {metricsByFamily[family].map((m) => (
                    <label key={m.key} className="text-sm">{m.label}
                      <input className={rowClass} type="number" min="0" step={m.key.endsWith("percent") ? "0.1" : "1"}
                        placeholder={m.placeholder} value={numbers[m.key] ?? ""}
                        onChange={(e) => setNumbers((v) => ({ ...v, [m.key]: e.target.value }))} />
                    </label>
                  ))}
                </div>
              </>}
          <ErrorNote error={error} />
          {success && <p role="status" className="rounded border border-emerald-700 bg-emerald-950/50 p-3 text-sm text-emerald-200">{success}</p>}
          <button disabled={busy} className="rounded bg-sky-600 px-5 py-2 font-medium hover:bg-sky-500 disabled:opacity-50">
            {busy ? "Processing…" : method === "manual" ? "Save real device and readings" : "Read device and add to inventory"}
          </button>
        </form>
      </Card>}
      {canAdd && <Card title="Import multiple access points or switches from CSV">
        <BulkDeviceImport onImported={() => { devices.reload(); physicalSites.reload(); }} />
      </Card>}
      <Card title="Existing SIMS Wi-Fi Manager features">
        <p className="text-sm text-slate-300">
          The original SIMS Instant-cluster site management, AP/client telemetry,
          experience scoring, configuration snapshots and alerts are preserved.
          Live controller registration requires a separate restricted live-mode configuration;
          adding a REAL device here does not silently create an Instant cluster or enable polling.
        </p>
        <Link className="mt-3 inline-block text-sm text-sky-400 underline" to="/sites">Open Aruba Instant cluster sites</Link>
      </Card>
      <Card title="Your real devices">
        <ErrorNote error={devices.error} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[750px] text-left text-sm">
            <thead className="text-xs uppercase text-slate-400">
              <tr>{["Name", "Type", "Model", "Serial", "Status", "Data source", "Observed", ""].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {real.map((d: Device) => (
                <tr key={d.id}>
                  <td className="px-2 py-2 font-medium">{d.hostname}</td>
                  <td className="px-2 py-2">{d.family === "access_point" ? "AP" : "Switch"}</td>
                  <td className="px-2 py-2">{d.model}</td>
                  <td className="px-2 py-2 font-mono text-xs">{d.serial_number}</td>
                  <td className="px-2 py-2"><Badge value={d.status} /></td>
                  <td className="px-2 py-2 text-xs text-emerald-300">{d.adapter === "manual_snapshot" ? "REAL · MANUAL" : "REAL · REST READ"}</td>
                  <td className="px-2 py-2 text-xs">{fmtTime(d.last_observed_at)}</td>
                  <td className="px-2 py-2 text-sky-400">
                    <div className="flex flex-wrap gap-2">
                      <Link to={`/devices/${d.id}`}>View details</Link>
                      {canAdd && <button type="button" onClick={() => editDevice(d)}
                        className="text-slate-300 underline hover:text-sky-300">Edit details</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {real.length === 0 && <p className="p-4 text-sm text-slate-500">No real devices yet. Use the form above to add the first one.</p>}
        </div>
      </Card>
    </div>
  );
}
