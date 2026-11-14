import { PageHeader } from "../components/viz";
import { useState } from "react";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { DemoBadge, PlannedNotice, btnPrimary } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { v1 } from "../services/inm";

const RISK: Record<string, string> = { high: "critical", medium: "warning", low: "fair" };

export default function AiLearning() {
  const recs = useLoad(() => v1.recommendations(), [], 60000, ["recommendations"]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const canRun = ["admin", "operator"].includes(getSession()?.role ?? "");
  const analyze = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try { const r = await v1.analyze(); setMsg(`${r.created} recommendation(s) from ${r.engine} ${r.ruleset_version}`); }
    catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <PageHeader eyebrow="Intelligence & automation" title="Rule-based network intelligence" subtitle="Evidence-based analysis and versioned rules; not a trained predictive model." />
        {canRun && <button className={btnPrimary} onClick={analyze} disabled={busy}>{busy ? "Analysing…" : "Analyze now"}</button>}
      </div>
      <div className="rounded-lg border border-sky-900/60 bg-sky-950/30 p-3 text-sm text-sky-100">
        <b>Deterministic rules, not machine learning.</b> Recommendations come from a versioned rule set in the isolated AI service.
        It receives pseudonymised site aggregates only, has no device credentials or network route, and nothing it returns is executed.
      </div>
      <ErrorNote error={err || recs.error} />
      {msg && <div className="text-sm text-emerald-300">{msg}</div>}
      <Card title="Recommendations">
        <ul className="divide-y divide-slate-800">
          {recs.data?.map((r) => (
            <li key={r.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge value={RISK[r.risk] ?? r.risk} /><span className="font-medium">{r.title}</span>
                <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] font-bold text-sky-300">{r.engine} · {r.rule_version}</span>
                <DemoBadge source={r.source_type} /><span className="text-[10px] text-slate-500">{r.approval_state}</span>
              </div>
              <p className="mt-1 text-sm text-slate-300">{r.explanation}</p>
              <div className="mt-1 text-xs text-slate-500">
                {r.site ?? "all sites"} · rule <code>{r.rule_id}</code> · evidence <code>{JSON.stringify(r.evidence)}</code> · {r.input_window} · by {r.requested_by} · {fmtTime(r.created_at)}
              </div>
            </li>
          ))}
          {recs.data?.length === 0 && <li className="py-6 text-center text-sm text-slate-500">No recommendations yet. Run “Analyze now”.</li>}
        </ul>
      </Card>
      <PlannedNotice phase={8} title="Model training, registry and evaluation">
        Trained models, feature lineage, v1/v2 comparisons and accepted/rejected feedback arrive in Phase 8 after the ML quality review.
      </PlannedNotice>
    </div>
  );
}
