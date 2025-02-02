"""Regional (NUTS) supply layer.

The finding that shapes this module
-----------------------------------
Eurostat publishes health workforce at **national** level only in its current
series (``hlth_rs_phys`` / ``hlth_rs_nurse``). Regional figures exist, but in
``hlth_rs_prsrg``, which:

1. is keyed by ISCO08 occupation rather than by profession and age,
2. has been **discontinued** -- coverage is strong to 2015 and then collapses
   (2014: 4063 values, 2016: 1625, 2020: 671, 2021: 54), and
3. is reported at a **different NUTS level per country**.

Point 3 is the trap. Germany, Italy and Spain report at NUTS 1; the
Netherlands at NUTS 2; France and Poland publish NUTS 2 *and* NUTS 3 in the
same table. Summing the ``geo`` column therefore double counts France by 91.6%
and Poland by 25.3%, while producing a plausible-looking number.

So this module never trusts the raw geography. It assigns each code its level
from the official NUTS 2021 classification, determines which level each country
actually reports at, and reconciles the result back to the national total. A
country that fails reconciliation is reported, not quietly included.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

NUTS_INDEX_PATH = Path("data/geo/nuts_level_index.json")

#: hlth_rs_prsrg ISCO08 codes -> internal profession codes.
ISCO_PROFESSION = {
    "OC221": "PHYS",
    "OC222_322": "NURS",
    "OC2261": "DENT",
    "OC2262": "PHARM",
    "OC2264": "PHYSIO",
}

#: Acceptable |regional sum - national| as a share of national.
RECONCILE_TOLERANCE = 0.05


def load_nuts_index(path: Path = NUTS_INDEX_PATH) -> dict:
    """Official NUTS 2021 code -> level, country and name."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def annotate_nuts(df: pd.DataFrame, index: dict) -> pd.DataFrame:
    """Attach ``nuts_level``, ``region_name`` and ``country_code`` by code."""
    if df.empty or "geo" not in df.columns:
        return df
    out = df.copy()
    out["nuts_code"] = out["geo"]
    out["nuts_level"] = out["nuts_code"].map(
        lambda c: (index.get(str(c)) or {}).get("nuts_level")
    )
    out["region_name"] = out["nuts_code"].map(
        lambda c: (index.get(str(c)) or {}).get("region_name")
    )
    out["country_code"] = out["nuts_code"].str[:2]
    return out


def detect_reporting_level(
    regional: pd.DataFrame,
    national: pd.DataFrame,
    year: str,
    tolerance: float = RECONCILE_TOLERANCE,
) -> pd.DataFrame:
    """Find which NUTS level each country actually reports workforce at.

    For each candidate level, sums the regional rows for that country and
    compares against the national figure for the same year. The level that
    reconciles is the one the country reports at. A country that reconciles at
    no single level is flagged rather than assigned one.
    """
    if regional.empty or national.empty:
        return pd.DataFrame()

    # Headcounts only. HAB_P and P_HTHAB are per-capita ratios; including them
    # triples the row count and inflates every regional sum.
    if "unit" in regional.columns:
        regional = regional[regional["unit"] == "NR"]

    # Level 0 is the country row itself. Matching at level 0 with a single
    # region reconciles trivially and tells us nothing, so candidates start
    # at level 1.
    candidates = sorted(
        {int(v) for v in regional["nuts_level"].dropna().unique() if int(v) >= 1}
    )
    rows = []
    for country, block in regional.groupby("country_code"):
        nat = national[
            (national["geo"] == country) & (national["time"] == year)
        ]
        if nat.empty:
            continue
        nat_value = float(pd.to_numeric(nat["value"]).sum())
        if nat_value <= 0:
            continue

        best = None
        for level in candidates:
            part = block[(block["nuts_level"] == level)
                         & (block["time"] == year)]
            if part.empty or len(part) < 2:
                # A single "region" is a country row in disguise, not a
                # regional breakdown.
                continue
            total = float(pd.to_numeric(part["value"]).sum())
            if total <= 0:
                continue
            diff = abs(total - nat_value) / nat_value
            if diff <= tolerance and (best is None or diff < best[1]):
                best = (level, diff, total, len(part))

        rows.append({
            "country_code": country,
            "year": year,
            "national_total": nat_value,
            "reporting_level": int(best[0]) if best else None,
            "regional_total": best[2] if best else None,
            "n_regions": best[3] if best else 0,
            "rel_diff": round(best[1], 5) if best else None,
            "reconciles": best is not None,
        })
    return pd.DataFrame(rows).sort_values("country_code").reset_index(
        drop=True
    )


