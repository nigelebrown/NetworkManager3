import { useState } from "react";
import { Card } from "./ui";
import { useLoad } from "./useLoad";
import { DevicePicker, Notice, Pill, btn, btn2, btnDanger, statusTone, useAction } from "./viz";
import { getSession, request } from "../services/api";
import type { Device } from "../services/inm";

type Image = { id: string; filename: string; version: string; sha256: string; size: number; authenticity: string; uploaded_by: string; uploaded_at: string };
type Plan = { id: string; device_id: string; serial: string; target_version: string; previous_version: string | null; status: string;
  requested_by: string; approved_by: string | null; staged_slot?: string; history: { at: string; step: string; by: string }[] };

/** AOS-CX switch firmware (rc17): vendor-hash-verified images, independent approval, stage-only, verified boot, recovery. */
export default function CxFirmware() {
  const me = getSession();
  const admin = me?.role === "admin";
  const imgs = useLoad(() => me && ["admin", "operator"].includes(me.role) ? request<Image[]>("/v1/switch-firmware/images") : Promise.resolve([]), [me?.role], 0);
  const plans = useLoad(() => request<Plan[]>("/v1/switch-firmware/plans"), [], 15000);
  const [file, setFile] = useState<File | null>(null);
  const [sha, setSha] = useState("");
  const [device, setDevice] = useState<Device | undefined>();
  const [image, setImage] = useState("");
  const [downgrade, setDowngrade] = useState(false);
  const act = useAction();
  const call = (path: string, body?: unknown) => request<Plan>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
  const run = (fn: () => Promise<string>) => act.run(async () => { const m = await fn(); plans.reload(); imgs.reload(); return m; });
  return <Card title="CX switch firmware">
    <p className="text-sm text-slate-600">Images are accepted only when their SHA-256 matches the value published on the HPE support portal; the switch also
      validates the image signature on upload. Staging writes the non-booted slot and never reboots. Booting is a separate, acknowledged step that is verified
      (running version and serial) and never re-sent; recovery boots the previous image.</p>
    <Notice error={act.error || imgs.error || plans.error} message={act.message} />
    {admin && <div className="mt-2 flex flex-wrap items-end gap-2 text-sm">
      <label>Image file (e.g. ML_10_14_1020.swi)<input type="file" accept=".swi" className="block" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <label>Vendor-published SHA-256<input className="ml-1 w-[28rem] rounded border px-2 py-1 font-mono" value={sha} onChange={e => setSha(e.target.value.trim())} /></label>
      <button className={btn} disabled={act.busy || !file || sha.length !== 64} onClick={() => run(async () => {
        const f = new FormData(); f.set("file", file!); f.set("vendor_sha256", sha);
        const r = await request<Image>("/v1/switch-firmware/images", { method: "POST", body: f });
        setSha(""); return `${r.filename} stored (${r.version}); hash matches the vendor value.`;
      })}>Upload verified image</button></div>}
    {!!imgs.data?.length && <table className="mt-3 w-full text-left text-sm"><thead><tr>{["Image", "Version", "SHA-256", "Authenticity", "Uploaded"].map(h => <th key={h} scope="col" className="p-1">{h}</th>)}</tr></thead>
      <tbody>{imgs.data.map(i => <tr key={i.id} className="border-t"><td className="p-1">{i.filename}</td><td className="p-1">{i.version}</td>
        <td className="p-1 font-mono text-xs">{i.sha256.slice(0, 16)}…</td><td className="p-1"><Pill value={i.authenticity} tone="good" /></td>
        <td className="p-1">{new Date(i.uploaded_at).toLocaleString()} by {i.uploaded_by}</td></tr>)}</tbody></table>}
    {admin && <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
      <div className="w-72"><DevicePicker family="switch" value={device?.id ?? ""} onChange={(_, d) => setDevice(d)} /></div>
      <label>Image<select className="ml-1 rounded border px-2 py-1" value={image} onChange={e => setImage(e.target.value)}>
        <option value="">Choose…</option>{(imgs.data ?? []).map(i => <option key={i.id} value={i.id}>{i.version}</option>)}</select></label>
      <label className="flex items-center gap-1"><input type="checkbox" checked={downgrade} onChange={e => setDowngrade(e.target.checked)} />Allow downgrade</label>
      <button className={btn2} disabled={act.busy || !device || !image} onClick={() => run(async () => {
        await call("/v1/switch-firmware/plans", { device_id: device!.id, image_id: image, allow_downgrade: downgrade });
        return "Plan created; an independent approver must approve it."; })}>Plan upgrade</button></div>}
    <table className="mt-3 w-full text-left text-sm"><thead><tr>{["Switch serial", "From → to", "Status", "Requested / approved", "Actions"].map(h => <th key={h} scope="col" className="p-1">{h}</th>)}</tr></thead>
      <tbody>{(plans.data ?? []).map(p => <tr key={p.id} className="border-t">
        <td className="p-1">{p.serial}</td><td className="p-1">{p.previous_version ?? "?"} → {p.target_version}</td>
        <td className="p-1"><Pill value={p.status} tone={statusTone(p.status)} /></td><td className="p-1">{p.requested_by} / {p.approved_by ?? "—"}</td>
        <td className="space-x-1 p-1">
          {p.status === "PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== p.requested_by &&
            <button className={btn2} disabled={act.busy} onClick={() => run(async () => { await call(`/v1/switch-firmware/plans/${p.id}/approve`); return "Approved."; })}>Approve</button>}
          {admin && ["APPROVED", "STAGE_FAILED"].includes(p.status) && <button className={btn2} disabled={act.busy} onClick={() => run(async () => {
            const r = await call(`/v1/switch-firmware/plans/${p.id}/stage`); return r.status === "STAGED" ? `Staged in the ${r.staged_slot} slot (no reboot).` : "Stage readback failed."; })}>Stage</button>}
          {admin && p.status === "STAGED" && <button className={btnDanger} disabled={act.busy} onClick={() => {
            if (!window.confirm("Reboot the switch into the staged image? Every port on the switch goes down during the reboot.")) return;
            run(async () => { const r = await call(`/v1/switch-firmware/plans/${p.id}/boot`, { acknowledge_reboot: true });
              return r.status === "BOOT_VERIFIED" ? "Rebooted and verified." : "Reboot not verified — recovery required."; }); }}>Reboot into image</button>}
          {admin && ["RECOVERY_REQUIRED", "BOOT_VERIFIED", "BOOT_SENT", "RECOVERY_FAILED"].includes(p.status) && <button className={btn2} disabled={act.busy}
            onClick={() => run(async () => { const r = await call(`/v1/switch-firmware/plans/${p.id}/recover`); return `Recovery: ${r.status}.`; })}>Recover previous image</button>}
        </td></tr>)}</tbody></table>
    {!plans.data?.length && <p className="text-sm text-slate-500">No switch firmware plans.</p>}
  </Card>;
}
