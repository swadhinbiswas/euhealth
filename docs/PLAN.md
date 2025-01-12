# EU Healthcare Workforce Crisis Analytics & Forecasting Platform
## Build Plan v1.0

Status: plan authored against the live Eurostat API on 2026-10-05.
Every source, dataset code, and dimension name below was **verified by probe**, not assumed.

---

## 0. Executive summary

The repository currently contains a sound ingestion skeleton and **nothing else**.
`src/ingestion/http_client.py` is production-quality. `src/ingestion/eurostat.py` is broken
in three independent ways and returns **zero rows from every dataset**.

The platform is buildable in full. The binding constraint is not the ambition of the spec —
it is that three of the required source families (EURES, OECD, several national portals)
have **no usable public API from this host**. Those are handled explicitly in §4 with
documented substitutes, not hand-waved.

**Critical path:** fix the flattener → verify a single country loads → build silver/gold
→ only then ML and BI. Forecasting on zero rows is wasted work.

---

## 1. Verified state of the existing code

| Component | File | Status |
|---|---|---|
| Config / constants | `src/config.py` | Good. Some constants wrong (§1.2) |
| HTTP + landing zone | `src/ingestion/http_client.py` | **Good.** Retries, backoff, atomic write, JSONL audit |
| Eurostat loader | `src/ingestion/eurostat.py` | **Broken.** 3 bugs |
| Runner | `scripts/ingest_all.py` | Runs; 6/9 loaders yield nothing |
| bronze / silver / gold / geo / models / export | — | Empty |
| Tests / CI / docs / notebooks | — | Do not exist |
| `scratchpad/` | 22 probe files | Dead end; delete at Phase 0 |

### 1.1 The three bugs in `eurostat.py`

**Bug 1 — `flatten_jsonstat` decodes the wrong index format. (root cause, blocks everything)**

```python
parts = [int(p) for p in key.split(",")]   # assumes tuple keys
```

Eurostat returns **flat row-major integer keys** — `'0'`, `'3'`, `'60'`. Every key raises
`ValueError`, is swallowed by `except ValueError: continue`, and the function returns `[]`
even for a fully successful response. Correct decoding is modular arithmetic against `size`:

```python
def unravel(flat: int, size: list[int]) -> list[int]:
    out = []
    for s in reversed(size):
        out.append(flat % s)
        flat //= s
    return list(reversed(out))
```

Verified: flat key `60`, `size=[1,1,7,3,1,3]` → `age=Y_GE75, sex=F, time=2024`. ✔

**Bug 2 — multi-value dimension filters silently return nothing.**

Probed directly:

```
geo=DE                                  → 200, 456 values  ✔
geo=DE&age=TOTAL                        → 200,  15 values  ✔
geo=DE&age=TOTAL,Y35-44                 → 200,   0 values  ✗
geo=DE&sex=T,M,F                        → 200,   0 values  ✗
```

Both `+` and `,` collapse the dimension to size 0 while still returning `200 OK`.
The module docstring's workaround (one country per request) is correct but **incomplete** —
`age` and `sex` must also be single-valued or omitted.
**Decision:** omit `age`/`sex` filters entirely, pull the full cube per country
(`hlth_rs_phys?geo=DE&sinceTimePeriod=2000` → 456 rows in one call), filter locally.
This also captures `status` flags (`e` = estimated, `be` = break in series) needed for DQ.

**Bug 3 — four dataset codes and several dimension names are wrong → `HTTP 400`.**

The response `400 INVALID_QUERY_DIMENSION` is **not** the empty-result case; it is a hard
error that the loader treats as an empty frame. Confirmed wrong:

| Configured | Reality |
|---|---|
| `hlth_rs_prs2` with `age=` | no `age` dim; has `wstatus`/`med_spec`. `med_spec=TOTAL` invalid |
| `demo_pjanind` with `age=,sex=` | no `age`/`sex`; dimension is `indic_de` |
| `hlth_rs_bds` with `age=` | no `age`; dims are `facility`/`unit` |
| `demo_mlexpec` with `age=Y0` | `Y0` wrong; omit the filter |

