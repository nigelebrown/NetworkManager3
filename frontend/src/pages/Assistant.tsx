import { useEffect, useState } from "react";
import AiAnswerBox from "../components/AiAnswerBox";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { aiApi, api, getSession, type AiAnswer } from "../services/api";

const EXAMPLES = ["Which clients have the worst experience right now?", "Which APs had the most disconnects in the last 24 hours?",
  "How many clients are on each SSID and how are they doing?", "Are there any open alerts?", "Has the experience score been getting better or worse today?"];

export default function Assistant() {
  const isAdmin = getSession()?.role === "admin";
  const status = useLoad(() => aiApi.status(), [], 0);
  const sites = useLoad(() => api.sites(), [], 0);
  const [siteId, setSiteId] = useState<number | null>(null);
  const [hours, setHours] = useState(24);
  const [question, setQuestion] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (status.data?.model) setModel(status.data.model); }, [status.data?.model]);

  const run = async (label: string, fn: () => Promise<AiAnswer>) => {
    setBusy(label); setErr(null); setAnswer(null);
    try { setAnswer(await fn()); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(null); }
  };
  const st = status.data;
  const ready = !!st?.enabled && st.reachable === true && !!st.model && st.model_installed !== false;
  const saveModel = async () => { try { await aiApi.setModel(model); status.reload(); setErr(null); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">AI assistant <span className="ml-2 rounded bg-violet-500/20 px-2 py-0.5 text-xs font-medium text-violet-200">local · Ollama</span></h1>
        <select aria-label="Site" className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={siteId ?? ""} onChange={(e) => setSiteId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">All sites</option>{sites.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <ErrorNote error={status.error ?? err} />

      <Card title="Status">
        {!st ? <p className="text-sm text-slate-400">Checking…</p> : !st.enabled ? (
          <p className="text-sm text-amber-200">The assistant is <b>off</b>. Install Ollama, run <code className="rounded bg-slate-800 px-1">ollama pull &lt;model&gt;</code>, set <code className="rounded bg-slate-800 px-1">SIMS_OLLAMA_ENABLED=true</code> in <code>.env</code> and restart SIMS.</p>
        ) : st.reachable === false ? (
          <p className="text-sm text-red-300">{st.error}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-emerald-300">● Ollama reachable</span><span className="text-slate-400">{st.url}</span>
            {isAdmin ? (
              <><select className="rounded border border-slate-700 bg-slate-950 px-2 py-1" value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">Choose a model…</option>{st.models.map((m) => <option key={m}>{m}</option>)}</select>
                <button disabled={!model || model === st.model} onClick={saveModel} className="rounded bg-sky-600 px-3 py-1 text-sm font-medium hover:bg-sky-500 disabled:opacity-40">Use this model</button></>
            ) : <span className="text-slate-300">model: {st.model ?? "none selected"}</span>}
            {!st.model && <span className="text-amber-300">{st.models.length ? "No model selected yet." : "No models installed. Run: ollama pull <model>"}</span>}
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">Runs on your own machine. The model sees scores and metrics only: client MACs, IPs and hostnames are replaced with “client-1”-style labels, and it has no way to change any setting.</p>
      </Card>

      <Card title="Ask a question">
        <form className="flex flex-col gap-2 md:flex-row" onSubmit={(e) => { e.preventDefault(); if (question.trim().length >= 3) run("ask", () => aiApi.ask(question.trim(), siteId)); }}>
          <input value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. Which APs had the most disconnects today?" className="flex-1 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
          <button disabled={!ready || !!busy || question.trim().length < 3} className="rounded bg-violet-600 px-4 py-2 text-sm font-medium hover:bg-violet-500 disabled:opacity-40">{busy === "ask" ? "Thinking…" : "Ask"}</button>
        </form>
        <div className="mt-2 flex flex-wrap gap-2">{EXAMPLES.map((x) => <button key={x} onClick={() => setQuestion(x)} className="rounded-full border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800">{x}</button>)}</div>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Summarize">
          <div className="flex items-center gap-2 text-sm">
            <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1.5" value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[6, 24, 72, 168].map((h) => <option key={h} value={h}>Last {h} h</option>)}</select>
            <button disabled={!ready || !!busy} onClick={() => run("summary", () => aiApi.summary(siteId, hours))} className="rounded bg-violet-600 px-4 py-1.5 font-medium hover:bg-violet-500 disabled:opacity-40">{busy === "summary" ? "Writing…" : "Summarize alerts & trends"}</button>
          </div>
        </Card>
        <Card title="Recommendations">
          <button disabled={!ready || !!busy} onClick={() => run("recommend", () => aiApi.recommend(siteId))} className="rounded bg-violet-600 px-4 py-1.5 text-sm font-medium hover:bg-violet-500 disabled:opacity-40">{busy === "recommend" ? "Thinking…" : "What should I look at?"}</button>
          <p className="mt-2 text-xs text-slate-500">Suggestions to investigate, based only on what SIMS has measured. Nothing is changed automatically.</p>
        </Card>
      </div>

      {busy && <p className="text-sm text-slate-400">Waiting for the local model… this can take a minute on CPU.</p>}
      {answer && <AiAnswerBox a={answer} />}
    </div>
  );
}
