import { useCallback, useEffect, useRef, useState } from "react";
import { DATA_TOPICS, live } from "../services/live";

/** Load data on mount, re-query on live invalidations for `topics`, and poll every `refreshMs` as a fallback.
 *  While the live channel is connected the poll interval is stretched 4x (it only guards against a silent gap);
 *  when the channel drops, the original interval applies. Keeps last good data on transient errors. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], refreshMs = 30000, topics: string[] = DATA_TOPICS) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn); fnRef.current = fn;
  const last = useRef(0);
  const reload = useCallback(async () => {
    last.current = Date.now();
    try { setData(await fnRef.current()); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setLoading(false); }
  }, []);
  const topicKey = topics.join(",");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    setLoading(true); reload();
    const t = refreshMs ? setInterval(() => {
      if (live.state === "live" && Date.now() - last.current < refreshMs * 4) return;
      reload();
    }, refreshMs) : undefined;
    let debounce: number | undefined;
    const off = live.onTopics((changed) => {
      if (!changed.includes("*") && !changed.some((c) => topics.includes(c))) return;
      window.clearTimeout(debounce);
      debounce = window.setTimeout(reload, 300);
    });
    return () => { if (t) clearInterval(t); off(); window.clearTimeout(debounce); };
  }, [...deps, refreshMs, topicKey]);
  return { data, error, loading, reload };
}
