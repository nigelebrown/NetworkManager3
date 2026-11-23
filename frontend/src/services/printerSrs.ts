import { ApiError, getSession, request } from "./api";

export type Mon = { enabled: boolean; reachability: string; snmp_state: string; operational_state: string;
  health_score: number | null; health_status: string | null; last_success_at: string | null; freshness: string | null;
  discovery_confidence: string };
export type PrinterRow = { id: string; manufacturer: string; model: string; serial_number: string; hostname: string | null;
  management_ip: string | null; firmware: string | null; status: string; provenance: string; station: string | null;
  building: string | null; division: string | null; area: string | null; branch: string | null; section_unit: string | null;
  site_id: number | null; site: string | null; identity_state: string; monitoring: Mon; capabilities: Record<string, string> };
export type LiveRow = PrinterRow & { active_faults: { code: string; category: string; severity: string; since: string }[];
  supplies: { supply: string; colour: string; percent: number | null; status: string; level_state: string }[];
  next_poll_at: string | null; last_error: string | null; consecutive_failures: number };
export type Ops = { monitored: number; queue_depth: number; leased: number; fresh: number; stale: number; never_polled: number;
  in_backoff: number; poll_success_rate: number | null; worker_errors: { printer_id: string; error: string; consecutive_failures: number }[];
  jobs: Record<string, number> };
export type Overview = { total: number; monitored: number; reachable: number; unreachable: number;
  health: { median: number | null; with_evidence: number; by_status: Record<string, number> };
  open_faults: { category: string; severity: string; count: number }[]; supplies: Record<string, number>;
  recommendations_pending: number; by_manufacturer: Record<string, number>;
  collection: { queue_depth: number; fresh: number; stale: number; never_polled: number; in_backoff: number; poll_success_rate: number | null } };
export type Bucket = { bucket_start: string; bucket_end: string; volume: number | null; coverage: number; full_bucket_coverage: number;
  measured_zero: boolean; estimated_full_volume: number | null; confidence: string; includes_gap_spread: boolean; excluded_intervals: number };
export type Utilization = { meter_kind: string | null; status?: string; reason?: string; granularity: string; buckets: Bucket[];
  summary: { total: number | null; buckets: number; covered_buckets: number; missing_buckets: number; mean_coverage: number;
    average_per_covered_bucket: number | null; confidence: string };
  interval_quality: Record<string, number>; attribution: string; available_meters: string[];
  averages?: { per_day: number | null; per_month: number | null; basis: string };
  insights: { duty_cycle: { status: string; reason?: string; mean_recommended_percent?: number; months_evaluated: number;
    monthly?: { month: string; volume: number; recommended_percent: number; max_duty_percent: number | null; coverage: number }[] };
    anomalies: { day: string; volume: number; median: number; robust_z: number; kind: string }[]; model_verified: boolean } };
export type Capability = { capability: string; state: string; evidence: string; observed_at: string | null };
export type Sample = { metric_key: string; instance: string; oid: string | null; raw_value: string | null; normalized_value: number | null;
  text_value: string | null; unit: string | null; value_state: string; interpretation: string | null; source: string;
  profile: string | null; profile_version: number | null; observed_at: string };
export type Consumable = { supply_index: string; supply_type: string; colour: string; description: string | null; part_number: string | null;
  unit: string | null; max_capacity: number | null; level: number | null; level_state: string; percent_remaining: number | null;
  status: string; installed_at: string | null; installed_meter: number | null; last_observed_at: string | null };
export type SupplyEvent = { id: number; supply_index: string; supply_type: string; colour: string; event_type: string; status: string;
  level_before: number | null; level_after: number | null; meter_value: number | null; cost_amount: number | null; currency: string;
  confirmed_by: string | null; note: string | null; observed_at: string };
export type Forecast = { supply: string; supply_type: string; colour: string; status: string; days_to_empty: number | null;
  days_to_empty_low?: number | null; days_to_empty_high?: number | null; confidence?: string; expected_empty_date?: string;
  points?: number; reason?: string };
export type Yield = { supply: string; supply_type: string; colour: string; installed_at: string; removed_at: string;
  measured_yield: number; cost_amount: number | null; currency: string; cost_per_1000: number | null; basis: string };
