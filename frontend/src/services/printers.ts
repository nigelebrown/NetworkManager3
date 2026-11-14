import { request } from "./api";

export type Printer = {
  id: string; manufacturer: string; model: string; serial_number: string; hostname: string | null;
  management_ip: string | null; firmware: string | null; status: string; provenance: string;
  area: string | null; division: string | null; branch: string | null; station: string | null;
  section_unit: string | null; building: string | null; last_checked_at: string | null;
  created_at: string | null; capabilities: Record<string, string>;
};
export type Supply = { id: number; supply_name: string; colour: string; percent_remaining: number | null;
  observed_at: string; source: string; alert: string | null };
export type Meter = { id: number; meter_kind: string; counter_value: number; observed_at: string;
  source: string; quality: string };
export type CycleAnalysis = { cycle_id: string; quality: string; counter_delta?: number;
  impressions_during_cycle: number | null; currency: string; cost_per_1000_impressions: number | null;
  declared_yield_ratio_percent: number | null; measured_ink_ml_per_impression: number | null;
  pages_per_ml_added: number | null; warnings: string[] };
export type Cycle = { id: string; consumable_kind: string; colour: string; part_number: string | null;
  supply_brand: string | null; quality_class: string; start_meter: number; end_meter: number | null;
  status: string; currency: string; cost_amount: number | null; quantity_added_ml: number | null;
  declared_yield: number | null; analysis: CycleAnalysis };
export type Maintenance = { id: number; component: string; event_type: string; description: string;
  severity: string; observed_at: string; cost_amount: number | null; currency: string };
export type PrinterDetail = { device: Printer; meters: Meter[]; meter_deltas: Record<string, { delta: number | null; quality: string }>;
  supplies: Supply[]; cycles: Cycle[]; maintenance: Maintenance[] };
export type DiscoveredPrinter = {
  management_ip: string; hostname: string | null; system_description: string | null;
  meter: { meter_kind: string; counter_value: number } | null;
};
export type PrinterDashboard = { total: number; online: number; faulted: number; manufacturers: string[];
  low_supplies: { printer_id: string; hostname: string; supply: string; colour: string; percent_remaining: number;
    severity: string; source: string }[]; model_rankings: string };
export type PrinterRecommendation = { printer_id: string; condition: string; severity: string;
  recommended_action: string; engine: string; confidence: string; approval: string };
export type PrinterAnalysis = { engine: string; recommendations: PrinterRecommendation[];
  model_comparison: { model: string; verified_cycles: number;
    mean_consumable_cost_per_1000_impressions: number | null; quality: string }[]; disclaimer: string };

const path = (id: string) => `/printers/${encodeURIComponent(id)}`;

export const printerApi = {
  list: () => request<Printer[]>("/printers"),
  dashboard: () => request<PrinterDashboard>("/printers/dashboard"),
  add: (record: Record<string, unknown>) =>
    request<Printer>("/printers", { method: "POST", body: JSON.stringify(record) }),
  detail: (id: string) => request<PrinterDetail>(path(id)),
  meter: (id: string, b: Record<string, unknown>) =>
    request<{ id: number }>(`${path(id)}/meters`, { method: "POST", body: JSON.stringify(b) }),
  supply: (id: string, b: Record<string, unknown>) =>
    request<{ id: number; alert: string | null }>(`${path(id)}/supplies`, { method: "POST", body: JSON.stringify(b) }),
  openCycle: (id: string, b: Record<string, unknown>) =>
    request<Cycle>(`${path(id)}/cycles`, { method: "POST", body: JSON.stringify(b) }),
  closeCycle: (id: string, cycle: string, b: Record<string, unknown>) =>
    request<Cycle>(`${path(id)}/cycles/${encodeURIComponent(cycle)}/close`, { method: "POST", body: JSON.stringify(b) }),
  maintenance: (id: string, b: Record<string, unknown>) =>
    request<{ id: number }>(`${path(id)}/maintenance`, { method: "POST", body: JSON.stringify(b) }),
  analyse: () => request<PrinterAnalysis>("/printers/analyse-now", { method: "POST", body: "{}" }),
  discover: (b: Record<string, unknown>) =>
    request<{ scanned: number; printers: DiscoveredPrinter[] }>("/printers/discover-snmpv3", { method: "POST", body: JSON.stringify(b) }),
  probe: (b: Record<string, unknown>) => request<{
    hostname: string | null; system_description: string | null; system_object_id: string | null;
    meter: { meter_kind: string; counter_value: number } | null; device_writes: string;
  }>("/printers/probe-snmpv3", { method: "POST", body: JSON.stringify(b) }),
};
