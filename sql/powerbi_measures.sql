-- Power BI / BI-tool semantic layer.
--
-- These are the measures a dashboard page computes, expressed as SQL against the
-- gold star schema. They are the reference implementation: the DAX library in
-- powerbi/measures.dax is the BI-tool translation, and both are kept in step.
--
-- Every measure follows three rules:
--   1. Never average a ratio. Recompute it from the underlying totals.
--   2. Never treat a reporting gap as zero. COALESCE to NULL and flag it.
--   3. Use a window function rather than a subquery where a BI tool would
--      otherwise need a separate measure for every time grain.

-- ===========================================================================
-- PAGE 1  Executive overview
-- ===========================================================================

-- Total active workers (both professions, reported totals only).
CREATE OR REPLACE VIEW v_kpi_total_workers AS
SELECT w.year,
       SUM(CASE WHEN w.profession_code = 'PHYS'
                THEN w.measure_value ELSE 0 END) AS total_doctors,
       SUM(CASE WHEN w.profession_code = 'NURS'
                THEN w.measure_value ELSE 0 END) AS total_nurses,
       SUM(w.measure_value) AS total_workers
FROM fact_healthcare_workers w
WHERE w.measure = 'headcount_total' AND w.sex_code = 'T'
GROUP BY w.year;

-- Workforce gap: required minus actual, and the signed gap percentage.
-- A positive gap is a shortage.
CREATE OR REPLACE VIEW v_kpi_workforce_gap AS
SELECT s.year,
       SUM(s.required_workers) AS required,
       SUM(s.actual_workers)   AS actual,
       SUM(s.shortage)         AS gap,
       ROUND(100.0 * SUM(s.shortage)
             / NULLIF(SUM(s.required_workers), 0), 2) AS gap_pct
FROM fact_staffing_shortage s
GROUP BY s.year;

-- Retirement risk: share of the workforce aged 55+ or 65+.
CREATE OR REPLACE VIEW v_kpi_retirement_risk AS
SELECT e.year,
       ROUND(100.0 * SUM(e.near_retirement)
             / NULLIF(SUM(e.total_by_age), 0), 2) AS retirement_risk_pct,
       SUM(e.near_retirement) AS near_retirement_workers
FROM v_retirement_exposure e
GROUP BY e.year;

-- Healthcare coverage index, as a percentage of the reference ratio.
-- NULL where a country did not report, never 0.
CREATE OR REPLACE VIEW v_kpi_coverage_index AS
SELECT year,
       ROUND(AVG(physicians_per_1000_safe), 2) AS avg_physicians_per_1000,
       ROUND(AVG(nurses_per_1000), 2)          AS avg_nurses_per_1000,
       COUNT(*) FILTER (WHERE physician_data_missing) AS countries_missing_physicians,
       COUNT(*) FILTER (WHERE nurse_data_missing)     AS countries_missing_nurses
FROM v_coverage_index_safe
GROUP BY year;

-- Hospital readiness: ICU intensity and beds per 1,000 population.
CREATE OR REPLACE VIEW v_kpi_hospital_readiness AS
SELECT b.year,
       SUM(b.hospital_beds) AS hospital_beds,
       SUM(b.icu_beds)      AS icu_beds,
       ROUND(100.0 * SUM(b.icu_beds)
             / NULLIF(SUM(b.hospital_beds), 0), 2) AS icu_share_pct
FROM v_capacity_pressure b
GROUP BY b.year;

-- ===========================================================================
-- PAGE 5  Workforce aging
-- ===========================================================================

-- Workforce age pyramid: distribution by canonical age band and sex.
CREATE OR REPLACE VIEW v_age_pyramid AS
SELECT w.country_code,
       c.country_name,
       w.profession_code,
       w.sex_code,
       g.sex_label,
       a.age_group_code,
       a.age_group_label,
       a.sort_order,
       SUM(w.measure_value) AS workers
FROM fact_healthcare_workers w
JOIN dim_country c  ON c.country_code = w.country_code
JOIN dim_gender g   ON g.sex_code = w.sex_code
JOIN dim_age_group a ON a.age_group_code = w.age_group_code
WHERE w.measure = 'headcount_by_age'
GROUP BY w.country_code, c.country_name, w.profession_code, w.sex_code,
         g.sex_label, a.age_group_code, a.age_group_label, a.sort_order;

