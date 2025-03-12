.PHONY: help setup ingest geo warehouse regional forecast views pbip preview-pbip export-data site screenshots readme-charts test lint fmt check all clean

PY := .venv/bin/python

help:
	@echo "setup      install dev dependencies into .venv (python 3.12)"
	@echo "ingest     pull Eurostat into data/raw (idempotent, cached)"
	@echo "geo        install NUTS 2021 boundaries for map visuals"
	@echo "warehouse  quality gate + build gold star schema into DuckDB"
	@echo "views      rebuild every SQL view (semantic layer)"
	@echo "pbip       generate the Power BI Project (model + 8-page report)"
	@echo "preview-pbip  render the report preview and screenshot it"
	@echo "site       generate the static dashboard from the warehouse"
	@echo "screenshots  capture the dashboard for the README (needs chromium)"
	@echo "regional   build observed NUTS workforce (level-harmonised)"
	@echo "forecast   run 7 model families with walk-forward validation"
	@echo "test       run the test suite"
	@echo "lint       ruff check"
	@echo "check      lint + test  (what CI runs)"
	@echo "all        ingest -> warehouse -> regional -> forecast -> check"
	@echo "clean      remove caches and the warehouse (keeps data/raw)"

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[ml,viz,dev]"

ingest:
	$(PY) scripts/ingest_all.py

geo:
	$(PY) scripts/install_geo.py

warehouse:
	PYTHONPATH=src $(PY) -m src.warehouse.cli

regional:
	PYTHONPATH=src $(PY) -m src.geo.cli

forecast:
	PYTHONPATH=src $(PY) -m src.ml.cli

views:
	$(PY) scripts/build_views.py

# Generate the Power BI Project: semantic model + 8-page report.
pbip:
	PYTHONPATH=src $(PY) -m src.powerbi.generate_pbip
	PYTHONPATH=src $(PY) -m src.powerbi.generate_report

# Render the PBIP report's content to HTML and screenshot it. Power BI
# Desktop is Windows-only, so this is a preview, not a Desktop capture.
preview-pbip:
	$(PY) scripts/preview_pbip.py

export-data:
	PYTHONPATH=src $(PY) -m src.dashboard.export

# Regenerate the static dashboard from the current warehouse.
site: export-data
	$(PY) scripts/build_site.py

# Screenshot the site for the README. Requires chromium on PATH.
screenshots:
	$(PY) scripts/screenshot_site.py

# Animated SVG data charts for the README.
readme-charts:
	PYTHONPATH=src $(PY) scripts/build_readme_charts.py

test:
	PYTHONPATH=src $(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tests scripts

fmt:
	.venv/bin/ruff format src tests scripts

check: lint test

all: ingest geo warehouse regional forecast views pbip site check

clean:
	rm -rf .pytest_cache .ruff_cache src/__pycache__ src/*/__pycache__ \
	       tests/__pycache__ scripts/__pycache__ data/healthcare_dw.duckdb