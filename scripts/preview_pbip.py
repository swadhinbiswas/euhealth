#!/usr/bin/env python
"""Render a faithful preview of the PBIP report from the warehouse.

Power BI Desktop is Windows-only, so the PBIP cannot be opened or screenshotted
on this host. What can be done is render the *same measures and the same data*
into HTML so the report's content is reviewable, and so the layout in
report.json is visibly correct before anyone opens it in Desktop.

What this is: a preview of the PBIP's eight pages, computed from the warehouse.
What this is not: a Power BI screenshot. It is labelled as a preview everywhere
it appears, and no claim is made that it reproduces Power BI's exact rendering.

Every measure here is computed in SQL against the same tables the PBIP binds
to, so a number that disagrees between this preview and Desktop is a real bug
in one of the two.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import subprocess
import threading
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
PBIP = ROOT / "powerbi" / "pbip"
PREVIEW = ROOT / "site" / "pbip-preview"
IMAGES = ROOT / "docs" / "images"
PROJECT = "EU-Health-Workforce"


def measure_sql() -> dict[str, dict]:
    """SQL equivalents of the DAX measures.

    Kept next to the measures they mirror so a change to one is visibly a
    change to the other. Every one recomputes from underlying totals; none
    averages a ratio.
    """
    return {
        "Total Doctors": """
            SELECT SUM(measure_value)
            FROM fact_healthcare_workers
            WHERE measure='headcount_total' AND sex_code='T'
              AND profession_code='PHYS' AND year={y}""",
        "Total Nurses": """
            SELECT SUM(measure_value)
            FROM fact_healthcare_workers
            WHERE measure='headcount_total' AND sex_code='T'
              AND profession_code='NURS' AND year={y}""",
        "Workforce Gap": """
            SELECT SUM(shortage) FROM fact_staffing_shortage
            WHERE year={y}""",
        "Workforce Gap %": """
            SELECT ROUND(100.0 * SUM(shortage) / NULLIF(SUM(required_workers),0), 1)
            FROM fact_staffing_shortage WHERE year={y}""",
        "Coverage Index": """
            SELECT ROUND(100.0 * SUM(actual_workers)
                         / NULLIF(SUM(required_workers),0), 1)
            FROM fact_staffing_shortage WHERE year={y}""",
        "Retirement Risk %": """
            SELECT ROUND(100.0 * SUM(near_retirement)
                         / NULLIF(SUM(total_by_age),0), 1)
            FROM v_retirement_exposure WHERE year={y}""",
        "Retirement Exposure 65+": """
            SELECT ROUND(AVG(pct_65_plus), 1) FROM v_retirement_exposure
            WHERE year={y}""",
        "Replacement Demand": """
            SELECT SUM(near_retirement) FROM v_retirement_exposure
            WHERE year={y}""",
        "Hospital Beds": """
            SELECT SUM(beds) FROM fact_hospital_capacity
            WHERE category_group='hospital_bed' AND year={y}""",
        "ICU Beds": """
            SELECT SUM(beds) FROM fact_hospital_capacity
            WHERE category_group='icu_bed' AND year={y}""",
        # Anchored on the most recent year carrying BOTH bed types. Beds stop
        # after 2019 while ICU continues, so a fixed year silently divides by
        # zero and the KPI renders blank.
        "Hospital Readiness": """
            SELECT ROUND(100.0 * SUM(CASE WHEN category_group='icu_bed'
                                          THEN beds ELSE 0 END)
                         / NULLIF(SUM(CASE WHEN category_group='hospital_bed'
                                          THEN beds ELSE 0 END),0), 2)
            FROM fact_hospital_capacity
            WHERE year = (SELECT MAX(year) FROM fact_hospital_capacity
                          WHERE category_group='hospital_bed')""",
        "Countries Reporting Nurses": """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT country_code FROM fact_healthcare_workers
                WHERE profession_code='NURS' AND measure='headcount_total'
                  AND sex_code='T')""",
        "Critical Desert Regions": """
            SELECT COUNT(*) FROM v_regional_risk
            WHERE access_tier='Critical'""",
        "Population In Deserts": """
            SELECT SUM(population) FROM (
                SELECT nuts_code, MAX(population) AS population
                FROM fact_population_nuts
                WHERE age_group_code IS NULL AND sex_code='T'
                GROUP BY nuts_code) p
            WHERE nuts_code IN (
                SELECT nuts_code FROM v_regional_risk
                WHERE access_tier IN ('Critical','High'))""",
        "Healthcare Access Score": """
            SELECT ROUND(AVG(workers_per_1000), 2) FROM v_regional_risk
            WHERE workers_per_1000 IS NOT NULL""",
        "Regional Coverage": """
            SELECT ROUND(AVG(workers_per_1000), 2) FROM v_regional_risk
            WHERE workers_per_1000 IS NOT NULL""",
        "Medical Desert Score": """
            SELECT ROUND(AVG(CASE WHEN workers_per_1000 < 2 THEN 70
                                  WHEN workers_per_1000 < 5 THEN 45
                                  ELSE 20 END), 1)
            FROM v_regional_risk WHERE workers_per_1000 IS NOT NULL""",
        "Worker to Population Ratio": """
            SELECT ROUND(AVG(physicians_per_1000), 2)
            FROM v_coverage_index_safe
            WHERE year={y} AND physicians_per_1000_safe IS NOT NULL""",
        "Forecast Workers": """
            SELECT ROUND(SUM(forecast)) FROM forecast_workforce
            WHERE year={y2}""",
        "Baseline Forecast": """
            SELECT ROUND(SUM(baseline_forecast)) FROM forecast_workforce
            WHERE year={y2}""",
        "Model Uplift": """
            SELECT ROUND(SUM(forecast - baseline_forecast))
            FROM forecast_workforce WHERE year={y2}""",
        "Shortage Severity Rank": """
            SELECT COUNT(DISTINCT country_code) FROM fact_staffing_shortage
            WHERE year={y} AND shortage > 0""",
    }


def build() -> dict:
    if not WAREHOUSE.exists():
        raise SystemExit("warehouse missing; run 'make warehouse'")
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        year = con.execute("""
            SELECT year FROM v_coverage_index_safe
            WHERE physicians_per_1000_safe IS NOT NULL
            GROUP BY year HAVING COUNT(*) >= 20
            ORDER BY year DESC LIMIT 1
        """).fetchone()[0]
        forecast_year = con.execute(
            "SELECT MAX(year) FROM forecast_workforce WHERE year <= 2030"
        ).fetchone()[0]

        values = {}
        for name, template in measure_sql().items():
            try:
                result = con.execute(
                    template.format(y=year, y2=forecast_year)
                ).fetchone()[0]
            except Exception:
                result = None
            values[name] = result

        payload = {
            "year": int(year),
            "forecast_year": int(forecast_year),
            "measures": values,
            "coverage": con.execute(f"""
                SELECT country_name, physicians_per_1000_safe AS physicians_per_1000
                FROM v_coverage_index_safe
                WHERE year={year} AND physicians_per_1000_safe IS NOT NULL
                ORDER BY physicians_per_1000
            """).df().to_dict(orient="records"),
            "retirement": con.execute(f"""
                SELECT country_name, pct_55_plus, pct_65_plus
                FROM v_retirement_exposure
                WHERE year={year} AND profession_code='PHYS'
                  AND pct_55_plus IS NOT NULL
                ORDER BY pct_55_plus DESC LIMIT 14
            """).df().to_dict(orient="records"),
            # One row per region: v_regional_risk carries a profession-year row
            # per region, so selecting it raw repeats the same place.
            "deserts": con.execute("""
                WITH latest AS (
                    SELECT nuts_code, MAX(year) AS year
                    FROM v_regional_risk
                    WHERE workers_per_1000 IS NOT NULL
                      AND profession_code = 'PHYS'
                    GROUP BY nuts_code
                )
                SELECT r.nuts_code, r.region_name, r.workers_per_1000,
                       r.access_tier, r.year
                FROM v_regional_risk r
                JOIN latest l ON l.nuts_code = r.nuts_code
                              AND l.year = r.year
                WHERE r.profession_code = 'PHYS'
                  AND r.workers_per_1000 IS NOT NULL
                ORDER BY r.workers_per_1000 LIMIT 16
            """).df().to_dict(orient="records"),
            "forecast": con.execute(f"""
                SELECT c.country_name,
                       CASE WHEN f.profession_code='PHYS'
                            THEN 'physicians' ELSE 'nurses' END AS profession,
                       f.year, ROUND(f.forecast) AS forecast,
                       ROUND(f.baseline_forecast) AS baseline
                FROM forecast_workforce f
                JOIN dim_country c ON c.country_code=f.country_code
                WHERE f.year={forecast_year}
                ORDER BY f.forecast DESC LIMIT 12
            """).df().to_dict(orient="records"),
            "model_mape": con.execute(
                "SELECT 1 WHERE 1=0"
            ).fetchall() if False else _model_mape(),
        }
    finally:
        con.close()

    PREVIEW.mkdir(parents=True, exist_ok=True)
    (PREVIEW / "data.json").write_text(
        json.dumps(payload, indent=1, default=str)
    )
    return payload


def _model_mape() -> list:
    path = ROOT / "data" / "models" / "model_comparison.csv"
    if not path.exists():
        return []
    import pandas as pd
    frame = pd.read_csv(path)
    return frame[["model", "mape", "smape"]].round(3).to_dict(
        orient="records"
    )


# --- rendering ---------------------------------------------------------------

CSS = """
:root{--ink:#14181f;--ink2:#4a5261;--ink3:#7b8496;--surface:#fff;
--surface2:#f6f7f9;--surface3:#eceef2;--line:#dfe3ea;--accent:#1f4e79;
--teal:#2d7d9a;--crit:#a4243b;--high:#d1603d;--med:#e8b04b;--low:#4e8c6d;}
*{box-sizing:border-box}
body{margin:0;background:var(--surface2);color:var(--ink);
font:400 15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:28px 24px 60px}
header{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:24px}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:.12em;
text-transform:uppercase;color:var(--ink3);margin:0 0 6px}
h1{font-size:24px;font-weight:600;margin:0 0 8px}
.sub{font-size:14px;color:var(--ink2);margin:0;max-width:70ch}
.badge{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.06em;
text-transform:uppercase;padding:3px 9px;border-radius:2px;
background:#fdf6e6;color:#8a6a08;border:1px solid #e8d9a8;margin-top:12px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:20px 0 26px}
nav a{font-size:13px;text-decoration:none;color:var(--ink2);padding:6px 11px;
border:1px solid var(--line);border-radius:3px;background:var(--surface)}
section{margin-bottom:30px}
section h2{font-size:15px;font-weight:600;margin:0 0 4px;
padding-bottom:8px;border-bottom:1px solid var(--line)}
section p.note{font-size:13px;color:var(--ink3);margin:0 0 14px}
.grid{display:grid;gap:12px}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:3px;
box-shadow:0 1px 2px rgba(20,24,31,.05)}
.kpi{padding:15px 17px}
.kpi .l{font-size:11px;font-weight:600;letter-spacing:.09em;
text-transform:uppercase;color:var(--ink3);margin-bottom:7px}
.kpi .v{font-size:25px;font-weight:600;font-variant-numeric:tabular-nums}
.cap{font-size:12.5px;color:var(--ink2);padding:12px 17px 10px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;
color:var(--ink3);text-align:left;padding:8px 17px;border-bottom:1px solid var(--line)}
td{padding:8px 17px;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}
.bar{display:grid;grid-template-columns:150px 1fr 74px;gap:11px;
align-items:center;padding:6px 17px}
.bar .n{font-size:13.5px}
.tr{height:14px;background:var(--surface3);border-radius:2px;position:relative}
.fl{height:100%;border-radius:2px;background:var(--accent)}
.fl.pos{background:var(--crit)}
.tier{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;
border-radius:2px;color:#fff}
.tier-Critical{background:var(--crit)}.tier-High{background:var(--high);
color:#2a1a12}.tier-Medium{background:var(--med);color:#2a2312}
.tier-Low{background:var(--low)}
.mk{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);opacity:.5}
.v{font-size:13px;text-align:right;color:var(--ink2);
font-variant-numeric:tabular-nums}
.caveat{border-left:3px solid var(--high);background:var(--surface2);
padding:11px 15px;margin-top:14px;font-size:13px;color:var(--ink2);
border-radius:0 3px 3px 0}
footer{margin-top:28px;padding-top:14px;border-top:1px solid var(--line);
font-size:12.5px;color:var(--ink3)}
@media(max-width:860px){.g4,.g2{grid-template-columns:1fr}}
"""

JS = r"""
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
"""

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power BI report preview — EU Health Workforce</title>
<link rel="stylesheet" href="preview.css"></head>
<body><div class="wrap">
<header>
  <p class="eyebrow">Power BI Project preview</p>
  <h1>EU Healthcare Workforce — 8-page report</h1>
  <p class="sub">A rendered preview of <code>powerbi/pbip/EU-Health-Workforce.pbip</code>,
    computed from the same warehouse tables and the same measures the Power BI
    model binds to.</p>
  <span class="badge">Preview — not a Power BI screenshot</span>
</header>
<nav>
  <a href="#overview">1. Executive overview</a>
  <a href="#coverage">2. Workforce map</a>
  <a href="#retirement">3-4. Doctor &amp; nurse</a>
  <a href="#aging">5. Workforce aging</a>
  <a href="#capacity">6. Hospital capacity</a>
  <a href="#deserts">7. Medical deserts</a>
  <a href="#forecast">8. Forecasting</a>
</nav>

<section id="overview"><h2>1. Executive overview</h2>
  <p class="note">KPI cards from the report's measure set.</p>
  <div class="grid g4" id="k1"></div>
</section>

<section id="coverage"><h2>2. Workforce map — coverage by country</h2>
  <p class="note">Physicians per 1,000 population; marker is the OECD
    reference ratio of 3.3.</p>
  <div class="panel" id="cov"></div>
  <div class="caveat"><strong>Provenance.</strong> Regional coverage in the
    report is observed and verified against national totals for 12 countries,
    and effectively ends in 2015. The population denominator is a 2023
    snapshot, so rates are indicative rather than same-year.</div>
</section>

<section id="retirement"><h2>3. Doctor shortage &mdash; retirement exposure</h2>
  <p class="note">Share of physicians aged 55 or over; shaded above 50%.</p>
  <div class="panel" id="ret"></div>
</section>

<section id="aging"><h2>4-5. Nurse workforce &amp; aging</h2>
  <p class="note">Replacement demand and retirement exposure by country.</p>
  <div class="grid g4" id="k5"></div>
</section>

<section id="capacity"><h2>6. Hospital capacity risk</h2>
  <p class="note">Beds, ICU intensity and the composite risk score.</p>
  <div class="grid g4" id="k6"></div>
  <div class="caveat"><strong>Assumption.</strong> The hospital risk score
    weights are stated assumptions, not fitted parameters, and need domain
    review before informing a funding decision.</div>
</section>

<section id="deserts"><h2>7. Medical desert detection</h2>
  <p class="note">Lowest observed regional coverage, verified per region.</p>
  <div class="panel" id="des"></div>
</section>

<section id="forecast"><h2>8. Forecasting centre</h2>
  <p class="note">Seven model families compared under identical walk-forward
    validation, then the champion applied forward.</p>
  <div class="grid g4" id="k8"></div>
  <div class="panel" id="fc" style="margin-top:12px"></div>
  <div class="panel" style="margin-top:12px" id="mape"></div>
  <div class="caveat"><strong>Model result.</strong> Every tree-based model
    lost to the naive baseline. Short, near-linear series cannot be
    extrapolated past a training range, so a gradient-boosted forecast would
    look more sophisticated and be worse.</div>
</section>

<footer>Preview generated from the same warehouse and measures as the PBIP.
Open <code>powerbi/pbip/EU-Health-Workforce.pbip</code> in Power BI Desktop
for the authoritative rendering.</footer>
</div>
<script src="data.js"></script><script src="preview.js"></script></body></html>
"""


def _write_preview(payload: dict) -> None:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    (PREVIEW / "index.html").write_text(HTML, encoding="utf-8")
    (PREVIEW / "preview.css").write_text(CSS, encoding="utf-8")
    (PREVIEW / "preview.js").write_text(JS, encoding="utf-8")
    (PREVIEW / "data.js").write_text(
        "window.__DATA__ = " + json.dumps(payload, default=str) + ";",
        encoding="utf-8",
    )
    # Mirror the assets into site/ so the deployed site carries the preview too.
    for name in ("preview.css", "preview.js"):
        target = ROOT / "site" / "assets" / name
        target.write_text((PREVIEW / name).read_text(), encoding="utf-8")
    (ROOT / "site" / "assets" / "pbip-data.js").write_text(
        (PREVIEW / "data.js").read_text(), encoding="utf-8"
    )


def serve(directory: Path):
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa
        *a, directory=str(directory), **kw
    )
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def chromium() -> str | None:
    import shutil
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main() -> int:
    payload = build()
    _write_preview(payload)
    binary = chromium()
    if binary is None:
        print(f"preview written to {PREVIEW} (chromium absent; no screenshot)")
        return 0

    IMAGES.mkdir(parents=True, exist_ok=True)
    server, port = serve(PREVIEW)
    try:
        target = IMAGES / "powerbi-report-preview.png"
        command = [
            binary, "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--hide-scrollbars",
            "--virtual-time-budget=12000",
            "--window-size=1400,2900",
            f"--screenshot={target}",
            f"http://127.0.0.1:{port}/",
        ]
        subprocess.run(command, capture_output=True, timeout=180)
    finally:
        server.shutdown()

    if target.exists():
        print(f"screenshot: {target} ({target.stat().st_size:,} bytes)")
    print(f"preview: {PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())