**Invented dataset codes that do not exist at all** (confirmed against the official
Eurostat catalogue, 1.97 MB TOC): `hlth_rs_wdsy`, `hlth_rs_empt`, `lfsa_3une_r`,
`hlth_ges11_hf`, `hlth_inpatient`, `hlth_care`, `censis_r`.

### 1.2 Wrong constants in `config.py`

`AGE_BANDS` assumes `Y_GE65`. **No such code exists.** Real ladders:

```
hlth_rs_phys : TOTAL, Y_LT35,   Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75
hlth_rs_nurse: TOTAL, Y_LT25, Y25-34, Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75
```

The two professions have genuinely different ladders. `NUTS_LEVELS` filenames are
**confirmed correct** (all four GISCO files return 200 GeoJSON).

---

## 2. Verified source inventory

Legend: ✔ verified working · ⚠ works with caveats · ✖ no usable public API

### Eurostat — all verified via probe, single-geo pulls

| Dataset | Content | Dims | Role |
|---|---|---|---|
| ✔ `hlth_rs_phys` | Physicians by age/sex | freq unit age sex geo time | Core workforce |
| ✔ `hlth_rs_nurse` | Nurses & midwives by age/sex | same | Core workforce |
| ✔ `hlth_rs_prs1` | Staff by ISCO + work status | wstatus isco08 | Specialisation |
| ✔ `hlth_rs_prs2` | Staff by medical speciality | wstatus med_spec | Specialisation |
| ✔ `hlth_rs_spec` | Consultants vs specialists | med_spec | Skill mix |
| ✔ `hlth_rs_grd` | Graduating health staff | isco08 | Training pipeline |
| ✔ `hlth_rs_bds` | Hospital beds by facility | facility | Capacity |
| ✔ `hlth_rs_bds2` | Beds by ownership | owner | Public/private split |
| ✔ `hlth_rs_bdsicu` | ICU beds | facility statinfo | Critical care risk |
| ✔ `hlth_rs_bdsrg` | Beds by region | facility | Regional capacity |
| ✔ `hlth_rs_tech` | Tech staff, facilities | facility | Allied health |
| ✔ `hlth_rs_wkmg` | Health worker migration | tngplace | Retention risk |
| ✔ `hlth_co_hosday` | Hospital days, ICD-10 | indic_he icd10 | Patient demand |
| ✔ `demo_r_pjangrp3` | Population NUTS 2/3 by age | age sex unit geo | Denominator |
| ✔ `demo_pjanind` | National population indicators | indic_de | Country rollup |
| ✔ `demo_mlexpec` | Life expectancy | sex age | Outcomes |
| ✔ `demo_magec` | Mortality by age | sex age | Outcomes |
| ✔ `proj_25np` | **Population projections 2025 base** | projection sex age | Demand forecast |
| ✔ `demo_r_d3dens` | Population density | unit | Accessibility |
| ✔ `lfsa_egaps` | Labour by status | wstatus age sex | Labour market |
| ✔ `lfsa_egaed` | Labour by education | isced11 age sex | Skill supply |
| ⚠ `migr_asyctz` | Asylum by citizenship | citizen | Returns 0 rows for DE |

Use `proj_25np`, not `proj_23np` — newer baseline, covers 2030 directly.

### Other sources

| Source | Status | Note |
|---|---|---|
| ✔ **GISCO NUTS 2021** | L0–L3 all 200 GeoJSON | Official boundaries, config correct |
| ✔ **WHO GHO OData** | `/api/Indicator` 200 | Use for indicator metadata/cross-check |
| ⚠ **OSM Overpass** | Works, **slow/flaky** | PT returned 66 hospitals; 504s common. Async + cached + partial-tolerant |
| ✔ **NL CBS OpenData** | OData 200 | Real national portal |
| ✔ **DE Destatis** | HTML 200 | GENESIS API needs registration |
| ✖ **EURES** | No public API | Only HTML portal. Substitute §4.1 |
| ✖ **OECD SDMX** | Times out >100 s | Retry w/ long timeout in CI; else substitute §4.2 |
| ✖ **FR INSEE** | `401` | Needs API key |
| ✖ **SE SCB / AT STATcube** | Connection refused | Blocked from this host |
| ✖ **WHO GHO datapoint** | 404 | Wrong entity set |

