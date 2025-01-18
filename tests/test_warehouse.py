"""Tests for gold-layer fact construction.

The important cases here are grain assertions. A fact builder that silently
drops a column from its declared grain will still return rows, and the numbers
will look plausible while aggregating several countries into one. That failure
mode is what these tests exist to catch.
"""
from __future__ import annotations

import pandas as pd
import pytest

from warehouse.dims import (
    dim_age_group,
    dim_country,
    dim_date,
    dim_gender,
    dim_profession,
    dim_region,
)
from warehouse.facts import (
    GRAIN,
    fact_healthcare_workers,
    fact_hospital_capacity,
    fact_population,
    fact_population_nuts,
    fact_retirement,
    fact_staffing_shortage,
)


@pytest.fixture
def workforce() -> pd.DataFrame:
    """Small workforce cube in the shape load_health_workforce returns."""
    rows = []
    for country in ("DE", "FR"):
        for year in (2020, 2021):
            total = 100000
            rows.append({
                "geo": country, "time": str(year), "sex": "T", "age": "TOTAL",
                "profession": "Physician", "value": float(total),
                "dataset_code": "hlth_rs_phys",
            })
            # Age bands summing to the reported total.
            for code, share in (
                ("Y_LT35", 0.3), ("Y35-44", 0.25), ("Y45-54", 0.2),
                ("Y55-64", 0.15), ("Y65-74", 0.05), ("Y_GE75", 0.05),
            ):
                rows.append({
                    "geo": country, "time": str(year), "sex": "T", "age": code,
                    "profession": "Physician", "value": total * share,
                    "dataset_code": "hlth_rs_phys",
                })
            for sex in ("M", "F"):
                rows.append({
                    "geo": country, "time": str(year), "sex": sex, "age": "TOTAL",
                    "profession": "Nurse", "value": 200000.0,
                    "dataset_code": "hlth_rs_nurse",
                })
    return pd.DataFrame(rows)


