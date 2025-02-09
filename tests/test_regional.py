"""Tests for the regional layer.

The failure these guard against is specific and was actually observed: a
regional table that mixes NUTS levels double counts silently while still
looking like a plausible headcount.
"""
from __future__ import annotations

import pandas as pd
import pytest

from geo.regional import (
    ISCO_PROFESSION,
    RECONCILE_TOLERANCE,
    annotate_nuts,
    build_regional_workforce,
    detect_reporting_level,
    keep_reconciling,
    load_nuts_index,
    reconciliation_report,
)

NUTS_INDEX = {
    "DE": {"nuts_code": "DE", "nuts_level": 0, "country_code": "DE",
           "region_name": "Germany", "is_eu_stat": True},
    "DE1": {"nuts_code": "DE1", "nuts_level": 1, "country_code": "DE",
            "region_name": "Schleswig-Holstein", "is_eu_stat": True},
    "DE2": {"nuts_code": "DE2", "nuts_level": 1, "country_code": "DE",
            "region_name": "Hamburg", "is_eu_stat": True},
    "FR10": {"nuts_code": "FR10", "nuts_level": 2, "country_code": "FR",
             "region_name": "Ile-de-France", "is_eu_stat": True},
    "FR20": {"nuts_code": "FR20", "nuts_level": 2, "country_code": "FR",
             "region_name": "Centre-Val de Loire", "is_eu_stat": True},
    "FR101": {"nuts_code": "FR101", "nuts_level": 3, "country_code": "FR",
              "region_name": "Paris", "is_eu_stat": True},
    "FR102": {"nuts_code": "FR102", "nuts_level": 3, "country_code": "FR",
              "region_name": "Val-d'Oise", "is_eu_stat": True},
}


@pytest.fixture
def mixed_level() -> pd.DataFrame:
    """France reports both NUTS 2 and NUTS 3; Germany reports NUTS 1.

    France's NUTS 2 and NUTS 3 rows are two views of the same national total,
    so each level sums to 100,000 on its own. Summing both levels together --
    the naive approach -- gives 200,000, twice the truth.
    """
    rows = []
    for code, value in (("FR10", 50000), ("FR20", 50000),
                        ("FR101", 50000), ("FR102", 50000)):
        rows.append({
            "isco08": "OC221", "geo": code, "time": "2015",
            "unit": "NR", "value": float(value),
        })
    for code, value in (("DE1", 100000), ("DE2", 238129)):
        rows.append({
            "isco08": "OC221", "geo": code, "time": "2015",
            "unit": "NR", "value": float(value),
        })
    return pd.DataFrame(rows)


@pytest.fixture
def national() -> pd.DataFrame:
    return pd.DataFrame([
        {"geo": "FR", "time": "2015", "value": 100000.0},
        {"geo": "DE", "time": "2015", "value": 338129.0},
    ])


class TestAnnotation:
    def test_assigns_level_from_official_index(self, mixed_level):
        out = annotate_nuts(mixed_level, NUTS_INDEX)
        assert dict(zip(out.geo, out.nuts_level))["FR10"] == 2
        assert dict(zip(out.geo, out.nuts_level))["FR101"] == 3
        assert dict(zip(out.geo, out.nuts_level))["DE1"] == 1

    def test_unknown_codes_get_no_level(self):
        frame = pd.DataFrame([{"isco08": "OC221", "geo": "ZZ99",
                               "time": "2015", "unit": "NR", "value": 1.0}])
        out = annotate_nuts(frame, NUTS_INDEX)
        assert pd.isna(out["nuts_level"].iloc[0])

    def test_load_index_is_safe_when_absent(self, tmp_path):
        assert load_nuts_index(tmp_path / "missing.json") == {}


