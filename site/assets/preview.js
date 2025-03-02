
const D = window.__DATA__;
const REF = 3.3;
function fmt(v,d){ if(v===null||v===undefined||isNaN(v)) return "—";
  return Number(v).toLocaleString("en-GB",{minimumFractionDigits:d===undefined?1:d,
  maximumFractionDigits:d===undefined?1:d}); }
function el(t,c,x){const n=document.createElement(t);if(c)n.className=c;
if(x!==undefined&&x!==null)n.textContent=String(x);return n;}
function kpi(hostId,label,value,foot){
  const host=document.getElementById(hostId);
  if(!host) return;
  const p=el("div","panel kpi"); p.appendChild(el("div","l",label));
  p.appendChild(el("div","v",value)); if(foot)p.appendChild(el("div","foot",foot));
  host.appendChild(p);}

function render(){
  const y=D.year, m=D.measures;
  kpi("k1","Active doctors",
      fmt(m["Total Doctors"],0), y+" · national totals");
  kpi("k1","Active nurses",
      fmt(m["Total Nurses"],0), "6 states report none");
  kpi("k1","Workforce gap",
      fmt(m["Workforce Gap"],0), y+" · vs reference ratio");
  kpi("k1","Retirement risk",
      fmt(m["Retirement Risk %"])+"%", "aged 55+");

  const cov=document.getElementById("cov");
  if(!cov){return;}
  let mx=0; D.coverage.forEach(r=>{if(r.physicians_per_1000>mx)mx=r.physicians_per_1000;});
  D.coverage.forEach(r=>{
    const row=el("div","bar");
    row.appendChild(el("div","n",r.country_name));
    const tr=el("div","tr");
    const fl=el("div","fl");
    fl.style.width=Math.max(1,(r.physicians_per_1000/mx)*100)+"%";
    tr.appendChild(fl);
    if(REF<mx){const mk=el("div","mk");mk.style.left=(REF/mx)*100+"%";
      mk.title="OECD reference 3.3";tr.appendChild(mk);}
    row.appendChild(tr);
    row.appendChild(el("div","v",fmt(r.physicians_per_1000,2)));
    cov.appendChild(row);});

  const ret=document.getElementById("ret");
  let rmax=0; D.retirement.forEach(r=>{if(r.pct_55_plus>rmax)rmax=r.pct_55_plus;});
  D.retirement.forEach(r=>{
    const row=el("div","bar");
    row.appendChild(el("div","n",r.country_name));
    const tr=el("div","tr");
    const fl=el("div","fl"+(r.pct_55_plus>50?" pos":""));
    fl.style.width=Math.max(1,(r.pct_55_plus/rmax)*100)+"%";
    tr.appendChild(fl);
    if(50<rmax){const mk=el("div","mk");mk.style.left=(50/rmax)*100+"%";
      mk.title="50% threshold";tr.appendChild(mk);}
    row.appendChild(tr);
    row.appendChild(el("div","v",fmt(r.pct_55_plus)+"%"));
    ret.appendChild(row);});

  const des=document.getElementById("des");
  let dmax=0; D.deserts.forEach(r=>{if(r.workers_per_1000>dmax)dmax=r.workers_per_1000;});
  D.deserts.forEach(r=>{
    const row=el("div","bar");
    row.appendChild(el("div","n",(r.region_name||r.nuts_code)+" · "+r.nuts_code));
    const tr=el("div","tr");
    const fl=el("div","fl"+(r.access_tier==="Critical"?" pos":""));
    fl.style.width=Math.max(1,(r.workers_per_1000/dmax)*100)+"%";
    tr.appendChild(fl);
    row.appendChild(tr);
    row.appendChild(el("div","v",fmt(r.workers_per_1000,2)));
    des.appendChild(row);});

  const fc=document.getElementById("fc"); let fmax=0;
  D.forecast.forEach(r=>{if(r.forecast>fmax)fmax=r.forecast;});
  D.forecast.forEach(r=>{
    const row=el("div","bar");
    row.appendChild(el("div","n",r.country_name+" · "+r.profession));
    const tr=el("div","tr");
    const fl=el("div","fl"); fl.style.width=Math.max(1,(r.forecast/fmax)*100)+"%";
    tr.appendChild(fl); row.appendChild(tr);
    row.appendChild(el("div","v",fmt(r.forecast,0)));
    fc.appendChild(row);});

  const cap=document.getElementById("mape");
  if(D.model_mape && D.model_mape.length){
    const t=el("table"); const th=el("thead"); const tr=el("tr");
    ["Model","MAPE %","sMAPE %"].forEach((h,i)=>{
      const c=el("th",i?"n":"",h); tr.appendChild(c);});
    th.appendChild(tr); t.appendChild(th);
    const tb=el("tbody");
    D.model_mape.forEach(r=>{
      const row=el("tr");
      row.appendChild(el("td",null,r.model));
      row.appendChild(el("td","n",fmt(r.mape,3)));
      row.appendChild(el("td","n",fmt(r.smape,3)));
      tb.appendChild(row);});
    t.appendChild(tb); cap.appendChild(t);}

  kpi("k5","Replacement demand",
      fmt(m["Replacement Demand"],0), y+" · workers aged 55+");
  kpi("k5","Aged 65+",
      fmt(m["Retirement Exposure 65+"])+"%", "closest to retirement");
  kpi("k5","Nurse coverage",
      fmt(m["Worker to Population Ratio"],2)+"", "workers per 1,000");
  kpi("k5","Coverage index",
      fmt(m["Coverage Index"])+"%", "actual vs required");

  kpi("k8","Forecast workers",
      fmt(m["Forecast Workers"],0), D.forecast_year+" projection");
  kpi("k8","Model uplift",
      fmt(m["Model Uplift"],0), "vs carry-forward baseline");
  kpi("k6","ICU beds",
      fmt(m["ICU Beds"],0), "readiness "+fmt(m["Hospital Readiness"],2)+"%");
  kpi("k6","Critical regions",
      fmt(m["Critical Desert Regions"],0),
      fmt(m["Population In Deserts"],0)+" people affected");
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", render);
} else {
  render();
}
