"""Dimension construction.

Dimensions are built conformed so every fact joins on the same surrogate key
regardless of which Eurostat table the measure came from. That is what lets a
single query span workforce, capacity and demand without re-mapping codes.

Slowly changing dimensions
--------------------------
``dim_country`` and ``dim_hospital`` are SCD Type 2: a country that is renamed
or a hospital that changes ownership must not rewrite history in past facts.
``valid_from``/``valid_to`` bracket each version and ``is_current`` marks the
live one. The remaining dimensions are effectively immutable, so they are SCD
Type 1 and carry no versioning columns.
"""
from __future__ import annotations

import pandas as pd

from ingestion.registry import EU27, EU27_ISO3
from transform.age_map import AGE_CODE_MAP

EU27_NAMES = {
    "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "DE": "Germany", "EL": "Greece", "HU": "Hungary",
    "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LT": "Lithuania",
    "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "AT": "Austria",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
}

SEX_LABEL = {"T": "Total", "M": "Male", "F": "Female"}

#: Eurostat hlth_rs_prs2 med_spec codes.
MED_SPEC = {
    "PHYS": "Physician", "MWS": "Midwife", "NRS": "Nurse",
    "DENT": "Dentist", "PHARM": "Pharmacist",
    "PER_CARE": "Personal care worker", "PHYSIO": "Physiotherapist",
}

#: hlth_rs_bds facility codes.
BED_FACILITY = {
    "HBEDT": "Total hospital beds", "HBEDT_CUR": "Curative beds",
    "HBEDT_REH": "Rehabilitation beds", "HBEDT_LT": "Long-term care beds",
    "HBEDT_OTH": "Other hospital beds", "HBEDI_PSY": "Psychiatric beds",
}

#: hlth_rs_bdsicu facility codes.
ICU_FACILITY = {
    "ICU_BED": "Intensive care beds", "ICU_BED_ADL": "Adult intensive care",
    "ICU_BED_CRIT_ADL": "Adult critical care",
    "ICU_BED_NEO": "Neonatal intensive care",
    "ICU_BED_PAED": "Paediatric intensive care",
}

#: hlth_rs_bds2 owner codes.
BED_OWNER = {"PUB": "Public", "PRV_NP": "Private non-profit",
             "PRV_P": "Private for-profit"}

#: hlth_rs_wkmg tngplace codes -> foreign-trained dependency.
TRAINING_PLACE = {
    "TOTAL": "Total", "DOM": "Domestically trained",
    "FOR": "Foreign-trained", "NAT_FOR": "Nationally trained, foreign diploma",
    "FOR_IF": "Foreign-trained, in force", "UNK": "Unknown",
}

SECTORS = [
    "Hospital", "Primary Care", "Long-Term Care",
    "Community Care", "Pharmacy", "Public Health",
]


def _keys(df: pd.DataFrame, name: str, start: int = 1) -> pd.DataFrame:
    """Assign a dense surrogate key, ordered deterministically."""
    out = df.reset_index(drop=True)
    out.insert(0, f"{name}_key", range(start, start + len(out)))
    return out


def dim_country() -> pd.DataFrame:
    """SCD Type 2 country dimension.

    One current version per member state. ``valid_to`` is NULL for the live
    row, which is the portable convention across DuckDB, Postgres, BigQuery
    and Snowflake.
    """
    rows = []
    for iso2 in sorted(EU27):
        rows.append({
            "country_code": iso2,
            "iso3_code": EU27_ISO3[iso2],
            "country_name": EU27_NAMES[iso2],
            "nuts_prefix": EU27_ISO3[iso2],
            "is_eu27": True,
            "valid_from": pd.Timestamp("2000-01-01"),
            "valid_to": pd.NaT,
            "is_current": True,
            "version": 1,
        })
    return _keys(pd.DataFrame(rows), "country")


