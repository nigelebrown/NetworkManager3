// Typed client for the operations centre and phases 5–11 modules.
import { request } from "./api";

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const enc = encodeURIComponent;

export type Health = { score: number | null; band: "GOOD" | "FAIR" | "POOR" | "NO_DATA"; reasons: string[] };
export interface NocTile {
  device_id: string; hostname: string | null; site: string | null; family: string; model: string | null;
  firmware: string | null; status: string; source_type: "REAL" | "DEMO"; freshness: string; health: Health;
  metrics: Record<string, number | null>; last_failure: string | null;
  state: "ONLINE" | "DEGRADED" | "STALE" | "OFFLINE" | "UNKNOWN"; state_reason: string; last_persisted_at: string | null;
}
export interface Noc {
  generated_at: string; devices: NocTile[];
  summary: { devices: number; up: number; down: number; unknown: number; fresh: number; stale_or_gap: number;
    never_polled: number; clients: number; active_alerts: number; health_bands: Record<string, number>;
    states: Record<string, number> };
  collection_last_hour: Record<string, number>; worst_experience: NocTile[];
  events: { at: string; device_id: string | null; severity: string; message: string; config_change: boolean }[];
  alerts: { id: number; severity: string; message: string; created_at: string; simulated: boolean }[];
}
export interface RuleResult { id: string; category: string; severity: string; title: string;
  status: "PASS" | "FAIL" | "WARN" | "UNKNOWN" | "N/A"; evidence: string | null; why: string; fix: string }
export interface DeviceCompliance {
  device_id: string; hostname: string | null; family: string; model: string | null; source_type: string;
  score: number | null; grade: string; counts: Record<string, number>; critical_failures?: boolean;
  config_available: boolean; config_captured_at: string | null; config_integrity: string | null;
  results: RuleResult[]; intended_sources: { id: string; name: string; scope: string }[];
  intended_vs_installed: { status: string; differences: { field: string; intended: unknown; installed: unknown }[]; note?: string };
}
export interface FleetCompliance {
  fleet_score: number | null; devices_without_config: number; grades: Record<string, number>;
  failures_by_category: Record<string, number>; drift: Record<string, number>;
  top_failures: { id: string; title: string; severity: string; category: string; devices: number }[];
  devices: { device_id: string; hostname: string | null; family: string; model: string | null; source_type: string;
    site: string | null; score: number | null; grade: string; drift: string; critical_failures: boolean;
    config_available: boolean; config_captured_at: string | null }[];
}
export interface Trends {
  hourly: { hour: string; clients?: number; util_5g_percent?: number; cpu_percent?: number }[];
  changes_by_day: { day: string; plans: number; approved: number; rejected: number }[];
  attribution: Record<string, number>; recommendations: Record<string, number>; audit_events_24h: number;
}
export interface Executive {
  devices_real: number; devices_counted: number; sources_counted: string[]; by_site: Record<string, Record<string, number>>; by_model: Record<string, number>;
  poll_coverage_percent: number | null; poll_coverage_reason: string | null; devices_never_polled: number;
  recommendations: { total: number; reviewed: number; by_state: Record<string, number> };
  attribution: Record<string, number>; vault: { artifacts: number; restore_verified: number };
  kpis_not_measured: string[]; generated_at: string; generation_sha: string;
}
export interface Plan {
  id: string; device_id: string; hostname: string | null; source_type: string | null; adapter_key: string; metric_families: string[]; interval_seconds: number;
  state: string; next_due_at: string | null; last_success_at: string | null; last_failure_at: string | null;
  last_failure_category: string | null; consecutive_failures: number; freshness: string; credential_bound: boolean;
}
export interface Job { id: string; plan_id: string; device_id: string; metric_family: string; attempt: number;
  state: string; failure_category: string | null; points_written: number; started_at: string; retry_at: string | null }
export interface ChangePlan {
  id: string; device_id: string; title: string; operations: Record<string, unknown>[];
  capability_report: { op: string; capability: string }[]; state: string; requester: string;
  approver: string | null; approved_at: string | null; decision_note: string | null; revision: number;
  created_at: string; expected_config_hash: string | null; source?: string;
}
export interface Decision { id: string; kind: string; plan_id: string | null; device_id: string; decision: string;
  guards: { guard: string; pass: boolean }[]; denial_reason: string | null; evaluated_by: string; evaluated_at: string }
