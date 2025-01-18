-- Named, reusable analyses for the EU healthcare workforce platform.
-- Every query is written against the gold star schema and is safe to run
-- against DuckDB, Postgres or BigQuery without modification.
--
-- Provenance caveat: hlth_rs_phys and hlth_rs_nurse are published at national
-- level only. There is no NUTS dimension on the source tables, so any regional
-- figure requires an explicit allocation step. Queries here are therefore
-- country-grain by default. See sql/regional_allocation.sql.

-- ---------------------------------------------------------------------------
-- 1. Coverage index: workforce per 1,000 population vs a reference ratio
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_coverage_index AS
WITH workforce AS (
    SELECT country_code, year,
           SUM(CASE WHEN profession_code = 'PHYS' THEN measure_value ELSE 0 END)
               AS physicians,
           SUM(CASE WHEN profession_code = 'NURS' THEN measure_value ELSE 0 END)
               AS nurses
    FROM fact_healthcare_workers
    WHERE measure = 'headcount_total' AND sex_code = 'T'
    GROUP BY country_code, year
)
SELECT w.country_code,
       c.country_name,
       w.year,
       w.physicians,
       w.nurses,
       p.population,
       ROUND(w.physicians / NULLIF(p.population, 0) * 1000, 2)
           AS physicians_per_1000,
       ROUND(w.nurses / NULLIF(p.population, 0) * 1000, 2)
           AS nurses_per_1000
FROM workforce w
JOIN fact_population p
  ON p.country_code = w.country_code AND p.year = w.year
JOIN dim_country c
  ON c.country_code = w.country_code
WHERE p.population > 0;

-- ---------------------------------------------------------------------------
-- 2. Workforce aging index and retirement exposure by country
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_retirement_exposure AS
SELECT w.country_code,
       c.country_name,
       w.profession_code,
       p.profession_name,
       w.year,
       SUM(w.measure_value) AS total_by_age,
       SUM(CASE WHEN w.age_group_code IN ('Y55_64', 'Y_GE65')
                THEN w.measure_value ELSE 0 END) AS near_retirement,
       ROUND(100.0 * SUM(CASE WHEN w.age_group_code IN ('Y55_64', 'Y_GE65')
                              THEN w.measure_value ELSE 0 END)
             / NULLIF(SUM(w.measure_value), 0), 1) AS pct_55_plus,
       -- Median-approximating share: the single band most exposed to exit.
       ROUND(100.0 * SUM(CASE WHEN w.age_group_code = 'Y_GE65'
                              THEN w.measure_value ELSE 0 END)
             / NULLIF(SUM(w.measure_value), 0), 1) AS pct_65_plus
FROM fact_healthcare_workers w
JOIN dim_country c USING (country_code)
JOIN dim_profession p ON p.profession_code = w.profession_code
WHERE w.measure = 'headcount_by_age' AND w.sex_code = 'T'
GROUP BY w.country_code, c.country_name, w.profession_code,
         p.profession_name, w.year;

-- ---------------------------------------------------------------------------
-- 3. Top shortage countries by required-minus-actual physicians
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_shortage_ranking AS
SELECT s.country_code,
       c.country_name,
       s.profession_code,
       s.year,
       s.actual_workers,
       ROUND(s.required_workers, 0) AS required_workers,
       ROUND(s.shortage, 0) AS shortage,
       ROUND(s.coverage_index, 1) AS coverage_index,
       DENSE_RANK() OVER (PARTITION BY s.year ORDER BY s.shortage DESC)
           AS severity_rank
FROM fact_staffing_shortage s
JOIN dim_country c USING (country_code);

-- ---------------------------------------------------------------------------
-- 4. Year-over-year change and 3-year rolling mean of coverage
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_coverage_trend AS
SELECT country_code,
       country_name,
       year,
       physicians_per_1000,
       physicians_per_1000
           - LAG(physicians_per_1000) OVER
               (PARTITION BY country_code ORDER BY year) AS yoy_change,
       ROUND(AVG(physicians_per_1000) OVER (
             PARTITION BY country_code ORDER BY year
             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS rolling_3y_mean
FROM v_coverage_index;

-- ---------------------------------------------------------------------------
-- 5. Hospital capacity: beds per 1,000 population and ICU intensity
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_capacity_pressure AS
WITH beds AS (
    SELECT country_code, year,
           SUM(CASE WHEN category_group = 'hospital_bed' THEN beds ELSE 0 END)
               AS hospital_beds,
           SUM(CASE WHEN category_group = 'icu_bed' THEN beds ELSE 0 END)
               AS icu_beds
    FROM fact_hospital_capacity
    GROUP BY country_code, year
)
SELECT b.country_code,
       c.country_name,
       b.year,
       b.hospital_beds,
       b.icu_beds,
       ROUND(b.icu_beds / NULLIF(b.hospital_beds, 0) * 100, 2)
           AS icu_share_pct
FROM beds b
JOIN dim_country c USING (country_code);

-- ---------------------------------------------------------------------------
-- 6. Sex composition of the workforce (the gender gap in health careers)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sex_composition AS
SELECT country_code,
       profession_code,
       year,
       SUM(CASE WHEN sex_code = 'F' THEN measure_value ELSE 0 END) AS female,
       SUM(CASE WHEN sex_code = 'M' THEN measure_value ELSE 0 END) AS male,
       ROUND(100.0 * SUM(CASE WHEN sex_code = 'F' THEN measure_value ELSE 0 END)
             / NULLIF(SUM(measure_value), 0), 1) AS pct_female
FROM fact_healthcare_workers
WHERE measure = 'headcount_total'
GROUP BY country_code, profession_code, year;

-- ---------------------------------------------------------------------------
-- 7. Reporting completeness by country: how much age detail exists
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_reporting_completeness AS
SELECT country_code,
       country_name,
       profession_code,
       year,
       COUNT(*) AS age_rows,
       COUNT(DISTINCT age_group_code) AS distinct_bands,
       CASE WHEN COUNT(DISTINCT age_group_code) >= 5
            THEN 'full' ELSE 'partial' END AS coverage
FROM fact_healthcare_workers w
JOIN dim_country c USING (country_code)
WHERE w.measure = 'headcount_by_age' AND w.sex_code = 'T'
GROUP BY country_code, country_name, profession_code, year;