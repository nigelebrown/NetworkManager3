import { ApiError, getSession, request } from "./api";

export type IntelRec = { id: string; rule_id: string; rule_version: string; engine: string; rule: { title: string; version: number; link: string } | null;
  title: string; explanation: string; risk: string; source_type: string; state: string; evidence: Record<string, unknown>; reason_codes: string[];
  affected: Record<string, unknown>; device: { id: string; hostname: string | null; family: string } | null; site: string | null;
  created_at: string; last_seen_at: string; occurrences: number; freshness: string; reviewed_by: string | null; review_note: string | null;
  feedback: { decision: string; reason: string; operator: string } | null; outcomes: Record<string, unknown>[];
  evidence_links: { label: string; href: string }[] };
export type Rule = { rule_id: string; engine: string; title: string; description: string; inputs: string[]; condition: string; output: string;
  risk: string; precedence: number; params: Record<string, number>; default_params: Record<string, number>;
  schema: Record<string, { type: string; min: number; max: number }>; enabled: boolean; suppression_minutes: number; version: number;
  pending: { params: Record<string, number>; enabled: boolean; suppression_minutes: number } | null; pending_by: string | null;
  pending_note: string | null; updated_by: string; updated_at: string; triggered_total: number; open_findings: number;
  versions?: { version: number; params: Record<string, number>; enabled: boolean; suppression_minutes: number; proposed_by: string; approved_by: string; note: string; at: string }[];
  recent_findings?: { id: string; title: string; state: string; rule_version: string; created_at: string }[] };
export type Evaluation = { windows: number; state: string; reason?: string | null; base_rate?: number; brier?: number; brier_skill?: number | null;
  precision?: number | null; recall?: number | null; false_alert_rate?: number | null;
  calibration?: { range: string; n: number; mean_predicted: number; observed_rate: number }[] };
export type PredRow = { id: number; device_id: string; hostname: string | null; family: string; source_type: string; metric: string; threshold: number;
  window_start: string; window_end: string; state: string; probability: number | null; predicted_peak: number | null; confidence: string;
  features: Record<string, unknown> | null; reason: string | null; outcome: string | null; observed_peak: number | null;
  validation_state: string; label: string };
export type PredRun = { id: string; created_at: string; requested_by: string; model_version: string; horizon_hours: number; status: string;
  validation_state: string; summary: Record<string, any>; evaluation: Record<string, Evaluation> | null };  // eslint-disable-line @typescript-eslint/no-explicit-any
export type Stats = { n: number; avg: number | null; max: number | null; p95: number | null };
export type SiteRow = { site: string; devices: number; reporting: number; stale: number; no_data: number; samples: number; last_observed: string | null;
  metrics: Record<string, Stats>; alerts_opened: number; changes_requested: number };
export type DeviceRow = { device_id: string; hostname: string | null; family: string; model: string | null; source_type: string; site: string;
  samples: number; expected_samples: number | null; coverage: number | null; freshness: string; last_observed: string | null;
  metrics: Record<string, Stats>; alerts_opened: number; changes_requested: number };
export type Analytics = { window: string; generated_at: string; source: string; totals: Record<string, number>; sites: SiteRow[];
  reconciliation: { reconciled: boolean; site_sum_devices: number; inventory_devices_in_scope: number }; telemetry_store: string; notes: string[] };

const B = "/v1/intelligence";
const qs = (o: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") s.set(k, String(v)); });
  const t = s.toString(); return t ? `?${t}` : "";
};
const post = <T,>(p: string, b: unknown = {}) => request<T>(p, { method: "POST", body: JSON.stringify(b) });

export const intel = {
  recs: (f: Record<string, string | boolean | undefined>) => request<{ items: IntelRec[]; counts: Record<string, number>; policy: string }>(`${B}/recommendations${qs(f)}`),
  rec: (id: string) => request<IntelRec & { audit: { at: string; action: string; by: string; detail: string }[]; history: IntelRec[] }>(`${B}/recommendations/${id}`),
  triage: (id: string, action: string, note: string) => post(`${B}/recommendations/${id}/triage`, { action, note }),
  analyzeSites: () => post<{ created: number; updated?: number; suppressed?: number; ruleset_version: string }>("/v1/ai/analyze"),
  analyzeDevices: () => post<{ created: number; updated: number; suppressed: number; evaluated_devices: number }>("/v1/phase4/analyze"),
  rules: () => request<{ rules: Rule[]; precedence: string; governance: string }>(`${B}/rules`),
  rule: (id: string) => request<Rule>(`${B}/rules/${id}`),
  propose: (id: string, b: Record<string, unknown>) => post<Rule>(`${B}/rules/${id}/propose`, b),
  decide: (id: string, approve: boolean, note: string) => post<Rule>(`${B}/rules/${id}/decide`, { approve, note }),
  predictive: (state?: string) => request<{ run: PredRun | null; predictions: PredRow[]; model: string; statement: string; live_evaluation?: Evaluation }>(`${B}/predictive${qs({ state })}`),
  runPredictive: () => post<PredRun>(`${B}/predictive/run`),
  scorePredictive: () => post<{ scored: number; live_evaluation: Evaluation }>(`${B}/predictive/score`),
  analytics: (f: Record<string, string | undefined>) => request<Analytics>(`${B}/analytics${qs(f)}`),
  analyticsDevices: (f: Record<string, string | undefined>) => request<{ devices: DeviceRow[] }>(`${B}/analytics/devices${qs(f)}`),
  inventory: () => request<{ complete: boolean; workspaces: Record<string, { complete: boolean; items: { kind: string; method?: string; target: string; registered: boolean }[] }> }>(`${B}/inventory`),
  modelsCompare: () => request<{ models: any[]; active: string | null; baseline: string }>("/v1/ai/models/compare"),  // eslint-disable-line @typescript-eslint/no-explicit-any
  rollback: (id: string) => post<{ rolled_back: string; active: string | null; mode: string }>(`/v1/ai/models/${id}/rollback`),
};

export async function exportAnalytics(f: Record<string, string | undefined>, format: string) {
  const s = getSession();
  const r = await fetch(`/api${B}/analytics/export${qs({ ...f, format })}`, { headers: s ? { Authorization: `Bearer ${s.access_token}` } : {} });
  if (!r.ok) throw new ApiError(r.status, "Export failed");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = `analytics-${f.window ?? "24h"}.${format}`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