export type ConsumablesView = { current: Consumable[]; events: SupplyEvent[]; measured_yields: Yield[]; forecasts: Forecast[];
  model_costs: { supply_type: string; colour: string; unit_cost: number; currency: string; declared_yield: number | null; verified: boolean }[] };
export type FaultsView = { faults: { id: number; code: string; category: string; severity: string; description: string;
    started_at: string; ended_at: string | null; duration_minutes: number; active: boolean; service_ticket_ref: string | null;
    recurrences_in_period: number }[];
  state_history: { dimension: string; state: string; started_at: string; ended_at: string | null; duration_minutes: number }[];
  availability_7d: number | null; recurring: Record<string, number>;
  maintenance: Maint[]; reliability: Reliability };
export type Maint = { id: number; printer_id: string; component: string; event_type: string; description: string; observed_at: string;
  incident_type: string | null; cause_category: string; qualifying_failure: boolean; reported_at: string | null; restored_at: string | null;
  technician: string | null; vendor: string | null; cost_amount: number | null; currency: string; service_ticket_ref: string | null; source: string };
export type Reliability = { operating_hours: number; qualifying_failures: number; excluded_non_mechanical: number; mtbf_hours: number | null;
  mtbf_note?: string | null; mttr_hours: number | null; incidents_per_100k_impressions: number | null; status: string };
export type Cohort = { manufacturer: string; model: string; segment: { colour: boolean | null; multifunction: boolean | null; technology: string | null };
  in_service: number; sample: { devices: number; device_months: number }; completeness: number; total_monthly_volume: number | null;
  median_monthly_volume_per_device: number | null; median_recommended_utilization_percent: number | null; median_measured_yield: number | null;
  median_cost_per_1000: number | null; incidents: number; incidents_per_100k_impressions: number | null; median_mtbf_hours: number | null;
  median_mttr_hours: number | null; tco_per_1000: number | null; tco_status: string; workload_band: string; evidence: string;
  confidence: string; suitability: string | null };
export type Rec = { id: string; code: string; title: string; printer_id: string | null; model: string | null; reason_codes: string[];
  evidence: Record<string, unknown>; assumptions: string[]; period_start: string | null; period_end: string | null; confidence: string;
  rule_version: string; status: string; created_at: string; reviewed_by: string | null; decision_note: string | null };
export type Job = { id: string; kind: string; status: string; params: Record<string, unknown>; progress: number; total: number;
  result: Record<string, any> | null; errors: { address?: string; error: string }[] | string[] | null; requested_by: string;  // eslint-disable-line @typescript-eslint/no-explicit-any
  created_at: string; finished_at: string | null; artifact_name: string | null; download: string | null };
export type MibModule = { id: string; module_name: string; version: number; filename: string; status: string; unresolved: string[] | null;
  object_count: number; enterprise: number | null; vendor: string | null; uploaded_by: string; uploaded_at: string; active: boolean };
export type MibObject = { id: number; name: string; oid: string | null; parent: string | null; kind: string; syntax: string | null;
  access: string | null; units: string | null; description: string; suggested_metric: string | null };
export type MetricDef = { metric_key: string; oid: string; access: string; value_type: string; unit?: string | null; category: string;
  verification: string; description?: string | null; interval_seconds?: number | null };
export type Profile = { id: string; key: string; name: string; vendor: string | null; match_enterprise: number | null;
  match_descr_regex: string | null; priority: number; builtin: boolean; enabled: boolean; version: number; printers: number;
  metrics: (MetricDef & { id: number })[] };
export type Model = { id: string; manufacturer: string; model: string; technology: string; colour_capable: boolean | null;
  multifunction: boolean | null; recommended_monthly_volume: number | null; max_monthly_duty_cycle: number | null;
  verification: string; in_service: number };

const P = "/printers";
const j = (b: unknown) => ({ method: "POST", body: JSON.stringify(b) });
const q = (o: Record<string, string | number | boolean | null | undefined>) => {
  const s = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== "" && v != null) s.set(k, String(v)); });
  const t = s.toString(); return t ? `?${t}` : "";
};
const id = (x: string) => `${P}/${encodeURIComponent(x)}`;

