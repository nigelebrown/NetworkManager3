import { useState } from "react";
import { onboardingApi, type RestPreflight } from "../services/onboarding";
import { Notice, btn, btn2, inputCls, useAction } from "./viz";

/** Device-specific REST bootstrap. Nothing is changed until a human confirms SSH identity and the device IP. */
export default function DeviceRestBootstrap({ credentialId, address, family, deviceId, onEnabled }: {
  credentialId: string; address: string; family: "switch" | "access_point"; deviceId?: string;
  onEnabled: () => Promise<void>;
}) {
  const act = useAction();
  const [preflight, setPreflight] = useState<RestPreflight | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [vrf, setVrf] = useState<"default" | "mgmt">("default");
  const [result, setResult] = useState("");
  const check = () => act.run(async () => {
    setResult(""); setConfirmation("");
    const r = await onboardingApi.checkRestApi(credentialId, address, deviceId);
    setPreflight(r);
    return `REST check: ${r.state}`;
  });
  const canEnable = preflight?.ssh?.fingerprint &&
    preflight.ssh.trust !== "CHANGED" &&
    (preflight.state === "DISABLED" || preflight.state === "UNREACHABLE") &&
    confirmation === preflight.confirm_phrase && !preflight.write_unlock_required;
  const enable = () => act.run(async () => {
    if (!preflight?.ssh?.fingerprint) throw new Error("Confirm the SSH host fingerprint first.");
    const r = await onboardingApi.enableRestApi(credentialId, {
      management_ip: address, host_key_sha256: preflight.ssh.fingerprint,
      confirm_text: confirmation, vrf, device_id: deviceId,
    });
    setResult(r.message);
    if (r.ok) { await onEnabled(); setPreflight(null); }
    return r.ok ? "REST enabled and device discovery restarted." : "REST enablement not verified.";
  });
  return <div className="mt-2 space-y-2 rounded border border-amber-800 bg-amber-950/20 p-3 text-sm">
    <div className="font-semibold">REST API prerequisite — {address}</div>
    <p className="text-xs text-slate-400">Some Aruba devices ship without an enabled REST server. Check first, then optionally enable through SSH. No setting is changed without administrator confirmation.</p>
    <button className={btn2} disabled={act.busy} onClick={check}>Check REST and SSH identity</button>
    {preflight && <div className="space-y-2 text-xs">
      <p><b>{preflight.state}.</b> {preflight.detail}</p>
      <p>Device HTTPS: {preflight.untrusted_device_certificates_accepted ?
        "Self-signed/untrusted TLS certificates accepted (identity not verified by a CA)." : "Trusted certificate required."}</p>
      {preflight.ssh?.fingerprint && <>
        {preflight.ssh.trust === "CHANGED" && <p role="alert">Saved SSH identity changed. Review this AP before enabling REST.</p>}
        <p className="text-amber-200">Verify this SSH fingerprint through an independent, trusted channel before confirming:</p>
        <code className="block break-all rounded bg-slate-900 p-2">{preflight.ssh.fingerprint}</code>
        <p className="text-slate-400">Commands: {family === "switch" ? `configure terminal → https-server vrf ${vrf} → end → write memory` :
          "configure terminal → allow-rest-api → end → commit apply"}</p>
        {family === "switch" && <label className="block">CX VRF (6100: default only)
          <select className={inputCls} value={vrf} onChange={e => setVrf(e.target.value as "default" | "mgmt")}>
            <option value="default">default — in-band management</option>
            <option value="mgmt">mgmt — CX 6200 out-of-band</option>
          </select></label>}
        <label className="block">Confirm the device and host key: type <b>{preflight.confirm_phrase}</b>
          <input className={inputCls} aria-label="Confirm REST enable" value={confirmation}
            onChange={e => setConfirmation(e.target.value)} /></label>
        {preflight.ap_rest_only_authorized && <p className="text-emerald-300">Authorized registered AP-505/AP-535: only the fixed REST-bootstrap sequence is permitted. The global device configuration-write lock remains engaged.</p>}
        {preflight.write_unlock_required && <p className="text-amber-300">REST enablement is a configuration change. This device is not eligible for the registered AP-505/AP-535 REST-only exception. Global writes remain locked.</p>}
        <button className={btn} disabled={!canEnable || act.busy} onClick={enable}>Enable REST on this device</button>
      </>}
      {preflight.ssh?.error && <p className="text-amber-300">{preflight.ssh.error} If ping succeeds but TCP 22 is unavailable, NetworkManager cannot run SSH commands. Enable SSH through an authorized device console/management interface first, then retry. Ping alone cannot enable REST.</p>}
      {preflight.state === "AUTH_REJECTED" && <p className="text-amber-300">REST is already available. Correct the credential profile instead of changing device configuration.</p>}
      {preflight.state === "UNSUPPORTED_API" && <p className="text-amber-300">REST is available but this firmware/API version is not supported by the adapter.</p>}
    </div>}
    {result && <p className="text-xs">{result}</p>}
    <Notice error={act.error} message={act.message} />
  </div>;
}
