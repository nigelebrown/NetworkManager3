import { Fragment } from "react";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const label = (k: string) => k.replace(/_/g, " ");

/** Generic editor for a nested object of numbers (the experience scoring configuration). */
export default function ConfigEditor({ value, onChange, path = [] }: { value: Obj; onChange: (v: Obj) => void; path?: string[] }) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const nums = Object.entries(value).filter(([, v]) => !isObj(v));
  const groups = Object.entries(value).filter(([, v]) => isObj(v));
  return (
    <div className="space-y-3">
      {nums.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {nums.map(([k, v]) => (
            <label key={k} className="text-xs text-slate-400">{label(k)}
              <input type="number" step="any" value={String(v)} onChange={(e) => set(k, e.target.value === "" ? 0 : Number(e.target.value))}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100" /></label>
          ))}
        </div>
      )}
      {groups.map(([k, v]) => (
        <Fragment key={k}>
          <fieldset className="rounded-lg border border-slate-800 p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-slate-300">{[...path, k].map(label).join(" › ")}</legend>
            <ConfigEditor value={v as Obj} onChange={(nv) => set(k, nv)} path={[...path, k]} />
          </fieldset>
        </Fragment>
      ))}
    </div>
  );
}
