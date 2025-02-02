"""Build the regional (NUTS) layer.

Usage:
    python -m src.geo.cli
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from config import WAREHOUSE  # noqa: E402
from geo.regional import (  # noqa: E402
    keep_reconciling,
    ISCO_PROFESSION,
    annotate_nuts,
    build_regional_workforce,
    detect_reporting_level,
    load_nuts_index,
    reconciliation_report,
)
from ingestion import eurostat  # noqa: E402

OUT = ROOT / "data" / "export"
OUT.mkdir(parents=True, exist_ok=True)

#: The last year in which hlth_rs_prsrg is broadly reported. Coverage falls
#: away sharply after this (2015 -> ~200 regions by 2020, 39 by 2021), so
#: anchoring here keeps the regional layer observed rather than extrapolated.
ANCHOR_YEAR = "2015"

#: National benchmarks, one per ISCO group, chosen to match the regional table's
#: own occupation definition. Using hlth_rs_nurse for the ISCO "nurses and
#: midwives" group reconciles for almost no country, because the two tables do
#: not cover the same population.
BENCHMARKS = {
    "OC221": ("physicians", "hlth_rs_phys"),
    "OC222_322": ("nursing_prsns", "hlth_rs_prsns"),
}


def national_benchmark(isco: str) -> pd.DataFrame:
    """National headcount for one ISCO group, in loader frame shape."""
    if isco == "OC221":
        df = eurostat.load_physicians()
        df = df[(df["sex"] == "T") & (df["age"] == "TOTAL")]
        return df
    df = eurostat.load_regional_nursing()
    df = df[
        (df["unit"] == "NR")
        & (df["isco08"] == isco)
        & (df["wstatus"] == "PRACT")
    ]
    return df.groupby(["geo", "time"], as_index=False)["value"].sum()


def build() -> dict:
    index = load_nuts_index()
    raw = eurostat.load_regional_workforce()
    regional = annotate_nuts(raw, index)
    regional = regional[regional["nuts_level"].notna()]
    print(f"regional workforce rows: {len(regional):,} "
          f"(geo codes recognised against NUTS 2021)")

    # --- detect each country's reporting level, per occupation group -------
    levels = []
    for isco in BENCHMARKS:
        benchmark = national_benchmark(isco)
        if benchmark.empty:
            print(f"  {isco}: no national benchmark available")
            continue
        detected = detect_reporting_level(
            regional[regional["isco08"] == isco], benchmark, ANCHOR_YEAR
        )
        if detected.empty:
            continue
        detected["isco08"] = isco
        detected["profession_code"] = ISCO_PROFESSION[isco]
        levels.append(detected)
        ok = int(detected["reconciles"].sum())
        print(f"  {isco} ({ISCO_PROFESSION[isco]}): "
              f"{ok}/{len(detected)} countries reconcile at a single NUTS level")

    level_table = pd.concat(levels, ignore_index=True) if levels else pd.DataFrame()
    if level_table.empty:
        raise SystemExit("no reporting level could be detected")

    level_table.to_csv(OUT / "regional_reporting_levels.csv", index=False)

    # --- build the fact, restricted to reconciling countries ---------------
    fact = build_regional_workforce(regional, level_table)

    # --- verify, and publish only what reconciles --------------------------
    # Some country-years in the discontinued series contain duplicated region
    # rows that double the regional sum. Those are dropped rather than
    # published, so every figure in the fact is observed AND verified.
    verified = []
    for isco, (label, _) in BENCHMARKS.items():
        benchmark = national_benchmark(isco)
        if benchmark.empty:
            continue
        target = ISCO_PROFESSION[isco]
        subset = keep_reconciling(fact, benchmark, target)
        verified.append((target, subset))
    fact = pd.concat([s for _, s in verified], ignore_index=True) if verified \
        else fact

    # Final guard: re-verify the concatenated fact per profession. Building
    # the union above can reintroduce country-years that one profession's
    # filter removed, so the published table is checked once more and only
    # reconciling rows survive.
    final = []
    for isco, (label, _) in BENCHMARKS.items():
        benchmark = national_benchmark(isco)
        if benchmark.empty:
            continue
        target = ISCO_PROFESSION[isco]
        final.append(keep_reconciling(fact, benchmark, target))
    if final:
        fact = pd.concat(final, ignore_index=True)

    fact = fact.sort_values(
        ["country_code", "year", "profession_code", "nuts_code"]
    ).reset_index(drop=True)

    print(f"\nfact_regional_workforce: {len(fact):,} rows, "
          f"{fact['nuts_code'].nunique()} regions, "
          f"{fact['country_code'].nunique()} countries, "
          f"years {fact['year'].min()}-{fact['year'].max()}")

    # --- prove it reconciles ------------------------------------------------
    checks = []
    for isco, (label, _) in BENCHMARKS.items():
        benchmark = national_benchmark(isco)
        report = reconciliation_report(
            fact, benchmark, profession_code=ISCO_PROFESSION[isco]
        )
        if report.empty:
            continue
        report.insert(0, "benchmark", label)
        checks.append(report)
    if checks:
        check_table = pd.concat(checks, ignore_index=True)
        rate = check_table["within_tolerance"].mean() * 100
        print(f"reconciliation vs national totals: "
              f"{check_table['within_tolerance'].sum()}/{len(check_table)} "
              f"country-years within tolerance ({rate:.1f}%)")
        worst = check_table[~check_table["within_tolerance"]]
        if not worst.empty:
            print("  worst offenders:")
            print(worst.head(5)[["benchmark", "country_code", "year",
                                 "value", "regional_sum", "rel_diff"]]
                  .to_string(index=False))
        check_table.to_csv(OUT / "regional_reconciliation.csv", index=False)

    # --- write into the warehouse ------------------------------------------
    import duckdb

    con = duckdb.connect(str(WAREHOUSE))
    try:
        con.register("_regional", fact)
        con.execute(
            "CREATE OR REPLACE TABLE fact_regional_workforce AS "
            "SELECT * FROM _regional"
        )
        con.unregister("_regional")
        con.register("_levels", level_table)
        con.execute(
            "CREATE OR REPLACE TABLE dim_region_reporting AS "
            "SELECT * FROM _levels"
        )
        con.unregister("_levels")
    finally:
        con.close()

    print(f"\nwritten to {WAREHOUSE}")
    return {"fact_rows": len(fact), "levels": len(level_table)}


if __name__ == "__main__":
    build()