export interface Artifact { id: string; created_at: string; trigger: string; restore_state: string; fidelity: string;
  capture_source: string; plaintext_sha256: string; key_id: string; size: number; model: string | null;
  firmware: string | null; created_by: string }
export interface Attribution { id: string; device_id: string; change_at: string; level: string; actor: string | null;
  change_type: string; candidates: string[]; supersedes_id: string | null; note: string | null; current?: boolean;
  evidence: Record<string, unknown> }
export interface Timeline { device_id: string; attributions: Attribution[];
  log_events: { id: string; observed_at: string; severity: string; event_code: string | null; message: string;
    user: string | null; is_config_change: boolean; transport: string }[];
  coverage: { present: string[]; missing: string[]; complete: boolean } }
export interface OptState { device_id: string; mode: string; status: string; badge: string; display?: string;
  enrollment: Record<string, unknown> | null; global_write_lock: boolean; emergency_stop: boolean;
  native_vendor_optimization: string }
export interface LearningItem { id: string; created_at: string; device_id: string | null; title: string; why: string;
  kind: string; analyzer_version: string; evidence_status: string; observed_samples: number; source_type: string;
  state: string; feedback: { decision: string; reason: string; operator: string; at: string } | null;
  outcomes: Record<string, unknown>[]; change_occurred: boolean }
export interface Model { id: string; name: string; kind: string; algorithm: string; status: string;
  metrics: Record<string, unknown>; trained_by: string; trained_at: string; decided_by: string | null }
export interface Adapter { key: string; vendor: string; family: string; models: string[]; protocols: string[];
  certification: string; operations: Record<string, string>; notes: string }
export interface DemoStatus { loaded: boolean; loaded_at: string | null; by: string | null; removed_explicitly: boolean; demo_devices: number }
export interface Series { points: { t: string; v: number | null; q: string; src: string }[] }

