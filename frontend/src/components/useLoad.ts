import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/api";
import { DATA_TOPICS, live } from "../services/live";

/** Coalesce live invalidations, serialize requests, and respect server backoff. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], refreshMs = 30000, topics: string[] = DATA_TOPICS) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn); fnRef.current = fn;
  const runRef = useRef<() => Promise<void>>(async () => {});
  const reload = useCallback(() => runRef.current(), []);
  const topicKey = topics.join(",");
  useEffect(() => {
    let active = true, inFlight = false, queued = false;
    let lastStarted = 0, backoffUntil = 0;
    let timer: number | undefined;
    const gap = Math.min(10000, Math.max(1000, refreshMs || 10000));
    const schedule = () => {
      if (!active || timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        void run();
      }, Math.max(0, lastStarted + gap - Date.now(), backoffUntil - Date.now()));
    };
    const run = async () => {
      if (!active) return;
      if (inFlight) { queued = true; return; }
      if (Date.now() < backoffUntil) { schedule(); return; }
      inFlight = true;
      lastStarted = Date.now();
      try {
        const result = await fnRef.current();
        if (active) { setData(result); setError(null); }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "Request failed");
          backoffUntil = Date.now() + (e instanceof ApiError && e.status === 429 ? e.retryAfterMs ?? 60000 : gap);
          schedule();
        }
      } finally {
        inFlight = false;
        if (active) {
          setLoading(false);
          if (queued) { queued = false; schedule(); }
        }
      }
    };
    runRef.current = run;
    setLoading(true);
    void run();
    const poll = refreshMs ? window.setInterval(() => {
      if (live.state === "live" && Date.now() - lastStarted < refreshMs * 4) return;
      schedule();
    }, refreshMs) : undefined;
    const off = refreshMs ? live.onTopics(changed => {
      if (changed.includes("*") || changed.some(c => topics.includes(c))) schedule();
    }) : () => {};
    return () => {
      active = false;
      if (poll !== undefined) window.clearInterval(poll);
      if (timer !== undefined) window.clearTimeout(timer);
      off();
      if (runRef.current === run) runRef.current = async () => {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, refreshMs, topicKey]);
  return { data, error, loading, reload };
}
