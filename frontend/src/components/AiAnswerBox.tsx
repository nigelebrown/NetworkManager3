import { SimBadge } from "./ui";
import type { AiAnswer } from "../services/api";

/** Always labels AI output, names the model and lets the user inspect exactly what data the model was given. */
export default function AiAnswerBox({ a }: { a: AiAnswer }) {
  return (
    <div className="rounded-lg border border-violet-800/60 bg-violet-950/20 p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded bg-violet-500/20 px-2 py-0.5 font-semibold text-violet-200">AI-generated</span>
        <span className="text-slate-400">model {a.model}</span>
        {a.simulated && <SimBadge />}
        {a.tool && <span className="text-slate-500">used data: {a.tool}</span>}
      </div>
      <div className="whitespace-pre-wrap text-slate-100">{a.text}</div>
      <p className="mt-2 text-xs text-slate-500">Advisory only. Check it against the numbers before acting. The assistant cannot change any setting.</p>
      {Object.keys(a.facts).length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-400">Data the model was given (client names are pseudonyms)</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-950 p-2 text-slate-300">{JSON.stringify(a.facts, null, 1)}</pre>
        </details>
      )}
    </div>
  );
}
