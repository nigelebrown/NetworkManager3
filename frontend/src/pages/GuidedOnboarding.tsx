import { FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { onboardingApi, type CredentialProfile, type DiscoveryJob, type Registered } from "../services/onboarding";

type Family = "access_point" | "switch";
const steps = [
  "Choose site & device", "Credential profile", "Target address or range",
  "Test & scan", "Review devices", "Import configuration", "Manage",
];
const input = "mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm";
const primary = "rounded bg-sky-600 px-4 py-2 text-sm font-semibold disabled:opacity-40 hover:bg-sky-500";
const secondary = "rounded border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800 disabled:opacity-40";

export default function GuidedOnboarding() {
  const allowed = getSession()?.role === "admin";
  const profiles = useLoad(onboardingApi.credentials, [], 30000, ["onboarding-credentials"]);
  const [step, setStep] = useState(0);
  const [family, setFamily] = useState<Family>("access_point");
  const [site, setSite] = useState("");
  const [selection, setSelection] = useState<"existing" | "new">("existing");
  const [profileId, setProfileId] = useState("");
  const [profileForm, setProfileForm] = useState({ name: "", username: "", password: "", description: "" });
  const [rotateSecret, setRotateSecret] = useState("");
  const [scope, setScope] = useState("");
  const [testIp, setTestIp] = useState("");
  const [testResult, setTestResult] = useState("");
  const [discovery, setDiscovery] = useState<DiscoveryJob | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const [registered, setRegistered] = useState<Registered | null>(null);
  const [captures, setCaptures] = useState<Record<string, string>>({});
  const [resumeId, setResumeId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const options = useMemo(() =>
    (profiles.data ?? []).filter(p => p.enabled && p.device_family === family),
    [profiles.data, family]
  );
  const picked = options.find(p => p.id === profileId);
  const hasSite = site.trim().length > 0;

  const work = async (run: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null);
    try { await run(); } catch (exc) { setError(exc instanceof Error ? exc.message : "Operation failed"); }
    finally { setBusy(false); }
  };

  const makeProfile = (e: FormEvent) => {
    e.preventDefault();
    work(async () => {
      const p = await onboardingApi.createCredential({ ...profileForm, device_family: family });
      setProfileId(p.id);
      setProfileForm({ name: "", username: "", password: "", description: "" });
      setSelection("existing");
      profiles.reload();
      setNotice(`Encrypted credential profile "${p.name}" created. Select it again for additional devices.`);
    });
  };
  const rotate = () => work(async () => {
    if (!profileId || !rotateSecret) return;
    await onboardingApi.rotateCredential(profileId, { password: rotateSecret });
    setRotateSecret("");
    profiles.reload();
    setNotice("Password rotated. All devices bound to this profile will use the new secret on future reads.");
  });
  const test = () => work(async () => {
    if (!profileId || !testIp) throw new Error("Choose a profile and enter an approved device IP.");
    const result = await onboardingApi.testCredential(profileId, testIp);
    setTestResult(`Authenticated: ${result.hostname || testIp} (${result.model || "model not reported"})`);
    setNotice("Read-only REST authentication succeeded; no device configuration was modified.");
  });
  const scan = (targetScope: string = scope) => work(async () => {
    if (!profileId) throw new Error("Select a credential profile before scanning.");
    const result = await onboardingApi.discover({ credential_id: profileId, scope: targetScope, site_name: site });
    setScope(targetScope);
    setDiscovery(result);
    setChosen(result.results.map((item, index) => item.enrollable ? index : -1).filter(x => x >= 0));
    setStep(4);
  });
  const register = () => work(async () => {
    if (!discovery) throw new Error("Run a read-only discovery first.");
    const result = await onboardingApi.register(discovery.id, chosen);
    setRegistered(result);
    setStep(5);
  });
  const captureAll = () => work(async () => {
    if (!registered) return;
    const results: Record<string, string> = {};
    for (const d of registered.registered) {
      try {
        const snap = await onboardingApi.capture(d.device_id, profileId);
        results[d.device_id] = `${snap.completeness}: ${snap.warning || "Read-only configuration captured"}`;
      } catch (exc) {
        results[d.device_id] = "Not captured: " + (exc instanceof Error ? exc.message : "Unreachable");
      }
      setCaptures({ ...results });
    }
    setStep(6);
    setNotice("Configuration read attempts are finished. Review each device and save any proposed edits as drafts.");
  });
  const resume = () => work(async () => {
    const job = await onboardingApi.discovery(resumeId.trim());
    setDiscovery(job);
    setSite(job.site_name);
    setProfileId(job.credential_id);
    const p = (profiles.data ?? []).find(p => p.id === job.credential_id);
    if (p) setFamily(p.device_family);
    setChosen(job.results.map((x, i) => x.enrollable ? i : -1).filter(i => i >= 0));
    setStep(4);
    setNotice("Saved discovery restored. Recheck each device before registering.");
  });
  const clear = () => {
    setStep(0); setDiscovery(null); setChosen([]); setRegistered(null); setCaptures({});
    setError(null); setNotice(null); setTestResult(""); setProfileId(""); setScope(""); setTestIp("");
  };

  if (!allowed) return <Card>Only administrators can run equipment onboarding and access saved device credentials.</Card>;
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">Onboard network equipment</h1>
        <p className="mt-1 text-sm text-slate-400">Guided, read-only commissioning. Reuse saved credentials, discover, import baseline and manage drafts.</p>
      </div>
      <Link className={secondary} to="/onboarding/manual">Manual or CSV import</Link>
    </div>
    <div className="grid gap-1 sm:grid-cols-4 lg:grid-cols-7">
      {steps.map((label, index) =>
        <div key={label} className={`rounded border px-2 py-2 text-xs ${step === index
            ? "border-sky-400 bg-sky-950/50 text-sky-200"
            : index < step ? "border-emerald-900 text-emerald-300" : "border-slate-700 text-slate-500"}`}>
          <b>{index + 1}.</b> {label}
        </div>)}
    </div>
    <ErrorNote error={error ?? profiles.error} />
    {notice && <p role="status" className="rounded border border-emerald-800 p-3 text-sm text-emerald-200">{notice}</p>}
    {step === 0 && <Card title="1. Select the device family and its physical location">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">What are you onboarding?
          <select className={input} value={family} onChange={e => { setFamily(e.target.value as Family); setProfileId(""); }}>
            <option value="access_point">Aruba Instant access points (AP-505 / AP-535)</option>
            <option value="switch">Aruba CX switches (CX 6100 / CX 6200)</option>
          </select>
        </label>
        <label className="text-sm">Physical site / location
          <input required className={input} value={site} onChange={e => setSite(e.target.value)}
            placeholder="e.g. Commissioner’s Office" maxLength={100}/>
        </label>
      </div>
      <p className="mt-3 text-xs text-slate-400">Device family determines which REST API the credential profile uses. Physical site is separate from a legacy Instant Virtual Controller cluster.</p>
    </Card>}

    {step === 1 && <Card title="2. Select an existing profile or create one">
      <div className="flex flex-wrap gap-2">
        <button type="button" className={selection === "existing" ? primary : secondary} onClick={() => setSelection("existing")}>Select existing credentials</button>
        <button type="button" className={selection === "new" ? primary : secondary} onClick={() => setSelection("new")}>Create credential profile</button>
      </div>
      {selection === "existing" && <>
        <label className="mt-4 block text-sm">Saved {family === "switch" ? "Aruba CX" : "Aruba Instant"} credential profile
          <select className={input} value={profileId} onChange={e => setProfileId(e.target.value)}>
            <option value="">Select an existing profile</option>
            {options.map(p => <option value={p.id} key={p.id}>{p.name} ({p.username})</option>)}
          </select>
        </label>
        {options.length === 0 && <p className="mt-2 text-xs text-amber-300">No reusable profiles for this device type yet. Choose Create credential profile.</p>}
        {picked && <div className="mt-3 rounded border border-slate-700 p-3 text-sm">
          <p><b>{picked.name}</b> · {picked.protocol} · username {picked.username}</p>
          <p className="text-xs text-slate-400">Password: securely stored (not readable) · Last test: {picked.last_test_status || "not tested"}</p>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="text-xs">Rotate password for all devices using this profile
              <input className={input} type="password" autoComplete="new-password"
                value={rotateSecret} onChange={e => setRotateSecret(e.target.value)} />
            </label>
            <button className={secondary} type="button" onClick={rotate}
              disabled={busy || !rotateSecret}>Update stored password</button>
          </div>
        </div>}
      </>}
      {selection === "new" && <form className="mt-3 space-y-3" onSubmit={makeProfile}>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm">Profile name (descriptive and reusable)
            <input className={input} required minLength={2} maxLength={100}
              value={profileForm.name} placeholder="e.g. HQ Aruba Instant read-only"
              onChange={e => setProfileForm(p => ({ ...p, name: e.target.value }))} />
          </label>
          <label className="text-sm">Device API username
            <input className={input} required value={profileForm.username}
              onChange={e => setProfileForm(p => ({ ...p, username: e.target.value }))}/>
          </label>
          <label className="text-sm">Device password
            <input className={input} type="password" autoComplete="new-password"
              required value={profileForm.password}
              onChange={e => setProfileForm(p => ({ ...p, password: e.target.value }))}/>
          </label>
          <label className="text-sm">Description / ownership (optional)
            <input className={input} value={profileForm.description} maxLength={255}
              onChange={e => setProfileForm(p => ({ ...p, description: e.target.value }))}/>
          </label>
        </div>
        <button className={primary} disabled={busy}>Save encrypted credential profile</button>
      </form>}
      <p className="mt-3 text-xs text-amber-300">
        Secrets are encrypted at rest, not returned to the browser, and never included in discovery results. Only an administrator can create or rotate a profile.
      </p>
    </Card>}

    {step === 2 && <Card title="3. Enter approved management IP or discovery range">
      <label className="block text-sm">Individual IPv4 or CIDR
        <input className={input} required value={scope} onChange={e => {
          setScope(e.target.value); setTestResult("");
        }} placeholder="e.g. 10.103.10.25 or 10.103.10.24/29" />
      </label>
      <p className="mt-3 text-sm text-slate-300">
        Each active read-only scan covers up to 8 IP addresses. The server administrator must separately approve the target IPs using the private management allowlist. All targets are checked before the first connection.
      </p>
      <p className="mt-2 text-xs text-slate-400">
        Larger CIDRs, including /9, can be previewed in <Link className="text-sky-400 underline" to="/discovery">Discovery ranges</Link>, then broken into authorized small scan batches. Public ranges are not scanned.
      </p>
    </Card>}

    {step === 3 && <Card title="4. Test the shared credentials, then discover">
      <p className="mb-3 text-sm">Credential: <b>{picked?.name ?? profileId}</b> · Target: <code>{scope}</code></p>
      <label className="block text-sm">An approved, reachable device IP to test authentication (optional but recommended)
        <input className={input} value={testIp} onChange={e => setTestIp(e.target.value)}
          placeholder="e.g. 10.103.10.25"/>
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className={secondary} disabled={busy || !testIp} onClick={test}>Test credentials (read only)</button>
        <button className={primary} disabled={busy} onClick={() => scan()}>{busy ? "Discovering authorized IPs…" : "Scan approved range"}</button>
        {testIp.trim() && <button className={secondary} disabled={busy}
          onClick={() => scan(testIp.trim() + (testIp.includes("/") ? "" : "/32"))}>
          Discover test IP only
        </button>}
      </div>
      {testResult && <p className="mt-3 text-sm text-emerald-300">{testResult}</p>}
      <p className="mt-2 text-sm text-amber-300">
        Live discovery is limited to 8 addresses per scan. An approved /24 is a management allowlist, not a single scan request.
        For your first AP, use "Discover test IP only" to scan one address, then review it before registration.
      </p>
      <p className="mt-2 text-xs text-slate-400">
        Discovery reads supported device identity via REST. An unsuccessful address is reported individually. There is no SSH configuration, port sweep or device-write operation.
      </p>
    </Card>}

    {step === 4 && discovery && <Card title="5. Review detected devices and select what to register">
      <p className="text-xs text-slate-400">Discovery ID: <code>{discovery.id}</code> — keep this ID to resume later.</p>
      <div className="mt-3 max-h-80 overflow-x-auto">
        <table className="w-full min-w-[750px] text-left text-sm">
          <thead className="text-slate-400"><tr>{["Add", "Address", "Device", "Model", "Serial", "Already exists", "Result"].map(h => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
          <tbody>{discovery.results.map((d, i) => <tr key={i} className="border-t border-slate-800">
            <td className="px-2 py-2"><input aria-label={`Select device ${d.management_ip}`}
              type="checkbox" checked={chosen.includes(i)} disabled={!d.enrollable}
              onChange={e => setChosen(prev => e.target.checked ? [...prev, i] : prev.filter(x => x !== i))}/></td>
            <td className="px-2 py-2 font-mono">{d.management_ip}</td>
            <td className="px-2 py-2">{d.hostname || "—"}</td><td className="px-2 py-2">{d.model || "—"}</td>
            <td className="px-2 py-2">{d.serial_number || "Not verified"}</td>
            <td className="px-2 py-2">{d.existing_device_id ? "Yes — update" : "New"}</td>
            <td className="px-2 py-2">{d.status} · {d.result_note}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-amber-300">Only API-verified devices with a serial number are selectable. Failed or unidentified devices are not silently added.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className={primary} disabled={busy || chosen.length === 0} onClick={register}>
          Register {chosen.length} selected device(s)
        </button>
        <button className={secondary} disabled={busy} onClick={() => setStep(3)}>Try another scan</button>
      </div>
    </Card>}

    {step === 5 && registered && <Card title="6. Import existing configuration as a read-only baseline">
      <p className="mb-3 text-sm text-slate-300">
        Registered {registered.registered.length} device(s). Each device is now linked to the reusable credential profile. The following action retrieves available configuration from the actual equipment; it does not push changes.
      </p>
      {registered.registered.map(d => <div key={d.device_id} className="mb-2 rounded border border-slate-700 p-3 text-sm">
        <b>{d.hostname}</b> · {d.serial_number}
        <p className="text-xs text-slate-400">{captures[d.device_id] || "Existing configuration not yet imported"}</p>
      </div>)}
      <div className="flex flex-wrap gap-2">
        <button className={primary} disabled={busy} onClick={captureAll}>{busy ? "Reading configuration…" : "Read existing configurations"}</button>
        <button className={secondary} disabled={busy} onClick={() => setStep(6)}>Continue without capture</button>
      </div>
      <p className="mt-3 text-xs text-amber-300">Passwords, keys and complete restore images are not imported. Aruba Instant configuration is parsed/redacted. CX support is limited to REST-exposed VLAN and interface information.</p>
    </Card>}

    {step === 6 && <Card title="7. Inventory, configuration baselines and proposed edits">
      <p className="mb-3 text-sm text-slate-300">
        Review imported devices and open their configuration editor. All edits are saved as drafts and cannot change equipment.
      </p>
      {registered?.registered.map(d => <div key={d.device_id}
        className="flex flex-wrap justify-between gap-3 border-t border-slate-800 p-3 text-sm">
        <span><b>{d.hostname}</b> · {d.serial_number}</span>
        <Link className="text-sky-400 underline" to={`/devices/${d.device_id}/config`}>View and edit draft configuration</Link>
      </div>)}
      <div className="mt-3 flex flex-wrap gap-2">
        <Link className={primary} to="/aps">Access point inventory</Link>
        <Link className={secondary} to="/switches">Switch inventory</Link>
        <button className={secondary} onClick={clear}>Start another onboarding</button>
      </div>
    </Card>}
    {step < 4 && <div className="flex flex-wrap justify-between gap-2">
      {step > 0 && <button className={secondary} disabled={busy} onClick={() => setStep(x => Math.max(0, x - 1))}>Back</button>}
      {step < 3 && <button className={primary} disabled={busy || (step === 1 ? !profileId : step === 2 ? !scope.trim() : !hasSite)}
        onClick={() => setStep(x => x + 1)}>Next</button>}
    </div>}
    <Card title="Resume an earlier discovery">
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[240px] flex-1 text-sm">Discovery ID
          <input className={input} value={resumeId} onChange={e => setResumeId(e.target.value)}
            placeholder="Paste the discovery ID saved at step 5"/>
        </label>
        <button className={secondary} disabled={busy || !resumeId.trim()} onClick={resume}>Resume</button>
      </div>
    </Card>
  </div>;
}
