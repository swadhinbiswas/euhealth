"""Generate the Power BI Project (PBIP) from the live warehouse.

A PBIP is a folder Power BI Desktop opens directly: the semantic model as TMDL
and the report layout as JSON. No conversion step, no import wizard, and both
files are diffable in git -- which is the actual reason to prefer it over a
binary .pbix.

Why generated rather than hand-written
--------------------------------------
The warehouse is the source of truth. Column lists, relationship endpoints and
measure references are read from it, so the project cannot drift from the data
the way a hand-maintained model does. ``tests/test_pbip.py`` then re-validates
the output independently.

Data source
-----------
A **live DuckDB connection** is the default, so the report refreshes against the
real warehouse. A Parquet snapshot of every table is written alongside it for
machines without DuckDB; the M query prefers the live source and falls back to
the snapshot.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
PBIP_DIR = ROOT / "powerbi" / "pbip"
PROJECT_NAME = "EU-Health-Workforce"

#: Report-level tables that are views, not physical tables. They are exposed to
#: the model as computed expressions where useful, but the physical tables they
#: derive from are the ones that get loaded.
PHYSICAL_FACTS = [
    "fact_healthcare_workers",
    "fact_retirement",
    "fact_staffing_shortage",
    "fact_population",
    "fact_population_nuts",
    "fact_hospital_capacity",
    "fact_regional_workforce",
    # Demo indicators, not counts. Used for the reported share of the
    # population aged 65+, so it belongs in the model even though no
    # dashboard page plots it directly.
    "fact_population_indicators",
]
PHYSICAL_DIMS = [
    "dim_country",
    "dim_profession",
    "dim_age_group",
    "dim_gender",
    "dim_date",
    "dim_region",
    "dim_specialization",
    "dim_sector",
]

#: The colour ramp. Sequential for the four risk tiers, diverging for the
#: workforce gap so surplus and shortage are distinguishable without relying on
#: colour alone (each tier is also labelled in the report).
RISK_COLOURS = {
    "Critical": "#A4243B",
    "High": "#D1603D",
    "Medium": "#E8B04B",
    "Low": "#4E8C6D",
    "Unknown": "#7B8496",
}


def _columns(con, table: str) -> list[dict]:
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [
        {
            "name": name,
            # Power BI type map. Everything numeric imports as a whole or
            # decimal number; a BIGINT headcount must not become a float and
            # pick up rounding artefacts in a chart axis.
            "dataType": _pbi_type(dtype),
        }
        for name, dtype in rows
    ]


def _pbi_type(dtype: str) -> str:
    if dtype.startswith("BIGINT") or dtype.startswith("INTEGER"):
        return "int64"
    if dtype.startswith("DOUBLE") or dtype.startswith("FLOAT") \
            or dtype.startswith("DECIMAL") or dtype.startswith("REAL"):
        return "double"
    if dtype.startswith("BOOLEAN"):
        return "bool"
    return "string"


def _tmdl_type(pbi_type: str) -> str:
    return {
        "int64": "int64",
        "double": "double",
        "bool": "boolean",
    }.get(pbi_type, "string")


def _partition(con, table: str, snapshots: Path) -> dict:
    """M partition against the live DuckDB file, with a Parquet fallback."""
    parquet = snapshots / f"{table}.parquet"
    if not parquet.exists():
        con.execute(
            f"COPY (SELECT * FROM {table}) TO '{parquet.as_posix()}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    return {
        "mode": "import",
        "source": {
            # DuckDB is tried first because it is the live source. If the
            # warehouse is absent the Parquet snapshot loads instead, so the
            # report still opens rather than erroring.
            "type": "m",
            "expression": (
                "let\n"
                "    DuckDbPath = "
                f'"{WAREHOUSE.as_posix()}",\n'
                "    UseLive = File.Exists(DuckDbPath),\n"
                "    Result = if (UseLive) "
                "Table.FromDatabase(DuckDbPath, \"main\", {"
                f'"{table}"}}) '
                f'else File.Contents("{parquet.as_posix()}")\n'
                "in\n"
                "    Result"
            ),
        },
    }


#: A measure name starts a definition when the identifier begins a line. DAX
#: ``VAR`` declarations inside a body also do, so a declaration keyword is
#: excluded explicitly -- matching only ``Name :=`` would silently capture
#: ``VAR LatestYear`` as a measure called "VAR LatestYear".
MEASURE_START = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9 +%]*) :=(?!=)", re.MULTILINE
)


def parse_dax(path: Path) -> list[dict]:
    """Extract measures from the DAX library.

    The library is the single source of truth for measure logic; the PBIP is
    generated from it rather than duplicating the definitions by hand.
    """
    text = path.read_text()
    out = []
    for match in MEASURE_START.finditer(text):
        name = match.group("name").strip()
        # A DAX statement keyword is never a measure name.
        if name.split(" ")[0] in ("VAR", "RETURN", "DEFINE", "MEASURE",
                                 "EVALUATE", "IF", "CALCULATE"):
            continue
        body = text[match.end():]
        # Trim whatever belongs to the next definition or to the trailing
        # formatting notes.
        body = re.split(r"\n// ={5,}", body)[0]
        body = body.split("\n// Formatting")[0]
        expression = body.strip()
        if not expression:
            continue
        out.append({"name": name, "expression": expression})
    return out


def build() -> dict:
    import duckdb

    if not WAREHOUSE.exists():
        raise SystemExit(f"{WAREHOUSE} not found. Run 'make warehouse' first.")
    if PBIP_DIR.exists():
        shutil.rmtree(PBIP_DIR)
    (PBIP_DIR / "definition").mkdir(parents=True, exist_ok=True)
    (PBIP_DIR / "definition" / "snapshots").mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        measures = parse_dax(ROOT / "powerbi" / "measures.dax")
        if len(measures) < 30:
            raise SystemExit(
                f"only {len(measures)} measures parsed from measures.dax; "
                f"the regex no longer matches the file format"
            )

        tables = []
        for name in PHYSICAL_DIMS + PHYSICAL_FACTS:
            columns = _columns(con, name)
            tables.append({
                "name": name,
                "columns": columns,
                "partitions": [{
                    "name": f"{name}-parquet",
                    "source": _partition(con, name,
                                         PBIP_DIR / "definition" / "snapshots"),
                }],
            })

        # Forecast measures project the champion model. They are merged into
        # the DAX library rather than kept separately, so the .dax file stays
        # the single source of truth for measure logic.
        from src.powerbi.generate_report import (
            FORECAST_SOURCE,
            forecast_measure_expressions,
        )

        forecast_partitions = []
        if FORECAST_SOURCE.exists():
            target = (
                PBIP_DIR / "definition" / "snapshots" / "fact_forecast.parquet"
            )
            con.execute(
                f"COPY (SELECT * FROM forecast_workforce) TO "
                f"'{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            forecast_partitions = [{
                "name": "fact_forecast-parquet",
                "source": {
                    "mode": "import",
                    "source": {
                        "type": "m",
                        "expression": (
                            "let\n"
                            "    Source = "
                            f'File.Contents("{target.as_posix()}")\n'
                            "in\n"
                            "    Source"
                        ),
                    },
                },
            }]
            forecast_columns = _columns(con, "forecast_workforce")
        else:
            raise SystemExit(
                f"{FORECAST_SOURCE} not found. Run 'make forecast' before "
                f"'make pbip'; the forecasting page depends on it."
            )

        tables.append({
            "name": "fact_forecast",
            "columns": forecast_columns,
            "partitions": forecast_partitions,
        })

        for name, expression in forecast_measure_expressions().items():
            measures.append({"name": name, "expression": expression})

        # Measures live on a dedicated table so the model has one obvious home
        # for the semantic layer, and so they are not scattered across facts.
        tables.append({
            "name": "_measures",
            "columns": [],
            "partitions": [],
            "measures": [
                {
                    "name": m["name"],
                    "expression": m["expression"],
                    "formatString": _format_for(m["name"]),
                    "displayFolder": _folder_for(m["name"]),
                }
                for m in measures
            ],
        })

        relationships = _relationships(con) + [
            {
                "name": f"fact_forecast_{fcol}_to_{dim}_{dcol}",
                "fromTable": "fact_forecast",
                "fromColumn": fcol,
                "toTable": dim,
                "toColumn": dcol,
                "type": "single",
                "crossFilteringBehavior": "oneDirection",
                # Inactive: the forecast is a fixed projection and must not be
                # filtered by the dimension slicers, or a country filter
                # silently turns a national projection into a partial one.
                "isActive": False,
            }
            for fcol, dim, dcol in (
                ("country_code", "dim_country", "country_code"),
                ("profession_code", "dim_profession", "profession_code"),
                ("year", "dim_date", "year"),
            )
        ]

        model = {
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-GB",
            "culture": "en-GB",
            "tables": tables,
            "relationships": relationships,
        }

        (PBIP_DIR / "definition.pbid").write_text(json.dumps({
            "version": "1.0",
            "datasetReference": {
                "byPath": {
                    "path": f"definition/{PROJECT_NAME}.SemanticModel"
                },
                "byConnection": None,
            },
        }, indent=2), encoding="utf-8")

        model_dir = (
            PBIP_DIR / "definition" / f"{PROJECT_NAME}.SemanticModel"
        )
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "definition.pbism").write_text(json.dumps({
            "version": "1.0",
            "settings": {
                "qnaEnabled": False,
                "allowCompositeModels": True,
                "disableInlineExploration": True,
                "loadTarget": 2,
            },
        }, indent=2), encoding="utf-8")
        (model_dir / "model.bim").write_text(
            json.dumps(model, indent=2), encoding="utf-8"
        )

        project = {
            "version": "1.0",
            "name": PROJECT_NAME,
            "report": {"path": f"{PROJECT_NAME}.Report"},
            "settings": {
                "enableAutoRecovery": False,
                "typeEncoding": True,
            },
        }
        (PBIP_DIR / f"{PROJECT_NAME}.pbip").write_text(
            json.dumps(project, indent=2), encoding="utf-8"
        )

        written = {
            "measures": len(measures),
            "tables": len(tables),
            "relationships": len(model["relationships"]),
            "snapshots": len(list(
                (PBIP_DIR / "definition" / "snapshots").glob("*.parquet")
            )),
        }
    finally:
        con.close()
    return written


def _format_for(name: str) -> str:
    """Format string per measure, so a KPI does not ship as 0.2754000001."""
    lowered = name.lower()
    if "%" in name or "risk %" in lowered or "index" in lowered \
            or "sufficiency" in lowered or "equity" in lowered \
            or "ratio" in lowered or "density" in lowered:
        return "0.0%"
    if "per 1000" in lowered or "per_1000" in lowered:
        return "0.00"
    if "score" in lowered:
        return "0.0"
    if "workers" in lowered or "beds" in lowered or "population" in lowered \
            or "count" in lowered or "regions" in lowered \
            or "countries" in lowered:
        return "#,0"
    return "#,0.00"


def _folder_for(name: str) -> str:
    """Group measures in the field list so 42 measures stay navigable."""
    lowered = name.lower()
    if "gap" in lowered or "shortage" in lowered or "sufficiency" in lowered \
            or "coverage" in lowered:
        return "1 · Coverage and gap"
    if "retirement" in lowered or "aging" in lowered or "replacement" in lowered \
            or "55" in name or "65" in name:
        return "2 · Retirement exposure"
    if "hospital" in lowered or "bed" in lowered or "icu" in lowered:
        return "3 · Hospital capacity"
    if "desert" in lowered or "access" in lowered or "regional" in lowered:
        return "4 · Regional access"
    if "forecast" in lowered or "model" in lowered or "direction" in lowered:
        return "5 · Forecasting"
    return "0 · Headline KPIs"


def _relationships(con) -> list[dict]:
    """Read the declared relationship model and emit it for the project.

    Cardinality and cross-filter direction come from ``RELATIONSHIPS`` rather
    than being restated here, so the warehouse and the Power BI model cannot
    disagree about how the model joins.
    """
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from warehouse.facts import RELATIONSHIPS

    out = []
    for fact, fcol, dim, dcol, _cardinality, active in RELATIONSHIPS:
        out.append({
            "name": f"{fact}_{fcol}_to_{dim}_{dcol}",
            "fromTable": fact,
            "fromColumn": fcol,
            "toTable": dim,
            "toColumn": dcol,
            # Many-to-one from fact to dimension: the dimension filters the fact,
            # never the reverse. Single direction only; bidirectional filtering
            # on these would let a year slicer silently restrict unrelated
            # visuals.
            "type": "single",
            "crossFilteringBehavior": "oneDirection",
            "isActive": bool(active),
        })
    return out


if __name__ == "__main__":
    result = build()
    print(f"PBIP written to {PBIP_DIR}")
    for key, value in result.items():
        print(f"  {key:14s} {value}")