def build_regional_workforce(
    regional: pd.DataFrame,
    levels: pd.DataFrame,
    profession_filter: set[str] | None = None,
) -> pd.DataFrame:
    """Regional workforce restricted to each country's actual reporting level.

    This is the difference between an observed regional figure and a fabricated
    one: no allocation is performed here. Where a country does not reconcile at
    any single level, its rows are dropped and the omission is visible in
    ``levels`` rather than silently averaged in.
    """
    if regional.empty or levels.empty:
        return pd.DataFrame()

    keep = levels[levels["reconciles"]][
        ["country_code", "reporting_level"]
    ]
    work = regional.merge(keep, on="country_code", how="inner")
    work = work[work["nuts_level"] == work["reporting_level"]]

    if "unit" in work.columns:
        work = work[work["unit"] == "NR"]
    work["profession_code"] = work["isco08"].map(ISCO_PROFESSION)
    work = work[work["profession_code"].notna()]
    if profession_filter:
        work = work[work["profession_code"].isin(profession_filter)]

    work["year_int"] = work["time"].astype(int)
    work["measure_value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work[work["measure_value"].notna()]

    out = work[[
        "nuts_code", "nuts_level", "region_name", "country_code",
        "profession_code", "year_int", "measure_value",
    ]].rename(columns={"year_int": "year"})
    out = out.groupby(
        ["nuts_code", "nuts_level", "country_code", "profession_code", "year"],
        as_index=False,
        dropna=False,
    )["measure_value"].sum()
    return out.sort_values(
        ["country_code", "year", "profession_code", "nuts_code"]
    ).reset_index(drop=True)


def keep_reconciling(
    regional_fact: pd.DataFrame,
    national: pd.DataFrame,
    profession_code: str,
    tolerance: float = RECONCILE_TOLERANCE,
) -> pd.DataFrame:
    """Restrict the fact to country-years that reconcile to the national total.

    Some country-years in the discontinued regional series carry duplicated or
    inconsistent region rows, which inflates the regional sum to exactly twice
    the national figure (observed for Italy and Romania in 2018+). Rather than
    publish a number that is provably wrong, the affected country-years are
    dropped. The published fact is therefore observed *and* verified, and the
    omissions are countable in the reconciliation report.
    """
    report = reconciliation_report(
        regional_fact, national, profession_code=profession_code
    )
    if report.empty:
        return regional_fact

    good = report[report["within_tolerance"]].assign(
        year=lambda d: d["year"].astype(int)
    )
    allowed = set(zip(good["country_code"], good["year"]))

    fact = regional_fact.copy()
    fact["year"] = fact["year"].astype(int)
    mask = [
        (c, y) in allowed
        for c, y in zip(fact["country_code"], fact["year"])
    ]
    return fact[mask].reset_index(drop=True)


def reconciliation_report(
    regional_fact: pd.DataFrame,
    national: pd.DataFrame,
    profession_code: str | None = None,
) -> pd.DataFrame:
    """Cross-check the built fact against national totals, per country/year.

    This is the check that catches a mixed-level table. Anything above the
    tolerance means the regional figure cannot be trusted at that level.

    ``profession_code`` must be supplied when the fact holds more than one
    profession. Summing doctors and nurses and comparing against a doctors-only
    benchmark yields a ratio of ~2 for every country, which looks like a
    systematic double count but is really a missing filter.
    """
    if regional_fact.empty or national.empty:
        return pd.DataFrame()
    fact = regional_fact
    if profession_code is not None and "profession_code" in fact.columns:
        fact = fact[fact["profession_code"] == profession_code]
    if fact.empty:
        return pd.DataFrame()

    nat = national.copy()
    nat["value"] = pd.to_numeric(nat["value"], errors="coerce")
    nat = nat[nat["value"].notna()]
    nat = nat.groupby(["geo", "time"], as_index=False)["value"].sum()
    nat = nat.rename(columns={"geo": "country_code"})

    reg = fact.groupby(
        ["country_code", "year"], as_index=False
    )["measure_value"].sum().rename(columns={"measure_value": "regional_sum"})

    merged = reg.merge(
        nat.assign(year=nat["time"].astype(int)),
        on=["country_code", "year"], how="inner",
    )
    merged["rel_diff"] = (
        (merged["regional_sum"] - merged["value"]).abs() / merged["value"]
    )
    merged["within_tolerance"] = merged["rel_diff"] <= RECONCILE_TOLERANCE
    return merged.sort_values("rel_diff", ascending=False).reset_index(
        drop=True
    )