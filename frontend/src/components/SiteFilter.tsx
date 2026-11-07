import { api } from "../services/api";
import { useLoad } from "./useLoad";

export default function SiteFilter({ value, onChange }: { value?: number; onChange: (v?: number) => void }) {
  const { data } = useLoad(() => api.sites(), [], 0);
  return (
    <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)} aria-label="Filter by site">
      <option value="">All sites</option>
      {data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select>
  );
}
