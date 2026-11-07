import { useEffect, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import AiAnswerBox from "../components/AiAnswerBox";
import ConfigEditor from "../components/ConfigEditor";
import SiteFilter from "../components/SiteFilter";
import { Badge, Card, ErrorNote, NA, SimBadge, Stat, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { aiApi, expApi, getSession, type AiAnswer, type ExpConfig, type ExpResult } from "../services/api";

const tone = (b?: string) => (b === "good" ? "ok" : b === "fair" ? "warn" : b === "poor" ? "bad" : undefined);

export default function Experience() {
  const isAdmin = getSession()?.role === "admin";
  const canSee = getSession()?.role !== "viewer";
  const [site, setSite] = useState<number | undefined>();
  const [hours, setHours] = useState(24);
  const ov = useLoad(() => expApi.overview(site), [site]);
  const hist = useLoad(() => expApi.history(hours, site), [site, hours]);
  const ev = useLoad(() => expApi.events(site), [site]);
  const cfg = useLoad(() => (canSee ? expApi.config() : Promise.resolve(null)), [], 0);
  const [why, setWhy] = useState<{ id: number; title: string; r: ExpResult } | null>(null);
  const canAi = getSession()?.role !== "viewer";
  const [aiOn, setAiOn] = useState(false);
  const [ai, setAi] = useState<AiAnswer | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);
  useEffect(() => { if (canAi) aiApi.status().then((s) => setAiOn(s.enabled && s.reachable === true && !!s.model)).catch(() => setAiOn(false)); }, [canAi]);
  const closeWhy = () => { setWhy(null); setAi(null); setAiErr(null); };
  const askAi = async () => {
    if (!why) return; setAiBusy(true); setAiErr(null); setAi(null);
    try { setAi(await aiApi.explainClient(why.id)); } catch (e) { setAiErr(e instanceof Error ? e.message : "Failed"); } finally { setAiBusy(false); }
  };
  const [draft, setDraft] = useState<ExpConfig | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const explain = async (id: number, title: string) => { try { setWhy({ id, title, r: (await expApi.client(id)).result }); } catch (e) { setErr(String(e)); } };
  const save = async () => {
    if (!draft) return; setErr(null); setMsg(null);
    try { await expApi.saveConfig(draft); setMsg("Saved. New scores use it from the next poll."); cfg.reload(); setDraft(null); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  };
  const reset = async () => { try { await expApi.resetConfig(); setMsg("Reset to defaults."); setDraft(null); cfg.reload(); } catch (e) { setErr(String(e)); } };

  const o = ov.data, s = o?.summary;
  const points = (hist.data ?? []).map((p) => ({ ...p, t: new Date(p.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Digital experience {o?.simulated && <SimBadge />}</h1>
        <div className="flex items-center gap-2">
          <SiteFilter value={site} onChange={setSite} />
          <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="History range">
            {[6, 24, 72, 168].map((h) => <option key={h} value={h}>Last {h}h</option>)}
          </select>
        </div>
      </div>
      <ErrorNote error={ov.error ?? err} />
      {msg && <div className="rounded-lg border border-emerald-800 bg-emerald-950/40 p-2 text-sm text-emerald-200">{msg}</div>}

      <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-xs text-slate-400">
        Scores combine what the AP reports (signal, connection speed) with connection stability <b>inferred from polling</b>. Retransmissions,
        channel congestion, latency, jitter, packet loss, DHCP and application responsiveness are <b>not measured yet</b>, so they are left out and never estimated.
        Every score lists the inputs it used and what was missing.
      </div>

      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Experience score" value={s.score ?? NA} tone={tone(s.band)} sub={s.score == null ? "no scorable clients" : `${s.band} · mean of clients`} />
          <Stat label="Good" value={s.good} tone="ok" sub={`score ≥ ${o?.bands.good_min}`} />
          <Stat label="Fair" value={s.fair} tone="warn" sub={`score ≥ ${o?.bands.fair_min}`} />
          <Stat label="Poor" value={s.poor} tone={s.poor ? "bad" : undefined} sub={`score < ${o?.bands.fair_min}`} />
          <Stat label="Not scorable" value={s.unscored} sub="not enough measured data" />
        </div>
      )}

      <Card title="Experience score over time">
        {points.some((p) => p.score != null) ? (
          <div className="h-64"><ResponsiveContainer>
            <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis dataKey="t" stroke="#64748b" fontSize={11} minTickGap={32} />
              <YAxis domain={[0, 100]} stroke="#64748b" fontSize={11} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              {o && <ReferenceLine y={o.bands.good_min} stroke="#34d399" strokeDasharray="4 4" />}
              {o && <ReferenceLine y={o.bands.fair_min} stroke="#fbbf24" strokeDasharray="4 4" />}
              <Line type="monotone" dataKey="score" name="Experience score" stroke="#38bdf8" strokeWidth={2} dot={false} connectNulls={false} />
            </LineChart>
          </ResponsiveContainer></div>
        ) : <p className="py-8 text-center text-sm text-slate-500">No history yet. Scores are recorded on every poll.</p>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Lowest-scoring clients">
          <table className="w-full text-left text-sm"><thead className="text-xs uppercase text-slate-400"><tr>{["Client", "AP", "SSID", "Score", ""].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-800">
              {o?.worst_clients.map((c) => (
                <tr key={c.id}><td className="px-2 py-2 font-mono text-xs">{c.mac}</td><td className="px-2 py-2">{c.ap_name ?? NA}</td><td className="px-2 py-2">{c.ssid ?? NA}</td>
                  <td className="px-2 py-2">{c.score} <Badge value={c.band} /></td>
                  <td className="px-2 py-2 text-right"><button className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800" onClick={() => explain(c.id, c.mac)}>Why?</button></td></tr>
              ))}
              {o?.worst_clients.length === 0 && <tr><td colSpan={5} className="px-2 py-6 text-center text-slate-500">No scorable clients.</td></tr>}
            </tbody></table>
        </Card>
        <Card title="Access points">
          <table className="w-full text-left text-sm"><thead className="text-xs uppercase text-slate-400"><tr>{["AP", "Site", "Clients", "Score"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-800">
              {o?.aps.map((a) => (
                <tr key={a.id}><td className="px-2 py-2">{a.name}</td><td className="px-2 py-2">{a.site_name}</td><td className="px-2 py-2">{a.client_count ?? NA}</td>
                  <td className="px-2 py-2">{a.score == null ? <Badge value={a.band === "offline" ? "down" : null} /> : <>{a.score} <Badge value={a.band} /></>}</td></tr>
              ))}
            </tbody></table>
        </Card>
      </div>

      <Card title="Recent connection events" right={<span className="text-xs text-slate-500" title="Compared between polls; anything shorter than the poll interval is invisible">inferred from polling</span>}>
        <table className="w-full text-left text-sm"><thead className="text-xs uppercase text-slate-400"><tr>{["Time", "Event", "Client", "From → To", "SSID"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-800">
            {ev.data?.events.map((e) => (
              <tr key={e.id}><td className="px-2 py-2">{fmtTime(e.ts)}</td><td className="px-2 py-2"><Badge value={e.kind === "disconnect" ? "poor" : e.kind === "roam" ? "fair" : "good"} /> {e.kind}</td>
                <td className="px-2 py-2 font-mono text-xs">{e.mac}</td><td className="px-2 py-2">{e.ap_from ?? "—"} → {e.ap_to ?? "—"}</td><td className="px-2 py-2">{e.ssid ?? NA}</td></tr>
            ))}
            {ev.data?.events.length === 0 && <tr><td colSpan={5} className="px-2 py-6 text-center text-slate-500">No events yet. They appear as clients connect, roam and leave between polls.</td></tr>}
          </tbody></table>
      </Card>

      {canSee && cfg.data && (
        <Card title="Scoring settings" right={isAdmin && <div className="flex gap-2">
          {draft ? <><button className="rounded bg-sky-600 px-3 py-1 text-sm font-medium hover:bg-sky-500" onClick={save}>Save</button>
            <button className="rounded border border-slate-600 px-3 py-1 text-sm hover:bg-slate-800" onClick={() => setDraft(null)}>Cancel</button></>
            : <><button className="rounded border border-slate-600 px-3 py-1 text-sm hover:bg-slate-800" onClick={() => setDraft(structuredClone(cfg.data!))}>Edit</button>
              <button className="rounded border border-slate-600 px-3 py-1 text-sm hover:bg-slate-800" onClick={reset}>Reset to defaults</button></>}
        </div>}>
          <p className="mb-3 text-xs text-slate-500">“worst” scores 0 and “best” scores 100, linearly between. Weights are relative; inputs that cannot be measured are dropped and the rest renormalized. Formula: docs/SCORING.md.</p>
          {draft ? <ConfigEditor value={draft} onChange={setDraft} /> : <ConfigEditor value={cfg.data} onChange={() => {}} />}
        </Card>
      )}

      {why && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={closeWhy} role="presentation">
          <div className="max-h-[85vh] w-full max-w-lg overflow-auto rounded-xl border border-slate-700 bg-slate-900 p-5" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <h2 className="text-base font-semibold">Why this score? <span className="font-mono text-xs text-slate-400">{why.title}</span></h2>
            <p className="mt-1 text-sm">{why.r.score ?? NA} <Badge value={why.r.band} /> <span className="text-xs text-slate-500">coverage {Math.round(why.r.coverage * 100)}% of the model</span></p>
            <table className="mt-3 w-full text-left text-sm"><thead className="text-xs uppercase text-slate-400"><tr>{["Input", "Measured", "Sub-score", "Weight", "Source"].map((h) => <th key={h} className="px-1 py-1">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-slate-800">{Object.entries(why.r.inputs).map(([k, i]) => (
                <tr key={k}><td className="px-1 py-1.5">{k}</td><td className="px-1 py-1.5">{i.value}</td><td className="px-1 py-1.5">{i.subscore}</td><td className="px-1 py-1.5">{Math.round(i.weight * 100)}%</td><td className="px-1 py-1.5 text-xs text-slate-400">{i.source}</td></tr>))}</tbody></table>
            {Object.keys(why.r.unavailable).length > 0 && (
              <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm"><div className="font-semibold">Unavailable (left out, not estimated)</div>
                <ul className="mt-1 space-y-1 text-slate-300">{Object.entries(why.r.unavailable).map(([k, r]) => <li key={k}><b>{k}</b>: {r}</li>)}</ul></div>)}
            {aiErr && <div className="mt-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300">{aiErr}</div>}
            {ai && <div className="mt-3"><AiAnswerBox a={ai} /></div>}
            <div className="mt-4 flex justify-end gap-2">
              {canAi && aiOn && <button disabled={aiBusy} className="rounded border border-violet-700 px-3 py-1.5 text-sm text-violet-200 hover:bg-violet-950 disabled:opacity-50" onClick={askAi}>{aiBusy ? "Thinking…" : "Explain with AI"}</button>}
              <button className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500" onClick={closeWhy}>Close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
