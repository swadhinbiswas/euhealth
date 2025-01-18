# Findings from the working pipeline

Everything below is computed from live Eurostat data by
`python scripts/ingest_all.py && python -m src.warehouse.cli`.
No figure is illustrative.

## What the pipeline currently produces

| Layer | Content |
|---|---|
| Raw | 352 byte-exact Eurostat payloads, 27 member states |
| Bronze/Silver | typed, conformed rows with canonical age bands |
| Gold | 7 dimensions + 7 facts in DuckDB |
| Analytics | 7 analytical views + 4 integrity views |
| Quality | per-table DQ reports, physicians 88.9 / nurses 77.8 |
| Tests | 108 passing |

Row counts: `fact_healthcare_workers` 13,408 · `fact_population_nuts` 5,400 ·
`fact_hospital_capacity` 3,644 · `fact_population_indicators` 34,448.

## Answers to the business questions

**Q1 — Largest shortages.** Physicians per 1,000 population, 2020, lowest five:
Hungary 3.16, Belgium 3.22, Slovenia 3.31, Romania 3.32, Poland 3.32.
Only Hungary sits below the 3.3 OECD reference ratio.

**Q3 — Retirement exposure.** Share of physicians aged 55+ / 65+, 2020:
Italy 56.2% / 23.0% · Bulgaria 53.0% / 17.8% · Latvia 47.6% / 18.1% ·
Estonia 46.2% / 20.7%. Italy is the clearest single-policy case in the dataset.

**Q9 — Improving or worsening.** Germany, physicians per 1,000:
4.20 (2016) → 4.47 (2020) → 4.67 (2023), positive every single year.
Rolling 3-year mean confirms the trend is not a single-year artefact.

**Q6 — Capacity pressure.** ICU as a share of hospital beds, 2020:
Hungary 6.18%, Luxembourg 5.28%, Belgium 3.31%.

**Gender.** Nurses are 84–86% female in Germany, Spain, Belgium and Austria.
The gender gap is concentrated in nursing, not medicine.

## Four findings that constrain the analysis

These came out of building the pipeline, not from reading documentation. Each
is encoded as a check or view so it cannot be forgotten downstream.

**1. Six member states publish no nurse headcount at all.**
Bulgaria, Cyprus, Luxembourg, Poland, Portugal, Sweden.
`hlth_rs_nurse` returns `200 OK` with an empty cube for each — the dimensions
exist, the observations do not. Any EU-wide nurse total that includes them
silently understates coverage by roughly a fifth of the member states.

**2. Missing is not zero, and the distinction is load-bearing.**
Germany publishes hospital beds through 2019 and nothing after. A naive join
reports "Germany: 0 hospital beds", which on a dashboard reads as *Germany
closed every hospital*. `v_coverage_index_safe` and `v_data_gaps` return NULL
and an explicit flag instead. Never let a gap render as a zero.

**3. Only 43% of country-years report a full age breakdown.**
Many countries publish TOTAL only. Any age-partitioned metric computed for
them is wrong, and the failure is invisible because the total is present.
`reporting_completeness` records this per country-year.

**4. Eurostat's TOTAL is not always the sum of its own age bands.**
On fully reported country-years the residual reaches 0.53% (physicians) and
1.53% (nurses, one outlier). Small, but it means a strict equality assertion
would fail on the source rather than on our logic. The reconciliation check
allows 1% and fails beyond it.

## Methodological constraint on all regional output

`hlth_rs_phys` and `hlth_rs_nurse` are published at **national level only**.
There is no NUTS dimension on either source table.

Every regional figure this project can produce is therefore an *allocation*: the
national age-sex workforce distributed onto NUTS-level population. That
allocation is a modelling assumption, not an observation, and it affects 6 of
the 10 business questions — most visibly Q1, Q8 (medical deserts) and Q5
(funding priority). It is called out here, in `sql/analytics.sql`, and must
appear on every regional page and in the data dictionary.

A sensitivity analysis against at least one alternative allocation is required
before any regional claim is presented as fact.

## Not yet built

Phases 4–9 of `docs/PLAN.md`: DQ score KPI page, remaining SQL analyses,
ML forecasting (5 model families), GIS maps, Power BI artefacts, documentation
and the portfolio wrap.

Current gap to the brief: the pipeline, dimensional model and analytical base
are real and tested. The forecasting, GIS and BI layers are not started.