---

## 3. Architecture

```text
   SOURCES
   Eurostat · GISCO · WHO · OSM Overpass · CBS NL · Destatis DE
        |
        |  [idempotent, retry, sha256 manifest, raw fidelity]
        v
   RAW  data/raw/<source>/<name>.json|.geojson     immutable, never edited
        |
        |  [parse · flatten · schema-cast · attach status flags]
        v
   BRONZE  bronze_<entity>        typed, source-shaped, no business logic
        |
        |  [conform dims · map codes · dedupe · SCD2 · DQ gate]
        v
   SILVER  silver_<entity>        conformed, business keys, nulls resolved
        |
        |  [join to dims · derive ratios · assign risk tiers]
        v
   GOLD  gold_fact_* / gold_dim_*  star schema, analysis-ready
        |
        v
   WAREHOUSE  DuckDB (primary) · Parquet (portable) · Postgres/BigQuery/Snowflake
        |                                            via portable SQL dialect
        v
   SERVING  Power BI (8 pages, DAX semantic layer) · HTML GIS map · ML forecasts
```

Layer contracts:

| Layer | Contains | Never contains |
|---|---|---|
| Raw | Byte-exact payloads + manifest | Any transformation |
| Bronze | Source-shaped rows, typed | Cross-source joins, business keys |
| Silver | Conformed, deduped, SCD2 | Presentation aggregates |
| Gold | Fact/dim star schema | Raw codes |

**Load targets.** DuckDB is the working warehouse. BigQuery/Snowflake/Delta are simulated
honestly: one portable SQL dialect + DDL generation scripts that emit equivalent DDL, with a
documented list of what does not port (e.g. `SCD2` merge syntax). No fake screenshots.

---

## 4. Source gaps and honest substitutes

Stated explicitly because the taste spec says: *"real sources preferred, and wants that
tradeoff called out."*

**4.1 EURES job vacancies → substitute.** EURES exposes no open API. Substitutes, in order:
1. `lfsa_egaps` — labour force by work status, as recruitment-pressure proxy
2. `hlth_rs_wkmg` — `tngplace` = `FOR`/`NAT_FOR` → foreign-trained dependency
3. `hlth_rs_grd` — graduating staff → domestic pipeline capacity

`fact_job_vacancies` is built from these three as **documented proxies**, with a
`data_basis` column (`observed` | `proxy`) so no consumer mistakes a proxy for a vacancy count.

**4.2 OECD → Eurostat equivalent.** `DSD_HEALTH_REAC` timed out from this host.
Mapping: nurses → `hlth_rs_nurse`; physicians → `hlth_rs_phys`; beds → `hlth_rs_bds`;
expenditure → deferred. Retry with a 300 s timeout and `format=csvfile` in CI; if it still
fails, the Eurostat path is already sufficient and the report notes the substitution.

**4.3 Regional granularity.** `hlth_rs_phys/nurse` are **national only** — no NUTS
dimension exists. Regional analysis therefore joins national workforce to NUTS-level
population with an explicit **allocation assumption** (national age-sex distribution
applied to NUTS population). This is the single most important methodological caveat in
the project and must be stated on every regional page and in the data dictionary.

**4.4 National portals.** CBS NL is the one portal with a clean open API. Destatis GENESIS
is HTML-only without registration. Scope national portals to **NL + DE** rather than
pretending to cover 13 countries.

---

## 5. Build phases

### Phase 0 — Foundation (½ day)
- Delete `scratchpad/` (22 files, superseded).
- Rewrite `flatten_jsonstat` with `unravel()`; capture `status` flags.
- Replace all dataset codes/dims per §1.1 + §2; per-profession age-band maps.
- Fix `config.py` age bands; add verified `DATASETS` registry with per-dataset dims.
- `sys.path` hack → `pyproject.toml` installable package, `src` layout.
- Add `pytest`, `ruff`, `.gitignore`, **initialise git** (currently not a repo).
- **Gate:** `hlth_rs_phys?geo=DE` returns >0 rows in a unit test.

