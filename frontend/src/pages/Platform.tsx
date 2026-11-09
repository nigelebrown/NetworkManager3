import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { PageHeader, Pill } from "../components/viz";
import { v1 } from "../services/inm";
import { ops } from "../services/ops";

const tone = (s: string) => s.startsWith("IMPLEMENTED") ? "good" : s === "GUIDED" ? "fair" : s === "SCAFFOLD" ? "demo" : "none";

export default function Platform() {
  const ad = useLoad(ops.adapters, [], 0);
  const topo = useLoad(() => ops.topology().catch(() => null), [], 60000);
  const rules = useLoad(ops.rules, [], 0);
  const feats = useLoad(v1.features, [], 0);
  const ftone = (s: string) => s.startsWith("LIVE") || s === "WORKFLOW" || s === "CERTIFIED_WRITE_ENGINE" ? "good"
    : s === "NOT_IMPLEMENTED" || s === "PLACEHOLDER" ? "poor" : s === "DEMO" ? "demo" : "fair";
  return <div className="space-y-4">
    <PageHeader title="Platform: vendors, roles & rules" subtitle="Which device families each adapter supports, how the seven service roles are deployed, and the best-practice rule catalogue." />
    <ErrorNote error={ad.error} />
    <Card title="Module status" right={<span className="text-xs text-slate-500">Hardware writes: {feats.data?.device_writes}</span>}>
      <table className="w-full text-xs"><tbody>{(feats.data?.modules ?? []).map(m => <tr key={m.key} className="border-t border-slate-800">
        <td className="py-1 font-mono">{m.key}</td><td><Pill value={m.status.replace(/_/g, " ")} tone={ftone(m.status)} /></td>
        <td className="text-slate-400">{m.note}</td></tr>)}</tbody></table>
    </Card>
    <Card title="Vendor adapter capability matrix" right={<span className="text-xs text-slate-500">{ad.data?.write_policy}</span>}>
      <div className="overflow-auto"><table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th className="py-1">Adapter</th><th>Models</th><th>Protocols</th>
        {(ad.data?.operations ?? []).map(o => <th key={o} className="px-1">{o.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>{(ad.data?.adapters ?? []).map(a => <tr key={a.key} className="border-t border-slate-800">
          <td className="py-1.5"><b>{a.vendor}</b> {a.family}<div className="font-mono text-[10px] text-slate-500">{a.key} · {a.certification}</div></td>
          <td>{a.models.join(", ") || "any"}</td><td>{a.protocols.join(", ")}</td>
          {(ad.data?.operations ?? []).map(o => <td key={o} className="px-1"><Pill value={(a.operations[o] ?? "UNSUPPORTED").replace("IMPLEMENTED_", "")} tone={tone(a.operations[o])} /></td>)}</tr>)}</tbody></table></div>
    </Card>
    {topo.data && <Card title={`Service roles — ${topo.data.deployment.replace("_", " ").toLowerCase()}`}>
      <div className="grid gap-2 md:grid-cols-4">{topo.data.roles.map(r => <div key={r.role} className="rounded-lg border border-slate-800 p-2 text-sm">
        <div className="font-medium">{r.role}</div><div className="text-xs text-slate-400">{r.configured ? `${r.host ?? "local"}${r.port ? ":" + r.port : ""}` : "in-process / not configured"}</div>
        <div className="mt-1 flex gap-1">{r.configured && <Pill value={r.co_located ? "co-located" : "remote"} tone="info" />}{r.tls && <Pill value="TLS" tone="good" />}</div></div>)}</div>
      <p className="mt-2 text-xs text-slate-500">Moving a role to its own VM is a configuration change (URL/TLS/secret); no code changes.</p></Card>}
    <Card title={`Best-practice rule catalogue (${rules.data?.length ?? 0})`}><table className="w-full text-xs"><thead><tr className="text-left text-slate-500"><th>ID</th><th>Family</th><th>Category</th><th>Severity</th><th>Rule</th><th>Recommended</th></tr></thead>
      <tbody>{(rules.data ?? []).map(r => <tr key={r.id} className="border-t border-slate-800"><td className="py-1 font-mono">{r.id}</td><td>{r.family === "switch" ? "Switch" : "AP"}</td><td>{r.category}</td>
        <td><Pill value={r.severity} tone={r.severity === "critical" ? "poor" : r.severity === "high" ? "fair" : "none"} /></td><td>{r.title}</td><td className="font-mono text-sky-200">{r.fix}</td></tr>)}</tbody></table></Card>
  </div>;
}
