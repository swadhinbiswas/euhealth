.PHONY: help setup ingest warehouse regional forecast test lint fmt check all clean

PY := .venv/bin/python

help:
	@echo "setup      install dev dependencies into .venv (python 3.12)"
	@echo "ingest     pull Eurostat into data/raw (idempotent, cached)"
	@echo "warehouse  quality gate + build gold star schema into DuckDB"
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

warehouse:
	PYTHONPATH=src $(PY) -m src.warehouse.cli

regional:
	PYTHONPATH=src $(PY) -m src.geo.cli

forecast:
	PYTHONPATH=src $(PY) -m src.ml.cli

test:
	PYTHONPATH=src $(PY) -m pytest -q

lint:
	.venv/bin/ruff check src tests scripts

fmt:
	.venv/bin/ruff format src tests scripts

check: lint test

all: ingest warehouse regional forecast check

clean:
	rm -rf .pytest_cache .ruff_cache src/__pycache__ src/*/__pycache__ \
	       tests/__pycache__ scripts/__pycache__ data/healthcare_dw.duckdb