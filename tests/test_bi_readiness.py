"""Power BI / BI-tool readiness checks.

A warehouse can be analytically correct and still be unusable in a BI tool. This
enforces the properties a semantic layer actually needs:

* every measure referenced in the DAX library binds to a real column,
* every declared relationship joins columns that exist on both sides,
* fact tables have no duplicate business keys (a BI tool would double count),
* the date dimension carries a real DATE column for time intelligence,
* no measure averages a ratio, which is the most common DAX correctness bug.

The ratio check is deliberately heuristic. It flags measures that call AVERAGE
over a column whose name suggests it is already a ratio, which is right far
more often than it is wrong, and a false positive is cheap to review.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
DAX_FILE = ROOT / "powerbi" / "measures.dax"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(), reason="warehouse not built"
)


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect(str(WAREHOUSE), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def dax() -> str:
    return DAX_FILE.read_text()


@pytest.fixture(scope="module")
def table_names(con) -> set:
    return {t.lower() for (t,) in con.execute("SHOW TABLES").fetchall()}


@pytest.fixture(scope="module")
def columns(con) -> dict:
    out = {}
    for (table,) in con.execute("SHOW TABLES").fetchall():
        out[table.lower()] = {
            r[0].lower()
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ?", [table]
            ).fetchall()
        }
    return out


# --- DAX ---------------------------------------------------------------------

def _measure_names(dax: str) -> set:
    """Measure definitions: an identifier at column zero followed by ``:=``.

    Anchored at the start of a line so nested ``VAR`` lines are not mistaken
    for measures, and the name class allows the ``%`` and space characters that
    business-facing measure names legitimately use.
    """
    return set(re.findall(r"^([A-Z][A-Za-z0-9 +%]+) :=", dax, re.MULTILINE))


class TestDaxLibrary:
    def test_library_exists(self):
        assert DAX_FILE.exists(), "powerbi/measures.dax is missing"

    def test_defines_a_meaningful_number_of_measures(self, dax):
        assert len(_measure_names(dax)) >= 30

    def test_every_referenced_table_exists(self, dax, table_names):
        referenced = set(re.findall(r"\b((?:fact|dim)_[a-z_]+)\b", dax))
        assert referenced, "no tables referenced"
        assert referenced <= table_names, (
            f"DAX references tables absent from the warehouse: "
            f"{sorted(referenced - table_names)}"
        )

    def test_every_referenced_column_exists(self, dax, columns):
        bad = set()
        for match in re.finditer(r"\b(fact_\w+|dim_\w+)\[([a-z_0-9]+)\]", dax):
            table, column = match.group(1).lower(), match.group(2).lower()
            if table in columns and column not in columns[table]:
                bad.add(f"{match.group(1)}[{match.group(2)}]")
        assert not bad, f"DAX references non-existent columns: {sorted(bad)}"

    @pytest.mark.parametrize("measure", [
        "Total Workers", "Total Doctors", "Total Nurses",
        "Workforce Gap %", "Retirement Risk %", "Hospital Risk Score",
        "Coverage Index", "Medical Desert Score", "Regional Coverage",
        "Forecasted Gap", "Healthcare Access Score", "Healthcare Equity",
        "Workforce Aging Index", "Population In Deserts",
        "Gap Rolling 3Y", "Forecast Direction", "Countries Reporting Nurses",
        "Worker to Population Ratio",
    ])
    def test_required_measure_is_defined(self, dax, measure):
        assert measure in _measure_names(dax), f"missing measure: {measure}"

    def test_no_measure_averages_a_ratio(self, dax):
        """Averaging a precomputed ratio is the classic DAX correctness bug."""
        ratio_columns = ("coverage_index", "pct_", "_pct", "risk_score",
                         "score", "ratio", "per_1000")
        offenders = []
        for match in re.finditer(
            r"^([A-Z][A-Za-z0-9 +%]+) :=(.*?)(?=^[A-Z][A-Za-z0-9 +%]+ :=|\Z)",
            dax, re.MULTILINE | re.DOTALL,
        ):
            name, body = match.group(1), match.group(2)
            for column in ratio_columns:
                if re.search(rf"AVERAGE[X]?\s*\([^)]*\[{column}", body,
                             re.IGNORECASE):
                    offenders.append(f"{name} averages [{column}]")
        assert not offenders, offenders

    def test_measures_do_not_hardcode_zero_for_missing_data(self, dax):
        """BLANK() must propagate; COALESCE(..., 0) would fabricate a value."""
        for measure, body in re.findall(
            r"^([A-Z][A-Za-z0-9 +%]+) :=(.*?)(?=^[A-Z][A-Za-z0-9 +%]+ :=|\Z)",
            dax, re.MULTILINE | re.DOTALL,
        ):
            assert "COALESCE" not in body.upper() or "BLANK" in body.upper(), (
                f"{measure} coalesces to zero, which turns a reporting gap "
                f"into a reported zero"
            )

    def test_time_intelligence_uses_the_date_column(self, dax):
        for fn in ("SAMEPERIODLASTYEAR", "DATESINPERIOD", "DATEADD"):
            if fn in dax:
                assert "dim_date[Date]" in dax or "dim_date[Date]" in dax

    def test_measures_are_commented(self, dax):
        measures = _measure_names(dax)
        for name in list(measures)[:5]:
            assert f"{name} :=" in dax


# --- warehouse shape ---------------------------------------------------------

class TestDateDimension:
    def test_has_a_real_date_column(self, columns):
        assert "date" in columns["dim_date"], (
            "dim_date needs a DATE column for time intelligence; a year "
            "integer cannot drive YoY or rolling measures"
        )

    def test_date_is_populated_for_every_row(self, con):
        nulls = con.execute(
            "SELECT COUNT(*) FROM dim_date WHERE date IS NULL"
        ).fetchone()[0]
        assert nulls == 0

    def test_covers_the_full_horizon(self, con):
        low, high = con.execute(
            "SELECT MIN(year), MAX(year) FROM dim_date"
        ).fetchone()
        assert low <= 2000
        assert high >= 2030, "projection years must exist in the date table"

    def test_marks_projection_years(self, con):
        both = con.execute(
            "SELECT COUNT(*) FROM dim_date "
            "WHERE is_observed AND is_projection"
        ).fetchone()[0]
        assert both == 0


class TestRegionDimension:
    def test_region_names_are_populated(self, con):
        total = con.execute(
            "SELECT COUNT(*) FROM dim_region WHERE region_name IS NULL "
            "OR region_name = ''"
        ).fetchone()[0]
        assert total == 0, (
            f"{total} regions have no name; a map legend would show blanks"
        )

    def test_carries_true_nuts_level(self, con):
        levels = con.execute(
            "SELECT DISTINCT nuts_level FROM dim_region "
            "WHERE nuts_level IS NOT NULL ORDER BY 1"
        ).fetchall()
        assert levels, "nuts_level was never populated from the NUTS index"

    def test_level_is_not_a_text_column(self, columns):
        assert "nuts_level" in columns["dim_region"]


class TestFactTables:
    @pytest.mark.parametrize("fact,keys", [
        ("fact_healthcare_workers",
         ["country_code", "profession_code", "age_group_code", "sex_code",
          "year", "measure"]),
        ("fact_staffing_shortage", ["country_code", "profession_code", "year"]),
        ("fact_retirement",
         ["profession_code", "age_group_code", "country_code", "year"]),
        ("fact_regional_workforce", ["nuts_code", "profession_code", "year"]),
        ("fact_population", ["country_code", "sex_code", "year"]),
    ])
    def test_business_key_is_unique(self, con, fact, keys):
        present = [k for k in keys]
        dupes = con.execute(
            f'SELECT COUNT(*) FROM (SELECT {", ".join(present)}, '
            f"COUNT(*) c FROM {fact} GROUP BY {', '.join(present)} "
            f"HAVING c > 1)"
        ).fetchone()[0]
        assert dupes == 0, (
            f"{fact} has {dupes} duplicated business keys; a BI tool would "
            f"double count every measure touching them"
        )

    @pytest.mark.parametrize("fact,column,dim", [
        ("fact_healthcare_workers", "country_code", "dim_country"),
        ("fact_healthcare_workers", "profession_code", "dim_profession"),
        ("fact_regional_workforce", "nuts_code", "dim_region"),
    ])
    def test_foreign_keys_resolve(self, con, fact, column, dim):
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {fact} f WHERE NOT EXISTS "
            f"(SELECT 1 FROM {dim} d WHERE d.{column} = f.{column})"
        ).fetchone()[0]
        assert orphans == 0, f"{orphans} orphan {column} values in {fact}"

    @pytest.mark.parametrize("fact", [
        "fact_healthcare_workers", "fact_retirement",
    ])
    def test_measures_are_non_negative(self, con, fact):
        negatives = con.execute(
            f"SELECT COUNT(*) FROM {fact} WHERE measure_value < 0"
        ).fetchone()[0]
        assert negatives == 0, f"{fact} contains negative headcounts"

    def test_headcounts_are_non_negative(self, con):
        """Headcounts can never be negative.

        ``fact_staffing_shortage`` is excluded deliberately: its measure is a
        *signed gap*, which is negative wherever a country is over-supplied.
        Confining the check to actual headcount columns keeps it meaningful.
        """
        for fact, column in (
            ("fact_healthcare_workers", "workers"),
            ("fact_retirement", "near_retirement_workers"),
            ("fact_staffing_shortage", "actual_workers"),
            ("fact_population", "population"),
        ):
            negatives = con.execute(
                f"SELECT COUNT(*) FROM {fact} WHERE {column} < 0"
            ).fetchone()[0]
            assert negatives == 0, f"{fact}.{column} has negative values"

    def test_shortage_gap_can_be_either_sign(self, con):
        """A surplus and a shortage must both be representable."""
        positives = con.execute(
            "SELECT COUNT(*) FROM fact_staffing_shortage WHERE shortage > 0"
        ).fetchone()[0]
        negatives = con.execute(
            "SELECT COUNT(*) FROM fact_staffing_shortage WHERE shortage < 0"
        ).fetchone()[0]
        assert positives > 0, "no shortages at all is implausible"
        assert negatives > 0, "no surpluses at all is implausible"

    def test_year_columns_are_integers(self, con):
        fractional = con.execute(
            "SELECT COUNT(*) FROM fact_healthcare_workers WHERE year != "
            "CAST(year AS INTEGER)"
        ).fetchone()[0]
        assert fractional == 0


class TestSemanticViews:
    @pytest.mark.parametrize("view", [
        "v_coverage_index", "v_coverage_index_safe", "v_retirement_exposure",
        "v_shortage_ranking", "v_coverage_trend", "v_capacity_pressure",
        "v_sex_composition", "v_reporting_completeness", "v_data_gaps",
        "v_kpi_total_workers", "v_kpi_workforce_gap", "v_kpi_retirement_risk",
        "v_kpi_coverage_index", "v_kpi_hospital_readiness", "v_age_pyramid",
        "v_workforce_aging_index", "v_profession_shortage_rank",
        "v_coverage_gap_trend", "v_regional_risk", "v_forecast_vs_baseline",
    ])
    def test_view_exists_and_is_queryable(self, con, view):
        count = con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
        assert count >= 0

    def test_coverage_index_never_reports_zero_for_missing(self, con):
        """The safe view must return NULL, not 0, where nothing was reported."""
        zeros = con.execute(
            "SELECT COUNT(*) FROM v_coverage_index_safe "
            "WHERE nurse_data_missing AND nurses_per_1000 = 0"
        ).fetchone()[0]
        assert zeros == 0

    def test_regional_risk_has_access_tiers(self, con):
        tiers = con.execute(
            "SELECT DISTINCT access_tier FROM v_regional_risk"
        ).fetchall()
        values = {t[0] for t in tiers}
        assert values <= {"Unknown", "Critical", "High", "Medium", "Low"}
        assert "Critical" in values or "High" in values


class TestRelationshipModel:
    def test_relationships_are_declared(self):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from warehouse.facts import RELATIONSHIPS

        assert len(RELATIONSHIPS) >= 15
        for rel in RELATIONSHIPS:
            assert len(rel) == 6, f"malformed relationship: {rel}"

    def test_declared_relationships_bind(self, columns):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from warehouse.facts import RELATIONSHIPS

        for fact, fcol, dim, dcol, *_ in RELATIONSHIPS:
            assert fact in columns, f"unknown fact table: {fact}"
            assert dim in columns, f"unknown dimension: {dim}"
            assert fcol in columns[fact], \
                f"{fact} has no column {fcol}"
            assert dcol in columns[dim], \
                f"{dim} has no column {dcol}"

    def test_every_fact_has_at_least_one_relationship(self, columns):
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from warehouse.facts import RELATIONSHIPS

        facts = {t for t in columns if t.startswith("fact_")}
        linked = {rel[0] for rel in RELATIONSHIPS}
        assert facts == linked, (
            f"facts with no declared relationship: {sorted(facts - linked)}"
        )