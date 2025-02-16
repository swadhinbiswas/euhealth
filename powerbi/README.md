# Power BI semantic layer

| Artefact | Contents |
|---|---|
| [`measures.dax`](measures.dax) | 42 measures, BI-tool native |
| [`../sql/powerbi_measures.sql`](../sql/powerbi_measures.sql) | Same measures as SQL views, for audit and for tools that query the warehouse directly |
| [`../docs/POWERBI.md`](../docs/POWERBI.md) | Connection, relationship model, eight page specs, formatting, accessibility |
| [`../data/geo/`](../data/geo) | Verified NUTS 2021 boundaries (`make geo`) |

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

## Build order

```bash
make ingest     # Eurostat landing zone
make geo        # NUTS boundaries for map visuals
make warehouse  # star schema + quality gate
make regional   # verified NUTS workforce
make forecast   # 7-model comparison
make views      # semantic layer, fails loudly on any bad view
make test
```

## One thing to know before mapping

`fact_regional_workforce` joins `dim_country`, **not** `dim_region`. Countries
report workforce at a different NUTS level than the regional population table
uses — Germany at NUTS 1, the Netherlands at NUTS 2. A `nuts_code` join drops
most regions, which is exactly the trap that made an earlier build report the
Netherlands at 0.96 physicians per 1,000.

## Honest limitations

- **No `.pbix` binary.** The file format cannot be written from the command
  line. Every input needed to build it is here.
- **Regional data covers 12 countries** and effectively ends in 2015. The
  population denominator is a 2023 snapshot, so regional rates are indicative,
  not same-year.
- **Scenario bands are not modelled yet.** Page 8 shows the champion forecast
  against the naive baseline; best/expected/worst bands driven by `proj_25np`
  remain to be built.
- **Hospital risk score weights are stated assumptions**, not fitted
  parameters. They are documented in the measure and should be reviewed by a
  domain expert before use in a funding decision.