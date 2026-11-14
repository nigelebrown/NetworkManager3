import { PageHeader } from "../components/viz";
import { useState } from "react";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { Table, btn, input } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { downloadAuditCsv, v1, type ChainResult } from "../services/inm";

export default function Audit() {
  const [action, setAction] = useState("");
  const rows = useLoad(() => v1.audit(action || undefined), [action], 30000, ["audit"]);
  const releases = useLoad(() => v1.releases(), [], 0, ["release"]);
  const [chain, setChain] = useState<ChainResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const verify = async () => { setErr(null); try { setChain(await v1.verifyAudit()); rows.reload(); } catch (e) { setErr(e instanceof Error ? e.message : "failed"); } };
  const exportCsv = async () => { setErr(null); try { await downloadAuditCsv(getSession()!.access_token); } catch (e) { setErr(e instanceof Error ? e.message : "failed"); } };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PageHeader eyebrow="Governance & security" title="Audit trail" subtitle="Operator actions, security events, and verifiable evidence history." />
        <div className="flex gap-2">
          <input className={input} placeholder="filter by action" value={action} onChange={(e) => setAction(e.target.value.trim())} />
          <button className={btn} onClick={verify}>Verify hash chain</button>
          <button className={btn} onClick={exportCsv}>Export CSV</button>
        </div>
      </div>
      <ErrorNote error={err || rows.error} />
      {chain && (
        <div data-testid="chain-result" className={`rounded-lg border p-3 text-sm ${chain.ok ? "border-emerald-800 bg-emerald-950/40 text-emerald-200" : "border-red-800 bg-red-950/40 text-red-200"}`}>
          {chain.ok ? `Chain intact: ${chain.checked} entries verified (head ${chain.head?.slice(0, 16)}…).` : `CHAIN BROKEN at entry ${chain.broken_at_id ?? "tail"}: ${chain.reason}`}
          {chain.legacy_unchained > 0 && ` ${chain.legacy_unchained} imported legacy entries predate the chain.`}
        </div>
      )}
      <Card title="Append-only events (newest first)">
        <Table head={["Time (UTC)", "Actor", "Action", "Target", "Outcome", "Channel", "Request", "Hash"]} empty={rows.data?.length === 0}>
          {rows.data?.map((a) => (
            <tr key={a.id}>
              <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-400">{fmtTime(a.ts)}</td>
              <td className="px-2 py-1.5 text-xs">{a.actor ?? "—"}<div className="text-[10px] text-slate-500">{a.actor_role ?? a.actor_kind}</div></td>
              <td className="px-2 py-1.5 text-xs font-medium">{a.action}</td>
              <td className="max-w-[16rem] truncate px-2 py-1.5 text-xs text-slate-400" title={a.detail ?? ""}>{a.target}</td>
              <td className={`px-2 py-1.5 text-xs ${a.outcome === "success" ? "text-emerald-300" : "text-red-300"}`}>{a.outcome}</td>
              <td className="px-2 py-1.5 text-[10px] text-slate-400">{a.channel}</td>
              <td className="px-2 py-1.5 font-mono text-[10px] text-slate-500">{a.request_id?.slice(0, 12) ?? "—"}</td>
              <td className="px-2 py-1.5 font-mono text-[10px] text-slate-500" title={a.event_hash ?? "legacy (unchained)"}>{a.event_hash?.slice(0, 10) ?? "legacy"}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Deployment history (install / update / rollback / backup)">
        <Table head={["Time", "Operation", "Operator", "Version", "Commit", "Outcome"]} empty={releases.data?.length === 0}>
          {releases.data?.map((r) => (
            <tr key={r.id}>
              <td className="px-2 py-1.5 text-xs text-slate-400">{fmtTime(r.ts)}</td><td className="px-2 py-1.5 text-xs">{r.operation}</td>
              <td className="px-2 py-1.5 text-xs">{r.operator}</td><td className="px-2 py-1.5 text-xs">{r.version}</td>
              <td className="px-2 py-1.5 font-mono text-[10px]">{r.git_commit?.slice(0, 12)}</td><td className="px-2 py-1.5 text-xs">{r.outcome}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