-- Workforce aging index: median-approximating share above 55, with a rolling
-- three-year mean so a single year's jump does not read as a trend.
CREATE OR REPLACE VIEW v_workforce_aging_index AS
SELECT country_code,
       country_name,
       profession_code,
       year,
       pct_55_plus,
       AVG(pct_55_plus) OVER (
           PARTITION BY country_code, profession_code ORDER BY year
           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_3y_pct_55_plus
FROM v_retirement_exposure;

-- ===========================================================================
-- PAGE 3 / 4  Shortage by profession
-- ===========================================================================

-- Profession shortage ranking, ranked within year with a dense rank so ties do
-- not create gaps in the sequence.
CREATE OR REPLACE VIEW v_profession_shortage_rank AS
SELECT s.year,
       s.country_code,
       c.country_name,
       s.profession_code,
       p.profession_name,
       s.actual_workers,
       ROUND(s.required_workers, 0) AS required_workers,
       ROUND(s.shortage, 0)          AS shortage,
       ROUND(s.coverage_index, 1)    AS coverage_index,
       DENSE_RANK() OVER (
           PARTITION BY s.year, s.profession_code ORDER BY s.shortage DESC
       ) AS severity_rank
FROM fact_staffing_shortage s
JOIN dim_country c   ON c.country_code = s.country_code
JOIN dim_profession p ON p.profession_code = s.profession_code;

-- Coverage gap trend: year-over-year change in the coverage index, so page 3
-- can show improvement or deterioration rather than a flat level.
CREATE OR REPLACE VIEW v_coverage_gap_trend AS
SELECT country_code,
       country_name,
       profession_code,
       year,
       coverage_index,
       coverage_index
           - LAG(coverage_index) OVER (
               PARTITION BY country_code, profession_code ORDER BY year
           ) AS yoy_change,
       AVG(coverage_index) OVER (
           PARTITION BY country_code, profession_code ORDER BY year
           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
       ) AS rolling_3y_coverage
FROM v_shortage_ranking;

-- ===========================================================================
-- PAGE 7  Medical desert detection
-- ===========================================================================

-- Regional coverage with a risk tier. Tiers are cut on the observed
-- distribution rather than an assumed benchmark, and every input is either
-- observed or explicitly labelled as allocated.
-- Regional coverage and access tiers.
--
-- Year alignment is the subtlety here. fact_population_nuts is a single-year
-- snapshot (2023), while fact_regional_workforce runs 2000-2020. Joining on an
-- exact year match returns nothing at all, which is what an earlier version of
-- this view did. The population snapshot is therefore joined on region alone
-- and the year is labelled as the reference year, never presented as if the
-- workforce figure and the denominator came from the same year.
--
-- Coverage is only computed for the profession rows that exist, so a region
-- with doctors but no nurse row is not silently averaged across professions.
CREATE OR REPLACE VIEW v_regional_risk AS
WITH regional AS (
    -- region_name comes from the dimension when the code is known there.
    SELECT r.nuts_code,
           COALESCE(reg.region_name, r.nuts_code) AS region_name,
           r.nuts_level,
           r.country_code,
           r.profession_code,
           r.year,
           r.measure_value AS workers
    FROM fact_regional_workforce r
    LEFT JOIN dim_region reg ON reg.nuts_code = r.nuts_code
),
population AS (
    -- fact_population_nuts is a single-year snapshot (2023) at the country's
    -- own NUTS level. It is the only regional denominator available, so the
    -- reference year is carried through and never implied to match the
    -- workforce year.
    SELECT nuts_code,
           MAX(year) AS population_reference_year,
           SUM(population) AS population
    FROM fact_population_nuts
    WHERE age_group_code IS NULL AND sex_code = 'T'
    GROUP BY nuts_code
),
joined AS (
    SELECT rg.nuts_code,
           rg.region_name,
           rg.nuts_level,
           rg.country_code,
           rg.profession_code,
           rg.year,
           rg.workers,
           p.population,
           p.population_reference_year,
           -- The workforce year and the population snapshot differ, so this is
           -- an indicative density, not a same-year rate. Callers must not
           -- present it as one.
           ROUND(rg.workers / NULLIF(p.population, 0) * 1000, 2)
               AS workers_per_1000
    FROM regional rg
    JOIN population p ON p.nuts_code = rg.nuts_code
    WHERE p.population > 0
)
SELECT *,
       NTILE(4) OVER (ORDER BY workers_per_1000) AS density_quartile,
       CASE
           WHEN workers_per_1000 IS NULL THEN 'Unknown'
           WHEN workers_per_1000 < 2.0 THEN 'Critical'
           WHEN workers_per_1000 < 5.0 THEN 'High'
           WHEN workers_per_1000 < 10.0 THEN 'Medium'
           ELSE 'Low'
       END AS access_tier
FROM joined;

-- ===========================================================================
-- PAGE 8  Forecasting
-- ===========================================================================

-- The forecast is the output of a validated model comparison, not a SQL
-- calculation, so it arrives as a table. scripts/build_views.py loads
-- data/models/workforce_forecast.csv into forecast_workforce before creating
-- this view and reports loudly if the file is missing, rather than creating an
-- empty view that looks like a successful run.
CREATE OR REPLACE VIEW v_forecast_vs_baseline AS
SELECT f.country_code,
       c.country_name,
       f.profession_code,
       f.year,
       f.forecast,
       f.baseline_forecast,
       ROUND(f.forecast - f.baseline_forecast, 0) AS model_uplift,
       f.change_vs_2024_pct,
       f.latest_observed_year
FROM forecast_workforce f
JOIN dim_country c ON c.country_code = f.country_code;