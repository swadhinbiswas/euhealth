"""Gold-layer fact construction and DuckDB load.

Each fact states its grain explicitly in ``GRAIN``. Getting the grain wrong is
the most common way a dimensional model produces confidently incorrect numbers,
so it is recorded next to the code that builds it rather than only in a doc.

Grain declarations
------------------
``fact_healthcare_workers``  one row per profession x canonical age band x
                             sex x country x year
``fact_population``          one row per geo x canonical age band x sex x year
``fact_hospital_capacity``   one row per country x year x bed category
``fact_retirement``          one row per profession x age band x country x year
``fact_staffing_shortage``   one row per profession x country x year

``TOTAL`` rows are deliberately excluded from age-partitioned facts: they are
an aggregate over the bands and would double count every worker.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from transform.age_map import to_canonical
from warehouse.dims import (
    BED_FACILITY,
    ICU_FACILITY,
)

#: Declared relationship model. A BI tool infers relationships from column
#: names and usually guesses wrong on a fact table, so the model is stated here
#: and exported to docs/RELATIONSHIPS.md. Cardinality is many-to-one from each
#: fact to every dimension; ``dim_date`` is the many-to-one date table.
RELATIONSHIPS = [
    # (from_table, from_column, to_table, to_column, cardinality, active)
    ("fact_healthcare_workers", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_healthcare_workers", "profession_code", "dim_profession",
     "profession_code", "many-to-one", True),
    ("fact_healthcare_workers", "age_group_code", "dim_age_group",
     "age_group_code", "many-to-one", False),
    ("fact_healthcare_workers", "sex_code", "dim_gender",
     "sex_code", "many-to-one", False),
    ("fact_healthcare_workers", "year", "dim_date",
     "year", "many-to-one", False),
    ("fact_retirement", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_retirement", "profession_code", "dim_profession",
     "profession_code", "many-to-one", True),
    ("fact_retirement", "age_group_code", "dim_age_group",
     "age_group_code", "many-to-one", False),
    ("fact_retirement", "year", "dim_date", "year", "many-to-one", False),
    ("fact_staffing_shortage", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_staffing_shortage", "profession_code", "dim_profession",
     "profession_code", "many-to-one", True),
    ("fact_staffing_shortage", "year", "dim_date",
     "year", "many-to-one", False),
    ("fact_population", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_population", "sex_code", "dim_gender",
     "sex_code", "many-to-one", False),
    ("fact_population", "year", "dim_date", "year", "many-to-one", False),
    ("fact_population_nuts", "nuts_code", "dim_region",
     "nuts_code", "many-to-one", True),
    ("fact_population_nuts", "age_group_code", "dim_age_group",
     "age_group_code", "many-to-one", False),
    ("fact_population_nuts", "sex_code", "dim_gender",
     "sex_code", "many-to-one", False),
    ("fact_population_nuts", "year", "dim_date", "year", "many-to-one", False),
    # fact_regional_workforce intentionally does NOT relate to dim_region on
    # nuts_code. Countries report at a different NUTS level than the regional
    # population table uses (Germany at NUTS 1, the Netherlands at NUTS 2), so
    # a nuts_code-to-nuts_code join drops most regions. The correct key is
    # country_code, with the NUTS level recorded as an attribute for display.
    ("fact_regional_workforce", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_regional_workforce", "profession_code", "dim_profession",
     "profession_code", "many-to-one", True),
    ("fact_regional_workforce", "year", "dim_date",
     "year", "many-to-one", False),
    ("fact_hospital_capacity", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_hospital_capacity", "year", "dim_date", "year", "many-to-one", False),
    ("fact_population_indicators", "country_code", "dim_country",
     "country_code", "many-to-one", True),
    ("fact_population_indicators", "sex_code", "dim_gender",
     "sex_code", "many-to-one", False),
    ("fact_population_indicators", "year", "dim_date",
     "year", "many-to-one", False),
]

GRAIN = {
    "fact_healthcare_workers":
        "profession x age_group x sex x country x year",
    "fact_population": "geo x age_group x sex x year",
    "fact_hospital_capacity": "country x year x bed_category",
    "fact_retirement": "profession x age_group x country x year",
    "fact_staffing_shortage": "profession x country x year",
}

PROFESSION_CODE = {"Physician": "PHYS", "Nurse": "NURS"}
SEX_CODE = {"Total": "T", "Male": "M", "Female": "F"}


# --- facts --------------------------------------------------------------------

def fact_healthcare_workers(df: pd.DataFrame) -> pd.DataFrame:
    """Conformed workforce fact with canonical age bands.

    Expects the combined physicians+nurses frame from
    ``ingestion.eurostat.load_health_workforce``.
    """
    if df.empty:
        return _empty_fact([
            "profession_code", "age_group_code", "sex_code",
            "country_code", "year", "workers", "value",
        ])
    out = df.copy()
    out["year"] = out["time"].astype(int)
    out["profession_code"] = out["profession"].map(PROFESSION_CODE)
    out["sex_code"] = out["sex"] if "sex" in out.columns else "T"
    out["country_code"] = out["geo"]
    out["age_group_code"] = [
        to_canonical("phys" if p == "Physician" else "nurse", a)
        for p, a in zip(out["profession"], out["age"])
    ]
    # Keep the reported TOTAL as its own aggregate measure; drop it from the
    # age partition so bands never double count.
    totals = out[out["age"] == "TOTAL"].copy()
    totals["age_group_code"] = None
    totals["measure"] = "headcount_total"

    bands = out[out["age_group_code"].notna()].copy()
    bands["measure"] = "headcount_by_age"

    facts = pd.concat([bands, totals], ignore_index=True)
    facts["workers"] = pd.to_numeric(facts["value"], errors="coerce")
    return _finalise(facts, [
        "profession_code", "age_group_code", "sex_code",
        "country_code", "year", "measure", "workers",
    ], workers="workers")


def fact_retirement(workforce: pd.DataFrame) -> pd.DataFrame:
    """Retirement exposure: workers in bands approaching retirement.

    Bands 55-64 and 65+ carry the exposure. Retiring counts are modelled in
    Phase 6; this fact is the observable input to that model.
    """
    workers = fact_healthcare_workers(workforce)
    if workers.empty:
        return _empty_fact([
            "profession_code", "age_group_code", "country_code",
            "year", "near_retirement_workers", "value",
        ])
    exposure = workers[
        workers["age_group_code"].isin(["Y55_64", "Y_GE65"])
        & (workers["sex_code"] == "T")
    ].copy()
    grouped = (
        exposure.groupby(
            ["profession_code", "age_group_code", "country_code", "year"],
            as_index=False,
        )["measure_value"]
        .sum()
        .rename(columns={"measure_value": "near_retirement_workers"})
    )
    grouped["near_retirement_workers"] = grouped["near_retirement_workers"]
    return _finalise(grouped, [
        "profession_code", "age_group_code", "country_code",
        "year", "near_retirement_workers",
    ], workers="near_retirement_workers")


def fact_population_country(df: pd.DataFrame) -> pd.DataFrame:
    """National population *indicators* from demo_pjanind.

    These are ratios and shares (PC_Y0_14, MEDAGEPOP, OLDDEP1), not headcounts,
    so they are stored under their own indicator code and never summed into a
    total. Use :func:`fact_population` for actual counts.
    """
    if df.empty:
        return _empty_fact([
            "country_code", "indicator_code", "sex_code",
            "year", "population",
        ])
    out = df.copy()
    out["year"] = out["time"].astype(int)
    out["country_code"] = out["geo"]
    out["indicator_code"] = out["indic_de"]
    out["population"] = pd.to_numeric(out["value"], errors="coerce")
    out["sex_code"] = "T"
    return _finalise(out, [
        "country_code", "indicator_code", "sex_code",
        "year", "population",
    ], workers="population")


def fact_population_nuts(df: pd.DataFrame) -> pd.DataFrame:
    """NUTS-level population, the denominator for regional allocation."""
    if df.empty:
        return _empty_fact([
            "nuts_code", "country_code", "age_group_code",
            "sex_code", "year", "population", "value",
        ])
    out = df.copy()
    out["year"] = out["time"].astype(int)
    out["nuts_code"] = out["geo"]
    out["country_code"] = out["geo"].str[:2]
    out["sex_code"] = out["sex"] if "sex" in out.columns else "T"
    out["population"] = pd.to_numeric(out["value"], errors="coerce")
    out["age_source_code"] = out["age"]

    # demo_r_pjangrp3 uses 5-year bands; roll them up to the canonical set.
    out["age_group_code"] = _nuts_age_rollup(out["age"])
    return _finalise(out, [
        "nuts_code", "country_code", "age_group_code", "sex_code",
        "year", "population", "value",
    ], workers="population")


def _nuts_age_rollup(codes: pd.Series) -> pd.Series:
    """Map NUTS 5-year population bands onto canonical age groups."""
    mapping = {
        "Y_LT5": "Y_LT35", "Y5-9": "Y_LT35", "Y10-14": "Y_LT35",
        "Y15-19": "Y_LT35", "Y20-24": "Y_LT35",
        "Y25-29": "Y35_44", "Y30-34": "Y35_44", "Y35-39": "Y35_44",
        "Y40-44": "Y35_44",
        "Y45-49": "Y45_54", "Y50-54": "Y45_54",
        "Y55-59": "Y55_64", "Y60-64": "Y55_64",
        "Y65-69": "Y_GE65", "Y70-74": "Y_GE65", "Y75-79": "Y_GE65",
        "Y80-84": "Y_GE65", "Y85-89": "Y_GE65", "Y_GE90": "Y_GE65",
    }
    return codes.map(lambda c: mapping.get(c) if c != "TOTAL" else None)


def fact_hospital_capacity(beds: pd.DataFrame,
                           icu: pd.DataFrame | None = None) -> pd.DataFrame:
    """Beds per country/year, with ICU separated out for risk scoring."""
    if beds.empty:
        return _empty_fact([
            "country_code", "year", "bed_category", "beds", "value",
        ])
    frames = []
    b = beds.copy()
    b["year"] = b["time"].astype(int)
    b["country_code"] = b["geo"]
    b["bed_category"] = b["facility"].map(BED_FACILITY).fillna(b["facility"])
    b["category_group"] = "hospital_bed"
    frames.append(b)

    if icu is not None and not icu.empty:
        i = icu.copy()
        i["year"] = i["time"].astype(int)
        i["country_code"] = i["geo"]
        i["bed_category"] = i["facility"].map(ICU_FACILITY).fillna(i["facility"])
        i["category_group"] = "icu_bed"
        frames.append(i)

    out = pd.concat(frames, ignore_index=True)
    # Keep headcounts only; HAB_P / P_HTHAB are per-capita ratios and must not
    # be summed into a bed count.
    out = out[out["unit"] == "NR"] if "unit" in out.columns else out
    out["beds"] = pd.to_numeric(out["value"], errors="coerce")
    return _finalise(out, [
        "country_code", "year", "bed_category", "category_group", "beds",
    ], workers="beds")


def fact_population(df: pd.DataFrame) -> pd.DataFrame:
    """Total national population per country-year.

    Expects ``ingestion.eurostat.load_population_total``, which sources counts
    from demo_r_pjangrp3. demo_pjanind is not usable here because it holds
    ratios (PC_Y0_14 etc.), not headcounts.
    """
    if df.empty:
        return _empty_fact([
            "country_code", "sex_code", "year", "population",
        ])
    out = df.copy()
    out["year"] = out["time"].astype(int)
    out["country_code"] = out["geo"]
    out["sex_code"] = out.get("sex", "T")
    out["population"] = pd.to_numeric(out["value"], errors="coerce")
    return _finalise(out, [
        "country_code", "sex_code", "year", "population",
    ], workers="population")


def fact_staffing_shortage(
    workforce: pd.DataFrame,
    population: pd.DataFrame,
    reference_by_profession: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Observed gap between workforce and a reference staffing ratio.

    Required workers = reference ratio x population / 1000. The gap is
    required minus actual, so a positive gap is a shortage. The population
    input must be total headcount from ``fact_population``.

    ``reference_by_profession`` maps a profession code to its reference ratio
    per 1,000. This must be per profession: physicians and nurses have
    different benchmarks (3.3 and 9.0), and applying one ratio to both makes
    nurses look 250% staffed because they are measured against a doctor-sized
    target. A single scalar is accepted for back-compatibility and applied to
    every profession, which is only correct when the benchmarks match.
    """
    workers = fact_healthcare_workers(workforce)
    if workers.empty or population is None or population.empty:
        return _empty_fact([
            "profession_code", "country_code", "year",
            "actual_workers", "required_workers",
            "shortage", "coverage_index", "total_population",
        ])
    # Accept either a built fact or a raw Eurostat frame, so callers cannot
    # silently produce a shortage fact with no country dimension.
    pop_source = population
    if "country_code" not in pop_source.columns:
        if "geo" not in pop_source.columns:
            return _empty_fact([
                "profession_code", "country_code", "year",
                "actual_workers", "required_workers",
                "shortage", "coverage_index", "total_population",
            ])
        pop_source = pop_source.copy()
        pop_source["country_code"] = pop_source["geo"]
    if "year" not in pop_source.columns and "time" in pop_source.columns:
        pop_source = pop_source.copy()
        pop_source["year"] = pop_source["time"].astype(int)
    if "population" not in pop_source.columns:
        pop_source = pop_source.copy()
        pop_source["population"] = pd.to_numeric(
            pop_source["value"], errors="coerce"
        )
    actual = (
        workers[(workers["sex_code"] == "T")
                & (workers["measure"] == "headcount_total")]
        .groupby(["profession_code", "country_code", "year"], as_index=False)
        ["measure_value"].sum()
        .rename(columns={"measure_value": "actual_workers"})
    )
    pop = (
        pop_source.groupby(["country_code", "year"], as_index=False)["population"]
        .sum()
        .rename(columns={"population": "total_population"})
    )
    merged = actual.merge(pop, on=["country_code", "year"], how="inner")

    if reference_by_profession is None:
        # Fall back to a single ratio for every profession.
        merged["required_workers"] = (
            merged["total_population"] / 1000 * 3.3
        )
    else:
        # Each profession is measured against its own benchmark.
        merged["reference_per_1000"] = merged["profession_code"].map(
            reference_by_profession
        )
        merged["required_workers"] = (
            merged["total_population"] / 1000 * merged["reference_per_1000"]
        )

    merged["shortage"] = merged["required_workers"] - merged["actual_workers"]
    merged["coverage_index"] = (
        merged["actual_workers"] / merged["required_workers"] * 100
    )
    return _finalise(merged, [
        "profession_code", "country_code", "year",
        "actual_workers", "required_workers",
        "shortage", "coverage_index", "total_population",
        "reference_per_1000",
    ], workers="shortage", first_cols=[
        "actual_workers", "required_workers", "coverage_index",
        "total_population", "reference_per_1000",
    ])


