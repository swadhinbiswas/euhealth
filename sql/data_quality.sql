-- Availability and integrity of the analytical measures.
--
-- A country that stops reporting is NOT the same as a country that reports
-- zero. Germany publishes hospital beds through 2019 and nothing after; joining
-- naively yields beds = 0, which reads on a dashboard as "Germany closed every
-- hospital". These views separate missing from zero so no consumer can confuse
-- the two.

-- ---------------------------------------------------------------------------
-- Which profession/country/year combinations actually have a headcount?
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_measure_availability AS
SELECT profession_code,
       country_code,
       year,
       measure_value AS headcount,
       (measure_value IS NOT NULL) AS is_reported
FROM fact_healthcare_workers
WHERE measure = 'headcount_total' AND sex_code = 'T';

-- ---------------------------------------------------------------------------
-- Coverage gaps: countries expected but absent from each fact.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_data_gaps AS
SELECT 'nurses' AS domain,
       d.country_code,
       d.country_name
FROM dim_country d
WHERE NOT EXISTS (
    SELECT 1 FROM fact_healthcare_workers w
    WHERE w.country_code = d.country_code
      AND w.profession_code = 'NURS'
      AND w.measure = 'headcount_total'
)
UNION ALL
SELECT 'beds' AS domain, d.country_code, d.country_name
FROM dim_country d
WHERE NOT EXISTS (
    SELECT 1 FROM fact_hospital_capacity b
    WHERE b.country_code = d.country_code
      AND b.category_group = 'hospital_bed'
);

-- ---------------------------------------------------------------------------
-- Series that stop part-way: latest year present vs the global maximum.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_series_endpoints AS
SELECT country_code,
       profession_code,
       MIN(year) AS first_year,
       MAX(year) AS last_year,
       (SELECT MAX(year) FROM fact_healthcare_workers) AS dataset_max_year,
       MAX(year) < (SELECT MAX(year) FROM fact_healthcare_workers)
           AS is_stale
FROM fact_healthcare_workers
WHERE measure = 'headcount_total' AND sex_code = 'T'
GROUP BY country_code, profession_code;

-- ---------------------------------------------------------------------------
-- Safe coverage index: NULL rather than 0 where a country has not reported.
-- A NULL propagates through the KPI; a 0 would silently rank worst.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_coverage_index_safe AS
SELECT v.country_code,
       v.country_name,
       v.year,
       v.physicians,
       v.nurses,
       v.population,
       -- NULL, never 0, when a country did not report. A country that did not
       -- report is not a country with no workforce.
       CASE WHEN COALESCE(v.nurses, 0) > 0 AND COALESCE(v.population, 0) > 0
            THEN v.nurses / v.population * 1000
       END AS nurses_per_1000,
       CASE WHEN COALESCE(v.physicians, 0) > 0 AND COALESCE(v.population, 0) > 0
            THEN v.physicians / v.population * 1000
       END AS physicians_per_1000_safe,
       COALESCE(v.nurses, 0) <= 0 AS nurse_data_missing,
       COALESCE(v.physicians, 0) <= 0 AS physician_data_missing
FROM v_coverage_index v;