### Phase 1 — Ingestion (2 days)
- Batch loader over verified registry, one request per country per dataset.
- Incremental mode: `sinceTimePeriod` watermark from `silver` max time.
- Idempotent raw writes + `manifest.json` (url, sha256, bytes, fetched_at).
- Real failure accounting: `400`/`404` recorded as `FAIL` and surfaced, never silent.
- **Gate:** `data/logs/ingestion.jsonl` shows 0 rows of `200 OK + empty`.

### Phase 2 — Bronze → Silver (3 days)
- `dim_country` (ISO2/ISO3/NUTS prefix), `dim_region` (NUTS 2021, GISCO geometry),
  `dim_profession`, `dim_age_group` (canonical bands, per-source mapping table),
  `dim_gender`, `dim_date`, `dim_specialization`, `dim_sector`, `dim_hospital`.
- Canonical age bands: `Y_LT35` (nurse `Y_LT25`+`Y25-34`), `Y35_44`, `Y45_54`,
  `Y55_64`, `Y_GE65` (`Y65-74`+`Y_GE75`). Mapping table, not ad-hoc renames.
- `dim_country` and `dim_hospital` as **SCD Type 2**; others SCD1.
- Dedupe on natural key; NULL taxonomy: `UNKNOWN` vs `NOT_APPLICABLE` vs `NOT_REPORTED`.

### Phase 3 — Gold star schema (3 days)
Eight facts, each with a **stated grain**:

| Fact | Grain |
|---|---|
| `fact_healthcare_workers` | worker × profession × age × sex × geo × year |
| `fact_retirement` | profession × age band × geo × year |
| `fact_patient_demand` | geo × year × age × sex × ICD-10 group |
| `fact_hospital_capacity` | geo × year × facility type × unit |
| `fact_job_vacancies` | geo × year × profession (proxy-sourced) |
| `fact_workforce_forecast` | geo × profession × year × horizon × scenario |
| `fact_staffing_shortage` | geo × profession × year |
| `fact_population` | geo level × age × sex × year |

Surrogate keys `BIGINT` via DuckDB `ROW_NUMBER()` hash, degenerate `dim_date` FK,
composite FK indexes. ERD auto-generated from the DuckDB catalogue.

### Phase 4 — Data quality framework (2 days)
Seven checks per §spec: null rate, duplicate rate, freshness, range, schema, country
reference, region reference. Composite **quality score** 0–100, stored per table per run,
surfaced as a dashboard page. Gate: bronze→silver refuses to promote below threshold,
with an explicit override table.

### Phase 5 — SQL analytics (3 days)
14 named, reusable analyses in `sql/`. Real window functions:
- Retirement exposure: cohort × share-55+ × `NTILE(4)` risk tier
- Hospital risk: z-score composite of beds-per-nurse, ICU density, 65+ growth
- Coverage index: workers per 1,000 vs OECD reference, gap + rolling 3y mean
- YoY delta, `LAG`/`LEAD`, rolling averages, cohort analysis, `RANK`/`DENSE_RANK`
- Medical desert: density × travel-time proxy × scarcity tier

### Phase 6 — ML / forecasting (4 days)
Five model families on the same task, compared — never one model:

| Model | Role |
|---|---|
| ARIMA (`statsmodels`) | Interpretable baseline |
| Prophet | Trend + changepoints (note: not in `pyproject`, add or justify) |
| XGBoost | Non-linear, feature-rich |
| LightGBM | Efficient, handles missing natively |
| Random Forest | Robust, interpretable via impurity |

- Targets: workforce 2027/2028/2029/2030/2035; retirement by profession.
- Walk-forward validation, **no random split on time series**.
- Metrics: MAE, RMSE, MAPE, sMAPE — all four, in one comparison table.
- Feature importance (permutation for RF/XGB, gain for LGBM).
- Scenarios: best / expected / worst, driven by `proj_25np` variants
  (`LFRT` low fertility / `LMRT` / `BSL` baseline) joined to retention assumptions.
- Medical-desert classifier → discretised **Low/Medium/High/Critical** tiers.