# --- helpers ------------------------------------------------------------------

def _finalise(df: pd.DataFrame, columns: list[str], workers: str,
              first_cols: list[str] | None = None) -> pd.DataFrame:
    """Select, de-duplicate and aggregate to the declared grain.

    Aggregating rather than asserting uniqueness means an upstream loader
    change cannot silently multiply headcounts. The summed measure is emitted
    under the requested ``workers`` name so every fact exposes its measure
    consistently.

    ``first_cols`` lists non-additive columns (ratios, indexes) that are
    carried through with ``first`` instead of ``sum``. Summing a coverage
    ratio across rows would produce a meaningless number.
    """
    cols = [c for c in columns if c in df.columns]
    out = df[cols].copy()
    if workers not in out.columns:
        return out.drop_duplicates().reset_index(drop=True)

    first_cols = [c for c in (first_cols or []) if c in out.columns]
    grains = [c for c in out.columns
              if c not in (workers, "value", *first_cols)]

    agg: dict[str, str] = {workers: "sum"}
    for col in first_cols:
        agg[col] = "first"
    out = out.groupby(grains, as_index=False, dropna=False).agg(agg)
    out["measure_value"] = out[workers]
    return out.sort_values(grains).reset_index(drop=True)


def _empty_fact(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


# --- warehouse ----------------------------------------------------------------

def build_warehouse(
    facts: dict[str, pd.DataFrame],
    path: Path,
    dimensions: dict[str, pd.DataFrame] | None = None,
) -> dict[str, int]:
    """Write dimensions and facts to DuckDB, replacing prior versions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    counts: dict[str, int] = {}
    try:
        for name, frame in (dimensions or {}).items():
            con.register(f"_stage_{name}", frame)
            con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _stage_{name}')
            con.unregister(f"_stage_{name}")
            counts[name] = len(frame)
        for name, frame in facts.items():
            con.register(f"_stage_{name}", frame)
            con.execute(
                f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _stage_{name}'
            )
            con.unregister(f"_stage_{name}")
            counts[name] = len(frame)
    finally:
        con.close()
    return counts