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
PHYS: kept 2024/3749 rows (1725 country-years failed reconciliation)
NURS: kept  216/1756 rows (1540 country-years failed reconciliation)

fact_regional_workforce: 2,240 rows, 111 regions, 12 countries, 2000-2020
reconciliation vs national totals: 238/238 country-years (100.0%)
```

Reporting-level detection, anchored on 2015:

- **Doctors (`OC221`): 16 of 25 countries** reconcile at a single NUTS level
- **Nurses (`OC222_322`): 6 of 21 countries** reconcile

**Every published row is verified against a national total.** Country-years that
do not reconcile are dropped rather than published.

## Why coverage shrank, and why that is the right trade

An earlier build published 6,077 rows with 79.3% reconciliation. Filtering each
profession group in isolation — rather than applying one profession's allowed
set to the whole fact, which had been retaining the other profession's rows —
brought reconciliation to 100% and coverage to 2,240 rows.

The retention rates are themselves a finding:

| Profession | Retained country-years |
|---|---|
| Physicians | **54%** (2024 / 3749) |
| Nurses | **12%** (216 / 1756) |

Most of that loss is not a data fault but a genuine disagreement between two
Eurostat tables: `hlth_rs_prsrg` (regional, ISCO08) and `hlth_rs_prsns`
(national, ISCO08) do not cover the same population. Denmark is the clearest
case — its NUTS 2 rows sum to 96,078, internally consistent, while the national
table reports 57,897 for the same year and country, a 66% divergence.

Publishing only verified rows costs coverage and buys a guarantee. The
alternative is a regional map that looks authoritative and is wrong in places.

## Artefacts

| File | Contents |
|---|---|
| `data/geo/nuts_level_index.json` | 2,010 official NUTS codes with level and name |
| `data/export/regional_reporting_levels.csv` | Detected level per country/profession |
| `data/export/regional_reconciliation.csv` | Regional vs national, per country-year |
| `fact_regional_workforce` (DuckDB) | Verified regional workforce |
| `dim_region_reporting` (DuckDB) | Which level each country reports at |

## Honest limitations

**1. The regional series is discontinued.** Coverage is strong to 2015 and
collapses afterwards — 4,063 values (2014), 1,625 (2016), 671 (2020), 54 (2021).
Regional figures after ~2015 are thin and should be treated as indicative.

**2. It is keyed by ISCO08 occupation, not age or sex.** No regional age
pyramid or regional retirement projection is possible from this source.

**3. Twelve countries survive verification.** Reporting-level detection plus
reconciliation excludes 13 of 25. That is the price of not publishing
double-counted or unreconciled figures.

**4. Twelve countries is a small base for EU-level claims.** Ranking across 111
regions is defensible. A Europe-wide "medical desert" verdict built on this is
not.

## What this means for the original caveat

The allocation problem is now **smaller but not gone**. Observed regional supply
exists to ~2015 for 12 countries. Forward-looking regional projections to 2030/35
still require an explicit allocation assumption, and that assumption should be
stated and sensitivity-tested as originally planned.