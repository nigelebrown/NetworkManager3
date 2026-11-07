import { Card, ErrorNote, fmtTime } from "../components/ui";
import { PlannedNotice, Table } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { v1 } from "../services/inm";

export default function Recovery() {
  const { data, error } = useLoad(() => v1.recovery(), [], 60000, ["config"]);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Backups &amp; recovery</h1>
      <ErrorNote error={error} />
      <div className="grid gap-4 md:grid-cols-2">
        <PlannedNotice phase={5} title="Encrypted full-fidelity configuration vault">
          Versioned, encrypted, hash-verified snapshots with key references, retention and two-person access to secret-bearing raw configs.
        </PlannedNotice>
        <PlannedNotice phase={7} title="Same-model device replacement wizard">
          Compatibility checks (model, firmware, ports, licence), staged diff, separate approval and independent readback.
        </PlannedNotice>
      </div>
      <Card title="Sanitised configuration snapshots (display copies only)">
        <p className="mb-2 text-sm text-amber-200">
          These snapshots had passphrases and passwords removed before storage. They are useful for review and diffs but they
          <b> cannot restore a device</b>, so they are never offered as restore candidates.
        </p>
        <Table head={["Taken", "Site", "Kind", "SHA-256", "Classification", "Restorable"]} empty={data?.sanitized_snapshots.length === 0}>
          {data?.sanitized_snapshots.map((b) => (
            <tr key={b.id}>
              <td className="px-2 py-1.5 text-xs">{fmtTime(b.taken_at)}</td>
              <td className="px-2 py-1.5 text-xs">#{b.site_id}</td>
              <td className="px-2 py-1.5 text-xs">{b.kind}</td>
              <td className="px-2 py-1.5 font-mono text-[10px] text-slate-500">{b.sha256.slice(0, 16)}…</td>
              <td className="px-2 py-1.5 text-xs">{b.classification}</td>
              <td className="px-2 py-1.5"><span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-bold text-red-300" title={b.reason}>NOT RESTORABLE</span></td>
            </tr>
          ))}
        </Table>
        {data && data.restore_candidates.length === 0 && <div className="mt-2 text-xs text-slate-500">Restore candidates: none (no verified full-fidelity backup exists yet).</div>}
      </Card>
    </div>
  );
}
