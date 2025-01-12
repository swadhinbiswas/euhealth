"""Tests for the data quality framework."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from quality.dq import (
    DQReport,
    check_coverage,
    check_duplicate_rate,
    check_null_rate,
    check_range,
    check_referential_country,
    check_reporting_completeness,
    check_schema,
    check_total_reconciliation,
    check_freshness,
)

EU = {"DE", "FR", "IT", "ES", "NL", "PL"}


@pytest.fixture
def good() -> pd.DataFrame:
    return pd.DataFrame({
        "geo": ["DE", "FR", "IT"],
        "time": ["2020", "2020", "2020"],
        "sex": ["T", "T", "T"],
        "value": [100.0, 200.0, 300.0],
    })


class TestCompleteness:
    def test_null_rate_passes_when_full(self, good):
        r = check_null_rate(good, ["geo", "value"], max_rate=0.05)
        assert r.ok and r.observed == 0.0

    def test_null_rate_fails(self, good):
        bad = good.copy()
        bad.loc[0, "value"] = None
        r = check_null_rate(bad, ["value"], max_rate=0.05)
        assert r.status == "fail"

    def test_empty_table_is_a_failure_not_a_pass(self):
        r = check_null_rate(pd.DataFrame(), ["geo"])
        assert r.status == "fail"

    def test_coverage_detects_missing_countries(self, good):
        # good has DE/FR/IT = 3 of 6 expected -> 0.5, below the 0.7 warn floor.
        r = check_coverage(good, EU)
        assert r.status == "fail"
        assert "missing" in r.detail

    def test_partial_coverage_warns(self, good):
        # 3 of 4 present = 0.75, inside the warn band (0.7 <= r < 0.95).
        r = check_coverage(good, {"DE", "FR", "IT", "ES"})
        assert r.status == "warn"

    def test_coverage_passes_when_all_present(self):
        full = pd.DataFrame({"geo": sorted(EU)})
        assert check_coverage(full, EU).ok

    def test_coverage_of_empty_table_fails(self):
        assert check_coverage(pd.DataFrame({"geo": []}), EU).status == "fail"


class TestUniqueness:
    def test_no_duplicates_passes(self, good):
        r = check_duplicate_rate(good, ["geo", "time"])
        assert r.ok and r.observed == 0.0

    def test_duplicates_fail(self, good):
        dup = pd.concat([good, good.head(1)], ignore_index=True)
        r = check_duplicate_rate(dup, ["geo", "time"])
        assert r.status == "fail"

    def test_missing_key_is_a_warning_not_a_crash(self, good):
        r = check_duplicate_rate(good, ["nope"])
        assert r.status == "warn" and r.severity == "warning"


class TestValidity:
    def test_range_rejects_negative_headcounts(self, good):
        bad = good.copy()
        bad.loc[0, "value"] = -5.0
        r = check_range(bad, "value", low=0)
        assert r.status == "fail"

    def test_range_passes_valid_values(self, good):
        assert check_range(good, "value", low=0).ok

    def test_schema_missing_column_fails(self, good):
        r = check_schema(good, ["geo", "nope"])
        assert r.status == "fail" and "nope" in r.detail

    def test_referential_country_rejects_unknown(self, good):
        bad = good.copy()
        bad.loc[0, "geo"] = "XX"
        r = check_referential_country(bad, EU)
        assert r.status == "fail" and "XX" in r.detail

    def test_referential_country_accepts_known(self, good):
        assert check_referential_country(good, EU).ok


class TestFreshness:
    def test_recent_data_passes(self, good):
        recent = good.copy()
        recent["time"] = ["2099", "2099", "2099"]
        assert check_freshness(recent, max_age_days=900).ok

    def test_stale_data_fails(self, good):
        r = check_freshness(good, max_age_days=900)
        assert r.status == "fail"
        assert r.observed > 900

    def test_timezone_naive_is_handled(self, good):
        # Regression: pandas yields tz-naive timestamps and comparing them to
        # an aware utcnow() raises TypeError.
        r = check_freshness(good, max_age_days=900)
        assert r.observed is not None

    def test_unparseable_is_reported(self, good):
        bad = good.copy()
        bad["time"] = ["abc", "def", "ghi"]
        assert check_freshness(bad).status == "fail"


class TestReportingCompleteness:
    def _cube(self, codes_per_group: list[int]) -> pd.DataFrame:
        rows = []
        for i, n in enumerate(codes_per_group):
            for j in range(n):
                rows.append({
                    "geo": f"C{i}", "time": "2020", "sex": "T",
                    "age": "TOTAL" if j == 0 else f"A{j}", "value": 1.0,
                })
        return pd.DataFrame(rows)

    def test_all_groups_complete_passes(self):
        df = self._cube([7, 7, 7])
        assert check_reporting_completeness(df, "phys", 7).ok

    def test_partial_reporting_warns(self):
        df = self._cube([7, 7, 1])
        r = check_reporting_completeness(df, "phys", 7)
        assert r.status in ("warn", "fail")

    def test_empty_is_a_warning(self):
        r = check_reporting_completeness(pd.DataFrame(), "phys", 7)
        assert r.status == "warn"


class TestTotalReconciliation:
    def _consistent(self, bands: dict[str, float], total: float) -> pd.DataFrame:
        rows = [{"geo": "DE", "time": "2020", "sex": "T",
                 "age": "TOTAL", "value": total}]
        rows += [{"geo": "DE", "time": "2020", "sex": "T",
                  "age": c, "value": v} for c, v in bands.items()]
        return pd.DataFrame(rows)

    def test_consistent_bands_pass(self):
        bands = {"Y_LT35": 10.0, "Y35-44": 20.0, "Y45-54": 30.0,
                 "Y55-64": 20.0, "Y65-74": 10.0, "Y_GE75": 10.0}
        df = self._consistent(bands, 100.0)
        assert check_total_reconciliation(df, "phys").ok

    def test_broken_mapping_is_caught(self):
        # All source codes present but the bands sum to 10 against a claimed
        # TOTAL of 100: a genuine mapping error must fail, not warn.
        df = self._consistent({
            "Y_LT35": 1.0, "Y35-44": 2.0, "Y45-54": 3.0,
            "Y55-64": 2.0, "Y65-74": 1.0, "Y_GE75": 1.0,
        }, 100.0)
        r = check_total_reconciliation(df, "phys")
        assert r.status == "fail"

    def test_no_complete_rows_is_a_warning(self):
        r = check_total_reconciliation(pd.DataFrame(), "phys")
        assert r.status == "warn"


class TestReport:
    def test_score_is_weighted(self):
        from quality.dq import CheckResult

        report = DQReport(table="t")
        report.results = [
            CheckResult("a", "validity", "pass", 1, 1),
            CheckResult("b", "validity", "warn", 1, 1),
            CheckResult("c", "validity", "fail", 1, 1),
        ]
        assert report.score == 50.0  # 1 + 0.5 + 0 over 3

    def test_blocking_failures_only_include_errors(self):
        from quality.dq import CheckResult
        report = DQReport(table="t")
        report.results = [
            CheckResult("a", "validity", "fail", 1, 1, severity="error"),
            CheckResult("b", "validity", "fail", 1, 1, severity="warning"),
        ]
        assert len(report.blocking_failures) == 1

    def test_serialises_to_json(self):
        from quality.dq import CheckResult
        report = DQReport(table="t")
        report.results = [CheckResult("a", "validity", "pass", 1, 1)]
        payload = json.loads(json.dumps(report.to_dict()))
        assert payload["table"] == "t"
        assert payload["results"][0]["name"] == "a"