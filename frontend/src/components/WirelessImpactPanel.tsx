import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote } from "./ui";
import { useLoad } from "./useLoad";
import { request, getSession } from "../services/api";
import { btn, btn2 } from "./viz";

type AP={id:string;name:string;ip:string|null;location:string|null;model:string;arm_freshness:string};
type Group={key:string;controller:string;port:number;sites:string[];aps:AP[];fresh_aps:number;operations:Record<string,unknown>[];unsupported_changes:Record<string,unknown>;blockers:string[];scope_note:string};
type Window={samples:number;span_minutes:number;link_score:number|null;poor_percent:number|null;snr_db:number|null;rssi_dbm:number|null;mean_clients:number|null;client_measurement_coverage:number|null;arm:{util_percent:number|null;quality_percent:number|null;retry_percent:number|null;coverage:number;repeated_coverage:number};inferred_events:{disconnect:number;roam:number};intelligence_findings_seen:number};
type ClusterReview={group:Group;state:string;before:Window;after:Window|null;comparison:{state:string;reasons?:string[];link_score_delta?:number;poor_client_percentage_point_delta?:number}|null};
type Review={id:string;created_at:string;clusters:ClusterReview[]};
const root="/api/v1/intelligence/wireless-impact";
const value=(v:number|null|undefined,suffix="")=>v==null?"unavailable":String(Math.round(v*100)/100)+suffix;
const percent=(v:number|null|undefined)=>v==null?"unavailable":value(v*100,"%");
export default function WirelessImpactPanel(){
 const data=useLoad(()=>request<{groups:Group[]}>(root),[],60000);
 const reviews=useLoad(()=>request<{items:{id:string;created_at:string;clusters:number}[]}>(root+"/reviews"),[],60000);
 const [selected,setSelected]=useState<string[]>([]);
 const [reviewId,setReviewId]=useState("");
 const review=useLoad(()=>reviewId?request<Review>(root+"/reviews/"+reviewId):Promise.resolve(null),[reviewId],60000);
 const [error,setError]=useState<string|null>(null),[busy,setBusy]=useState(false);
 const me=getSession(),canCapture=me?.role==="admin"||me?.role==="operator";
 async function capture(){
  setBusy(true);setError(null);
  try{
   const r=await request<Review>(root+"/reviews",{method:"POST",body:JSON.stringify({cluster_keys:selected,window_minutes:30})});
   setReviewId(r.id);reviews.reload();
  }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
 }
 return <Card title="ARM + client experience — change impact">
  <p className="text-sm">Review where a change would apply and freeze measured evidence before an approved change. This panel never configures a device. After results require an exact, verified HARDWARE execution, a 10-minute settling period and a 30-minute observation window.</p>
  <p className="mt-2 text-sm text-slate-500">The configurable wireless-link assessment is partial. PHY speed is not throughput; radio retries are not per-client retries. DHCP, DNS, application latency, loss and throughput remain unmeasured until suitable telemetry exists. Native ClientMatch actions and inferred roams are different evidence.</p>
  <ErrorNote error={error||data.error||reviews.error||review.error}/>
  <div className="my-3 flex flex-wrap gap-2">
   <button className={btn2} onClick={()=>{data.reload();reviews.reload();review.reload();}}>Refresh impact evidence</button>
   {canCapture&&<button className={btn} disabled={busy||!selected.length||selected.length>5} onClick={capture}>Freeze before evidence ({selected.length}/5 clusters)</button>}
   <select aria-label="Impact review" value={reviewId} onChange={e=>setReviewId(e.target.value)} className="rounded border p-2">
    <option value="">Choose a saved impact review</option>
    {(reviews.data?.items??[]).map(r=><option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {r.clusters} cluster(s)</option>)}
   </select>
  </div>
  <p className="mb-3 text-xs text-slate-500">Freeze within ten minutes before the approved execution; capture again if it expires. Capture does not approve a plan or hardware certification. Comparable windows need repeated ARM readings, at least three client samples spanning 15 minutes, adequate measurement coverage and comparable client load.</p>
  <div className="max-h-96 overflow-auto"><table className="w-full text-sm"><thead><tr className="text-left"><th>Select</th><th>Application scope</th><th>APs / ARM coverage</th><th>Proposed settings and prerequisites</th></tr></thead><tbody>
   {(data.data?.groups??[]).map(g=><tr key={g.key} className="border-t align-top">
    <td><input aria-label={"Select "+g.controller} type="checkbox" checked={selected.includes(g.key)} disabled={!canCapture} onChange={e=>setSelected(s=>e.target.checked?[...s,g.key]:s.filter(k=>k!==g.key))}/></td>
    <td className="p-2"><b>{g.sites.join(" / ")}</b><div>{g.controller}:{g.port}</div><details><summary>Locations and APs</summary>{g.aps.map(a=><div key={a.id} className="py-1"><Link className="inm-link" to={"/devices/"+a.id+"/360"}>{a.name}</Link> · {a.ip} · {a.location??"physical location not assigned"} · {a.model}</div>)}<p>{g.scope_note}</p></details></td>
    <td className="p-2">{g.aps.length} APs · {g.fresh_aps} fresh</td>
    <td className="p-2"><details><summary>{g.operations.length} proposed operation(s)</summary><pre className="whitespace-pre-wrap text-xs">{JSON.stringify(g.operations,null,2)}</pre></details>{g.blockers.map(b=><div key={b} className="text-amber-700">{b}</div>)}{!!Object.keys(g.unsupported_changes).length&&<div className="text-xs">Additional controls need implementation/certification: {Object.keys(g.unsupported_changes).join(", ")}</div>}<div className="text-xs text-slate-500">Existing certification, approval, regulatory and backup gates still apply.</div></td>
   </tr>)}
  </tbody></table></div>
  {review.data&&<section className="mt-5"><h3 className="font-semibold">Before / after — {new Date(review.data.created_at).toLocaleString()}</h3>
   {review.data.clusters.map(r=><div key={r.group.key} className="mt-3 rounded border p-3">
    <h4 className="font-semibold">{r.group.sites.join(" / ")} · {r.group.controller}</h4><p className="my-2">{r.state.replace(/_/g," ")}</p>
    <table className="w-full text-sm"><thead><tr className="text-left"><th>Measured evidence</th><th>Before</th><th>After</th></tr></thead><tbody>
    {([["Partial wireless-link score",value(r.before.link_score),value(r.after?.link_score)],
      ["Poor scored-client observations",value(r.before.poor_percent,"%"),value(r.after?.poor_percent,"%")],
      ["Mean connected clients",value(r.before.mean_clients),value(r.after?.mean_clients)],
      ["Client measurement coverage",percent(r.before.client_measurement_coverage),percent(r.after?.client_measurement_coverage)],
      ["ARM utilization",value(r.before.arm.util_percent,"%"),value(r.after?.arm.util_percent,"%")],
      ["ARM channel quality",value(r.before.arm.quality_percent,"%"),value(r.after?.arm.quality_percent,"%")],
      ["ARM radio retry",value(r.before.arm.retry_percent,"%"),value(r.after?.arm.retry_percent,"%")],
      ["Mean client SNR",value(r.before.snr_db," dB"),value(r.after?.snr_db," dB")],
      ["Mean client RSSI",value(r.before.rssi_dbm," dBm"),value(r.after?.rssi_dbm," dBm")],
      ["Inferred disconnect / roam events",r.before.inferred_events.disconnect+" / "+r.before.inferred_events.roam,r.after?r.after.inferred_events.disconnect+" / "+r.after.inferred_events.roam:"not observed"],
      ["Intelligence findings seen",String(r.before.intelligence_findings_seen),r.after?String(r.after.intelligence_findings_seen):"not observed"],
      ["Sample count / span",r.before.samples+" / "+value(r.before.span_minutes," min"),r.after?r.after.samples+" / "+value(r.after.span_minutes," min"):"not observed"]
    ] as string[][]).map(row=><tr key={row[0]} className="border-t"><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td></tr>)}
    </tbody></table>{r.comparison?.reasons?.map(reason=><p key={reason} className="text-amber-700">{reason}</p>)}
    <p className="mt-2 text-xs">Results describe observed wireless-link changes, not proven causation or an end-to-end experience guarantee. Missing after evidence is not success.</p>
   </div>)}
  </section>}
 </Card>;
}
