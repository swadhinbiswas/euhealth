"""Data quality framework.

Each check returns a :class:`CheckResult` so the same evaluator produces both
a pass/fail gate and a stored KPI. Checks are grouped in four families:

* **completeness** - null rate, expected-dimension coverage
* **uniqueness**   - duplicate rate on the declared business key
* **validity**     - range, schema, referential (country/region) validation
* **consistency**  - internal reconciliation against a source-reported total
* **freshness**    - newest observation per source

Findings that came out of this being written against real payloads, and are
encoded as explicit checks rather than comments:

1. Several countries report **TOTAL only**, with no age breakdown. An
   age-partitioned metric computed for them would be silently wrong, so
   ``reporting_completeness`` records the share of expected source codes.
2. Eurostat's TOTAL is **not always the sum of its own age bands**. On fully
   reported rows the residual is up to ~0.5%. ``consistency`` allows that band
   and fails beyond it, rather than asserting false exactness.
3. Six countries (BG, CY, LU, PL, PT, SE) report **no nurse data at all**. That
   is a coverage fact, surfaced as a dimension attribute, not backfilled.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class CheckResult:
    name: str
    family: str
    status: str
    observed: Any
    threshold: Any
    detail: str = ""
    severity: str = "error"

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass
class DQReport:
    table: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
    )
    results: list[CheckResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Weighted 0-100 quality score.

        Failures score 0, warnings 0.5, passes 1. Non-error checks are scored
        too, because a low completeness rate is a real quality problem even
        though it is not a pipeline fault.
        """
        if not self.results:
            return 0.0
        points = sum(1.0 if r.status == PASS else 0.5 if r.status == WARN else 0.0
                     for r in self.results)
        return round(100 * points / len(self.results), 2)

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL and r.severity == "error"]

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "generated_at": self.generated_at,
            "score": self.score,
            "rows": len(self.results),
            "failures": len([r for r in self.results if r.status == FAIL]),
            "warnings": len([r for r in self.results if r.status == WARN]),
            "results": [asdict(r) for r in self.results],
        }


# --- individual checks --------------------------------------------------------

def check_null_rate(df: pd.DataFrame, columns: list[str],
                    max_rate: float = 0.05) -> CheckResult:
    worst_col, worst_rate = None, 0.0
    if len(df):
        for col in columns:
            if col in df.columns:
                rate = float(df[col].isna().mean())
                if rate > worst_rate:
                    worst_col, worst_rate = col, rate
    else:
        return CheckResult(
            "null_rate", "completeness", FAIL, None, max_rate,
            "table is empty", "error",
        )
    status = PASS if worst_rate <= max_rate else FAIL
    return CheckResult(
        "null_rate", "completeness", status, round(worst_rate, 4), max_rate,
        f"worst column: {worst_col}",
    )


def check_duplicate_rate(df: pd.DataFrame, keys: list[str],
                         max_rate: float = 0.0) -> CheckResult:
    """Duplicates on the declared business key.

    Zero duplicates are expected because a Eurostat cube has one observation
    per dimension combination; anything else means the loader re-ingested.
    """
    present = [k for k in keys if k in df.columns]
    if len(df) == 0 or len(present) != len(keys):
        return CheckResult(
            "duplicate_rate", "uniqueness", WARN, None, max_rate,
            "key columns unavailable", "warning",
        )
    rate = float(df.duplicated(subset=present).mean())
    return CheckResult(
        "duplicate_rate", "uniqueness",
        PASS if rate <= max_rate else FAIL, round(rate, 4), max_rate,
        f"key: {'+'.join(present)}",
    )


def check_range(df: pd.DataFrame, column: str, low: float | None = None,
                high: float | None = None) -> CheckResult:
    """Range validation. Headcounts cannot be negative; rates are bounded."""
    if column not in df.columns or df.empty:
        return CheckResult(
            f"range_{column}", "validity", WARN, None, (low, high),
            "column unavailable", "warning",
        )
    series = df[column].dropna()
    if series.empty:
        return CheckResult(
            f"range_{column}", "validity", WARN, None, (low, high),
            "no non-null values", "warning",
        )
    violations = int(((series < low) | (series > high)).sum()) if (low is not None
                                                                            and high is not None) else 0
    if low is not None:
        violations = int((series < low).sum())
    elif high is not None:
        violations = int((series > high).sum())
    rate = violations / len(series)
    return CheckResult(
        f"range_{column}", "validity",
        PASS if violations == 0 else FAIL, rate, 0.0,
        f"{violations} of {len(series)} outside [{low}, {high}]",
    )


def check_schema(df: pd.DataFrame, required: list[str]) -> CheckResult:
    missing = [c for c in required if c not in df.columns]
    return CheckResult(
        "schema", "validity", PASS if not missing else FAIL,
        len(required) - len(missing), len(required),
        f"missing columns: {missing}" if missing else "all present",
    )


def check_referential_country(df: pd.DataFrame, valid: set[str],
                              column: str = "geo") -> CheckResult:
    if column not in df.columns or df.empty:
        return CheckResult(
            "ref_country", "validity", WARN, None, len(valid),
            "column unavailable", "warning",
        )
    observed = set(df[column].dropna().unique())
    unknown = observed - valid
    return CheckResult(
        "ref_country", "validity", PASS if not unknown else FAIL,
        len(observed), len(valid),
        f"unknown geo codes: {sorted(unknown)[:10]}" if unknown else "all valid",
    )