class TestReportingLevelDetection:
    def test_detects_nuts1_for_germany(self, mixed_level, national):
        out = detect_reporting_level(
            annotate_nuts(mixed_level, NUTS_INDEX), national, "2015"
        )
        germany = out[out.country_code == "DE"].iloc[0]
        assert germany["reporting_level"] == 1
        assert germany["reconciles"]

    def test_detects_nuts2_for_france(self, mixed_level, national):
        # France publishes NUTS2 and NUTS3 which together double count. Each
        # level alone sums to the national total, so the detection must pick
        # one and the builder must discard the other.
        out = detect_reporting_level(
            annotate_nuts(mixed_level, NUTS_INDEX), national, "2015"
        )
        france = out[out.country_code == "FR"].iloc[0]
        assert france["reporting_level"] == 2
        assert france["rel_diff"] == pytest.approx(0.0)
        assert france["regional_total"] == pytest.approx(100000.0)

    def test_naive_sum_of_all_levels_double_counts(self, mixed_level, national):
        """The bug this module exists to prevent, asserted explicitly."""
        annotated = annotate_nuts(mixed_level, NUTS_INDEX)
        france = annotated[
            (annotated.country_code == "FR")
            & (annotated.nuts_level >= 1)
        ]
        naive = float(france["value"].sum())
        national_total = 100000.0
        assert naive == pytest.approx(2 * national_total)

    def test_level_zero_is_never_selected(self, national):
        """A country row matching itself is not a regional breakdown."""
        frame = pd.DataFrame([
            {"isco08": "OC221", "geo": "DE", "time": "2015",
             "unit": "NR", "value": 338129.0},
        ])
        out = detect_reporting_level(annotate_nuts(frame, NUTS_INDEX),
                                     national, "2015")
        assert out.empty or out["reporting_level"].isna().all()

    def test_per_capita_units_are_excluded(self, national):
        """HAB_P rows must not inflate the regional sum."""
        rows = [
            {"isco08": "OC221", "geo": "DE1", "time": "2015",
             "unit": "NR", "value": 100000.0},
            {"isco08": "OC221", "geo": "DE2", "time": "2015",
             "unit": "NR", "value": 238129.0},
            {"isco08": "OC221", "geo": "DE1", "time": "2015",
             "unit": "HAB_P", "value": 4.2},
            {"isco08": "OC221", "geo": "DE2", "time": "2015",
             "unit": "HAB_P", "value": 5.1},
        ]
        out = detect_reporting_level(
            annotate_nuts(pd.DataFrame(rows), NUTS_INDEX), national, "2015"
        )
        germany = out[out.country_code == "DE"].iloc[0]
        assert germany["regional_total"] == pytest.approx(338129.0)
        assert germany["n_regions"] == 2

    def test_non_reconciling_country_is_flagged_not_assigned(self):
        frame = pd.DataFrame([
            {"isco08": "OC221", "geo": "FR10", "time": "2015",
             "unit": "NR", "value": 100.0},
        ])
        nat = pd.DataFrame([{"geo": "FR", "time": "2015", "value": 500.0}])
        out = detect_reporting_level(
            annotate_nuts(frame, NUTS_INDEX), nat, "2015"
        )
        row = out[out.country_code == "FR"].iloc[0]
        assert not row["reconciles"]
        assert pd.isna(row["reporting_level"])

    def test_single_region_is_not_treated_as_a_breakdown(self):
        frame = pd.DataFrame([
            {"isco08": "OC221", "geo": "FR10", "time": "2015",
             "unit": "NR", "value": 100.0},
        ])
        nat = pd.DataFrame([{"geo": "FR", "time": "2015", "value": 100.0}])
        out = detect_reporting_level(
            annotate_nuts(frame, NUTS_INDEX), nat, "2015"
        )
        assert out.empty or not out["reconciles"].any()