### Phase 7 — GIS (2 days)
GISCO NUTS L0–L3 official boundaries. Folium/Plotly HTML maps: EU choropleth, country
drill-down, region drill-down, hospital points, risk heat, clusters, coverage rings,
population-weighted centroids for travel-distance proxy. Data
Engineering taste says geography is core, not decoration — so maps are generated
artifacts in `data/export/`, not screenshots of a BI tool.

### Phase 8 — Power BI (3 days)
Cannot generate a `.pbix` from CLI. Deliver:
- 8 page specs per §spec, exactly as listed
- **Complete `.dax` measure library** (~25 measures) in `powerbi/measures.dax`
- `measures.md` documenting each: formula, intent, format string, refresh cost
- `schema.md` — relationship model, M code for the NUTS region dimension
- Theme JSON (light + dark), accessibility contrast checked
- PBIX authoring guide so it is reproducible in desktop
Honest note in README: PBIX binary not generated here; every input needed to build it is.

### Phase 9 — Docs & portfolio wrap (2 days)
README · architecture · data-flow · ETL · lineage · ERD diagrams (Mermaid) ·
data dictionary (auto-generated from DuckDB) · SQL script index ·
business impact report · recruiter case study · presentation deck ·
**all four diagrams as Mermaid in-repo**, rendered in README.

---

## 6. Answering the 10 business questions

| # | Question | Delivered by |
|---|---|---|
| 1 | Largest shortage regions | Coverage index + medical-desert score, NUTS 2 |
| 2 | Professions most affected | Shortage by profession × age × sex |
| 3 | Retiring in 5–10 yrs | Cohort model on `Y55-64`/`Y_GE65`, by profession |
| 4 | Hospitals at highest risk | Composite risk: beds/nurse, ICU density, 65+ growth |
| 5 | Where funding goes first | Priority rank = severity × population-weighted equity |
| 6 | Population aging → demand | `proj_25np` 65+ × utilisation intensity |
| 7 | Additional workers by 2030 | Forecast gap vs reference ratio |
| 8 | Becoming medical deserts | Classifier → 4 risk tiers |
| 9 | Improving or worsening | YoY coverage delta, 3y rolling trend |
| 10 | Supportable policy actions | Scenario sim: retention vs training vs migration |

---

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Regional workforce is a modelled allocation, not observed | **High** — affects 6 of 10 questions | Flag everywhere; sensitivity analysis vs alternative allocation |
| Sparse national reporting (many countries report TOTAL only) | High | Null taxonomy + `reporting_completeness` dim; never impute silently |
| Overpass flakiness | Medium | Async, cached, partial-tolerant, retry ladder |
| OECD/EURES unavailable | Medium | Documented substitutes (§4) |
| Age ladder differs phys vs nurse | Medium | Explicit mapping table, no hard-coded joins |
| `proj_*` baseline revision | Low | Pin `proj_25np`; document as-of date |

---

## 8. Definition of done

- [ ] Every gold fact has a documented grain and ≥1 DQ check passing
- [ ] Ingestion log shows zero silent empty successes
- [ ] ≥5 models compared on one task with MAE/RMSE/MAPE/sMAPE
- [ ] All 8 Power BI pages specified with DAX measures
- [ ] Data dictionary auto-generated from the live warehouse
- [ ] Every diagram present as Mermaid in-repo
- [ ] All 10 business questions answerable from a single SQL query each
- [ ] Provenance caveat on every regional output

---

## 9. Honest assessment

**Achievable and genuinely impressive:** the full pipeline, star schema, DQ framework,
advanced SQL, 5-model forecast comparison with real metrics, GIS artifacts, DAX library,
docs.

**Weakest points — stated up front rather than buried:**
1. **Regional granularity is modelled, not observed.** Eurostat publishes health workforce
   nationally only. This is a genuine data limitation, and the most sophisticated-looking
   regional numbers in the project are allocations. A Senior reviewer *will* probe this.
2. **EURES and OECD are unreachable.** Vacancy facts are proxies. Covered by `data_basis`.
3. **No `.pbix` binary.** Deliverable is the complete spec + DAX + authoring guide.
4. **National portal coverage is NL + DE**, not 13 countries.

Better to be caught being honest about these than to have a reviewer discover them.