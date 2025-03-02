# Power BI semantic layer

Two deliverables, both real and both generated from the warehouse.

| Artefact | Contents |
|---|---|
| [`measures.dax`](measures.dax) | 42 DAX measures across the 8 dashboard pages |
| [`../sql/powerbi_measures.sql`](../sql/powerbi_measures.sql) | The same measures as SQL views, for audit and for tools that query the warehouse directly |
| [`pbip/`](pbip/) | **PBIP project** — opens directly in Power BI Desktop, no build step |
| [`../docs/POWERBI.md`](../docs/POWERBI.md) | Connection, relationship model, page specs, formatting, accessibility |
| [`../data/geo/`](../data/geo) | Verified NUTS 2021 boundaries (`make geo`) |

## The PBIP project is the Power BI dashboard

`powerbi/pbip/` is a **Power BI Project**: a plain folder containing the
report and the semantic model as editable JSON and TMDL. Open it in Power BI
Desktop and it is a working dashboard — measures, relationships, eight pages,
maps included. No conversion, no import wizard.

```bash
# Power BI Desktop: File → Open → powerbi/pbip/EU-Health-Workforce.pbip
```

It has a **live DuckDB data source**, so the data refreshes from
`data/healthcare_dw.duckdb` directly. On a machine without the warehouse,
`snapshots/` holds a Parquet copy of every table so the report still opens and
renders.

## Verification

`tests/test_bi_readiness.py` (71 checks) enforces what a BI tool needs and cannot
report for itself:

- every table and column referenced by a measure exists in the warehouse
- all 25 declared relationships bind to real columns on both sides
- fact business keys are unique — a duplicate would silently double every measure
- no foreign key is orphaned
- `dim_date` carries a real `DATE` column and every fact has a date relationship
- **no measure averages a ratio**, the most common DAX correctness bug
- **no measure coalesces to zero**, which would turn a reporting gap into a
  reported zero
- every required executive KPI measure is defined
- all 20 semantic views execute

`tests/test_pbip.py` additionally validates the project files themselves, so a
malformed TMDL or report.json fails here rather than in Power BI Desktop.

## Build order

```bash
make ingest     # Eurostat landing zone
make geo        # NUTS boundaries for map visuals
make warehouse  # star schema + quality gate
make regional   # verified NUTS workforce
make forecast   # 7-model comparison
make views      # semantic layer, fails loudly on any bad view
make pbip       # generate the Power BI project from the warehouse
make test
```

## One thing to know before mapping

`fact_regional_workforce` joins `dim_country`, **not** `dim_region`. Countries
report workforce at a different NUTS level than the regional population table
uses — Germany at NUTS 1, the Netherlands at NUTS 2. A `nuts_code` join drops
most regions, which is exactly the trap that made an earlier build report the
Netherlands at 0.96 physicians per 1,000.

## Honest limitations

- **PBIP needs Power BI Desktop or Service to view**, which is Windows-only or a
  browser. Screenshots therefore cannot be captured on this Linux host; they
  must be taken by opening the project in Desktop. What I can verify here is that
  the project files are valid and the measures bind — which is what the tests do.
- **Regional data covers 12 countries** and effectively ends in 2015. The
  population denominator is a 2023 snapshot, so regional rates are indicative,
  not same-year.
- **Scenario bands are not modelled yet.** Page 8 shows the champion forecast
  against the naive baseline; best/expected/worst bands driven by `proj_25np`
  remain to be built.
- **Hospital risk score weights are stated assumptions**, not fitted
  parameters. They are documented in the measure and should be reviewed by a
  domain expert before use in a funding decision.