export const ops = {
  noc: () => request<Noc>("/v1/noc/overview"),
  trends: () => request<Trends>("/v1/executive/trends"),
  executive: () => request<Executive>("/v1/reports/executive"),
  fleetCompliance: () => request<FleetCompliance>("/v1/compliance/fleet"),
  deviceCompliance: (id: string) => request<DeviceCompliance>(`/v1/compliance/devices/${enc(id)}`),
  series: (id: string, metric: string, hours = 24) => request<Series>(`/v1/devices/${enc(id)}/metrics/series?metric=${enc(metric)}&hours=${hours}`),
  experience: (id: string, window = "1h") => request<Record<string, unknown>>(`/v1/devices/${enc(id)}/experience?window=${window}`),
  demo: () => request<DemoStatus>("/v1/demo-data"),
  demoLoad: () => post<{ status: DemoStatus; created: Record<string, number> }>("/v1/demo-data/load"),
  demoRemove: () => post<{ status: DemoStatus; removed: Record<string, number> }>("/v1/demo-data/remove"),
  // collection
  plans: () => request<Plan[]>("/v1/collection/plans"),
  jobs: (plan?: string) => request<Job[]>(`/v1/collection/jobs${plan ? `?plan_id=${enc(plan)}` : ""}`),
  previewPlan: (b: Record<string, unknown>) => post<Record<string, unknown> & { warnings: string[] }>("/v1/collection/plans/preview", b),
  createPlan: (b: Record<string, unknown>) => post<Plan>("/v1/collection/plans", b),
  planAction: (id: string, a: "pause" | "resume" | "run-now") => post<Record<string, unknown>>(`/v1/collection/plans/${enc(id)}/${a}`),
  // change control
  changePlans: () => request<ChangePlan[]>("/v1/changes/plans"),
  createChange: (b: Record<string, unknown>) => post<ChangePlan>("/v1/changes/plans", b),
  decideChange: (id: string, a: "approve" | "reject", rev: number, note: string) =>
    post<ChangePlan>(`/v1/changes/plans/${enc(id)}/${a}`, { expected_revision: rev, note }),
  executeChange: (id: string) => post<{ decision: string; denial_reason: string | null; guards: Decision["guards"] }>(`/v1/changes/plans/${enc(id)}/execute`),
  decisions: () => request<Decision[]>("/v1/changes/decisions"),
  // vault
  versions: (id: string) => request<{ full_fidelity: Artifact[]; redacted_baselines: Record<string, unknown>[] }>(`/v1/devices/${enc(id)}/config-versions`),
  capture: (id: string, content: string, trigger = "MANUAL") => post<Artifact>(`/v1/vault/devices/${enc(id)}/artifacts`, { content, trigger }),
  verify: (id: string) => post<{ integrity: string; reason: string | null }>(`/v1/vault/artifacts/${enc(id)}/verify`),
  precheck: (id: string, device_id: string) => post<{ result: string; reasons: string[]; restore_state: string }>(`/v1/vault/artifacts/${enc(id)}/restore-validation`, { device_id }),
  diff: (a: string, b: string) => request<{ diff: string[] }>(`/v1/vault/artifacts/${enc(a)}/diff/${enc(b)}`),
  // attribution
  timeline: (id: string) => request<Timeline>(`/v1/devices/${enc(id)}/change-timeline`),
  drift: () => request<Attribution[]>("/v1/drift"),
  aaa: (b: Record<string, unknown>) => post<{ id: string }>("/v1/aaa/sessions", b),
  syslog: (b: Record<string, unknown>) => post<Record<string, number>>("/v1/syslog/ingest", b),
  correct: (id: string, b: Record<string, unknown>) => post<Attribution>(`/v1/attributions/${enc(id)}/correct`, b),
  // replacement
  replacements: () => request<Record<string, unknown>[]>("/v1/replacements"),
  planReplacement: (b: Record<string, unknown>) => post<Record<string, unknown>>("/v1/replacements/plan", b),
  // learning
  learning: () => request<{ items: LearningItem[]; active_model: Model | null; statement: string }>("/v1/ai/learning-history"),
  feedback: (recommendation_id: string, decision: string, reason: string) => post("/v1/ai/feedback", { recommendation_id, decision, reason }),
  outcome: (b: Record<string, unknown>) => post<Record<string, unknown>>("/v1/ai/outcomes", b),
  models: () => request<Model[]>("/v1/ai/models"),
  train: () => post<Record<string, unknown>>("/v1/ai/models/train"),
  decideModel: (id: string, a: "promote" | "reject") => post<Model>(`/v1/ai/models/${enc(id)}/${a}`),
  shadow: (b: Record<string, unknown>) => post<{ timeline: { at: string; op: string; result: string }[]; stopped: boolean; device_writes: number }>("/v1/ai/evaluation/shadow", b),
  // optimization (recommend + shadow only)
  optState: (id: string) => request<OptState>(`/v1/devices/${enc(id)}/optimization`),
  optEnroll: (id: string, b: Record<string, unknown>) => post<Record<string, unknown>>(`/v1/devices/${enc(id)}/optimization/enroll`, b),
  optApprove: (eid: string) => post<Record<string, unknown>>(`/v1/optimization/enrollments/${enc(eid)}/approve`),
  optDisable: (id: string) => post<OptState>(`/v1/devices/${enc(id)}/optimization/disable`),
  optEvaluate: (id: string, op: Record<string, unknown>) => post<{ decision: string; denial_reason: string | null; guard_evaluation: Decision["guards"] }>(`/v1/devices/${enc(id)}/optimization/evaluate`, { op }),
  optEnrollments: () => request<Record<string, unknown>[]>("/v1/optimization/enrollments"),
  optControl: () => request<{ emergency_stop: boolean; global_write_lock: boolean; reason: string | null; changed_by: string | null; automatic_optimization_feature?: boolean }>("/v1/optimization/control"),
  emergencyStop: (active: boolean, reason: string) => post("/v1/optimization/emergency-stop", { active, reason }),
  // platform
  adapters: () => request<{ adapters: Adapter[]; operations: string[]; write_policy: string }>("/v1/adapters"),
  deviceAdapter: (id: string) => request<{ adapter: Adapter; operations: { op: string; status: string; reason?: string }[] }>(`/v1/devices/${enc(id)}/adapter`),
  topology: () => request<{ deployment: string; roles: { role: string; env: string; configured: boolean; host?: string; port?: number | null; tls?: boolean; co_located?: boolean }[] }>("/v1/topology/roles"),
  exportReport: (b: Record<string, unknown>) => post<{ body: string; sha256: string; signature: string; meta: Record<string, unknown>; row_count: number }>("/v1/reports/exports", b),
  rules: () => request<{ id: string; family: string; category: string; severity: string; title: string; why: string; fix: string }[]>("/v1/compliance/rules"),
  phase4: () => request<{ devices: Record<string, unknown>[]; device_count: number }>("/v1/phase4/overview"),
  phase4Analyze: () => post<{ created: number; evaluated_devices: number }>("/v1/phase4/analyze"),
  phase4Recs: () => request<Record<string, unknown>[]>("/v1/phase4/recommendations"),
  phase4Review: (id: string, action: "ACKNOWLEDGED" | "DISMISSED") => post(`/v1/phase4/recommendations/${enc(id)}/review`, { action }),
};

