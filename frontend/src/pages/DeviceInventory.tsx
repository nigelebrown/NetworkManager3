import { useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useLoad } from "../components/useLoad";
import { ErrorNote } from "../components/ui";
import { ago, btn, inputCls } from "../components/viz";
import { ApiError, getSession, request } from "../services/api";
import { onboardingApi, type RestPreflight } from "../services/onboarding";
import DeviceRestBootstrap from "../components/DeviceRestBootstrap";
import { ops } from "../services/ops";
import { v1 } from "../services/inm";

/** Inventory view is backed exclusively by the real versioned inventory endpoints. */
export default function DeviceInventory() {
  const [params, setParams] = useSearchParams();
  const handoff = useLocation().state as {credentialDeviceIds?: string[]; family?: string} | null;
  const [family, setFamily] = useState(handoff?.family ?? "ALL");
  const [site, setSite] = useState("");
  const [needsAttention, setNeedsAttention] = useState(false);
  const query = params.get("q") ?? "";
  const devices = useLoad(v1.devices, [], 30000, ["devices"]);
  const sites = useLoad(v1.physicalSites, [], 60000, ["sites"]);
  const siteNames = useMemo(() => new Map((sites.data?.sites ?? []).map(s => [s.id, s.name])), [sites.data]);
  const clusterNames = useMemo(() => new Map((sites.data?.clusters ?? []).map(c => [c.id, c.name])), [sites.data]);
  const clusterLabel = (id: string | null) =>
    id ? clusterNames.get(id) ?? "Cluster name unavailable" : "No cluster linked";
  const rows = useMemo(() => (devices.data ?? []).filter(d =>
    (family === "ALL" || d.family === family) &&
    (!needsAttention || (d.source_type === "REAL" && !["ONLINE", "UP"].includes(
      (("state" in d ? String(d.state ?? d.status) : d.status)).toUpperCase()))) &&
    (!site || (d.site_id ?? "unassigned") === site) &&
    (!query || [d.hostname,d.model,d.serial_number,d.firmware,siteNames.get(d.site_id ?? ""),
      clusterNames.get(d.cluster_id ?? ""),...(d.management_addresses ?? [])]
      .some(v => (v ?? "").toLowerCase().includes(query.toLowerCase())))),
    [devices.data, family, query, site, siteNames, clusterNames, needsAttention]);
  const admin = getSession()?.role === "admin";
  const profiles = useLoad(() => admin ? onboardingApi.credentials() : Promise.resolve([]), [admin], 30000);
  const [selected, setSelected] = useState<string[]>(handoff?.credentialDeviceIds ?? []);
  const [result, setResult] = useState<{ profileId: string; profileName: string; devices: {
    id: string; name: string; address?: string;
  }[] } | null>(null);
  const [checks, setChecks] = useState<Record<string, {status: string; message: string;
    restState?: RestPreflight["state"]; clusterManaged?: boolean}>>({});
  const [onboardResults, setOnboardResults] = useState<Record<string, {status: string; message: string}>>({});
  const [onboardingBusy, setOnboardingBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{done: number; total: number} | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [restBootstrapDevice, setRestBootstrapDevice] = useState<string | null>(null);
  const [reviewAccess, setReviewAccess] = useState(false);
  const [profile, setProfile] = useState("");
  const [savedProfile, setSavedProfile] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{error?: string; message?: string}>({});
  const accessRows = (devices.data ?? []).filter(d => selected.includes(d.id)).map(d => {
    const address = d.management_addresses?.[0] ?? "";
    const parts = address.split(".");
    const [first, second] = parts.map(Number);
    const privateIp = parts.length === 4 && parts.every(p => /^(0|[1-9]\d{0,2})$/.test(p) && Number(p) <= 255)
      && (first === 10 || (first === 172 && second >= 16 && second <= 31) || (first === 192 && second === 168));
    const duplicate = address && (devices.data ?? []).some(other => other.id !== d.id &&
      other.source_type === "REAL" && other.management_addresses?.includes(address));
    return {device: d, address, reason: !address ? "Missing management IP" : !privateIp ? "Private IPv4 required"
      : duplicate ? "Repeated IP in inventory" : ""};
  });
  const eligibleAddresses = accessRows.filter(row => !row.reason).map(row => row.address);
  const selectedFamily = devices.data?.find(d => d.id === selected[0])?.family;
  const eligible = rows.filter(d => d.source_type === "REAL" && d.family === (selectedFamily ?? family));
  const allVisibleSelected = eligible.length > 0 && eligible.every(d => selected.includes(d.id));
  const toggleVisible = () => changeSelection(allVisibleSelected
    ? selected.filter(id => !eligible.some(d => d.id === id))
    : [...new Set([...selected, ...eligible.map(d => d.id)])].slice(0, 500));
  const matchingProfiles = profiles.data?.filter(p => p.enabled && p.device_family === selectedFamily) ?? [];
  const changeSelection = (ids: string[]) => {
    setSelected(ids);
    setReviewAccess(false);
    if (!ids.length || !selected.length) setProfile("");
    setFeedback({});
  };
  const [testingBatch, setTestingBatch] = useState(false);
  const successfulDevices = (result?.devices ?? []).filter(d => checks[d.id]?.restState === "ENABLED");
  const checkCounts = {
    successful: successfulDevices.length,
    failed: (result?.devices ?? []).filter(d => checks[d.id] && checks[d.id].restState !== "ENABLED").length,
    notTested: (result?.devices ?? []).filter(d => !checks[d.id]).length,
  };
  // Explicit administrator confirmation is required for each /32 before
  // the host agent opens worker egress. The API never modifies host firewalls.
  const testConnection = async (
    profileId: string,
    d: {id: string; address?: string},
  ) => {
    if (!d.address) return;
    setTesting(d.id);
    const labels: Record<string, string> = {
      ENABLED: "REST login verified", DISABLED: "REST unavailable or disabled",
      AUTH_REJECTED: "Credentials rejected", UNSUPPORTED_API: "Unsupported REST API",
      UNREACHABLE: "Unreachable",
    };
    const check = async () => {
      const response = await onboardingApi.checkRestApi(profileId, d.address!, d.id);
      setChecks(previous => ({...previous, [d.id]: {
        status: response.cluster_managed && response.state === "ENABLED"
          ? "Managed via cluster" : labels[response.state] ?? "Checked", message: response.detail,
        restState: response.state,
        clusterManaged: response.cluster_managed,
      }}));
    };
    try {
      await check();
    } catch (error) {
      setChecks(previous => ({...previous, [d.id]: {
        status: error instanceof ApiError && error.status === 403 ? "Blocked" : "Check failed",
        message: error instanceof Error ? error.message : "Connection test failed",
      }}));
    } finally { setTesting(null); }
  };
  const testAllConnections = async (batch = result) => {
    if (!batch || testingBatch || onboardingBusy) return;
    const targets = batch.devices.filter(d => d.address);
    if (!targets.length) return;
    setTestingBatch(true);
    setBatchProgress({done: 0, total: batch.devices.length});
    setOnboardResults({});
    try {
      for (const [index, device] of batch.devices.entries()) {
        if (device.address) {
          await testConnection(batch.profileId, device);
        } else {
          setChecks(previous => ({...previous, [device.id]: {
            status: "Missing IP", message: "Device has no management address; skipped",
          }}));
        }
        setBatchProgress({done: index + 1, total: batch.devices.length});
      }
    } finally {
      setTestingBatch(false);
      setBatchProgress(null);
    }
  };

  const testReviewedAddresses = async () => {
    const chosen = matchingProfiles.find(p => p.id === profile);
    const targets = accessRows.filter(row => !row.reason).map(({device, address}) => ({
      id: device.id, name: device.hostname || device.serial_number, address,
    }));
    if (!chosen || !targets.length || busy || testing || testingBatch || onboardingBusy) return;
    if (!window.confirm(
      `Assign "${chosen.name}" to ${targets.length} eligible devices, and test REST at the listed management addresses?`
    )) return;
    setBusy(true);
    setFeedback({});
    try {
      await request("/v1/onboarding/credentials/assign", {
        method: "POST", body: JSON.stringify({credential_id: chosen.id, device_ids: targets.map(d => d.id)}),
      });
      const batch = {profileId: chosen.id, profileName: chosen.name, devices: targets};
      setResult(batch);
      setChecks({});
      setRestBootstrapDevice(null);
      changeSelection([]);
      await testAllConnections(batch);
    } catch (error) {
      setFeedback({error: error instanceof Error ? error.message : "Could not start management connection tests"});
    } finally {
      setBusy(false);
    }
  };

  const onboardSuccessful = async () => {
    if (!result || !successfulDevices.length || testingBatch || onboardingBusy) return;
    if (!window.confirm(
      "Onboard " + successfulDevices.length + " REST-verified registered device(s)?\n\n" +
      "For each device: reverify login, import a read-only configuration baseline, " +
      "and schedule read-only monitoring every 5 minutes if supported. " +
      "Devices already monitored will not get duplicate plans. " +
      "Unreachable, rejected and untested devices are excluded.\n\n" +
      "No Aruba configuration changes will be made."
    )) return;
    setOnboardingBusy(true);
    setBatchProgress({done: 0, total: successfulDevices.length});
    let complete = 0;
    let needsAttention = 0;
    let index = 0;
    try {
      const plans = await ops.plans();
      for (const device of successfulDevices) {
        try {
          if (!device.address) throw new Error("Missing management IP");
          const live = await onboardingApi.checkRestApi(result.profileId, device.address, device.id);
          if (live.state !== "ENABLED") {
            setChecks(previous => ({...previous, [device.id]: {
              status: "REST not verified", message: live.detail, restState: live.state,
            }}));
            throw new Error("REST login no longer verified (" + live.state + "); do not onboard");
          }
          if (live.cluster_managed) {
            if (!live.cluster_monitoring_active) {
              throw new Error("Cluster REST verified. Check the cluster collector before marking this AP onboarded.");
            }
            setOnboardResults(previous => ({...previous, [device.id]: {
              status: "Onboarded", message: live.detail + " Existing cluster monitoring is active.",
            }}));
            complete++;
            continue;
          }
          setOnboardResults(previous => ({...previous, [device.id]: {
            status: "Importing baseline", message: "Reading device configuration without changes",
          }}));
          const config = await onboardingApi.deviceConfig(device.id);
          if (!config.baseline) await onboardingApi.capture(device.id, result.profileId);
          const existing = plans.find(p => p.device_id === device.id);
          if (existing) {
            if (existing.state !== "ACTIVE") {
              throw new Error("Baseline present, but the existing monitoring plan is paused; review it under Live monitoring");
            }
            setOnboardResults(previous => ({...previous, [device.id]: {
              status: "Onboarded", message: "Baseline present; existing active monitoring plan retained",
            }}));
            complete++;
          } else {
            const inventoryDevice = devices.data?.find(d => d.id === device.id);
            const metrics = inventoryDevice?.family === "switch"
              ? ["system", "interfaces", "poe"] : ["system", "radios", "clients"];
            const plan = {device_id: device.id, metric_families: metrics, interval_seconds: 300};
            const preview = await ops.previewPlan(plan);
            if (preview.warnings.length) {
              throw new Error("Baseline captured; monitoring not scheduled: " + preview.warnings.join("; "));
            }
            const created = await ops.createPlan(plan);
            plans.push(created);
            setOnboardResults(previous => ({...previous, [device.id]: {
              status: "Onboarded", message: "Configuration baseline captured; read-only monitoring scheduled every 5 minutes",
            }}));
            complete++;
          }
        } catch (error) {
          needsAttention++;
          setOnboardResults(previous => ({...previous, [device.id]: {
            status: "Needs attention", message: error instanceof Error ? error.message : "Onboarding failed",
          }}));
        }
        index++;
        setBatchProgress({done: index, total: successfulDevices.length});
      }
      setFeedback({message: "Onboarding complete: " + complete + " monitored; " +
        needsAttention + " need attention. Failed and unverified devices remain in inventory for retry."});
      devices.reload();
    } catch (error) {
      setFeedback({error: error instanceof Error ? error.message : "Unable to load monitoring plans"});
    } finally {
      setOnboardingBusy(false);
      setBatchProgress(null);
    }
  };

  const update = (value: string) => { const p = new URLSearchParams(params);
    changeSelection([]);
    if (value) p.set("q",value); else p.delete("q"); setParams(p,{replace:true}); };
  return <div className="space-y-4">
    <div className="inm-titlebar"><div><div className="inm-section-label">Sites & inventory</div>
      <h1 className="inm-pagetitle">Device inventory</h1>
      <p className="inm-subtitle">Manage registered access points and CX switches, inspect status, view existing configuration and open Device 360°.</p></div>
      {getSession()?.role === "admin" && <Link to="/onboarding" className="inm-header-action">＋ Add & configure devices</Link>}
    </div>
    <ErrorNote error={feedback.error ?? devices.error ?? profiles.error}/>
    {feedback.message && <p role="status">{feedback.message}</p>}
    <div className="inm-kpis" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:12}}>
      {[
        ["All devices",devices.data?.length ?? "—"],
        ["Access points",devices.data?.filter(d=>d.family==="access_point").length ?? "—"],
        ["CX switches",devices.data?.filter(d=>d.family==="switch").length ?? "—"],
        ["Live records",devices.data?.filter(d=>d.source_type==="REAL").length ?? "—"],
        ["Demo records",devices.data?.filter(d=>d.source_type==="DEMO").length ?? "—"],
      ].map(([label,value])=><div className="inm-kpi" key={String(label)}>
        <div style={{color:"#63748b",fontSize:11}}>{label}</div><div className="inm-kpi-value">{value}</div></div>)}
    </div>
    <section className="inm-card"><div className="inm-cardhead"><h2>Registered equipment ({rows.length})</h2>
      <div className="inm-actionbar"><input aria-label="Search inventory" disabled={busy} value={query} onChange={e=>update(e.target.value)}
        placeholder="Hostname, cluster, serial, model or IP" style={{border:"1px solid #d3e0ec",borderRadius:7,padding:"8px 10px"}}/>
        <select aria-label="Device family" disabled={busy} value={family} onChange={e=>{setFamily(e.target.value); changeSelection([]);}}
          style={{border:"1px solid #d3e0ec",borderRadius:7,padding:"8px 10px"}}>
          <option value="ALL">All equipment</option><option value="access_point">Access points</option><option value="switch">CX switches</option></select>
        <select className={inputCls} aria-label="Physical site" disabled={busy} value={site}
          onChange={e => {setSite(e.target.value); changeSelection([]);}}>
          <option value="">All sites</option><option value="unassigned">Unassigned</option>
          {[...(sites.data?.sites ?? [])].sort((a,b) => a.name.localeCompare(b.name)).map(s =>
            <option key={s.id} value={s.id}>{s.name}{s.source_type === "DEMO" ? " (Demo)" : ""}</option>)}
        </select>
        <label><input type="checkbox" checked={needsAttention} disabled={busy || (!!testing || testingBatch)}
          onChange={e => {setNeedsAttention(e.target.checked); changeSelection([]);}} /> Needs attention</label></div>
    </div>
      {admin && <form className="inm-cardbody flex flex-wrap items-end gap-3" onSubmit={async e => {
        e.preventDefault();
        if (!selected.length || !matchingProfiles.some(p => p.id === profile)) return;
        const chosen = matchingProfiles.find(p => p.id === profile)!;
        const targets = (devices.data ?? []).filter(d => selected.includes(d.id)).map(d => ({
          id: d.id, name: d.hostname || d.serial_number, address: d.management_addresses?.[0],
        }));
        setBusy(true); setFeedback({});
        try {
          const result = await request<{assigned: number}>("/v1/onboarding/credentials/assign", {
            method: "POST", body: JSON.stringify({credential_id: profile, device_ids: selected}),
          });
          setResult({profileId: chosen.id, profileName: chosen.name, devices: targets});
          setChecks({});
          setOnboardResults({});
          setRestBootstrapDevice(null);
          changeSelection([]);
          setFeedback({message: `Saved profile assigned to ${result.assigned} devices.`});
        } catch (e) {
          setFeedback({error: e instanceof Error ? e.message : "Could not assign the profile"});
        } finally { setBusy(false); }
      }}>
        <p>{selected.length} selected</p>
        <button type="button" className={btn} disabled={busy || !selected.length}
          onClick={() => setReviewAccess(!reviewAccess)}>Review management access</button>
        <button type="button" className={btn} disabled={busy || !eligible.length}
          onClick={toggleVisible}>{allVisibleSelected ? "Deselect visible" : "Select visible"}</button>
        <button type="button" className={btn} disabled={busy || !selected.length}
          onClick={() => changeSelection([])}>Clear selection</button>
        <label>Saved credential profile<select className={inputCls} value={profile} disabled={busy || !selected.length}
          onChange={e => setProfile(e.target.value)}>
          <option value="">Choose a profile</option>{matchingProfiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select></label>
        <button className={btn} disabled={busy || (!!testing || testingBatch) || onboardingBusy || !selected.length || !matchingProfiles.some(p => p.id === profile)}>
          {busy ? "Assigning…" : "Assign profile"}
        </button>
        <p className="basis-full text-sm text-slate-600">Filter to Access points or CX switches to select all visible devices, or check individual rows. Up to 500 devices of one type can share the selected profile.</p>
      </form>}
      {admin && reviewAccess && <section className="inm-cardbody space-y-2">
        <h3 className="font-semibold">Review management access</h3>
        <p>Choose a saved credential profile above, then test the eligible addresses. Missing or repeated addresses are skipped.</p>
        <ul className="max-h-48 overflow-auto">{accessRows.map(({device, address, reason}) =>
          <li key={device.id}>{device.hostname || device.serial_number}
            {device.family === "access_point" && <> · {clusterLabel(device.cluster_id)}</>}
            {" · "}{address || "No IP"} · {reason || "Ready to test"}</li>)}</ul>
        <button type="button" className={btn}
          disabled={!eligibleAddresses.length || !matchingProfiles.some(p => p.id === profile) ||
            busy || !!testing || testingBatch || onboardingBusy}
          onClick={() => void testReviewedAddresses()}>Assign profile &amp; test {eligibleAddresses.length} addresses</button>
        <button type="button" className={btn} disabled={!eligibleAddresses.length} onClick={() => {
          const url = URL.createObjectURL(new Blob([eligibleAddresses.join("\n") + "\n"], {type: "text/plain"}));
          const link = document.createElement("a");
          link.href = url; link.download = "inm-management-addresses.txt"; link.click();
          URL.revokeObjectURL(url);
        }}>Export {eligibleAddresses.length} management addresses</button>
      </section>}
      {admin && selectedFamily && <details className="inm-cardbody">
        <summary className="cursor-pointer font-medium">Create a credential profile for selected devices</summary>
        <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={async e => {
          e.preventDefault();
          const form = e.currentTarget;
          const data = new FormData(form);
          setBusy(true); setFeedback({});
          try {
            const saved = await onboardingApi.createCredential({
              name: String(data.get("name") ?? "").trim(), device_family: selectedFamily,
              username: String(data.get("username") ?? "").trim(), password: String(data.get("password") ?? ""),
            });
            profiles.reload(); setProfile(saved.id);
            setFeedback({message: "Profile saved. Click Assign profile to apply it to the selected devices."});
          } catch (error) {
            setFeedback({error: error instanceof Error ? error.message : "Could not create the profile"});
          } finally {
            form.reset(); setBusy(false);
          }
        }}>
          <label>Profile name<input className={inputCls} name="name" required /></label>
          <label>Username<input className={inputCls} name="username" required /></label>
          <label>Password<input className={inputCls} name="password" type="password" autoComplete="new-password" required /></label>
          <button className={btn} disabled={busy}>Save credential profile</button>
        </form>
      </details>}
      {admin && <section className="inm-cardbody flex flex-wrap items-end gap-3" aria-label="Resume saved device connection group">
        <label>Load saved device assignments
          <select className={inputCls} value={savedProfile}
            disabled={busy || testingBatch || onboardingBusy}
            onChange={e => setSavedProfile(e.target.value)}>
            <option value="">Choose a saved credential profile</option>
            {(profiles.data ?? []).filter(p => p.enabled).map(p =>
              <option key={p.id} value={p.id}>{p.name} ({p.device_family === "switch" ? "Switches" : "APs"})</option>)}
          </select>
        </label>
        <button type="button" className={btn} disabled={!savedProfile || busy || !!testing || testingBatch || onboardingBusy}
          onClick={async () => {
            setBusy(true); setFeedback({});
            try {
              const assigned = await onboardingApi.credentialDevices(savedProfile);
              setResult({profileId: assigned.profile_id, profileName: assigned.profile_name, devices: assigned.devices});
              setChecks({});
              setOnboardResults({});
              setRestBootstrapDevice(null);
              setFeedback({message: "Loaded " + assigned.devices.length + " registered devices with saved profile " +
                assigned.profile_name + ". Run Test All, then Onboard Successful."});
            } catch (error) {
              setFeedback({error: error instanceof Error ? error.message : "Could not load saved device assignments"});
            } finally { setBusy(false); }
          }}>Load assigned devices</button>
        <p className="basis-full text-sm text-slate-600">
          Resume testing already registered devices across sites, including after refreshing the browser.
          No duplicate equipment records are created.
        </p>
      </section>}
      {admin && result && <section className="inm-cardbody space-y-2" aria-label="Credential assignment results">
        <h3 className="font-semibold">Connection verification</h3>
        <p>Profile assigned: {result.profileName}. Test a device to check its REST login. Live status still depends on collection.</p>
        <div className="flex flex-wrap items-center gap-2" aria-label="Fleet connection and onboarding actions">
          <button type="button" className={btn} disabled={busy || !!testing || testingBatch || onboardingBusy || !result.devices.some(d => d.address)}
            onClick={() => void testAllConnections()}>
            {testingBatch ? "Testing devices…" : "Test All"}
          </button>
          <button type="button" className={btn} disabled={busy || !!testing || testingBatch || onboardingBusy || !successfulDevices.length}
            onClick={() => void onboardSuccessful()}>
            {onboardingBusy ? "Onboarding devices…" : `Onboard Successful (${successfulDevices.length})`}
          </button>
        </div>
        <p className="text-sm text-slate-600" role="status" aria-live="polite">
          REST login verified: {checkCounts.successful} · Other results: {checkCounts.failed} · Not tested: {checkCounts.notTested}
          {batchProgress && ` · Processed ${batchProgress.done}/${batchProgress.total}`}
        </p>
        <p className="text-sm text-slate-600">
          Already registered devices are not duplicated. Onboard Successful imports a read-only baseline and creates
          a five-minute monitoring plan only for REST-verified devices. Review scheduled collection in
          {" "}<Link className="inm-link" to="/collection">Live monitoring</Link>.
        </p>
        <div className="inm-tablewrap"><table className="inm-table">
          <thead><tr><th>Device</th><th>Management IP</th><th>Assignment</th><th>Connection</th><th>Onboarding</th><th>Action</th></tr></thead>
          <tbody>{result.devices.map(d => {
            const check = checks[d.id];
            const device = devices.data?.find(device => device.id === d.id);
            return <tr key={d.id}>
              <td>{d.name}{device?.family === "access_point" &&
                <div className="text-xs text-slate-600">{clusterLabel(device.cluster_id)}</div>}</td>
              <td>{d.address ?? "Missing"}</td><td>Saved</td>
              <td aria-live="polite">{testing === d.id ? "Testing…" : check
                ? check.status + (check.message ? " · " + check.message : "")
                : "Not tested"}</td>
              <td aria-live="polite">{onboardResults[d.id]
                ? onboardResults[d.id].status + " · " + onboardResults[d.id].message
                : check?.restState === "ENABLED" ? "Ready for onboarding" : "Awaiting REST verification"}</td>
              <td><div className="flex flex-wrap gap-2">
                <button type="button" className={btn} disabled={busy || !!testing || testingBatch || onboardingBusy || !d.address}
                  onClick={() => void testConnection(result.profileId, d)}>
                  Test connection
                </button>
                {!check?.clusterManaged && (check?.restState === "UNREACHABLE" || check?.restState === "DISABLED") &&
                  <button type="button" className={btn}
                    disabled={busy || !!testing || testingBatch || onboardingBusy}
                    onClick={() => setRestBootstrapDevice(current => current === d.id ? null : d.id)}>
                    {restBootstrapDevice === d.id ? "Close SSH diagnostic" : "Check SSH / Enable REST"}
                  </button>}
              </div></td>
            </tr>;
          })}</tbody>
        </table></div>
        {restBootstrapDevice && (() => {
          const target = result.devices.find(d => d.id === restBootstrapDevice);
          const family = devices.data?.find(d => d.id === restBootstrapDevice)?.family;
          return target?.address && family && ["access_point", "switch"].includes(family) ?
            <DeviceRestBootstrap key={restBootstrapDevice} deviceId={target.id}
              credentialId={result.profileId} address={target.address} family={family as "access_point" | "switch"}
              onEnabled={async () => {
                await testConnection(result.profileId, target);
                setRestBootstrapDevice(null);
              }}/> : null;
        })()}
      </section>}
      {rows.length ? <div className="inm-tablewrap"><table className="inm-table"><thead><tr>
        {admin && <th scope="col"><input type="checkbox" aria-label="Select visible devices of this type"
          checked={allVisibleSelected} disabled={busy || !eligible.length} onChange={toggleVisible} /></th>}<th>Device</th><th>Model / firmware</th><th>Site</th><th>Cluster</th><th>Management IP</th><th>Status</th><th>Source</th><th>Observed</th><th>Actions</th>
      </tr></thead><tbody>{rows.map(d=><tr key={d.id}>
        {admin && <td><input type="checkbox" aria-label={`Select ${d.hostname || d.serial_number}`}
          checked={selected.includes(d.id)} disabled={busy || d.source_type !== "REAL" ||
            (!!selectedFamily && selectedFamily !== d.family) || (selected.length >= 500 && !selected.includes(d.id))}
          onChange={e => changeSelection(e.target.checked ? [...selected, d.id] : selected.filter(id => id !== d.id))} /></td>}
        <td><Link className="inm-link" to={"/devices/"+d.id+"/360"}>{d.hostname || d.serial_number}</Link>
          <div style={{fontSize:10,color:"#7b8a9d"}}>{d.serial_number}</div></td>
        <td>{d.model ?? d.family}<div style={{fontSize:10,color:"#7b8a9d"}}>{d.firmware ?? "Not observed"}</div></td>
        <td>{d.site_id ? siteNames.get(d.site_id) ?? "Assigned site" : "Unassigned"}</td>
        <td>{d.family === "access_point" ? clusterLabel(d.cluster_id) : "—"}</td>
        <td>{d.management_addresses?.join(", ") || "Not reported"}</td>
        <td><span className={"inm-pill "+(d.status.toLowerCase()==="up"?"good":d.status.toLowerCase()==="down"?"bad":"warn")}>{d.status}</span></td>
        <td><span className={"inm-pill "+(d.source_type==="REAL"?"info":"warn")}>{d.source_type}</span></td>
        <td>{ago(d.last_observed_at)}</td>
        <td><Link className="inm-link" to={"/devices/"+d.id+"/360"}>Device 360°</Link></td>
      </tr>)}</tbody></table></div>
      : <div className="inm-cardbody"><div className="inm-empty">No devices match the current filter. Add hardware through guided onboarding.</div></div>}
      <div className="inm-cardfooter">All device data is retrieved from the management API. DEMO records are explicitly labelled.</div>
    </section>
  </div>;
}