def check_reporting_completeness(
    df: pd.DataFrame, profession: str, expected_codes: int
) -> CheckResult:
    """Share of country-years reporting a full age breakdown.

    Countries that report TOTAL only cannot support age-partitioned analysis.
    This is a warning rather than a failure: the data is usable for headline
    totals, and the dimension records the limitation.
    """
    if df.empty or "age" not in df.columns:
        return CheckResult(
            "reporting_completeness", "consistency", WARN, None, expected_codes,
            "table is empty", "warning",
        )
    keys = ["geo", "time"] + (["sex"] if "sex" in df.columns else [])
    per_group = df.groupby(keys)["age"].nunique()
    full = int((per_group == expected_codes).sum())
    rate = full / len(per_group) if len(per_group) else 0.0
    status = PASS if rate >= 0.9 else WARN if rate >= 0.5 else FAIL
    return CheckResult(
        "reporting_completeness", "consistency", status, round(rate, 4), 0.9,
        f"{full}/{len(per_group)} country-years report all "
        f"{expected_codes} age codes",
    )


def check_total_reconciliation(
    df: pd.DataFrame, profession: str, tolerance_pct: float = 1.0
) -> CheckResult:
    """Sum of age bands vs the source-reported TOTAL.

    Eurostat's TOTAL is not always exactly the sum of its age breakdown; the
    observed residual on fully reported rows is up to ~0.5%. The default
    tolerance of 1% accommodates that without hiding a genuine mapping error.
    """
    from transform.age_map import AGE_CODE_MAP, to_canonical

    if df.empty or "age" not in df.columns:
        return CheckResult(
            "total_reconciliation", "consistency", WARN, None, tolerance_pct,
            "table is empty", "warning",
        )
    expected = 1 + len(AGE_CODE_MAP[profession])
    keys = ["geo", "time"] + (["sex"] if "sex" in df.columns else [])
    work = df.copy()
    work["band"] = work["age"].map(lambda c: to_canonical(profession, c))

    reported = (
        work[work["age"] == "TOTAL"].groupby(keys)["value"].sum().rename("total")
    )
    counts = work.groupby(keys)["age"].nunique().rename("codes")
    mapped = (
        work[work["band"].notna()].groupby(keys)["value"].sum().rename("mapped")
    )
    joined = pd.concat([reported, mapped, counts], axis=1).dropna()
    complete = joined[joined["codes"] == expected]
    if complete.empty:
        return CheckResult(
            "total_reconciliation", "consistency", WARN, None, tolerance_pct,
            "no fully reported country-years to test", "warning",
        )
    pct = ((complete["mapped"] - complete["total"]) / complete["total"] * 100).abs()
    worst = float(pct.max())
    offenders = int((pct > tolerance_pct).sum())
    return CheckResult(
        "total_reconciliation", "consistency",
        PASS if offenders == 0 else FAIL, round(worst, 4), tolerance_pct,
        f"{offenders}/{len(complete)} fully-reported country-years exceed "
        f"tolerance; worst {worst:.3f}%",
    )


def check_freshness(df: pd.DataFrame, max_age_days: int = 900,
                    column: str = "time") -> CheckResult:
    """Age of the newest observation in the table."""
    if df.empty or column not in df.columns:
        return CheckResult(
            "freshness", "freshness", WARN, None, max_age_days,
            "column unavailable", "warning",
        )
    try:
        newest = pd.to_datetime(df[column].astype(str), errors="coerce").max()
    except Exception:
        return CheckResult(
            "freshness", "freshness", WARN, None, max_age_days,
            "unparseable time values", "warning",
        )
    if pd.isna(newest):
        return CheckResult(
            "freshness", "freshness", FAIL, None, max_age_days,
            "no parseable time values", "error",
        )
    # pandas returns a tz-naive timestamp; compare in UTC explicitly.
    observed = newest.to_pydatetime()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - observed).days
    return CheckResult(
        "freshness", "freshness",
        PASS if age_days <= max_age_days else FAIL, age_days, max_age_days,
        f"newest observation: {newest.date()}",
    )


def check_coverage(df: pd.DataFrame, expected_countries: set[str],
                   column: str = "geo") -> CheckResult:
    """How many EU27 member states the table covers at all."""
    if df.empty or column not in df.columns:
        return CheckResult(
            "country_coverage", "completeness", FAIL, 0, len(expected_countries),
            "table is empty", "error",
        )
    present = set(df[column].dropna().unique()) & expected_countries
    rate = len(present) / len(expected_countries)
    status = PASS if rate >= 0.95 else WARN if rate >= 0.7 else FAIL
    return CheckResult(
        "country_coverage", "completeness", status, round(rate, 4), 0.95,
        f"{len(present)}/{len(expected_countries)} countries present; "
        f"missing: {sorted(expected_countries - present)}",
    )


# --- suite -------------------------------------------------------------------

def assess_workforce(df: pd.DataFrame, profession: str,
                     expected_countries: set[str],
                     keys: list[str] | None = None) -> DQReport:
    """Standard assessment for a workforce fact table."""
    from transform.age_map import AGE_CODE_MAP

    keys = keys or [
        "geo", "time", "sex",
        "age", "profession", "dataset_code",
    ]
    keys = [k for k in keys if k in df.columns]
    report = DQReport(table=f"fact_healthcare_workers[{profession}]")
    report.results = [
        check_schema(df, ["geo", "time", "value"]),
        check_null_rate(df, ["geo", "time", "value"]),
        check_duplicate_rate(df, keys),
        check_range(df, "value", low=0),
        check_referential_country(df, expected_countries),
        check_coverage(df, expected_countries),
        check_reporting_completeness(
            df, profession, 1 + len(AGE_CODE_MAP[profession])
        ),
        check_total_reconciliation(df, profession),
        check_freshness(df),
    ]
    return report


def write_report(report: DQReport, path) -> None:
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")