export interface Port { name: string; admin: string | null; link: string; speed_bps: number | null; duplex: string | null;
  vlan_mode: string | null; vlan: number | null; description: string; rx_errors: number | null; tx_errors: number | null;
  poe_w: number | null; poe_status: string | null; lldp: { system_name: string; port: string } | null;
  rates?: { rx_bps: number | null; tx_bps: number | null; util_percent: number | null } | null }
export interface Ports { device_id: string; collected_at: string | null; environment?: string; ports: Port[];
  hardware: { cpu_percent?: number | null; memory_percent?: number | null; temp_max_c?: number | null; fans_total?: number;
    fans_failed?: number; psu_total?: number; psu_failed?: number }; note?: string }
export interface Cert { id: string; adapter_key: string; model_family: string; firmware_train: string; operation: string;
  status: string; environment: string; run_id: string; device_id: string; evidence: Record<string, unknown>;
  tested_by: string; tested_at: string; approved_by: string | null }
export interface Step { step: string; ok: boolean; at: string; op?: Record<string, unknown>; sent?: unknown; readback?: unknown;
  category?: string; message?: string; artifact?: string; authority?: string; detail?: string; matches_pre_change?: boolean }
export interface ExecResult { decision: string; outcome?: string; steps?: Step[]; denial_reason?: string | null;
  device_writes: number; environment?: string; guards?: { guard: string; pass: boolean }[] }
export interface InmAlert { id: string; device_id: string; rule_id: string; severity: string; message: string; value: number | null; entity?: string | null; correlation_id?: string | null;
  threshold: number | null; state: string; suppressed: boolean; opened_at: string; last_seen_at: string; resolved_at: string | null;
  acked_by: string | null; source_type: string }

export interface DeviceOriginLog {
  id: string; source: string; timestamp: string | null; level: string | null;
  actor: string | null; action: string | null; message: string;
}
export interface DeviceOriginLogs {
  device_id: string; environment: string; kind: string; count: number;
  hardware_verified: boolean; records: DeviceOriginLog[];
}
export interface DeviceDiagnostic {
  device_id: string; environment: string; kind: string; command: string; source: string;
  output: string; hardware_verified: boolean; device_writes: number;
}

