# Data Engineering & Analytics Taste

## Architecture
- Prefers the medallion / layered warehouse stack: raw → bronze → silver → gold → analytics warehouse → BI. Confidence: 0.9
- Lays out the architecture as a vertical ASCII pipeline in the brief and expects the implementation to follow that layering literally. Confidence: 0.75
- Expects both batch and incremental ingestion paths, and multiple load targets (DuckDB, PostgreSQL, BigQuery, Snowflake, Delta Lake concepts) to be represented. Confidence: 0.8

## Modeling
- Prefers a star schema with explicit fact/dimension separation, and wants the modeling decisions named: primary keys, surrogate keys, foreign keys, slowly changing dimensions, and a stated fact grain. Confidence: 0.9
- Wants a data dictionary covering the model. Confidence: 0.85
- Decomposes facts finely by business process (workers, retirement, demand, capacity, vacancies, forecast, shortage, population) rather than one wide fact table. Confidence: 0.8

## ETL / transformation
- Expects a documented transform stage covering cleaning, missing values, standardization, country/region/profession mapping, deduplication, and historical tracking. Expects real mapping tables, not ad-hoc renames. Confidence: 0.85
- Treats regional/geographic normalization (country codes, NUTS-style region mapping) as a first-class concern, not an afterthought. Confidence: 0.85

## Data quality
- Wants a data quality framework, not spot checks: null rate, duplicate rate, freshness, range/schema validation, and reference validation (valid country, valid region), with the results surfaced as quality-score KPIs. Confidence: 0.9

## SQL analytics
- Expects advanced SQL as a deliverable: window functions, rolling averages, cohort analysis, time-series, YoY deltas, and ranking — not just simple group-bys. Confidence: 0.9
- Prefers named, business-legible analyses (retirement risk, hospital risk score, medical desert score) expressed as reusable scored metrics rather than one-off queries. Confidence: 0.8

## Forecasting & ML
- Expects multiple model families (Prophet, XGBoost, LightGBM, Random Forest, ARIMA) compared on the same task, not one model. Confidence: 0.9
- Expects explicit multi-horizon forecasting (e.g. 2027/2030/2035) and scenario bands (best / expected / worst case), not a single point estimate. Confidence: 0.8
- Expects discretized risk tiers (low/medium/high/critical) for scores so results are actionable. Confidence: 0.75
- Prefers a feature-rich modeling surface: geography, profession, sector, specialization, age group, gender, and time are all expected as dimensions. Confidence: 0.8

## BI semantic layer
- Expects a dedicated semantic/measure layer of advanced formulas (e.g. DAX) for KPIs such as workforce gap %, retirement risk %, coverage index, accessibility score — computed, not just displayed. Confidence: 0.9
- Prefers multi-page drill-down reporting: executive overview → country → region → facility, with distinct pages per analytical theme. Confidence: 0.8

## Geospatial
- Expects a real GIS component: interactive maps, drill-down, heat/cluster layers, travel-distance and coverage analysis — geography is a core analytical dimension, not decoration. Confidence: 0.85