def dim_region(nuts_codes: pd.Series | None = None,
               level: str = "NUTS2") -> pd.DataFrame:
    """NUTS region dimension, built from observed NUTS identifiers.

    ``country_code`` is derived from the NUTS code prefix, which is how Eurostat
    encodes the hierarchy (e.g. ``DE30`` -> ``DE``).
    """
    if nuts_codes is None or len(nuts_codes) == 0:
        return pd.DataFrame(columns=[
            "region_key", "nuts_code", "country_code", "region_level",
            "region_name",
        ])
    codes = sorted(set(nuts_codes.dropna().astype(str)))
    rows = []
    for code in codes:
        prefix = code[:2]
        rows.append({
            "nuts_code": code,
            "country_code": prefix,
            "region_level": level,
            "region_name": None,  # joined from GISCO labels when available
        })
    return _keys(pd.DataFrame(rows), "region")


def dim_age_group() -> pd.DataFrame:
    """Canonical age bands, independent of any source profession."""
    rows = []
    for band, spec in _canonical_bands().items():
        rows.append({
            "age_group_code": band,
            "age_group_label": spec["label"],
            "min_age": spec["min_age"],
            "max_age": spec["max_age"],
            "sort_order": list(_canonical_bands()).index(band),
            "is_retirement_age": band in ("Y55_64", "Y_GE65"),
        })
    return _keys(pd.DataFrame(rows), "age")


def _canonical_bands() -> dict:
    from ingestion.registry import CANONICAL_AGE_BANDS

    return CANONICAL_AGE_BANDS


def dim_gender() -> pd.DataFrame:
    rows = [{"sex_code": c, "sex_label": label, "is_total": c == "T"}
            for c, label in SEX_LABEL.items()]
    return _keys(pd.DataFrame(rows), "gender")


def dim_profession() -> pd.DataFrame:
    """Profession dimension plus the source age-code mapping.

    Carries ``source_age_codes`` so a consumer can see which raw Eurostat codes
    roll into this profession's bands without leaving the warehouse.
    """
    rows = [
        {"profession_code": "PHYS", "profession_name": "Physician",
         "age_code_set": "phys"},
        {"profession_code": "NURS", "profession_name": "Nurse",
         "age_code_set": "nurse"},
        {"profession_code": "MWS", "profession_name": "Midwife",
         "age_code_set": "nurse"},
        {"profession_code": "DENT", "profession_name": "Dentist",
         "age_code_set": "nurse"},
        {"profession_code": "PHARM", "profession_name": "Pharmacist",
         "age_code_set": "nurse"},
    ]
    out = _keys(pd.DataFrame(rows), "profession")
    out["source_age_codes"] = out["age_code_set"].map(
        lambda s: ",".join(sorted(AGE_CODE_MAP[s]))
    )
    return out.drop(columns=["age_code_set"])


def dim_specialization() -> pd.DataFrame:
    rows = [{"spec_code": k, "spec_name": v} for k, v in sorted(MED_SPEC.items())]
    return _keys(pd.DataFrame(rows), "specialization")


def dim_sector() -> pd.DataFrame:
    rows = [{"sector_code": f"S{i+1}", "sector_name": n}
            for i, n in enumerate(SECTORS)]
    return _keys(pd.DataFrame(rows), "sector")


def dim_date(years: range | list[int] | None = None) -> pd.DataFrame:
    """Degenerate-ish date dimension, one row per year.

    Facts are annual, so a year-level dimension avoids inventing a granularity
    the data does not support.
    """
    if years is None:
        years = range(2000, 2036)
    years = sorted(set(int(y) for y in years))
    rows = []
    for y in years:
        rows.append({
            "year": y,
            "year_start": pd.Timestamp(f"{y}-01-01"),
            "is_observed": y <= pd.Timestamp.now("UTC").year,
            "is_projection": y > pd.Timestamp.now("UTC").year,
            "decade": (y // 10) * 10,
        })
    return _keys(pd.DataFrame(rows), "date", start=1900)


ALL_DIMENSIONS = {
    "dim_country": dim_country,
    "dim_age_group": dim_age_group,
    "dim_gender": dim_gender,
    "dim_profession": dim_profession,
    "dim_specialization": dim_specialization,
    "dim_sector": dim_sector,
}