class TestBuildRegional:
    def test_only_the_reporting_level_is_kept(self, mixed_level, national):
        annotated = annotate_nuts(mixed_level, NUTS_INDEX)
        levels = detect_reporting_level(annotated, national, "2015")
        fact = build_regional_workforce(annotated, levels)
        # France must not carry its NUTS3 rows alongside its NUTS2 rows.
        assert set(fact[fact.country_code == "FR"]["nuts_code"]) == {
            "FR10", "FR20"
        }
        assert set(fact[fact.country_code == "DE"]["nuts_code"]) == {"DE1", "DE2"}

    def test_built_regions_sum_to_the_national_total(self, mixed_level,
                                                     national):
        annotated = annotate_nuts(mixed_level, NUTS_INDEX)
        levels = detect_reporting_level(annotated, national, "2015")
        fact = build_regional_workforce(annotated, levels)
        france = fact[fact.country_code == "FR"]["measure_value"].sum()
        assert france == pytest.approx(100000.0)

    def test_profession_is_mapped_from_isco(self, mixed_level, national):
        annotated = annotate_nuts(mixed_level, NUTS_INDEX)
        levels = detect_reporting_level(annotated, national, "2015")
        fact = build_regional_workforce(annotated, levels)
        assert set(fact["profession_code"]) == {"PHYS"}

    def test_unknown_profession_codes_are_dropped(self, national):
        frame = pd.DataFrame([
            {"isco08": "OC999", "geo": "DE1", "time": "2015",
             "unit": "NR", "value": 1.0},
            {"isco08": "OC221", "geo": "DE1", "time": "2015",
             "unit": "NR", "value": 100.0},
        ])
        annotated = annotate_nuts(frame, NUTS_INDEX)
        levels = detect_reporting_level(
            annotated[annotated.isco08 == "OC221"], national, "2015"
        )
        fact = build_regional_workforce(annotated, levels)
        assert "OC999" not in set(fact["profession_code"])

    def test_grain_is_unique(self, mixed_level, national):
        annotated = annotate_nuts(mixed_level, NUTS_INDEX)
        levels = detect_reporting_level(annotated, national, "2015")
        fact = build_regional_workforce(annotated, levels)
        assert not fact.duplicated(
            subset=["nuts_code", "profession_code", "year"]
        ).any()


class TestReconciliation:
    def _fact(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"nuts_code": "DE1", "country_code": "DE", "profession_code": "PHYS",
             "year": 2015, "measure_value": 100000.0},
            {"nuts_code": "DE2", "country_code": "DE", "profession_code": "PHYS",
             "year": 2015, "measure_value": 238129.0},
        ])

    def _national(self) -> pd.DataFrame:
        return pd.DataFrame([
            {"geo": "DE", "time": "2015", "value": 338129.0},
        ])

    def test_exact_match_passes(self):
        rep = reconciliation_report(self._fact(), self._national(), "PHYS")
        assert rep["within_tolerance"].all()

    def test_double_count_is_caught(self):
        fact = self._fact()
        fact.loc[fact.nuts_code == "DE2", "measure_value"] = 238129.0 * 2
        rep = reconciliation_report(fact, self._national(), "PHYS")
        assert not rep["within_tolerance"].any()
        assert rep["rel_diff"].iloc[0] == pytest.approx(0.7, abs=0.01)

    def test_profession_filter_prevents_apples_to_oranges(self):
        """Summing two professions against one benchmark gives a false 2x."""
        fact = pd.concat([
            self._fact(),
            self._fact().assign(profession_code="NURS",
                                measure_value=100000.0),
        ])
        unfiltered = reconciliation_report(fact, self._national())
        filtered = reconciliation_report(fact, self._national(), "PHYS")
        assert not unfiltered["within_tolerance"].all()
        assert filtered["within_tolerance"].all()

    def test_keep_reconciling_drops_the_bad_country_year(self):
        fact = pd.concat([
            self._fact(),
            pd.DataFrame([{
                "nuts_code": "FR10", "country_code": "FR",
                "profession_code": "PHYS", "year": 2015,
                "measure_value": 96078.0,
            }]),
        ])
        national = pd.concat([self._national(), pd.DataFrame([
            {"geo": "FR", "time": "2015", "value": 57897.0},
        ])])
        kept = keep_reconciling(fact, national, "PHYS")
        assert set(kept["country_code"]) == {"DE"}

    def test_keep_reconciling_is_per_profession(self):
        """A group filtered in isolation must not delete the other's rows."""
        fact = pd.concat([
            self._fact(),
            self._fact().assign(profession_code="NURS",
                                measure_value=100000.0),
        ])
        kept_phys = keep_reconciling(
            fact[fact.profession_code == "PHYS"], self._national(), "PHYS"
        )
        assert len(kept_phys) == 2


class TestISCOProfiles:
    def test_every_isco_maps_to_a_profession(self):
        assert set(ISCO_PROFESSION) == {
            "OC221", "OC222_322", "OC2261", "OC2262", "OC2264",
        }

    def test_physicians_and_nurses_are_distinct(self):
        assert ISCO_PROFESSION["OC221"] != ISCO_PROFESSION["OC222_322"]

    def test_tolerance_is_five_percent(self):
        assert RECONCILE_TOLERANCE == pytest.approx(0.05)