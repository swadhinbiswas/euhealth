"""Export warehouse figures for the static dashboard.

The static site is generated from this export rather than querying DuckDB at
runtime, so it deploys to Cloudflare Pages as plain files with no server or
database. Every number here is read from the verified gold layer; nothing is
computed for effect.

Each payload carries the caveat that applies to it. A dashboard that shows a
number without its provenance invites someone to over-read it.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
OUT = ROOT / "data" / "export" / "site"

EU27_ORDER = [
    "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "AT", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
]


def _round(frame: pd.DataFrame, columns: dict) -> pd.DataFrame:
    for column, digits in columns.items():
        if column in frame.columns:
            frame[column] = frame[column].astype(float).round(digits)
    return frame


def export() -> dict:
    if not WAREHOUSE.exists():
        raise SystemExit(f"{WAREHOUSE} not found. Run 'make warehouse' first.")
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    OUT.mkdir(parents=True, exist_ok=True)
    payload: dict = {}

    try:
        # Anchor the site on the most recent year in which coverage is complete,
        # not the most recent headcount. Population reporting lags the
        # workforce series, so the newest headcount year has only three
        # countries with a matching denominator. Joining on year drops the
        # rest, and a three-row chart is worse than a one-year-old one that is
        # actually populated.
        latest = con.execute("""
            SELECT year FROM v_coverage_index_safe
            WHERE physicians_per_1000_safe IS NOT NULL
            GROUP BY year HAVING COUNT(*) >= 20
            ORDER BY year DESC LIMIT 1
        """).fetchone()
        latest = int(latest[0]) if latest else con.execute(
            "SELECT MAX(year) FROM fact_healthcare_workers "
            "WHERE measure = 'headcount_total'"
        ).fetchone()[0]

        # --- Page 1: executive KPIs -----------------------------------------
        payload["kpis"] = con.execute(f"""
            SELECT
                (SELECT SUM(measure_value) FROM fact_healthcare_workers
                 WHERE measure='headcount_total' AND sex_code='T'
                   AND profession_code='PHYS' AND year={latest})      AS doctors,
                (SELECT SUM(measure_value) FROM fact_healthcare_workers
                 WHERE measure='headcount_total' AND sex_code='T'
                   AND profession_code='NURS' AND year={latest})      AS nurses,
                (SELECT SUM(shortage) FROM fact_staffing_shortage
                 WHERE year={latest})                                  AS workforce_gap,
                (SELECT SUM(required_workers) FROM fact_staffing_shortage
                 WHERE year={latest})                                  AS required_workers,
                (SELECT retirement_risk_pct FROM v_kpi_retirement_risk
                 WHERE year={latest})                                  AS retirement_risk_pct,
                (SELECT icu_share_pct FROM v_kpi_hospital_readiness
                 WHERE year={latest})                                  AS icu_share_pct
        """).df().iloc[0].to_dict()

        payload["reference_year"] = int(latest)

        # Coverage index, latest year, safe (NULL where unreported).
        payload["coverage"] = _round(con.execute(f"""
            SELECT country_code, country_name,
                   physicians_per_1000_safe AS physicians_per_1000,
                   nurses_per_1000,
                   nurse_data_missing, physician_data_missing
            FROM v_coverage_index_safe
            WHERE year = {latest}
            ORDER BY country_name
        """).df(), {"physicians_per_1000": 2, "nurses_per_1000": 2})

        # --- Page 3/5: retirement exposure ---------------------------------
        # Retirement exposure uses the latest year with reported age bands.
        # A small country can report an age breakdown several years after its
        # national total is updated, so the anchor year is resolved per table
        # rather than assumed from the headline KPI.
        retirement_year = con.execute("""
            SELECT year FROM v_retirement_exposure
            WHERE profession_code = 'PHYS' AND pct_55_plus IS NOT NULL
            GROUP BY year HAVING COUNT(*) >= 10
            ORDER BY year DESC LIMIT 1
        """).fetchone()
        retirement_year = int(retirement_year[0]) if retirement_year else latest

        payload["retirement"] = _round(con.execute(f"""
            SELECT country_code, country_name, profession_code,
                   pct_55_plus, pct_65_plus
            FROM v_retirement_exposure
            WHERE year = {retirement_year} AND profession_code = 'PHYS'
              AND pct_55_plus IS NOT NULL
            ORDER BY pct_55_plus DESC
        """).df(), {"pct_55_plus": 1, "pct_65_plus": 1})
        payload["retirement_year"] = retirement_year

        # --- Age pyramid ----------------------------------------------------
        # Age pyramid needs a country with both sexes reported in every band.
        complete = con.execute("""
            SELECT country_code FROM v_age_pyramid
            WHERE profession_code = 'PHYS'
            GROUP BY country_code, sex_code, age_group_code
            HAVING COUNT(*) >= 5
        """).df()
        # Pick the country with the most fully populated sex x band cells.
        pyramid_country = "DE"
        if len(complete):
            counts = complete.groupby("country_code").size()
            pyramid_country = str(counts.idxmax())

        # Male and Female only. Including the "Total" row as a third bar would double
        # the apparent width of every band, since Total is by construction the
        # sum of the other two.
        payload["age_pyramid"] = con.execute(f"""
            SELECT country_code, profession_code, age_group_code,
                   age_group_label, sort_order, sex_label,
                   SUM(workers) AS workers
            FROM v_age_pyramid
            WHERE country_code = '{pyramid_country}'
              AND profession_code = 'PHYS'
              AND sex_label IN ('Male', 'Female')
            GROUP BY 1,2,3,4,5,6
            ORDER BY sort_order, sex_label
        """).df().to_dict(orient="records")
        payload["pyramid_country"] = pyramid_country

        # --- Page 6: capacity ----------------------------------------------
        payload["capacity"] = _round(con.execute(f"""
            SELECT country_code, country_name, hospital_beds, icu_beds,
                   icu_share_pct
            FROM v_capacity_pressure
            WHERE year = {latest} AND icu_share_pct IS NOT NULL
            ORDER BY icu_share_pct DESC
            LIMIT 20
        """).df(), {"icu_share_pct": 2})

        # --- Page 7: regional risk -----------------------------------------
        # One row per region. v_regional_risk carries every profession-year, so
        # selecting it raw yields the same region repeated across a decade of
        # years, which reads as a list of duplicate places. Take the most
        # recent observed year per region and keep physicians only.
        payload["regional"] = con.execute("""
            WITH latest AS (
                SELECT nuts_code, MAX(year) AS year
                FROM v_regional_risk
                WHERE workers_per_1000 IS NOT NULL
                  AND profession_code = 'PHYS'
                GROUP BY nuts_code
            )
            SELECT r.nuts_code,
                   r.region_name,
                   r.country_code,
                   r.nuts_level,
                   r.profession_code,
                   r.year,
                   r.workers_per_1000,
                   r.access_tier
            FROM v_regional_risk r
            JOIN latest l
              ON l.nuts_code = r.nuts_code AND l.year = r.year
            WHERE r.profession_code = 'PHYS'
              AND r.workers_per_1000 IS NOT NULL
              AND r.access_tier IS NOT NULL
            ORDER BY r.workers_per_1000
        """).df().to_dict(orient="records")
        payload["regional_year"] = int(
            con.execute("""
                SELECT MAX(year) FROM fact_regional_workforce
            """).fetchone()[0]
        )

        # --- Page 8: forecasts ---------------------------------------------
        payload["forecast"] = _round(con.execute("""
            SELECT country_code, country_name, profession_code, year,
                   forecast, baseline_forecast, model_uplift
            FROM v_forecast_vs_baseline
            WHERE year IN (2027, 2030, 2035)
            ORDER BY forecast DESC
        """).df(), {"forecast": 0, "baseline_forecast": 0, "model_uplift": 0})

        # --- Coverage trend for Germany -------------------------------------
        payload["coverage_trend"] = _round(con.execute("""
            SELECT year, physicians_per_1000, nurses_per_1000
            FROM v_coverage_index
            WHERE country_code = 'DE' AND year >= 2000
            ORDER BY year
        """).df(), {"physicians_per_1000": 2, "nurses_per_1000": 2})

        # --- Structural breaks (data-quality context) -----------------------
        breaks_path = ROOT / "data" / "models" / "structural_breaks.csv"
        if breaks_path.exists():
            breaks = pd.read_csv(breaks_path)
            payload["structural_breaks"] = _round(
                breaks.head(12), {"magnitude_pct": 1}
            ).to_dict(orient="records")

        # --- Sex composition (the nursing gender gap) -----------------------
        payload["sex_composition"] = con.execute(f"""
            SELECT c.country_name, v.profession_code,
                   ROUND(100.0 * v.female
                         / NULLIF(v.female + v.male, 0), 1) AS pct_female
            FROM v_sex_composition v
            JOIN dim_country c ON c.country_code = v.country_code
            WHERE v.year = {latest} AND v.profession_code = 'NURS'
              AND (v.female + v.male) > 0
            ORDER BY pct_female DESC
        """).df().to_dict(orient="records")

        # --- Data quality ----------------------------------------------------
        payload["quality"] = {
            "gaps": con.execute("""
                SELECT domain, COUNT(*) AS missing
                FROM v_data_gaps GROUP BY domain ORDER BY domain
            """).df().to_dict(orient="records"),
            "dq_physicians": json.loads((
                ROOT / "data" / "logs" / "dq_physicians.json"
            ).read_text())["score"] if (
                ROOT / "data" / "logs" / "dq_physicians.json"
            ).exists() else None,
            "dq_nurses": json.loads((
                ROOT / "data" / "logs" / "dq_nurses.json"
            ).read_text())["score"] if (
                ROOT / "data" / "logs" / "dq_nurses.json"
            ).exists() else None,
            "model_mape": _read_model_comparison(),
            "regional_rows": con.execute(
                "SELECT COUNT(*) FROM fact_regional_workforce"
            ).fetchone()[0],
            "regional_countries": con.execute(
                "SELECT COUNT(DISTINCT country_code) FROM fact_regional_workforce"
            ).fetchone()[0],
        }
    finally:
        con.close()

    # DuckDB returns numpy scalars and DataFrames in places; normalise the
    # whole payload so the JSON encoder never meets an unknown type.
    target = OUT / "data.json"
    payload = _nan_to_null(payload)
    text = json.dumps(payload, indent=1, default=_coerce, allow_nan=False)
    target.write_text(text)
    print(f"wrote {target} ({target.stat().st_size:,} bytes)")
    for key, value in payload.items():
        if isinstance(value, list):
            print(f"  {key:18s} {len(value):>5} rows")
        elif isinstance(value, dict):
            print(f"  {key:18s} {len(value):>5} fields")
        else:
            print(f"  {key:18s} {value}")
    return payload


def _nan_to_null(payload):
    """Replace every NaN in the payload with None.

    ``json.dumps`` only consults ``default=`` for types it cannot serialise at
    all. A bare float NaN *is* serialisable, so it is emitted as the literal
    token ``NaN`` -- which is not valid JSON. Browsers reject the file
    outright, leaving the page blank with only a console error to explain it.
    """
    if isinstance(payload, dict):
        return {k: _nan_to_null(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_nan_to_null(v) for v in payload]
    if isinstance(payload, float) and payload != payload:
        return None
    if isinstance(payload, pd.DataFrame):
        return _nan_to_null(payload.to_dict(orient="records"))
    if isinstance(payload, pd.Series):
        return _nan_to_null(payload.to_dict())
    return payload


def _coerce(value):
    """JSON fallback for numpy scalars, DataFrames and Timestamps."""
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(value)


def _read_model_comparison() -> list:
    path = ROOT / "data" / "models" / "model_comparison.csv"
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    keep = frame[["model", "mae", "rmse", "mape", "smape"]]
    return _round(keep, {"mae": 1, "rmse": 1, "mape": 3, "smape": 3}
                  ).to_dict(orient="records")


if __name__ == "__main__":
    export()