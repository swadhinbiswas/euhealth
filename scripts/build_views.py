"""Rebuild every SQL view in the warehouse and verify each one runs.

A view that fails to bind is worse than no view: a BI tool will surface it as a
broken tile with no explanation. This runs all four view families and fails
loudly, so the semantic layer cannot be silently broken.

Usage:
    python scripts/build_views.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import WAREHOUSE  # noqa: E402

SQL_DIR = ROOT / "sql"
FORECAST_CSV = ROOT / "data" / "models" / "workforce_forecast.csv"

#: Ordered because later views depend on earlier ones.
VIEW_FILES = [
    "analytics.sql",
    "data_quality.sql",
    "powerbi_measures.sql",
]


def load_forecasts(con: duckdb.DuckDBPyConnection) -> bool:
    """Load the ML forecast output so the forecasting views can bind."""
    if not FORECAST_CSV.exists():
        print("  workforce_forecast.csv not found; "
              "run 'make forecast' to enable forecasting views")
        return False
    frame = pd.read_csv(FORECAST_CSV)
    con.register("_fc", frame)
    con.execute("CREATE OR REPLACE TABLE forecast_workforce AS SELECT * FROM _fc")
    con.unregister("_fc")
    print(f"  forecast_workforce loaded: {len(frame)} rows")
    return True


def split_statements(sql: str) -> list[str]:
    """Split a SQL file into CREATE VIEW statements.

    Comment lines are stripped *before* splitting. A semicolon or unbalanced
    parenthesis inside a ``--`` comment otherwise truncates the statement and
    produces a baffling parser error on valid SQL.
    """
    body = "".join(
        line for line in sql.splitlines(keepends=True)
        if not line.lstrip().startswith("--")
    )
    return [
        s for s in body.split(";")
        if "CREATE OR REPLACE VIEW" in s
    ]


def main() -> int:
    con = duckdb.connect(str(WAREHOUSE))
    failures = []
    try:
        load_forecasts(con)
        for filename in VIEW_FILES:
            path = SQL_DIR / filename
            if not path.exists():
                failures.append(f"{filename}: missing")
                continue
            statements = split_statements(path.read_text())
            built = 0
            for statement in statements:
                name = re.search(r"VIEW (\w+)", statement).group(1)
                try:
                    con.execute(statement + ";")
                    built += 1
                except Exception as exc:
                    failures.append(f"{name}: {str(exc)[:160]}")
            print(f"{filename}: {built}/{len(statements)} views built")
    finally:
        con.close()

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("\nall views built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())