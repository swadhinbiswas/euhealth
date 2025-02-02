# Regional layer: what is observed, and what is not

`python -m src.geo.cli` builds `fact_regional_workforce`.

## Correction to an earlier claim

I previously stated that Eurostat publishes health workforce **nationally only**
and that every regional figure would therefore have to be an allocation.

**That was wrong.** It was true of the *current* series (`hlth_rs_phys`,
`hlth_rs_nurse`), which have no NUTS dimension. But Eurostat also publishes
`hlth_rs_prsrg`, "Health personnel by NUTS 2 region", with **320 regions** and
**77,008 observations**. Regional workforce in this project is therefore
**observed, not allocated**.

## The trap that table contains

`hlth_rs_prsrg` is reported at a **different NUTS level per country**:

| Country | Reporting level | Regions (2015, doctors) |
|---|---|---|
| Germany | NUTS **1** | 16 |
| Austria, Belgium, Greece, Spain, Italy, Netherlands, Portugal, Romania, Sweden, Slovakia | NUTS **2** | 2-21 |
| France, Poland | NUTS 2 **and** NUTS 3 in one table | mixed |

Summing the `geo` column therefore **double counts France by 91.6%** and
**Poland by 25.3%**, while producing a number that looks entirely reasonable.
Germany, Italy and Spain happen to reconcile exactly, which makes the problem
easy to miss.

`src/geo/regional.py` prevents this by assigning every code its level from the
official NUTS 2021 classification (GISCO, 2,010 codes), then determining which
level each country actually reports at by reconciling candidates against the
national total. A country that reconciles at no single level is excluded rather
than assigned one.

## Result

```
fact_regional_workforce: 6,077 rows, 112 regions, 12 countries, 2000-2020
```

Reporting-level detection, anchored on 2015:

- **Doctors (`OC221`): 16 of 25 countries** reconcile at a single NUTS level
- **Nurses (`OC222_322`): 6 of 21 countries** reconcile

Reconciliation of the published fact against national totals: **79.3%** of
country-years within 5%.

## Honest limitations

**1. The regional series is discontinued.** Coverage is strong to 2015 and
collapses afterwards — 4,063 values (2014), 1,625 (2016), 671 (2020), 54 (2021).
Regional figures after ~2015 are thin and should be treated as indicative. The
layer is keyed by ISCO08 occupation, not by age or sex, so no regional age
pyramid or retirement projection is possible from this source.

**2. Twelve countries only.** Reporting-level detection plus reconciliation
excludes 13 of 25. That is the price of not publishing double-counted figures.

**3. Open issue — the reconciliation filter is not fully effective.**
Denmark nurses remain **+66%** over the national benchmark (not a double count;
a definitional mismatch between ISCO `OC222_322` regionally and `hlth_rs_prsns`
nationally). The `keep_reconciling` filter should have removed these rows and
partly fails to, so 20.7% of published country-years still sit outside
tolerance. Fixing this means filtering per profession group in a single pass
rather than concatenating per-profession results. Until then, **treat
`data/export/regional_reconciliation.csv` as the authoritative filter** and join
on it before using any regional figure.

**4. Twelve countries is a small base for EU-level claims.** Regional ranking
across 112 regions is defensible; a Europe-wide "medical desert" verdict built
on this is not.

## Artefacts

| File | Contents |
|---|---|
| `data/geo/nuts_level_index.json` | 2,010 official NUTS codes with level and name |
| `data/export/regional_reporting_levels.csv` | Detected level per country/profession |
| `data/export/regional_reconciliation.csv` | Regional vs national, per country-year |
| `fact_regional_workforce` (DuckDB) | Verified regional workforce |
| `dim_region_reporting` (DuckDB) | Which level each country reports at |

## What this means for the original caveat

The allocation problem is now **smaller but not gone**. Observed regional supply
exists to ~2015 for 12 countries. Forward-looking regional projections to 2030/35
still require an explicit allocation assumption, and that assumption should be
stated and sensitivity-tested as originally planned.