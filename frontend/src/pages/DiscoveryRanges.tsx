import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { discoveryApi, type RangePlan, type RangeBatches } from "../services/discovery";

const button = "rounded bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50 hover:bg-sky-500";
export default function DiscoveryRanges() {
  const [cidr, setCidr] = useState("128.0.0.0/9");
  const [plan, setPlan] = useState<RangePlan | null>(null);
  const [page, setPage] = useState<RangeBatches | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const makePlan = async (e: FormEvent) => {
    e.preventDefault(); setError(null); setBusy(true); setPlan(null); setPage(null);
    try {
      const current = await discoveryApi.plan(cidr);
      const first = await discoveryApi.batches(current.canonical_cidr);
      setPlan(current); setPage(first);
    } catch (exc) { setError(exc instanceof Error ? exc.message : "Unable to preview range"); }
    finally { setBusy(false); }
  };
  const move = async (offset: number) => {
    if (!plan) return;
    setBusy(true); setError(null);
    try { setPage(await discoveryApi.batches(plan.canonical_cidr, offset)); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "Unable to load range page"); }
    finally { setBusy(false); }
  };
  return <div className="space-y-4">
    <h1 className="text-xl font-semibold">Discovery range planner</h1>
    <p className="text-sm text-slate-400">
      Define IPv4 ranges from /9 to /32 without flooding the network. This page
      previews bounded /24 batches. It does not send probes or configure devices.
    </p>
    <Card title="1. Define discovery CIDR">
      <form onSubmit={makePlan} className="flex flex-wrap items-end gap-3">
        <label className="min-w-[240px] flex-1 text-sm">IPv4 CIDR (minimum prefix /9)
          <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm"
            value={cidr} onChange={e => setCidr(e.target.value)}
            required placeholder="10.0.0.0/9" />
        </label>
        <button className={button} disabled={busy}>Preview range</button>
      </form>
      <ErrorNote error={error}/>
      <p className="mt-3 text-xs text-slate-400">
        Example 10.0.0.0/9 is RFC1918; 128.0.0.0/9 is public IPv4 and cannot be scanned automatically.
      </p>
    </Card>
    {plan && <Card title="2. Review scope and safety">
      <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>Range: <b className="font-mono">{plan.canonical_cidr}</b></div>
        <div>Addresses: <b>{plan.address_count.toLocaleString()}</b></div>
        <div>Preview batches: <b>{plan.batch_count.toLocaleString()} × /{plan.batch_prefix}</b></div>
        <div>Range type: <b>{plan.rfc1918_scope ? "RFC1918 private" : "Public / non-RFC1918"}</b></div>
      </div>
      <p className="mt-3 text-sm text-amber-300">
        {plan.rfc1918_scope
          ? "Private range accepted for planning only. Any future scan requires explicit per-scope approval and rate limits."
          : "Definition only: public or reserved address space is not eligible for automatic discovery."}
      </p>
      <p className="mt-1 text-xs text-slate-400">Device writes: {plan.device_writes} · No scan started.</p>
    </Card>}
    {page && <Card title="3. Browse planned /24 batches">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {page.batches.map(batch => <div key={batch}
          className="rounded border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm">{batch}</div>)}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 text-sm">
        <button className="rounded border border-slate-700 px-3 py-2 disabled:opacity-40"
          disabled={busy || page.offset === 0} onClick={() => move(Math.max(0, page.offset - 16))}>Previous batches</button>
        <span className="text-slate-400">{(page.offset + 1).toLocaleString()}–{(page.offset + page.batches.length).toLocaleString()} of {page.batch_count.toLocaleString()}</span>
        <button className="rounded border border-slate-700 px-3 py-2 disabled:opacity-40"
          disabled={busy || page.next_offset === null} onClick={() => move(page.next_offset!)}>Next batches</button>
      </div>
      <p className="mt-3 text-xs text-slate-400">
        This is a preview, not a scan queue. For a single permitted private device, use
        <Link className="ml-1 text-sky-400 underline" to="/onboarding">Add device</Link>
        or <Link className="ml-1 text-sky-400 underline" to="/printers">Printer Intelligence</Link>.
      </p>
    </Card>}
  </div>;
}
