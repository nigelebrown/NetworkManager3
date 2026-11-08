import { request } from "./api";

export type RangePlan = {
  requested_cidr: string;
  canonical_cidr: string;
  start_address: string;
  last_address: string;
  prefix_length: number;
  address_count: number;
  batch_prefix: number;
  batch_count: number;
  max_batches_per_page: number;
  rfc1918_scope: boolean;
  execution_status: "APPROVAL_REQUIRED" | "DEFINITION_ONLY_PUBLIC_OR_RESERVED";
  scan_started: false;
  device_writes: "LOCKED";
  notes: string[];
};
export type RangeBatches = {
  canonical_cidr: string;
  batch_count: number;
  batch_prefix: number;
  offset: number;
  next_offset: number | null;
  batches: string[];
  scan_started: false;
  rfc1918_scope: boolean;
  execution_status: string;
};
export const discoveryApi = {
  plan: (cidr: string) => request<RangePlan>("/v1/discovery/ranges/plan", {
    method: "POST", body: JSON.stringify({ cidr }),
  }),
  batches: (cidr: string, offset = 0, limit = 16) => request<RangeBatches>(
    "/v1/discovery/ranges/batches?cidr=" + encodeURIComponent(cidr) + "&offset=" + offset + "&limit=" + limit
  ),
};
