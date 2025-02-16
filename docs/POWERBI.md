# Power BI: model and page specifications

Everything needed to build the report in Power BI Desktop. The `.pbix` binary is
not generated from the command line, so this document plus
[`powerbi/measures.dax`](../powerbi/measures.dax) and the SQL views are the
deliverables.

## 1. Get the data

**Option A — DuckDB file (recommended for development)**

The warehouse is a single file: `data/healthcare_dw.duckdb`. In Power BI
Desktop use **Get Data → Database → DuckDB**, or import the tables directly and
then **Save as → Parquet** to publish to the Power BI Service.

| Setting | Value |
|---|---|
| File | `data/healthcare_dw.duckdb` |
| Mode | Import (the whole warehouse is under 3 MB) |
| Key columns | Business keys as verified in `tests/test_bi_readiness.py` |

**Option B — published dataset (recommended for sharing)**

```bash
make views    # rebuilds every semantic view after any data change
```

Upload `data/healthcare_dw.duckdb` as a read-only artifact, or convert the
tables to Parquet first:

```sql
COPY fact_healthcare_workers TO 'export/fact_healthcare_workers.parquet' (FORMAT PARQUET);
COPY dim_country            TO 'export/dim_country.parquet'            (FORMAT PARQUET);
```

**Map shapes.** Run `make geo`. That installs verified NUTS 2021 boundaries to
`data/geo/`:

| File | Use |
|---|---|
| `nuts_level0_60m.geojson` | EU country map |
| `nuts_level1_10m.geojson` | Country drill-down (Germany reports at this level) |
| `nuts_level2_10m.geojson` | Region drill-down (most countries) |
| `nuts_level3_10m.geojson` | Fine regional detail |
| `nuts_level2_60m.geojson` | Small multiples where size matters |

For **Shape Map**, paste the file URL into the shape-map settings or upload the
GeoJSON via the Azure Blob connector. For **Azure Map**, use the NUTS code as
the location key and skip the shape file entirely.

## 2. Relationship model

25 many-to-one relationships, declared in
`src/warehouse/facts.py::RELATIONSHIPS` and asserted by tests so they cannot
drift from the data.

```text
dim_country  1 ────< fact_healthcare_workers
            1 ────< fact_retirement
            1 ────< fact_staffing_shortage
            1 ────< fact_population
            1 ────< fact_hospital_capacity
            1 ────< fact_population_indicators
            1 ────< fact_regional_workforce        (ACTIVE)

dim_profession 1 ────< fact_healthcare_workers
               1 ────< fact_retirement
               1 ────< fact_staffing_shortage
               1 ────< fact_regional_workforce      (ACTIVE)

dim_age_group 1 ────< fact_healthcare_workers
            1 ────< fact_retirement
            1 ────< fact_population_nuts

dim_gender 1 ────< fact_healthcare_workers
          1 ────< fact_population
          1 ────< fact_population_nuts
          1 ────< fact_population_indicators

dim_region  1 ────< fact_population_nuts            (ACTIVE)

dim_date    1 ────< every fact                       (INACTIVE, filters dim_date)
```

Two decisions worth stating, because both are counter-intuitive:

**`dim_date` relationships are inactive.** A date dimension filters facts but
not each other; activating it would let a year slicer on one visual silently
restrict unrelated visuals. Set **Mark as date table** on `dim_date[Date]` in
Model view, which is what enables `SAMEPERIODLASTYEAR` and `DATESINPERIOD`.

**`fact_regional_workforce` joins `dim_country`, not `dim_region`.** Countries
report workforce at a different NUTS level than the regional population table
uses — Germany at NUTS 1, the Netherlands at NUTS 2 — so a `nuts_code` join
drops most regions. `nuts_level` is carried on the fact for display.

## 3. Pages

Eight pages. Every page states its measures and, where relevant, its caveat.

### Page 1 — Executive Overview

| Element | Measure |
|---|---|
| KPI cards | `Total Workers`, `Total Doctors`, `Total Nurses`, `Workforce Gap %`, `Retirement Risk %`, `Coverage Index`, `Hospital Risk Score` |
| Trend | `Total Workers` by year, line |
| Chart | `Shortage Severity Rank` top 10 by country |
| Context strip | `Countries Reporting Nurses`, `Hospitals at Critical Risk` |

Cards carry a conditional-format icon comparing against the previous year via
`Gap YoY Change`.

### Page 2 — Workforce Map

Azure Map or Shape Map, `dim_region[nuts_code]` as the location key, colour by
`Regional Coverage`. Drill path: EU → country → NUTS 1 → NUTS 2.

Bind to `v_regional_risk`, which already carries `access_tier`.

> **Caveat to display on this page:** regional workforce is *observed*, but
> verified for only 12 countries and it effectively ends in 2015. The
> population denominator is a 2023 snapshot, so the rate is indicative rather
> than same-year.

### Page 3 — Doctor Shortage

`v_profession_shortage_rank` filtered to `profession_code = "PHYS"`. Bar chart
by country, `Coverage Gap Trend` line with `yoy_change`, `Gap Rolling 3Y` for
trend smoothing.

### Page 4 — Nurse Workforce

Same layout as page 3 on `"NURS"`. Add a warning visual driven by
`Nurses Reported`: six member states publish no nurse data, so an EU-wide total
that includes them understates coverage by about a fifth of the member states.

### Page 5 — Workforce Aging

Age pyramid from `v_age_pyramid` (back-to-back bar: male left, female right,
`sort_order` on the axis). Beside it `Workforce Aging Index` by country and
`Replacement Demand` as the absolute count.

### Page 6 — Hospital Capacity

Matrix of country × bed category. `Hospital Risk Score` by country with
`Hospital Risk Tier` for the conditional-format bands.

### Page 7 — Medical Desert Detection

Map coloured by `access_tier`. Table of `Medical Desert Score` with
`Population In Deserts` as the equity figure for a funding decision.

### Page 8 — Forecasting Centre

`v_forecast_vs_baseline` for 2027–2035, with `model_uplift` shown alongside so a
reader can see what the model adds over carrying the last value forward.

Chart the champion model only. Publishing all seven would be misleading: ARIMA
(4.99% MAPE) beat naive (5.50), Prophet (5.74), drift (6.16) and every tree
model, and the tree models scored worse because they cannot extrapolate.

## 4. Formatting

| Measure type | Format string |
|---|---|
| Counts (`Total Workers`) | `#,0` |
| Percentages (`Coverage Index`) | `0.0%` |
| Per-1,000 rates (`Regional Coverage`) | `0.00` |
| Risk scores (`Hospital Risk Score`) | `0.0` — **not** a percentage; it is 0–100 |

## 5. Accessibility

- Colour is never the only encoding: tier labels accompany every colour band.
- Minimum 4.5:1 contrast on text; the four access tiers also differ in
  lightness, so they survive greyscale and the common forms of colour blindness.
- Alt text on every visual stating what it shows and its main caveat.
- Tab order follows the visual reading order left to right, top to bottom.

## 6. Refresh

Daily is more often than the source warrants. Eurostat updates `hlth_rs_*`
annually, so a weekly schedule is appropriate; `make ingest` is idempotent and
re-reads only what is missing. `data/logs/ingestion.jsonl` records every fetch,
including the `EMPTY` status that flags a source which returned no observations.