export type PrinterChange = { id: string; state: string; operation: string; before: string; after: string;
  created_at: string; created_by: string; finished_at?: string; result?: { readback_verified?: boolean; restored?: boolean } | null };
export type PrinterConfiguration = { hardware_verified: boolean; writes_enabled: boolean; current_location: string | null;
  read_error: string | null; history: PrinterChange[]; available: string[] };

export const prn = {
  discoveryJobs: () => request<{ id: string; status: string; target: string; created_at: string; progress: number; total: number; found: number }[]>(`${P}/discovery/jobs`),
  configuration: (p: string) => request<PrinterConfiguration>(`${id(p)}/configuration`),
  previewLocation: (p: string, location: string) => request<PrinterChange>(`${id(p)}/configuration/preview`, j({ location })),
  applyConfiguration: (p: string, change: string) => request<PrinterChange>(`${id(p)}/configuration/${encodeURIComponent(change)}/apply`, j({})),
  rollbackConfiguration: (p: string, change: string) => request<PrinterChange>(`${id(p)}/configuration/${encodeURIComponent(change)}/rollback`, j({})),
  list: (f: Record<string, string | number | boolean | undefined> = {}) => request<PrinterRow[]>(`${P}${q(f)}`),
  overview: () => request<Overview>(`${P}/overview`),
  live: () => request<{ printers: LiveRow[]; operations: Ops; observed_at: string }>(`${P}/live`),
  detail: (p: string) => request<any>(id(p)),  // eslint-disable-line @typescript-eslint/no-explicit-any
  capabilities: (p: string) => request<{ capabilities: Capability[] }>(`${id(p)}/capabilities`),
  metrics: (p: string, f: Record<string, string | undefined>) => request<{ samples: Sample[] }>(`${id(p)}/metrics${q(f)}`),
  utilization: (p: string, f: Record<string, string | undefined>) => request<Utilization>(`${id(p)}/utilization${q(f)}`),
  consumables: (p: string) => request<ConsumablesView>(`${id(p)}/consumables`),
  faults: (p: string, days = 90) => request<FaultsView>(`${id(p)}/faults?days=${days}`),
  setMonitoring: (p: string, b: Record<string, unknown>) => request<{ warnings: string[] }>(`${id(p)}/monitoring`, { method: "PUT", body: JSON.stringify(b) }),
  poll: (p: string) => request<Job>(`${id(p)}/poll`, j({})),
  walk: (p: string, root: string, max_rows: number) => request<Job>(`${id(p)}/diagnostic-walk`, j({ root, max_rows })),
  organization: (p: string, b: Record<string, unknown>) => request(`${id(p)}/organization`, { method: "PATCH", body: JSON.stringify(b) }),
  decideIdentity: (p: string, c: number, decision: string, note: string) => request(`${id(p)}/identity-changes/${c}/decide`, j({ decision, note })),
  decideSupply: (p: string, e: number, b: Record<string, unknown>) => request(`${id(p)}/supply-events/${e}/decide`, j(b)),
  addMaintenance: (p: string, b: Record<string, unknown>) => request<{ id: number; linked_faults: number }>(`${id(p)}/maintenance-records`, j(b)),
  job: (x: string) => request<Job>(`${P}/jobs/${encodeURIComponent(x)}`),
  cancelJob: (x: string) => request<Job>(`${P}/jobs/${encodeURIComponent(x)}/cancel`, j({})),
  discover: (b: Record<string, unknown>) => request<Job & { warnings: string[] }>(`${P}/discovery`, j(b)),
  approve: (jobId: string, b: Record<string, unknown>) => request<{ created: string[]; updated: string[] }>(`${P}/discovery/${jobId}/approve`, j(b)),
  importPreview: (text: string) => { const f = new FormData(); f.set("text", text); return request<any>(`${P}/import/preview`, { method: "POST", body: f }); },  // eslint-disable-line @typescript-eslint/no-explicit-any
  importCommit: (text: string, rows: string, enable: boolean, cred: string) => {
    const f = new FormData(); f.set("text", text); f.set("rows", rows); f.set("enable_monitoring", String(enable));
    if (cred) f.set("credential_profile_id", cred);
    return request<{ imported: { row: number }[]; skipped: { row: number; errors: string[] }[] }>(`${P}/import/commit`, { method: "POST", body: f });
  },
  serviceDesk: (text: string) => { const f = new FormData(); f.set("text", text); return request<{ created: number; skipped: unknown[] }>(`${P}/maintenance/import-service-desk`, { method: "POST", body: f }); },
  compare: (f: Record<string, string | undefined>) => request<{ cohorts: Cohort[]; method: string; disclaimer: string; period: { start: string; end: string } }>(`${P}/models/compare${q(f)}`),
  models: () => request<Model[]>(`${P}/models`),
  addModel: (b: Record<string, unknown>) => request<Model>(`${P}/models`, j(b)),
  updateModel: (m: string, b: Record<string, unknown>) => request<Model>(`${P}/models/${m}`, { method: "PATCH", body: JSON.stringify(b) }),
  addCost: (m: string, b: Record<string, unknown>) => request(`${P}/models/${m}/costs`, j(b)),
  recs: (status?: string) => request<{ items: Rec[]; rule_version: string; policy: string }>(`${P}/recommendations${q({ status })}`),
  runRecs: () => request<{ created: number; updated: number; superseded: number }>(`${P}/recommendations/run`, j({})),
  review: (r: string, decision: string, note: string) => request(`${P}/recommendations/${r}/review`, j({ decision, note })),
  reports: () => request<{ reports: { key: string; title: string }[]; formats: string[]; jobs: Job[] }>(`${P}/reports`),
  report: (b: Record<string, unknown>) => request<Job>(`${P}/reports`, j(b)),
  schedules: () => request<any[]>(`${P}/reports/schedules`),  // eslint-disable-line @typescript-eslint/no-explicit-any
  schedule: (b: Record<string, unknown>) => request(`${P}/reports/schedules`, j(b)),
  unschedule: (s: string) => request(`${P}/reports/schedules/${s}`, { method: "DELETE" }),
  mibs: () => request<MibModule[]>(`${P}/mibs`),
  uploadMib: (file: File) => { const f = new FormData(); f.set("file", file); return request<MibModule>(`${P}/mibs`, { method: "POST", body: f }); },
  mibObjects: (m: string, f: Record<string, string | boolean | undefined>) => request<MibObject[]>(`${P}/mibs/${m}/objects${q(f)}`),
  draft: (ids: number[]) => request<{ metrics: (MetricDef & { object: string; error?: string })[]; note: string }>(`${P}/mibs/profile-draft`, j({ object_ids: ids })),
  profiles: () => request<Profile[]>(`${P}/mib-profiles`),
  createProfile: (b: Record<string, unknown>) => request<Profile>(`${P}/mib-profiles`, j(b)),
  toggleProfile: (p: string, enabled: boolean) => request<Profile>(`${P}/mib-profiles/${p}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  testProfile: (p: string, printer_id: string) => request<any>(`${P}/mib-profiles/${p}/test`, j({ printer_id })),  // eslint-disable-line @typescript-eslint/no-explicit-any
  settings: () => request<any>(`${P}/settings`),  // eslint-disable-line @typescript-eslint/no-explicit-any
  saveSettings: (b: Record<string, unknown>) => request(`${P}/settings`, { method: "PUT", body: JSON.stringify(b) }),
  credentialProfiles: () => request<{ id: string; name: string; version: string }[]>(`${P}/credential-profiles`),
};

/** Wait for an asynchronous printer job to finish (poll every second, up to `timeoutMs`). */
export async function waitJob(jobId: string, timeoutMs = 120000): Promise<Job> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const job = await prn.job(jobId);
    if (!["QUEUED", "RUNNING"].includes(job.status) || Date.now() > end) return job;
    await new Promise(r => setTimeout(r, 1000));
  }
}

/** Authenticated file download (reports). */
export async function download(path: string, name: string) {
  const s = getSession();
  const r = await fetch(`/api${path.replace(/^\/api/, "")}`, { headers: s ? { Authorization: `Bearer ${s.access_token}` } : {} });
  if (!r.ok) throw new ApiError(r.status, "Download failed");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