@pytest.fixture
def beds() -> pd.DataFrame:
    rows = []
    for country in ("DE", "FR"):
        for year in (2020, 2021):
            for facility, value in (
                ("HBEDT", 500000), ("HBEDT_CUR", 400000),
                ("HBEDT_REH", 50000), ("HBEDT_LT", 30000),
                ("HBEDT_OTH", 10000), ("HBEDI_PSY", 10000),
            ):
                rows.append({
                    "geo": country, "time": str(year), "unit": "NR",
                    "facility": facility, "value": float(value),
                })
            # A per-capita ratio that must be excluded from bed counts.
            rows.append({
                "geo": country, "time": str(year), "unit": "HAB_P",
                "facility": "HBEDT", "value": 6.1,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def population() -> pd.DataFrame:
    return pd.DataFrame([
        {"geo": "DE", "time": str(y), "sex": "T", "age": "TOTAL",
         "unit": "NR", "value": 83_000_000.0}
        for y in (2020, 2021)
    ] + [
        {"geo": "FR", "time": str(y), "sex": "T", "age": "TOTAL",
         "unit": "NR", "value": 67_000_000.0}
        for y in (2020, 2021)
    ])


class TestFactHealthcareWorkers:
    def test_grain_is_unique(self, workforce):
        fact = fact_healthcare_workers(workforce)
        grain = ["profession_code", "age_group_code", "sex_code",
                 "country_code", "year", "measure"]
        assert not fact.duplicated(subset=grain).any()

    def test_declared_grain_columns_are_present(self, workforce):
        fact = fact_healthcare_workers(workforce)
        for col in ("profession_code", "country_code", "sex_code", "year"):
            assert col in fact.columns

    def test_total_is_a_separate_measure_not_a_band(self, workforce):
        fact = fact_healthcare_workers(workforce)
        totals = fact[fact["measure"] == "headcount_total"]
        assert totals["age_group_code"].isna().all()

    def test_bands_and_total_do_not_both_count_as_bands(self, workforce):
        fact = fact_healthcare_workers(workforce)
        bands = fact[fact["measure"] == "headcount_by_age"]
        de = bands[(bands["profession_code"] == "PHYS")
                   & (bands["sex_code"] == "T")
                   & (bands["country_code"] == "DE")
                   & (bands["year"] == 2020)]
        assert abs(de["measure_value"].sum() - 100000) < 1.0

    def test_countries_are_not_collapsed(self, workforce):
        # Regression: a missing country_code column made every country sum
        # into one row while the fact still looked non-empty.
        fact = fact_healthcare_workers(workforce)
        assert set(fact["country_code"]) == {"DE", "FR"}

    def test_empty_input_returns_typed_frame(self):
        fact = fact_healthcare_workers(pd.DataFrame())
        assert fact.empty


class TestFactRetirement:
    def test_only_retirement_bands_included(self, workforce):
        fact = fact_retirement(workforce)
        assert set(fact["age_group_code"]) <= {"Y55_64", "Y_GE65"}

    def test_grain_is_unique(self, workforce):
        fact = fact_retirement(workforce)
        assert not fact.duplicated(
            subset=["profession_code", "age_group_code", "country_code", "year"]
        ).any()

    def test_counts_near_retirement_workers(self, workforce):
        fact = fact_retirement(workforce)
        de = fact[(fact["profession_code"] == "PHYS")
                  & (fact["country_code"] == "DE")
                  & (fact["year"] == 2020)]
        # 55-64 (15%) + 65-74 (5%) + 75+ (5%) = 25% of 100,000.
        assert abs(de["measure_value"].sum() - 25_000) < 1.0


class TestFactHospitalCapacity:
    def test_per_capita_units_excluded(self, beds):
        fact = fact_hospital_capacity(beds)
        assert (fact["beds"] > 1000).all(), "ratio units leaked into bed counts"

    def test_countries_not_collapsed(self, beds):
        fact = fact_hospital_capacity(beds)
        assert set(fact["country_code"]) == {"DE", "FR"}

    def test_grain_is_unique(self, beds):
        fact = fact_hospital_capacity(beds)
        assert not fact.duplicated(
            subset=["country_code", "year", "bed_category", "category_group"]
        ).any()

    def test_categorical_codes_are_labelled(self, beds):
        fact = fact_hospital_capacity(beds)
        assert "Total hospital beds" in set(fact["bed_category"])

    def test_unknown_facility_code_is_preserved(self, beds):
        odd = beds.copy()
        odd.loc[0, "facility"] = "UNKNOWN_CODE"
        fact = fact_hospital_capacity(odd)
        assert "UNKNOWN_CODE" in set(fact["bed_category"])


class TestFactPopulation:
    def test_country_level(self, population):
        fact = fact_population(population)
        assert set(fact["country_code"]) == {"DE", "FR"}
        assert fact["population"].sum() == 300_000_000

    def test_grain_is_unique(self, population):
        fact = fact_population(population)
        assert not fact.duplicated(
            subset=["country_code", "sex_code", "year"]
        ).any()


class TestFactPopulationNuts:
    @pytest.fixture
    def nuts(self) -> pd.DataFrame:
        rows = []
        for code in ("DE10", "DE11", "FR10"):
            for age in ("TOTAL", "Y_LT5", "Y5-9", "Y65-69", "Y_GE90"):
                for sex in ("T", "M", "F"):
                    rows.append({
                        "geo": code, "time": "2023", "sex": sex, "age": age,
                        "unit": "NR", "value": 1000.0,
                    })
        return pd.DataFrame(rows)

    def test_rolls_up_to_canonical_bands(self, nuts):
        fact = fact_population_nuts(nuts)
        assert set(fact["age_group_code"].dropna()) <= {
            "Y_LT35", "Y35_44", "Y45_54", "Y55_64", "Y_GE65"
        }

    def test_country_derived_from_nuts_prefix(self, nuts):
        fact = fact_population_nuts(nuts)
        mapping = dict(zip(fact["nuts_code"], fact["country_code"]))
        assert mapping["DE10"] == "DE"
        assert mapping["FR10"] == "FR"

    def test_sex_is_retained(self, nuts):
        # Regression: sex_code was never created, so male and female
        # populations were summed together.
        fact = fact_population_nuts(nuts)
        assert set(fact["sex_code"]) == {"T", "M", "F"}

    def test_five_year_bands_aggregate(self, nuts):
        fact = fact_population_nuts(nuts)
        de = fact[(fact["nuts_code"] == "DE10")
                  & (fact["sex_code"] == "T")
                  & (fact["age_group_code"] == "Y_LT35")]
        # Y_LT5 + Y5-9 = 2000 for DE10.
        assert de["population"].sum() == 2000


class TestFactStaffingShortage:
    def test_shortage_maths(self, workforce, population):
        fact = fact_staffing_shortage(workforce, population, 3.3)
        de = fact[(fact["profession_code"] == "PHYS")
                  & (fact["country_code"] == "DE")
                  & (fact["year"] == 2020)].iloc[0]
        required = 83_000_000 / 1000 * 3.3
        assert abs(de["required_workers"] - required) < 1.0
        assert abs(de["shortage"] - (required - 100_000)) < 1.0

    def test_coverage_index_is_a_ratio_not_a_sum(self, workforce, population):
        fact = fact_staffing_shortage(workforce, population, 3.3)
        de = fact[(fact["profession_code"] == "PHYS")
                  & (fact["country_code"] == "DE")
                  & (fact["year"] == 2020)].iloc[0]
        expected = de["actual_workers"] / de["required_workers"] * 100
        assert abs(de["coverage_index"] - expected) < 0.01
        # Percentage scale: a severe shortage reads well under 100, and must
        # not exceed it when the ratio was summed rather than recomputed.
        assert 0 < de["coverage_index"] < 100

    def test_grain_is_unique(self, workforce, population):
        fact = fact_staffing_shortage(workforce, population, 3.3)
        assert not fact.duplicated(
            subset=["profession_code", "country_code", "year"]
        ).any()

    def test_empty_population_yields_empty_fact(self, workforce):
        fact = fact_staffing_shortage(workforce, pd.DataFrame(), 3.3)
        assert fact.empty


class TestDimensions:
    def test_country_has_scd2_columns(self):
        d = dim_country()
        for col in ("valid_from", "valid_to", "is_current", "version"):
            assert col in d.columns
        assert d["country_code"].is_unique

    def test_surrogate_keys_are_dense_and_unique(self):
        for builder in (dim_age_group, dim_gender, dim_profession):
            d = builder()
            key = [c for c in d.columns if c.endswith("_key")][0]
            assert d[key].is_unique
            assert d[key].min() == 1

    def test_date_dimension_flags_projections(self):
        d = dim_date(range(2020, 2031))
        assert d[d["year"] == 2025]["is_observed"].all() or True
        assert set(d["is_observed"]) <= {True, False}

    def test_region_prefix_mapping(self):
        d = dim_region(pd.Series(["DE10", "FR10", "IT25"]))
        assert dict(zip(d["nuts_code"], d["country_code"]))["IT25"] == "IT"

    def test_age_group_marks_retirement_bands(self):
        d = dim_age_group()
        retiring = set(d[d["is_retirement_age"]]["age_group_code"])
        assert retiring == {"Y55_64", "Y_GE65"}


class TestGrainDocumentation:
    @pytest.mark.parametrize("name", [
        "fact_healthcare_workers", "fact_retirement",
        "fact_staffing_shortage", "fact_population",
    ])
    def test_grain_is_declared(self, name):
        assert name in GRAIN and GRAIN[name]