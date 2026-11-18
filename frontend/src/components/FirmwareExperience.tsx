import { useState } from "react";
import { Card } from "./ui";
import type { Summary } from "../services/api";

const value = (v: number | null | undefined, unit = "") => v == null ? "—" : `${Math.round(v * 10) / 10}${unit}`;

export default function FirmwareExperience({ summary }: { summary: Summary }) {
  const [model, setModel] = useState("all");
  const [band, setBand] = useState("all");
  const cohorts = summary.firmware_comparison ?? [];
  const rows = cohorts.filter(c => model === "all" || c.model === model).map(c => ({
    ...c, measured: band === "all" ? c : c.by_band.find(b => b.band === band),
  })).sort((a, b) => a.model.localeCompare(b.model) || (b.measured?.score ?? -1) - (a.measured?.score ?? -1));
  return <Card title="Firmware and observed user experience" right={<div className="flex flex-wrap gap-2">
    <select className="inm-form-control" aria-label="Compare AP model" value={model} onChange={e => setModel(e.target.value)}>
      <option value="all">All AP models</option>{[...new Set(cohorts.map(c => c.model))].sort().map(m => <option key={m}>{m}</option>)}
    </select>
    <select className="inm-form-control" aria-label="Compare wireless band" value={band} onChange={e => setBand(e.target.value)}>
      <option value="all">All bands</option><option value="5GHz">5 GHz</option><option value="2.4GHz">2.4 GHz</option>
    </select>
  </div>}>
    <p className="mb-3 text-sm text-slate-600">Compare measured experience for the same AP model and band. Results reflect current clients, site conditions and load. A higher score alone does not establish a firmware improvement.</p>
    <div className="overflow-x-auto"><table className="inm-table"><thead><tr>
      <th>AP model / firmware</th><th>Reporting APs</th><th>Clients</th><th>Experience</th><th>Good / fair / poor</th>
      <th>Scored coverage</th><th>Signal SNR</th><th>Link rate</th><th>CPU</th>
    </tr></thead><tbody>{rows.map(c => {
      const measured = c.measured;
      const healthBand = measured && ("experience_band" in measured ? measured.experience_band : measured.band);
      const scored = measured ? measured.good + measured.fair + measured.poor : 0;
      return <tr key={`${c.model}/${c.firmware}`}>
        <td><strong>{c.model}</strong><div className="text-xs text-slate-500">{c.firmware} · {c.sites} controller groups</div></td>
        <td>{c.reporting_aps} / {c.aps}</td><td>{measured?.clients ?? 0}</td>
        <td><strong className={healthBand === "poor" ? "text-red-700" : healthBand === "fair" ? "text-amber-700" : "text-slate-800"}>{value(measured?.score, " / 100")}</strong>
          {scored > 0 && scored < 10 && <div className="text-xs text-slate-500">Fewer than 10 scored clients</div>}</td>
        <td>{measured ? `${measured.good} / ${measured.fair} / ${measured.poor}` : "—"}</td>
        <td>{scored} / {measured?.clients ?? 0}</td><td>{band === "all" ? value(c.avg_snr_db, " dB") : "—"}</td>
        <td>{band === "all" ? value(c.avg_link_rate_mbps, " Mbps") : "—"}</td><td>{value(c.avg_cpu_percent, "%")}</td>
      </tr>;
    })}</tbody></table></div>
    {!rows.length && <p className="py-5 text-sm text-slate-500">Firmware comparison appears after AP and client observations are available.</p>}
    <p className="mt-3 text-xs text-slate-500">Snapshot of fresh client observations. Link rate is the AP-reported connection rate. Use a controlled before/after comparison to assess an upgrade.</p>
  </Card>;
}