export const rw = {
  render: (id: string) => request<{ environment: string; operations: { op: Record<string, unknown>; requests?: unknown[]; error?: string }[];
    certification: Record<string, string> }>(`/v1/changes/plans/${enc(id)}/render`),
  executions: (id: string) => request<{ id: string; outcome: string; environment: string; steps: Step[]; started_at: string }[]>(`/v1/changes/plans/${enc(id)}/executions`),
  execute: (id: string) => post<ExecResult>(`/v1/changes/plans/${enc(id)}/execute`),
  certs: () => request<Cert[]>("/v1/certification"),
  certify: (b: Record<string, unknown>) => post<{ run_id: string; environment: string; model_family: string; firmware_train: string;
    results: { operation: string; passed: boolean; evidence: Record<string, unknown> }[] }>("/v1/certification/run", b),
  decideCert: (id: string, action: "approve" | "revoke") => post<Cert>(`/v1/certification/${enc(id)}/${action}`),
  deviceCert: (id: string) => request<{ environment: string; model_family: string; firmware_train: string; operations: Record<string, string> }>(`/v1/devices/${enc(id)}/certification`),
  ports: (id: string) => request<Ports>(`/v1/devices/${enc(id)}/ports`),
  deviceLogs: (id: string, kind: "audit" | "event" | "system" | "security" | "user", limit = 50) =>
    request<DeviceOriginLogs>(`/v1/devices/${enc(id)}/device-logs?kind=${enc(kind)}&limit=${limit}`),
  diagnostics: (id: string, kind: string) =>
    request<DeviceDiagnostic>(`/v1/devices/${enc(id)}/diagnostics?kind=${enc(kind)}`),
  authority: (id: string) => request<{ authority: string; detail: string; environment: string }>(`/v1/devices/${enc(id)}/authority`),
  scanKey: (id: string) => post<{ fingerprint?: string; sha256?: string; environment: string }>(`/v1/devices/${enc(id)}/ssh-key/scan`),
  pinKey: (id: string, fingerprint: string, vc_host?: string) => post(`/v1/devices/${enc(id)}/ssh-key/pin`, { fingerprint, vc_host: vc_host || null }),
  captureDevice: (id: string) => post<{ artifact_id: string; sha256: string; size: number; environment: string }>(`/v1/vault/devices/${enc(id)}/capture-from-device`),
  schedule: (id: string, interval_hours: number) => post(`/v1/vault/devices/${enc(id)}/schedule`, { interval_hours }),
  schedules: () => request<{ device_id: string; interval_hours: number; next_due_at: string; last_run_at: string | null; last_result: string | null }[]>("/v1/vault/schedules"),
  restorePlan: (artifact: string, target_device_id?: string) => post<{ plan_id: string }>(`/v1/vault/artifacts/${enc(artifact)}/restore-plan`, { target_device_id: target_device_id || null }),
  replacementRestore: (id: string) => post<{ plan_id: string }>(`/v1/replacements/${enc(id)}/restore-plan`),
  deployPlan: (id: string) => post<{ in_compliance: boolean; plan_id: string | null; operations?: Record<string, unknown>[] }>(`/v1/config-templates/devices/${enc(id)}/deploy-plan`),
  alerts: (state = "OPEN") => request<InmAlert[]>(`/v1/alerts?state=${state}`),
  ack: (id: string) => post<InmAlert>(`/v1/alerts/${enc(id)}/ack`),
  alertRules: () => request<{ catalogue: { id: string; family: string; metric: string; threshold: number; severity: string; title: string }[];
    policies: { scope: string; rule_id: string; threshold: number; severity: string; enabled: boolean }[] }>("/v1/alert-rules"),
  setRule: (rule: string, b: Record<string, unknown>) => request(`/v1/alert-rules/${enc(rule)}`, { method: "PUT", body: JSON.stringify(b) }),
  windows: () => request<{ id: string; site_id: string | null; device_id: string | null; starts_at: string; ends_at: string; reason: string; created_by: string }[]>("/v1/maintenance-windows"),
  addWindow: (b: Record<string, unknown>) => post("/v1/maintenance-windows", b),
  optimizerRun: () => post<Record<string, number>>("/v1/optimization/run-now"),
  sitePolicies: () => request<{ site_id: string; allowed_channels_5g: number[]; allowed_widths_mhz: number[]; min_tx_dbm: number; max_tx_dbm: number }[]>("/v1/optimization/site-policies"),
  setSitePolicy: (site: string, b: Record<string, unknown>) => request(`/v1/optimization/site-policies/${enc(site)}`, { method: "PUT", body: JSON.stringify(b) }),
};

export function download(name: string, body: string, type = "text/csv") {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
